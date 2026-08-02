# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (branch: `claude/fix-mark-stripe-event-processed`, same session/branch as the `mark_stripe_event_processed` fix) |
| Related issue or gap ID | `ACTION_ITEMS.md` C10 |

## 1. Issue / gap identified

Two code comments in this codebase (the old `mark_stripe_event_processed` docstring, and `routes/webhooks.py`'s unhandled-event-type branch) — plus the original table migration's own top comment (`migrations/22_stripe_events.sql`) — all claimed a background job reconciles `stripe_events` rows stuck at `processed_at IS NULL`. No such job existed anywhere in the codebase (verified by grep before starting this fix).

## 2. Root cause

The `stripe_events` table (migration 22, 2026-04-era) was designed with this reconciliation sweep as part of the plan from the start — the migration's own comment describes it and even ships a purpose-built index (`idx_stripe_events_unprocessed`) for exactly this query — but the sweep itself was apparently never implemented. The comments describing it were never removed or flagged as aspirational, so they read as documentation of existing behavior rather than a TODO.

## 3. Fix / remediation

Added `_reconcile_stuck_stripe_events()` to `backend/utils/stripe_reconcile.py`, wired into the existing daily `_run_reconciliation_tick()` (02:00 UTC, same Redis `SET NX EX` leader lock already used by every other check in that file — no new background loop). It queries `stripe_events` for `processed_at IS NULL` rows older than a 5-minute grace window (matching the original migration comment's suggested window) and surfaces each as a `STRIPE_EVENT_STUCK_UNPROCESSED` discrepancy via `logger.error` (Sentry-bridged, `domain=payments`) plus this file's existing daily `audit_logs` summary write.

**Deliberately detection/alert-only, not auto-replay.** The original comments all said the job "should replay" stuck events. This implementation does not do that — see §4 for why.

## 4. Risk & impact on existing functionality

- **Blast radius: additive, isolated to `utils/stripe_reconcile.py`'s existing daily tick.** No new loop registered in `core/lifespan.py`; reuses the file's existing lock/schedule infrastructure. `mark_stripe_event_processed` and `routes/webhooks.py` were touched only for comment corrections (no logic change) pointing at the real sweep instead of a nonexistent one.
- **Why detection-only, not replay:** `utils/stripe_reconcile.py`'s own established pattern for every other discrepancy type it already detects (`STRIPE_ORPHAN`, `RIDE_PAYMENT_STUCK_PROCESSING`, stranded payouts) is detection-only; where auto-heal exists at all (`_maybe_heal_stuck_processing`, for stuck-processing rides) it's behind an explicit, default-OFF app-setting flag ("ships dark"). Matched that convention rather than inventing a new one. Concretely: a stuck `stripe_events` row can mean either (a) the handler already finished all business logic successfully and only the final `processed_at` stamp failed — replaying this would re-run wallet credits, notifications, etc. a second time — or (b) the handler never ran at all (unhandled event type). This job cannot reliably distinguish (a) from (b) without trusting the stored payload's own claims about what already happened, which it does not do. Auto-replay risked a double-wallet-credit or double-notification bug in exchange for closing a rare, already-loudly-logged gap — not a good trade. Surfaced for manual review instead.
- **Interaction with the 16 background loops:** none — reuses an existing loop's tick, doesn't add a 17th/18th.
- **Interaction with money/wallet deltas:** none — this function only reads `stripe_events` and writes to `audit_logs` (the same table this file already writes to daily); it never touches `wallets`, `wallet_transactions`, or any Stripe API.
- **Query cost:** one additional `get_rows` call per daily tick against `stripe_events` filtered on `processed_at IS NULL`, using the purpose-built index from migration 22 (`idx_stripe_events_unprocessed`) — cheap, and this table's unprocessed-row count should be near-zero in steady state.
- **Test-mock collision guarded against:** the existing test suite mocks `db_supabase.get_rows` with a single blanket `return_value` across multiple tables within one tick (e.g. `_ride()`-shaped rows also getting returned when this new function's `stripe_events` query fires). Added a defensive `if not row.get("event_id")` re-assert (mirroring the sibling `_reconcile_stuck_processing_rides`'s own defensive pattern) specifically so a mismatched row shape from that mocking style can never be misread as a stuck Stripe event — verified this doesn't require touching any pre-existing test.

## 5. User-experience effect

`none` — backend-only. Internal-admin-facing indirectly: a genuinely stuck `stripe_events` row will now show up in the daily `audit_logs` summary and trip a Sentry alert, where previously it would have been permanently invisible.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/stripe_reconcile.py` | New `_reconcile_stuck_stripe_events()`, new `_STUCK_STRIPE_EVENT_AFTER` constant, wired into `_run_reconciliation_tick`'s discrepancy list + audit summary, module docstring updated | The actual sweep |
| `backend/tests/test_stripe_reconcile.py` | 4 new tests (aged-row flagged / fresh-row skipped / no-timestamp over-reported / query-failure never raises / defensive re-assert) | Regression coverage for the new function |
| `backend/repositories/wallet_repo.py` | `mark_stripe_event_processed` docstring + error-log message updated to point at the real sweep instead of describing a job that didn't exist | Keep the comment honest now that the sweep exists |
| `backend/routes/webhooks.py` | Two comments (mid-processing-crash path, unhandled-event-type path) corrected the same way | Same reason |
| `ACTION_ITEMS.md` | C10 closed out with the fix summary and scope-decision rationale | Tracking |
| `docs/change-log/2026-08-01-c10-stripe-events-reconciliation-sweep.md` | New file | This log |

## 7. Before / after

```python
# Before — routes/webhooks.py, unhandled-event-type path
        # Leave processed_at NULL for unknown/unhandled events so the nightly
        # reconciliation job can replay them if they later become actionable.
        # Return 200 to Stripe so it does not retry indefinitely.
        return {"received": True, "unhandled": True, "event_id": event_id}
```

```python
# After
        # Leave processed_at NULL for unknown/unhandled events so
        # utils/stripe_reconcile.py's daily sweep surfaces them for manual
        # review if they later become actionable (it does not auto-replay
        # -- see _reconcile_stuck_stripe_events, ACTION_ITEMS.md C10).
        # Return 200 to Stripe so it does not retry indefinitely.
        return {"received": True, "unhandled": True, "event_id": event_id}
```

`_run_reconciliation_tick()` behavior change: previously wrote a daily `audit_logs` summary with no visibility into `stripe_events`; now also includes `stripe_events_stuck_unprocessed` count and, when non-zero, `STRIPE_EVENT_STUCK_UNPROCESSED` entries in `discrepancy_detail` — purely additive to the summary's shape (existing keys unchanged).

## 8. Rollback plan

`git-revert-safe` — a plain `git revert` removes the new function, its call site, and the comment corrections. No schema, migration, or data change; the daily tick continues to run exactly as before, just without this one additional check. No feature flag needed since this is detection-only with no user/rider/driver-facing behavior to gate.

## 9. Verification performed

- [x] Automated tests run — `pytest tests/test_stripe_reconcile.py -q` → **45 passed** (41 pre-existing + 4 new).
- [x] New function coverage measured in isolation — `coverage run --source=utils.stripe_reconcile -m pytest tests/test_stripe_reconcile.py` → whole-file 87%, zero missed lines within `_reconcile_stuck_stripe_events`'s own line range (100% covered by the 4 new tests).
- [x] Regression check on touched call-adjacent files — `pytest tests/test_webhooks_main.py tests/test_wallet_repo.py -q` → 127 passed.
- [x] Full backend suite re-run: `pytest tests/ -q` → **6786 passed, 8 skipped, 1 xfailed, 0 failed** (279s) — exactly +4 over the pre-change 6782, confirming only the new tests were added and nothing else regressed.
- [ ] Manual repro against real Supabase/Stripe — not applicable; this is a pure DB-read reconciliation check, all mocked in tests per this file's existing convention.
- [x] Blast-radius grep performed — see §4.
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — followed the `spinr-background-loop` skill's guidance (reused existing loop infrastructure rather than adding a new one; idempotency key `event_id` already exists on the table); matched this file's own established detection-only / flag-gated-heal-if-any convention rather than introducing a new risk pattern.
- [ ] Feature-flagged — not applicable; detection-only, no user-visible behavior change to flag.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed — additive to one existing file/loop, every touched file enumerated in §6
- [x] No silent behavior change to an already-shipped flow — the daily tick's existing behavior (Stripe PI/payout/stuck-ride checks) is unchanged; this only adds a new, independent check to the same summary

## What was NOT verified

- Not exercised against a real Supabase instance with an actual stuck `stripe_events` row — all DB calls are mocked, consistent with this module's existing test convention (the whole file has never been tested against live Supabase/Stripe).
- The 5-minute grace window is a design choice matching the original migration comment's suggestion, not empirically tuned against real production webhook-latency data (none was available in this sandbox). If false positives turn out to be common in practice (e.g. genuinely slow webhook handlers), the window is a one-line, easily-adjusted constant.
- Auto-replay was deliberately not built (see §4) — if a future need arises to actually self-heal unhandled-event-type rows once they become actionable, that is separate, larger work with its own risk analysis, not silently deferred by omission here.
