# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | Claude Code (session_01XWMswUC9h7qTYCt6mLiw2A) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (added in this branch's PR) |
| Related issue or gap ID | Found during a manual review of real end-to-end test-ride data (rides/Stripe/Railway/Supabase), not a filed ACTION_ITEMS.md item beforehand |

## 1. Issue / gap identified

`backend/utils/payment_retry.py`'s `retry_failed_payments()` background loop (runs every 5 minutes on every replica) has a `requires_capture` branch that auto-captures a Stripe PaymentIntent's full owed amount (`grand_total + tip_amount`) whenever it finds one in that state — **without checking whether the ride has actually completed.** A booking-time manual-capture hold legitimately sits in `requires_capture` for the entire life of an active, uncompleted ride (that's just what "authorized, not yet captured" means); this loop treated that identically to the one case it was written for — a *stranded post-settlement* hold (settlement ran, capture failed, e.g. a blank Stripe key mid-flight).

A real test ride showed the resulting failure mode: a rider cancelled a scheduled ride ~26 minutes after booking, before any driver arrived, with the app itself computing a **$0** cancellation fee — yet the full fare had already been captured and nothing was refunded (`cancellation_fee_admin/driver = 0`, `payment_status = "paid"`, `auth_status = "captured"`, `refund_amount = 0.00`).

## 2. Root cause

Two independent gaps combine to produce the loss:

1. **This fix's scope:** the `requires_capture` branch (`payment_retry.py` ~line 542, pre-fix) has no ride-status guard, so it can capture a pre-trip ride's booking hold as if it were a stranded settlement.
2. **Separate, not fixed here:** `backend/routes/rides/cancellation.py`'s hold-handling only acts when `auth_status in ("authorized", "fare_only")` (`_hold_is_live`). Once auth_status is already `"captured"` (from gap 1, or any other path), the cancellation flow has no refund branch at all — confirmed via `grep -n refund backend/routes/rides/cancellation.py` returning zero matches. That gap is tracked separately; this entry only fixes the capture-side trigger.

Exact trigger sequence for the specific observed ride not confirmed via application logs — Railway (the standby deploy target) hadn't redeployed for that window and Fly.io (actual production) logs were not reachable from the reviewing session. The code-level gap (no status guard) is confirmed directly from source, independent of the precise sequence that produced this one row.

## 3. Fix / remediation

Added a status guard: the `requires_capture` branch now skips (logs a warning, takes no Stripe action) unless `ride.get("status") == RideStatus.COMPLETED`. Added `status` to the loop's `SELECT` column list (it wasn't being read before). No other branch of this loop changed.

**First version of this fix had its own bug, caught by a `spinr-money-auditor` review before commit (per CLAUDE.md's mandatory adversarial-review gate):** the skip branch `continue`d without releasing this loop's own earlier `payment_status → 'retrying'` claim. Since both this loop's own scan filter and `process_payment`'s settlement claim exclude `'retrying'`, that would have permanently wedged any skipped ride out of ever being collected — once it completed, settlement would 409 forever. Fixed by releasing the claim back to the ride's pre-claim `payment_status` (no `retry_count` bump, since skipping isn't a failed attempt) before the `continue`. Two regression tests added: one pinning the release itself, one proving the released ride is re-selectable by the scan's own filter on a later tick.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one branch of this one background loop.** `retry_failed_payments()` has exactly one caller (`payment_retry_loop`, registered once in `core/lifespan.py`); grepped every test file referencing `retry_failed_payments`/`payment_retry_loop` (9 files) and ran them all — all pass.
- **What else reads the same state:** `cancellation.py` reads `auth_status`/`payment_status` but this change doesn't touch those fields' write paths for the branches it doesn't gate (succeeded / requires_payment_method / requires_confirmation / canceled all unchanged).
- **Could this regress a flow that currently works?** The one legitimate case this branch existed for — a completed ride whose settlement capture failed transiently — is unaffected: `ride.status` is `"completed"` in that case, so the guard passes through exactly as before.
- **What this intentionally leaves un-recovered:** a *genuinely* stranded requires_capture hold on a ride that is still active (not yet completed, not cancelled) now sits un-captured by this loop. That's correct — it isn't stranded, the ride hasn't reached settlement yet — but if a ride's status update itself gets stuck (a separate, unrelated failure), this loop will no longer paper over that with a premature capture. No other loop currently redoes this specific job for a stuck-active ride; that's an acceptable trade given the alternative was charging riders before their trip happened.

