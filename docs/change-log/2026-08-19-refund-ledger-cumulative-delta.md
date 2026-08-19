# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (this branch) |
| Related issue or gap ID | Follow-up to PR #4244 — found by a security-auditor pass requested after that PR merged |

## 1. Issue / gap identified

`charge.refunded`'s ledger write used Stripe's `amount_refunded`, which is **cumulative** on the charge, not the delta of the specific event. Two sequential partial refunds on the same charge (e.g. $10, then $10 more out of $50) each fire their own webhook event with a different `event_id` — `claim_stripe_event` does not (and should not) dedupe them, since they're legitimately distinct events — and each wrote the full cumulative amount to `financial_events` instead of just its own increment. Net effect on the example above: -$30 recorded against a charge that only ever refunded $20.

## 2. Root cause

`record_refund_event(refund_cents=...)` was called with `refunded_cents = charge["amount_refunded"]` directly, with no tracking of what had already been recorded for that ride from a prior `charge.refunded` event. The same conflated variable also fed the rider-facing push notification and email, so a second partial refund would have told the rider the wrong (cumulative, not incremental) dollar amount was "just" refunded.

## 3. Fix / remediation

Before processing a `charge.refunded` event, read the ride's existing `refund_amount` (set by the previous event, defaulting to 0 for the first refund on a ride) and compute `delta_cents = amount_refunded - previous_refund_amount_in_cents`. That delta — not the cumulative total — is what's passed to `record_refund_event` for the ledger write, and what's shown in the push notification and refund email. `rides.refund_amount` itself continues to store the running cumulative total, which is the correct thing for that field to mean.

Added a companion guard: if `delta_cents <= 0` (a stale or out-of-order webhook delivery — an older event's retry arriving after a newer one already applied, or a duplicate that slipped past `claim_stripe_event` under a different `event_id`), the handler skips the ledger write and does not move `payment_status`/`refund_amount` backward, but still falls through to the function's shared `mark_stripe_event_processed(event_id)` tail call rather than returning early — an early `return` here would have left the event permanently unmarked-processed and caused Stripe to retry it forever.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to the `charge.refunded` handler in `backend/routes/webhooks.py` and its one caller of `record_refund_event`.** Grepped for other callers of `record_refund_event` — none exist outside this handler.
- `rides.refund_amount`'s meaning (cumulative total refunded) is unchanged — only what gets passed to the ledger, push, and email changed to be the per-event delta instead.
- No interaction with the ride state machine, background loops, or wallet deltas. `driver_earnings` is still untouched by a refund (unchanged policy, driver keeps their pay).
- Every existing test in this area passed unmodified, because a ride with no prior `refund_amount` computes a delta identical to the old cumulative value (0 previous → delta == cumulative) — the fix is only observably different starting from a ride's *second* refund event, which no pre-existing test constructed.

## 5. User-experience effect

Rider-facing: the refund push notification and email now report the correct incremental dollar amount for each refund event, instead of the ride's running cumulative total. A rider who receives two separate $10 refunds will now see "$10.00" both times, not "$10.00" then "$20.00" on the second one. This is a correction to existing behavior, not new behavior — the old message was simply wrong on any ride's second-or-later partial refund.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/webhooks.py` | `charge.refunded` handler now computes and uses `delta_cents`/`delta_amount` instead of the raw cumulative `amount_refunded` for the ledger write, push notification, and email; added a stale/out-of-order guard that skips writing without an early `return` | Fix the cumulative-vs-delta ledger double-count; prevent backward-moving writes from out-of-order delivery without breaking Stripe's retry contract |
| `backend/tests/test_routes_webhooks_coverage.py` | Added a sequential-second-partial-refund test (asserts the ledger gets only the delta) and a stale/out-of-order test (asserts no write occurs but the handler still completes and marks the event processed) | Cover both new branches with the exact scenario the security audit identified |

## 7. Before / after

```python
# Before
refunded_cents = int(charge.get("amount_refunded", 0))
...
await record_refund_event(..., refund_cents=refunded_cents, ...)   # cumulative, double-counts on 2nd+ refund
...
f"Your refund of ${refunded_amount:.2f} ..."                       # cumulative, wrong on 2nd+ refund
```

```python
# After
refunded_cents = int(charge.get("amount_refunded", 0))
previous_refunded_cents = dollars_to_cents(ride.get("refund_amount") or 0)
delta_cents = refunded_cents - previous_refunded_cents
if delta_cents <= 0:
    ...  # log + skip write, still fall through to mark_stripe_event_processed
else:
    delta_amount = cents_to_dollars(delta_cents)
    ...
    await record_refund_event(..., refund_cents=delta_cents, ...)  # only this event's increment
    ...
    f"Your refund of ${delta_amount:.2f} ..."                      # only this event's increment
```

## 8. Rollback plan

`git-revert-safe`. No migration, no schema change. Any `financial_events` rows already written under the old cumulative behavior before this fix are not automatically corrected by this change — a revert doesn't undo already-applied ledger writes. If a real production case of the double-count is later found to have already occurred, that needs a manual ledger correction, not a code rollback — this fix only prevents the defect going forward.

## 9. Verification performed

- [x] Automated tests run: full refund/ledger-adjacent test surface — `test_routes_webhooks_coverage.py`, `test_webhooks_coverage_gap.py`, `test_webhooks_main.py`, `test_admin_rides_coverage.py`, `test_admin_extended.py`, plus every test file matching `refund` or `ledger` repo-wide (199 passed, 1 unrelated skip, 0 failures).
- [x] Two new regression tests specifically reproduce the reported defect (sequential partial refunds ledger only the delta) and the adjacent out-of-order-delivery race the same fix closes.
- [x] `ruff check` on both changed files — clean.
- [x] Reviewed against `CLAUDE.md`'s "Do not silently swallow errors" convention — the stale-event skip path logs at `info` with full context (ride_id, both cent amounts, event_id) rather than silently dropping the event.
- [ ] Not run against a real Stripe webhook or live Supabase — mocked/unit-tested only, consistent with this repo's existing webhook test conventions.

## 10. Sign-off

- [x] Rollback plan is concrete (plain `git revert`, no migration) — with the explicit caveat above that already-applied bad ledger writes need manual correction, not a code revert
- [x] Blast radius is stated: isolated to one handler and its single caller of `record_refund_event`
- [x] No silent behavior change without the UX field filled in — the push/email amount correction is stated above

## What was NOT verified

- Not exercised against a real Stripe test-mode webhook delivering two genuinely sequential partial-refund events.
- Did not audit or attempt to correct any `financial_events` rows that may already exist in production from before this fix — this session has no way to determine whether the double-count defect has actually occurred on a real ride to date (per the existing A40 investigation, no native ride has completed a real payment yet, which would mean no real partial-refund sequence has occurred either — but that inference wasn't independently re-verified in this pass).
- ~~The out-of-order guard's `delta_cents <= 0` check assumes `rides.refund_amount` is only ever written by this one handler~~ — checked: grepped every `refund_amount` write across the backend. `routes/disputes.py` writes only to the separate `disputes.refund_amount` column (the dispute's own record of what it resolved), never `rides.refund_amount`; `payment_service.py`'s `refund_amount` reference is ledger-entry metadata, not a `rides` write. Confirmed `rides.refund_amount` has exactly one writer: this handler.
