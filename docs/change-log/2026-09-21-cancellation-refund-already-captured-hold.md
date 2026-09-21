# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | Claude Code (session_01XWMswUC9h7qTYCt6mLiw2A) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (added in this branch's PR) |
| Related issue or gap ID | Follow-up to `docs/change-log/2026-09-21-payment-retry-requires-capture-pre-trip-guard.md` — same underlying test-ride finding, second of the two gaps identified there |

## 1. Issue / gap identified

`backend/routes/rides/cancellation.py`'s hold-handling only acts when a booking-time Stripe hold is still LIVE (`_hold_is_live`: `auth_status in ("authorized", "fare_only")`). If the hold is already `"captured"` by the time a rider cancels — which can legitimately happen (e.g. the retry loop's `requires_capture` branch, now gated separately, or any other path that captures before trip completion) — the entire hold-handling block was skipped and **there was no refund path anywhere in this file.** Confirmed via `grep -n refund backend/routes/rides/cancellation.py` returning zero matches before this fix. A rider who legitimately owes a computed `$0` cancellation fee could keep paying the full captured fare with no way for the system to give it back.

## 2. Root cause

`_hold_is_live` and the refund-vs-capture branching below it were written assuming the hold could only ever be in an "uncaptured" state at cancel time — true when this code was first written (nothing captured a hold before settlement), no longer true once the retry loop's stranded-hold auto-capture existed. The two code paths (capture-happens-early, cancel-has-no-refund-branch) were never reconciled against each other.

## 3. Fix / remediation

Two additions:

1. **New Stripe helper** `refund_excess_capture()` in `backend/utils/stripe_charge.py`, alongside the existing `capture_cancellation_fee()`/`cancel_authorization()` this file already uses for the live-hold case. Reads the PaymentIntent's own `amount_received` from Stripe (source of truth, not a DB column) and refunds whatever exceeds the fee actually owed. Returns a `ChargeOutcome` with status `refunded` / `not_needed` / `failed` / `unconfigured`, matching the existing helpers' contract exactly so callers switch on it the same way.
2. **New branch** in `cancellation.py`: `elif _auth == "captured" and bool(_booking_pi):` (sibling to the existing `if _hold_is_live:`). Calls the new helper, records a `stripe_refund` ledger row via `payment_service.record_refund_event` (existing helper, already used by the dispute-refund path — not new), and writes `refund_amount` + `payment_status` (`"refunded"` if the computed fee is `$0`, `"partially_refunded"` if a fee was legitimately kept — same vocabulary `routes/webhooks.py` already uses for `charge.refunded` events). On a refund failure, logs at `ERROR` (never silently swallowed per CLAUDE.md) and deliberately does **not** fall through to a fresh fee charge on top of money that's still captured and unresolved.

