# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | Claude (agent session, on behalf of vikas@ngitservices.com) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin (import path); analytics functions touch `rides` read paths tagged `rides`/`admin` |
| PR / commit link | branch `worktree-agent-a93d5e5cae5ce69d4`, commits `f9ca62e`..`d84e3cc` (see `git log`) — not pushed, no PR opened per task instructions |
| Related issue or gap ID | ACTION_ITEMS.md A41 |

## 1. Issue / gap identified

941/1,210 (78%) of the previous app's bookings — 712 `cancelled` + 225 `failed` + 2 blank-status — had no import path into Spinr's `rides` table. `booking_import_service.py`'s `build_plan()` hard-filtered on `booking_status == 'completed'`, and its own docstring incorrectly claimed cancelled/failed rows "carry no fare, no earnings, and no history value."

## 2. Root cause

That claim was wrong: every cancelled/failed legacy row carries real pickup/dropoff GPS coordinates and a `created_at` timestamp, which PIPEDA and the Saskatchewan Transportation Act's retention rules require Spinr to retain for cancelled trips too (see `.claude/context/regulatory-sk.md` and `docs/audit/2026-08-19-full-mongodb-export-collection-inventory.md` finding #4). The original 2026-07-29 importer was scoped to the money-bearing case only and was never extended, so the ride-history/retention gap simply persisted uncaught until this session's audit.

## 3. Fix / remediation

Added a second, much lighter branch to `build_plan()` for legacy `booking_status in ('cancelled', 'failed')`, alongside the untouched completed-ride path:

- Reuses phone-based rider/driver matching (factored into a new pure helper, `_match_rider_driver`, also now used by the completed path — a behavior-neutral extraction, verified by the full existing test suite passing unmodified) and the same skip-if-neither-party-matched rule.
- Reuses the same NOT-NULL coordinate/address validation, including the "Address unavailable (imported ride)" fallback + warning.
- **Anomalous-row guard**: any row with BOTH `start_ride_at` AND `complete_delivery_at` populated is excluded from this path entirely (`skipped_looks_completed`), regardless of its legacy status. Verified against the real export: **exactly 7 rows**, all in the `failed` bucket, none in `cancelled`. See §"Deferred finding" below — these are NOT imported by any path in this change.
- Writes `status='cancelled'` (Spinr's state machine has no separate "failed" status), `cancelled_at` (estimated from `created_at` — the only timestamp the export has; no row has `updated_at` populated — flagged `cancelled_at_estimated: true`, mirroring the established `duration_estimated` convention), `cancellation_reason` (legacy text verbatim, or a synthetic `"No driver found (legacy import)"` when blank), `cancelled_by`/`cancellation_type` (mapped from legacy `cancelled_by`: `customer`→`rider`/`rider_cancel`, `driver`→`driver`/`driver_cancel`, blank→`system`/`no_drivers_found`), pickup/dropoff, and `legacy_import_metadata` (adds `cancelled_at_estimated` and `original_booking_status` on top of the completed path's existing shape).
- Writes **no** fare/earnings/payout fields and does **not** add the driver to `driver_ids_to_recount` — "skip payout-offset logic, keep GPS+timestamps," per two independent prior audit passes.
- Migration 349 adds the `legacy_import_metadata = '{}'::jsonb` exclusion predicate to the analytics functions this new class of row would otherwise skew (§4).

## 4. Risk & impact on existing functionality

**Blast-radius check performed** (grep + full read of every candidate before editing):

