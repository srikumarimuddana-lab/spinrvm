# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code (agent session) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (attached to the PR this commit ships in) |
| Related issue or gap ID | Sentry issue 7726298620 / 7726298683 (ride abdbb3a0-cb0a-4da5-984d-b41b400f3f30) |

## 1. Issue / gap identified

Every card pre-authorization hold placed at ride booking (`_preauthorize_ride_card` → `authorize_ride`) was failing outright with a Stripe 400 `invalid_request_error`: "This account is not eligible for the requested card features." The booking itself still succeeded (the caller degrades gracefully to no-hold-at-all, see §2 of `backend/routes/rides/booking.py`), but the pre-auth hold — the mechanism that surfaces a dead/declined card *before* a driver is dispatched — silently never got placed, for what looks like every card booking on this account, not just this one ride.

## 2. Root cause

`authorize_ride` (`backend/utils/stripe_charge.py`) always sends `payment_method_options.card.request_incremental_authorization: "if_available"` on the PaymentIntent create call, so a post-trip tip can ride on the same hold instead of a second charge. The code's own capability read-back (`_reads_incremental_support`) assumed Stripe would only ever report this as unavailable *per card*, on a successful charge. In fact this Stripe **account** is not enrolled in Stripe's incremental/extended-authorization feature at all, so Stripe rejects the *entire* PaymentIntent.create request with a 400 the moment the param is present — regardless of `"if_available"` — for every card, every time. This is the same class of bug flagged in this file's own comment two lines above (`automatic_payment_methods.allow_redirects: "never"`, added after redirect-enabled PaymentIntents broke every booking pre-auth the same way), and was explicitly called out as a risk in `backend/tests/test_authorize_incremental_shape.py`'s docstring ("a bad create param risks a 400 on EVERY booking authorization") — the risk that docstring warned about was account-level ineligibility, not a param-shape bug, so the existing shape tests didn't catch it.

## 3. Fix / remediation

`authorize_ride` now detects this specific Stripe error (`_is_incremental_auth_ineligible`, matched on Stripe's fixed error text — there is no stable `code` for it) and retries the PaymentIntent.create call exactly once, with `payment_method_options.card.request_incremental_authorization` removed and a distinct idempotency key (Stripe rejects idempotency-key reuse when the request params differ). Any other Stripe error is unaffected — it still returns `status="failed"` as before, and a decline on the retry still returns `status="declined"` with the real decline code. The hold now goes through on this account; the only loss versus a fully-enrolled account is the tip-merge optimization (a tip becomes its own charge — one extra Stripe fixed fee — instead of riding the same hold), which is the same fallback `_reads_incremental_support` already produces when a card itself doesn't support it.

## 4. Risk & impact on existing functionality

- `request_incremental_authorization` is sent from exactly one place in the codebase: `authorize_ride`. Grepped `backend/` for `request_incremental_authorization` and for every caller of `authorize_ride` — only `backend/routes/rides/booking.py`'s `_preauthorize_ride_card` (the booking-time hold) and its fare-only retry branch call it. `charge_ride` (immediate capture) and `capture_ride` (settlement) never send this param, so they are unaffected.
- Blast radius: isolated to the pre-auth hold path. No other table, state field, or background loop reads `authorize_ride`'s output besides the existing `_PreauthOutcome` handling already in `booking.py` (unchanged).
- No change to the ride state machine, wallet deltas, or corporate billing.
- Best-effort verification limit: I could not query Stripe directly (the Stripe MCP connector is unauthenticated in this session) to confirm whether this account's ineligibility is permanent/account-wide or a transient Stripe-side flag, and could not query Sentry for how many other rides hit the same error before this fix (Sentry MCP also unauthenticated here) — the fix is written to be safe either way (self-detects, retries, falls back to today's `failed` behavior if the retry itself errors), but I'm flagging this as unverified against Stripe/Sentry directly rather than assuming it.

## 5. User-experience effect