## 5. User-experience effect

Rider-facing, but only as an absence of a bad outcome: a rider whose test/real ride hasn't completed yet no longer risks having their booking hold captured as full payment out from under an in-progress or not-yet-started ride. Not visible mid-session in the sense of any UI change — this is a background-loop behavior change with no client-visible surface.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/payment_retry.py` | Added `status` to the SELECT columns; added a `ride.status == RideStatus.COMPLETED` guard before the `requires_capture` auto-capture, releasing the `'retrying'` claim back to the pre-claim `payment_status` on skip | Stop capturing a pre-trip ride's booking hold as if it were a stranded post-settlement hold, without wedging the ride out of future collection |
| `backend/tests/test_payment_retry.py` | `_make_ride()` now defaults `status="completed"`; added `test_requires_capture_skips_pre_trip_ride` (parametrized over all 5 pre-trip statuses, asserts the claim-release + no counter bump) and `test_requires_capture_skip_is_reselected_on_next_tick` | Keep existing coverage passing under the new guard; regression-cover both the guard and the claim-release |
| `backend/tests/test_payment_retry_coverage.py` | Same `_make_ride()` default-status fix | One pre-existing test (`test_requires_capture_owed_falls_back_to_total_fare`) constructs a ride without `status` and exercises the `requires_capture` branch — needed the same default to keep passing |

## 7. Before / after

```python
# Before
elif intent.status == "requires_capture":
    # A booking-time manual-capture hold that settlement never
    # captured ...
    owed = ride.get("grand_total")
    ...

# After
elif intent.status == "requires_capture":
    # ... (same comment, plus new reasoning)
    if ride.get("status") != RideStatus.COMPLETED:
        logger.warning(f"Payment retry: ride {ride_id} has a requires_capture hold but "
                        f"status={ride.get('status')} (not completed) — skipping auto-capture; ...")
        continue
    owed = ride.get("grand_total")
    ...
```

## 8. Rollback plan

Pure code change, no migration, no feature flag, no data mutation. Revert this commit (or the guard's `if` block alone) to restore the prior behavior — safe because the guard only ever causes the loop to skip a capture it would otherwise have made; it never captures anything the old code wouldn't have. No live data needs remediation to roll this specific change back (the separate refund gap in `cancellation.py` is untouched by this entry either way).

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_payment_retry.py backend/tests/test_payment_retry_coverage.py backend/tests/test_stripe_event_loop_offload.py backend/tests/test_replay_safety_payment_loops.py backend/tests/test_payment_exhausted_alert_once.py backend/tests/test_e4_d10_payment_3ds_quests.py backend/tests/test_booking_import_cancelled_failed.py` — all pass (149 tests across the 7 files), mocked `mock_supabase_client`-style fixtures throughout, no real DB/Stripe calls.
- [ ] Manual repro steps followed in staging — **not done**; no staging Stripe test-mode session run for this fix specifically.
- [x] Blast-radius grep performed: `retry_failed_payments`/`payment_retry_loop` across `backend/` (see Section 4).
- [x] Reviewed against relevant CLAUDE.md conventions: ride state machine (`RideStatus.COMPLETED`, not a hand-rolled string), background-loop replay-safety (guard is a pure read-then-`continue`, doesn't touch the claim/idempotency-key mechanics), Decimal-only money math (unchanged — guard runs before any money computation).
- [ ] Feature-flagged — **not flagged**. This is a background-loop-only behavior change with no client-visible surface and a trivially safe rollback (revert), so a flag was judged unnecessary; if the team disagrees, `app_settings` already has precedent for a loop-scoped kill switch and one can be added.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (single-commit revert)
- [x] Blast radius is stated, not assumed (one caller, 9 dependent test files identified and run)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (Section 5 states the (lack of) visible effect explicitly)

## Related, separate gap (not fixed in this entry)

`backend/routes/rides/cancellation.py` has no refund path for the case `auth_status == "captured"` with a computed `$0` (or partial) cancellation fee — see Section 2. This needs its own Change Impact Log entry, its own Stripe refund call (reusing `services/payment_service.record_refund_event` and the `stripe.Refund.create` pattern already used in `routes/disputes.py`), and its own test coverage, tracked as a follow-up in the same PR/session.