- `backend/services/booking_import_service.py` — grepped every reference to `TARGET_BOOKING_STATUS`, `plan.rides_to_insert`, `plan.stats`, `rides_planned`. Only two callers: `commit_plan` (same file, needed **zero** changes — it already inserts everything in `plan.rides_to_insert` and only pays out / recounts drivers from data the completed loop populates) and `backend/routes/admin/booking_import.py` (passes `dict(plan.stats)` through generically; the one field it reads explicitly, `sum_offset_payouts`, is untouched by this branch since it never adds to `payout_totals`).
- `rides_planned` stat key: **deliberately preserved its old completed-only meaning** via a new `completed_rides_count` boundary marker captured before the cancelled/failed loop runs, even though both paths now append to the same `plan.rides_to_insert` list. Any external consumer reading `stats.rides_planned` sees the exact same value it always would have for a completed-only CSV. New keys (`cancelled_failed_rides_planned`, `total_rides_planned`, etc.) are additive.
- **Five candidate admin analytics functions** were checked for legacy-cancelled-row blast radius (grepped for the latest `CREATE OR REPLACE FUNCTION` of each, per `backend/migrations/CLAUDE.md`):
  1. `admin_cancellation_breakdown` (migration 165) — **had zero legacy exclusion at all.** Fixed in migration 349.
  2. `admin_analytics_overview` (migration 166) — **had zero legacy exclusion at all**, on any of its total/completed/cancelled/in_progress/searching/scheduled/revenue/daily/hourly keys. Fixed in migration 349 (excluded unconditionally at the source CTE, matching how migration 341's `admin_dashboard_money` — the homepage's equivalent aggregate — already excludes legacy rides from its totals).
  3. `admin_earnings_overview_agg` (migration 341, originally 163/227) — its own COMMENT explicitly claimed "cancelled/funnel keys need no exclusion (completed-only importer)". That assumption is exactly what this change invalidates. Fixed in migration 349 via a new `cancelled_src` CTE (legacy-excluded) feeding only the existing `cancelled` CTE (`cx_count`/`cx_revenue`/`cx_rider_cancels`/`cx_driver_cancels`/`fn_cancelled_after_start`). The shared `cohort` CTE (`fn_requested`/`fn_reached_searching`/`fn_completed`/`fn_price_searches`) was **deliberately left unchanged** — whether legacy-imported *completed* rides should count toward funnel volume is a separate, pre-existing question this change does not decide; narrowing the fix to only what this session's change affects follows CLAUDE.md's "additive over destructive."
  4. `admin_earnings_daily_series` (migration 341) — verified **no fix needed**: sums `status = 'completed'` rows only, already excludes legacy (migration 341). Never counts a cancelled row.
  5. `admin_dashboard_money` (migration 341) — verified **no fix needed**: same reasoning as #4.