**Adversarial alternative considered:** cancel the ALREADY-captured PaymentIntent outright via `stripe.PaymentIntent.cancel` instead of a partial `stripe.Refund.create`. Rejected — Stripe rejects `cancel` on an intent that has already moved to `succeeded`/captured (it's a hold-only operation); a refund is the only correct instrument once money has actually moved, which is why `routes/webhooks.py`'s own `charge.refunded` handling and `routes/disputes.py`'s admin-refund path both already use `stripe.Refund.create` rather than `cancel` for this exact situation. Reusing that established pattern (idempotency-key shape, `ChargeOutcome` return contract, `record_refund_event` ledger write) was chosen over inventing a new one, for consistency with the rest of this money surface.

## 4. Risk & impact on existing functionality

- **Blast radius: two files, one new elif branch, isolated by construction.** The new branch only executes when `_auth == "captured"` — the pre-existing `if _hold_is_live:` branch (and its own three tests in `test_cancel_fee_from_hold.py`, `test_ride_cancellation_branches.py`, `test_preauth_release_on_cancel.py`, `test_e2e_cancellation.py`, `test_c2_driver_cancel_atomic.py`, `test_scheduled_cancel_notice_fee.py`, `test_cancellation_service_driver_push.py` — 85 tests total) is untouched and still the only path taken for a live hold. Ran all of them: no regressions.
- **What else reads `refund_amount`/`payment_status` on a ride:** admin-dashboard's rides screens and receipts (`routes/rides/queries.py`, `routes/rides/receipts.py`) read `payment_status` for display — `"refunded"`/`"partially_refunded"` are pre-existing values already handled by every place that branches on `payment_status` (confirmed via the `("paid", "waived_admin", "refunded", "partially_refunded")` tuple appearing identically in `routes/webhooks.py`, `routes/admin/rides.py`), so this doesn't introduce a new payment_status value nothing else expects.
- **Double-charge / double-refund guard:** `fee_taken_from_hold` is set inside the new branch whenever a fee is legitimately owed and covered by the capture, which is the same variable the later fresh-charge block (`if total_cancel_fee > 0 and fee_taken_from_hold <= 0:`) already checks — so the new branch cannot cause the rider to be billed a second time for a fee already covered by the capture. Pinned by `test_partial_fee_refunds_only_the_excess` and `test_capture_already_covers_the_fee_no_refund_needed`.
- **Stripe idempotency:** the refund's idempotency key (`ride-cancelrefund-{ride_id}-{refund_cents}`) includes the ride and the exact cent amount, in its own namespace distinct from `ride-cancelfee-*` (fee capture) and `ride-capture-*` (settlement capture) — a retried cancellation call cannot double-refund, and this key cannot collide with either of those other Stripe calls on the same PaymentIntent.
- **Interaction with background loops:** none directly — `orphaned_hold_reconciler`/`card_hold_release` both select on `OPEN_AUTH_STATES = ("authorized", "fare_only")`, which this branch's precondition (`auth_status == "captured"`) is disjoint from, so this doesn't interact with those sweepers.

## 5. User-experience effect

Rider-facing: a rider who cancels a ride whose hold was already captured (a narrow, previously-silent failure mode) now sees the correct amount refunded to their original payment method instead of losing it permanently. Not visible mid-session in any other sense — no UI/copy change, this is a backend money-correctness fix triggered only by the specific already-captured-hold condition.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/stripe_charge.py` | Added `refund_excess_capture()`; added `cents_to_dollars` to the existing money-helper import | New Stripe refund primitive for an already-captured hold, matching the existing `capture_cancellation_fee`/`cancel_authorization` helper contract |
| `backend/routes/rides/_deps.py` | Exposed `refund_excess_capture` (from `utils.stripe_charge`) and `record_refund_event` (from `services.payment_service`, pre-existing function, not new) in both dual-import branches | `cancellation.py` calls helpers exclusively via `_deps.<name>`, matching every other Stripe/ledger call in the file |
| `backend/routes/rides/cancellation.py` | Added `_excess_refunded` state var; added the `elif _auth == "captured" and bool(_booking_pi):` branch; added the `refund_amount`/`payment_status` write in the final `_base_update` construction | The actual fix — refund the excess when a cancellation lands on an already-captured hold |
| `backend/tests/test_refund_excess_capture.py` (new) | Direct unit coverage of the new helper against a mocked Stripe SDK (9 tests: not-needed / unconfigured / full refund / partial refund / fee-covers-capture / nothing-captured / retrieve error / refund-create error / idempotency-key shape) | Helper-level coverage, independent of the route's own mocking |
| `backend/tests/test_cancel_already_captured_refund.py` (new) | Route-level coverage of the new branch via `cancel_ride_rider` with `_deps` mocked (5 tests: zero-fee full refund, partial-fee excess-only refund, capture-already-covers-fee no-op, refund-failure not silently swallowed, live-hold ride never takes this path) | Mirrors `test_cancel_fee_from_hold.py`'s existing structure/conventions for the live-hold branch |

## 7. Before / after

```python
# Before — cancellation.py, hold-handling
_hold_is_live = bool(_booking_pi) and _auth in ("authorized", "fare_only")
if _hold_is_live:
    ...  # partial capture or full release
# (nothing else — an already-captured hold falls through untouched)
```

```python
# After
if _hold_is_live:
    ...  # unchanged
elif _auth == "captured" and bool(_booking_pi):
    _refund_outcome = await _deps.refund_excess_capture(
        ride_id=ride_id, payment_intent_id=_booking_pi, fee_owed=total_cancel_fee,
    )
    if _refund_outcome.status == "refunded":
        _excess_refunded = _round(_d(_refund_outcome.charged_amount))
        fee_taken_from_hold = total_cancel_fee if total_cancel_fee > 0 else _excess_refunded
        await _deps.record_refund_event(...)
    elif _refund_outcome.status == "not_needed":
        if total_cancel_fee > 0:
            fee_taken_from_hold = total_cancel_fee
    else:
        logger.error(...)  # surfaced loudly, not swallowed
```

## 8. Rollback plan

Pure code addition, no migration, no feature flag, no schema change. Revert this commit to remove the new branch and helper — the ride reverts to the pre-fix behavior (already-captured holds fall through with no refund, i.e. the original bug), which is safe to roll back to in the sense that it doesn't corrupt data, it just re-opens the gap this entry closes. **Not** safe to assume for any refund this code has already issued live: a `git revert` does not claw back a Stripe refund that already happened — that's expected and correct (the refund was correct), not something to roll back.

## Adversarial review (spinr-money-auditor, before commit)

Verdict: **safe to merge**, with 3 findings — 2 fixed in this same commit, 1 deferred as pre-existing/out-of-scope:

1. **Fixed:** `record_refund_event()` was called without a `dedupe_key`, unlike its only other two call sites (`routes/webhooks.py`) which always pass one per the function's own F1 replay-safety contract; its `Optional[str]` return (`None` on ledger-write failure) also went unchecked. Added `dedupe_key=f"stripe_refund|{payment_intent_id}|{refund_cents}"` (same shape webhooks.py uses) and now log at `ERROR` if the ledger write fails — Stripe money moved but the ledger row didn't, needs reconciliation, must not be silent.
2. **Fixed:** the `not_needed` outcome with `total_cancel_fee <= 0` AND nothing captured (an anomalous "auth_status says captured but amount_received is 0" state) previously fell through with no log line. Added a `WARNING` log for visibility.
3. **Deferred, not fixed here (pre-existing, not introduced by this diff):** `_auth`/`_booking_pi` are read from the `ride` fetch at the top of `cancel_ride_rider`, before the atomic cancel claim, and never re-read after it. A hold captured concurrently in that window stays invisible to both `_hold_is_live` and this new `elif`, and would fall into the OLD `_hold_is_live` branch's `capture_cancellation_fee`-fails→fresh-charge fallback against an already-fully-captured PI — a real double-charge path this diff's docstring cites as motivation but does not itself close, because doing so would mean re-reading the ride's payment fields after the claim throughout the whole function, a materially larger change than this fix's scope. Tracked as a follow-up; not blocking because it requires a specific narrow race window (a concurrent capture landing between this function's initial read and its hold-handling block) that is no more likely after this fix than before it — this diff closes the "already captured, cancellation has no refund path" gap; it does not introduce or worsen the separate "stale read before claim" one.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_refund_excess_capture.py backend/tests/test_cancel_already_captured_refund.py backend/tests/test_stripe_charge.py backend/tests/test_stripe_charge_coverage.py backend/tests/test_cancel_fee_from_hold.py backend/tests/test_cancellation_fee_card_charge.py backend/tests/test_preauth_release_on_cancel.py backend/tests/test_ride_cancellation_branches.py backend/tests/test_e2e_cancellation.py backend/tests/test_c2_driver_cancel_atomic.py backend/tests/test_scheduled_cancel_notice_fee.py backend/tests/test_cancellation_service_driver_push.py` — 179 tests, all pass. Mocked Stripe SDK / `mock_supabase_client`-style `_deps` patching throughout — no real DB or Stripe calls.
- [ ] Manual repro steps followed in staging — **not done**; no staging Stripe test-mode run reproducing the exact already-captured + cancel sequence.
- [x] Blast-radius grep performed (Section 4): every reader of `payment_status`/`refund_amount`, every existing hold-handling test file, `OPEN_AUTH_STATES` sweepers.
- [x] Reviewed against relevant CLAUDE.md conventions: Decimal-only money math throughout (`Decimal`/`dollars_to_cents`/`cents_to_dollars`, no float), Stripe idempotency (new distinct key namespace), "never silently swallow a payment/DB error" (the `failed` branch logs at ERROR and explicitly does not fall through to a fresh charge).
- [ ] Feature-flagged — **not flagged.** This closes a money-loss gap with no legitimate reason to keep the old (broken) behavior around as an option; a flag would mean deliberately choosing to keep failing to refund riders. Judged unnecessary; rollback (revert) is trivial if something is wrong with the mechanism itself.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (single-commit revert; explicitly notes what it does NOT undo — already-issued Stripe refunds)
- [x] Blast radius is stated, not assumed (isolated new branch, 179 dependent tests identified and run)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (Section 5 states the visible effect explicitly)