No rider- or driver-facing UI change. The rider-app booking flow is unchanged either way (it already handled the no-hold degrade silently). The user-visible effect is entirely on the ops/safety side: a card that would previously have gone undetected as dead/declined until settlement will now, once again, surface at booking time — this is a restoration of existing intended behavior, not a new behavior. No mid-session visibility change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/stripe_charge.py` | Added `_is_incremental_auth_ineligible()`; `authorize_ride`'s `_StripeBaseError` handler now retries once without `request_incremental_authorization` on that specific error, with its own Card/Base/Exception handling for the retry | Restore the pre-auth hold on an account not enrolled in Stripe's incremental-authorization feature |
| `backend/tests/test_authorize_incremental_shape.py` | Added `TestAccountIneligibleForIncrementalAuth`: retry-and-succeed, unrelated-error-does-not-retry, retry-also-fails-returns-failed | Regression coverage for the production incident |

## 7. Before / after

```python
# Before
except _StripeBaseError as e:
    logger.error("Stripe error authorizing ride=%s rider=%s: %s", ride_id, rider_id, e)
    return ChargeOutcome(status="failed", error_message=str(e))
```

```python
# After
except _StripeBaseError as e:
    if not _is_incremental_auth_ineligible(e):
        logger.error("Stripe error authorizing ride=%s rider=%s: %s", ride_id, rider_id, e)
        return ChargeOutcome(status="failed", error_message=str(e))
    logger.warning(
        "[preauth] account not eligible for incremental authorization; retrying hold without it for ride=%s",
        ride_id,
    )
    fallback_params = {**params, "payment_method_options": {"card": {}}}
    try:
        # Same call shape as the first attempt (secret + a fresh idempotency
        # key) — see authorize_ride() for the exact wiring.
        intent = await asyncio.to_thread(lambda: stripe.PaymentIntent.create(**fallback_params, ...))
    except _StripeCardError as e2:
        ...  # same decline mapping as the first attempt
    except _StripeBaseError as e2:
        logger.error(...); return ChargeOutcome(status="failed", error_message=str(e2))
```

## 8. Rollback plan

Pure code change, no migration, no feature flag, no data written differently — a `git revert` of this commit is a complete rollback (it only affects which Stripe API call is attempted at authorization time; no live data shape changes). If reverted, behavior returns to today's silent-degrade-to-no-hold — not a regression relative to current production, since that is the current state.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_authorize_incremental_shape.py tests/test_stripe_charge_coverage.py tests/test_ride_preauth.py tests/test_ride_preauth_booking.py tests/test_preauth_release_on_cancel.py -q --no-cov` → 125 passed, 0 failed.
- [x] `ruff check` on both changed files → clean.
- [ ] Manual repro steps followed in staging — **not done**; no staging Stripe account with the same ineligibility was available in this session.
- [x] Blast-radius grep performed: `request_incremental_authorization` (1 usage site), `authorize_ride(` (all callers, backend + tests).
- [x] Reviewed against relevant CLAUDE.md convention: Decimal-only money math (unchanged — no money values touched), Stripe idempotency (new idempotency key correctly derived, distinct per attempt), "do not silently swallow errors" (retry failure still surfaces as `failed`, still logged at `error`).
- [x] Not user-visible / non-trivial in the UI sense — no feature flag needed (backend-only, ops-facing behavior restoration).

## What was NOT verified

- Not verified against a real Stripe account/sandbox reproducing the exact ineligibility — reasoned from the Stripe SDK error shape and this repo's own established test-mocking conventions, not screenshotted/replayed against live Stripe.
- Did not confirm via Sentry how many rides were affected before this fix, or whether this Stripe account's ineligibility is a known, deliberate Stripe account configuration (in which case the account-level fix might instead be to ask Stripe to enroll it, making this code fallback permanent-safety-net rather than a first-choice fix) — Sentry/Stripe MCP connectors are unauthenticated in this session, so I could not check either.