- **Sixth function found beyond the 5 originally scoped**: `admin_driver_acceptance_rates` (migration 165, same file as #1, found while reading that file for the blast-radius check). It scans ALL rides with a matched `driver_id`, no legacy exclusion, and computes `cancelled_by_driver` via `cancellation_reason ILIKE '%driver%'` — which this change's own synthetic fallback text (`"No driver found (legacy import)"`) would itself match on top of genuine legacy free-text reasons mentioning "driver". A legacy row only reaches this function when its `driver_id` matched a real Spinr driver by phone (269/712 cancelled + 14/225 failed rows in the real export have a legacy `driver_id` at all). Fixed in migration 349, unconditionally excluding legacy rows from all three of its counts (`total_rides`/`completed`/`cancelled_by_driver`) — the same predicate also incidentally corrects a **pre-existing** gap where a matched driver's already-shipped legacy *completed* rides skewed `total_rides`/`completed` here too (not introduced by this session, but the identical low-risk fix closes both in one pass with no schema change). Flagged explicitly here rather than silently expanding scope without a record of why.
- **New test-account filtering behavior for the new branch** (not explicitly specified in the task, resolved conservatively): the completed path requires BOTH customer and driver country code to equal Canada, because a completed ride always has a driver assigned. A cancelled/failed row usually has **no** driver at all (only 269/712 cancelled + 14/225 failed rows do) — requiring the driver check even when no driver was ever assigned would incorrectly reject the overwhelming majority of this bucket as "test accounts." The new branch checks the customer's country code always, and the driver's only when a legacy driver row is actually present. Covered by three new tests (`test_test_account_customer_is_skipped`, `test_no_driver_at_all_is_not_treated_as_test_account`, `test_test_account_driver_is_skipped_when_driver_present`).
- No interaction with the ride state machine's live transitions (`_require_ride_in_state`) — these are direct inserts of already-terminal `status='cancelled'` rows, exactly like the existing completed path's direct inserts of already-terminal `status='completed'` rows. No WebSocket events are emitted (matches the completed path — these are historical writes, not live transitions).
- No interaction with any of the 18 background loops (`backend/core/lifespan.py`) — this is an on-demand admin-triggered batch import, not a loop.
- No money/wallet deltas of any kind (the entire point of this change).

## 5. User-experience effect

- **Rider**: a rider whose phone matched a legacy `cancelled`/`failed` booking will now see that ride appear in their trip history as a cancelled ride (previously invisible). This is a **visible, additive** change to ride-history content, not a mid-session change — it only affects historical data an operator explicitly imports via the existing admin Bulk Operations → Legacy Booking Import tool, not anything already in flight.
- **Driver**: same — a matched driver sees additional cancelled-ride history entries. `total_rides` is unaffected (these rows are never counted toward it).
- **Internal admin**: the existing Legacy Booking Import validate/commit report (`backend/routes/admin/booking_import.py`) will show the new `plan.stats` counters generically (no route code changes were needed — it already does `dict(plan.stats)`). The admin dashboard's own **UI** does not yet render these new counters distinctly — flagged as a follow-up below, not done in this task (backend-only scope).
- Cancellation-rate KPI dashboards (`admin_cancellation_breakdown`, `admin_analytics_overview`, `admin_earnings_overview_agg`'s `cx_*` keys, `admin_driver_acceptance_rates`) will **not** show a skew from this import, because migration 349 ships in the same change as the importer extension — there is no window where the importer is live but the analytics exclusion is not (both land in this same local commit set; the migration is not applied to any live database by this task per the strict "no DB writes" scope boundary).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/349_exclude_legacy_cancelled_from_cancellation_analytics.sql` | New migration: `CREATE OR REPLACE` on `admin_cancellation_breakdown`, `admin_driver_acceptance_rates`, `admin_analytics_overview`, `admin_earnings_overview_agg` to add `legacy_import_metadata = '{}'::jsonb` exclusion | Prevent legacy-imported cancelled rows from skewing live cancellation-rate KPIs |
| `backend/services/booking_import_service.py` | Docstring/comment corrections; new constants (`LEGACY_CANCELLED_STATUSES`, `NO_DRIVER_FOUND_REASON`); new `_match_rider_driver` helper (also now used by the completed path); target-selection loop now recognizes cancelled/failed statuses; new cancelled/failed per-row loop in `build_plan()`; new `plan.stats` counters; `print_report()` extended | Implements the cancelled/failed import path per A41 |
| `backend/tests/test_booking_import_service.py` | Renamed/updated `test_non_completed_bookings_are_skipped` → `test_unrecognized_status_bookings_are_skipped` (now uses genuinely unknown statuses instead of `cancelled`/`failed`, which the new path makes a false premise for the old assertion) | The old test literally asserted the behavior this task was scoped to change; every other test in the file is unmodified |
| `backend/tests/test_booking_import_cancelled_failed.py` | New file, 23 tests covering the new path | Test coverage for A41 |

`backend/routes/admin/booking_import.py` was read and verified to need **no changes** — see §4.

## 7. Before / after

Target-status filter (`build_plan()`, the behavior this task exists to change):

```python
# Before
for idx, b in enumerate(bookings, start=1):
    if (b.get("booking_status") or "").strip() != TARGET_BOOKING_STATUS:
        skipped_not_completed += 1
        continue
    # ... completed-only handling ...
```

```python
# After
for idx, b in enumerate(bookings, start=1):
    status = (b.get("booking_status") or "").strip()
    if status == TARGET_BOOKING_STATUS:
        # ... completed handling, byte-for-byte unchanged ...
    elif status in LEGACY_CANCELLED_STATUSES:
        # ... new cancelled/failed handling ...
    else:
        skipped_not_completed += 1
```

`rides_planned` stat (preserved meaning despite the shared list):

```python
# Before
"rides_planned": len(plan.rides_to_insert),
```

```python
# After
"rides_planned": completed_rides_count,  # captured before the new loop runs
...
"cancelled_failed_rides_planned": len(plan.rides_to_insert) - completed_rides_count,
"total_rides_planned": len(plan.rides_to_insert),
```

## 8. Rollback plan

- **Code**: none of these commits are pushed or merged (per task scope — local commits only in this worktree). Reverting is `git reset`/dropping the branch; no deploy has happened.
- **If this had shipped and needed to be turned off without a redeploy**: the import is admin-triggered per batch, not automatic — simply stop running the Legacy Booking Import tool against cancelled/failed CSVs (there is no background loop to disable). Already-imported rows can be identified via `legacy_import_metadata->>'original_booking_status' IN ('cancelled','failed')` for a targeted `DELETE` if a bad batch needs removing (no wallet/payout side effects to unwind, by design — this is the whole point of the "no money" scope).
- **Migration 349**: reversible on paper per its own header — re-run migrations 165/166/341's original (unfiltered) function bodies verbatim to restore pre-349 behavior. No new column/index, so there is nothing to drop.

## 9. Verification performed

- [x] Automated tests run (unit only — no live DB in this session):
  - `pytest backend/tests/test_booking_import_service.py -q --no-cov` → **40 passed** (existing suite, one test renamed/updated as described above, rest byte-identical)
  - `pytest backend/tests/test_booking_import_cancelled_failed.py -q --no-cov` → **23 passed** (new)
  - Both together: **63 passed**
- [x] `ruff check` on all touched Python files → all clean
- [x] Blast-radius grep performed — see §4 (service callers, admin route, 5 named + 1 additional analytics function)
- [x] Reviewed against relevant CLAUDE.md conventions: ride state machine (no illegal transition — direct terminal-state insert, matching the existing completed path), PIPEDA/SK retention (this change's entire purpose), migration numbering/append-only/override-annotation conventions
- [ ] Feature-flagged: **not applicable** — this is an admin-triggered one-shot batch import tool (same as the existing completed-ride importer), not a live user-facing flow; there is no "session in progress" to protect
- **Real-export verification** (read-only, no live DB): ran the actual `build_plan()` against the real legacy `bookings.csv`/`customers.csv`/`drivers.csv`/`driverearnings.csv` export from this session's scratchpad (1,210 rows) with an **empty fake Spinr users/drivers table** (no live DB available). Confirmed directly, not just asserted: 712 cancelled / 271 completed / 225 failed / 2 blank status breakdown matches prior research exactly; **anomalous "looks completed" count is exactly 7, and all 7 are in the `failed` bucket** (0 in `cancelled`); blank-status rows are excluded exactly as 2. See final report for the full instrumented output.

## 10. What was NOT verified

- **No live Supabase.** All tests run against the fake in-memory Supabase client (`_FakeSupabase`/`_FakeQuery`), same as every other importer in this session. The migration was never applied to any database.
- **No live Stripe cross-check** — not applicable to this change (no money touched), but noted since the CLAUDE.md template asks for it explicitly.
- **No admin-dashboard UI change or verification** — out of strict scope per the task. The dashboard's booking-import screen will receive the new `plan.stats` keys through the existing generic pass-through but has no dedicated display for them yet. **Follow-up flagged, not filed to ACTION_ITEMS.md** (per task instruction — the calling session will consolidate): the admin Bulk Operations → Legacy Booking Import screen should surface the new cancelled/failed counters (cancelled/failed target rows, imported, skipped-looks-completed, skipped-unmatched) alongside the existing completed-row report.
- **Real-world "N rows will actually import" cannot be stated as a single number without live DB access.** Against the real export, after the test-account filter and the anomalous-row guard, **865 rows** (663 cancelled + 209 failed − 7 anomalous) are viable candidates that reach the rider/driver phone-matching stage; how many of those actually resolve to a real Spinr `users`/`drivers` row (and therefore import — recall a row imports if EITHER party matches) can only be determined against the live database, exactly the same limitation already documented for the original completed-ride importer. This task did not have live DB access, per its own scope boundary ("Do NOT run any commit/apply against a real database").
- **7-anomalous-row deferred finding (new, not previously tracked)**: confirmed directly against the real export — 7 rows with `booking_status='failed'` but `driver_id` present, `start_ride_at` and `complete_delivery_at` both populated, real `total_amount` ($5.75–$9.48) and real `you_earn` ($6.22–$9.75). These are structurally indistinguishable from a completed trip, most likely mislabeled by a payment-settlement failure flag in the old app rather than "the ride never happened." **Not imported by any path in this task.** This needs a future session with Stripe/live-DB context to decide whether to import them via the completed-ride path (with the same earnings/payout rigor that path already has) or otherwise. Not filed to ACTION_ITEMS.md directly per this task's instructions — flagged here for the calling session to consolidate.
- **No visual/snapshot regression tooling exists for this surface** (backend-only, no UI touched) — not applicable here, but noted per the standing CLAUDE.md gap.

## Sign-off

- [x] Rollback plan is concrete and testable (targeted DELETE by `legacy_import_metadata->>'original_booking_status'`; migration re-run of pre-349 bodies)
- [x] Blast radius is stated, not assumed (5 named functions + 1 additional found and fixed, with reasoning for each; 2 functions in the same migration file verified as needing no change)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5)
