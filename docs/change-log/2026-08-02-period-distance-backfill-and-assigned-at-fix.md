# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | admin (regulatory/insurance audit reporting), dispatch (Period 2 timestamp) |
| PR / commit link | (this branch) |
| Related issue or gap ID | User follow-up: investigate the `driver_period_distances` coverage gap flagged in the 2026-08-01 billing-reports change log |

## 1. Issue / gap identified

Two things surfaced while investigating why only 2 of 258 completed rides have `driver_period_distances` rows (the source table for the new SGI/Knight Archer insurance billing reports):

1. **Not actually a gap** — the live writer (`record_ride_period_distances`, called from `ride_complete.py`) was deployed 2026-07-28. 256 of 258 completed rides finished *before* that date and were never going to have rows. The 2 rides completed since launch both wrote successfully (100%, not 2/258). No bug in the live write path's coverage.
2. **A real, separate bug found while investigating**: Period 2's `started_at` read `ride.get("driver_assigned_at")`, a key that never exists on a `rides` row (the real column is `assigned_at`) — so the lookup always silently missed and fell back to `driver_accepted_at`. CLAUDE.md is explicit that Period 2 starts at *assignment*, not *acceptance* ("the driver is already obligated to the ride"). This never affected billed distance or dollar amounts — only the `started_at` audit timestamp column.

Once (1) was understood as "no live bug, just no historical data," the user asked to backfill the 256 pre-launch rides so the new billing reports aren't scoped to only-after-2026-07-28.

## 2. Root cause

1. No root cause — the writer is new, historical rides predate it by construction.
2. Copy-paste/naming drift: the writer's field read (`driver_assigned_at`) never matched the actual `rides` schema column (`assigned_at`).

## 3. Fix / remediation

1. **`backend/routes/drivers/ride_complete.py`**: Period 2's `started_at` now reads `ride.get("assigned_at")` (the real column), falling back to `driver_accepted_at` only if `assigned_at` is genuinely absent. `dispatch_service.py` confirmed to write `assigned_at` at assignment time, so this fix is live-effective going forward, not just theoretical.
2. **`backend/scripts/backfill_period_distances.py`** (new, one-off operator script, dry-run by default — same pattern as `reconcile_orphaned_holds.py`): backfills `driver_period_distances` for completed rides that predate the live writer, sourcing Period 2/3 distance from `rides.ride_metrics.phases` (already stored on every completed ride). **Deliberately only backfills a phase with a real GPS-measured `actual_distance_km`** — a phase with only `estimated_distance_km` (the pre-trip route quote) is skipped, never used as a stand-in, since writing an estimate into a table whose entire purpose is GPS-measured driven distance for insurer billing would misstate its provenance. Reuses the existing `record_ride_period_distances()` writer (already replay-safe / idempotent), so the script itself does not duplicate insert logic or the unique-index safety net.

## 4. Risk & impact on existing functionality

- **`assigned_at` fix blast radius**: grepped every call site of `record_ride_period_distances` — only `ride_complete.py:682`. The changed line only affects the `started_at` value written to `driver_period_distances`; it does not touch `distance_km`, `ended_at`, `period`, `driver_id`, or `ride_id`, and does not touch the `rides` table, fare settlement, or any WS event. No other reader of `driver_period_distances.started_at` exists besides the two new billing report endpoints (`_insurance_billing_detail_rows` in `routes/admin/compliance.py`), where it's rendered as `trip_date` — a display-only change, no billed-amount change.
- **Backfill script blast radius**: writes only to `driver_period_distances` via the existing writer function — same table, same function, same safety guarantees (replay-safe, append-only, unique-index-backed) as the live path already has. It is a standalone script, not wired into any request path, background loop, or CI job — it does nothing unless an operator runs it with `--apply`.
- **Dry-run default**: running the script with no flags only reads and logs counts — zero writes. This mirrors `reconcile_orphaned_holds.py`'s established pattern for anything that mutates a live/regulatory table.
- **No double-counting risk**: `record_ride_period_distances` checks existing `(ride_id, period)` rows before inserting and the DB's unique index is the atomic backstop — running the backfill script against a ride the live writer already covered (or running the backfill twice) cannot create a duplicate row or double-bill an insurer.
- **Coverage is still partial by design**: the backfill only fills rides that have `actual_distance_km` in their stored `ride_metrics` — some historical rides only have `estimated_distance_km` (or empty `ride_metrics`) and will remain uncovered after the backfill runs. This is an explicit product decision (see AskUserQuestion response this session), not an oversight — extending coverage to those rides would require either accepting estimated distance as if it were GPS-measured (rejected) or reconstructing distance from raw GPS breadcrumbs (`driver_location_history`), which was not attempted here.

## 5. User-experience effect

**Internal admin only, and indirectly an external one.** No rider/driver-facing change. Once run with `--apply`, the SGI/Knight Archer insurance billing reports (admin-only, Compliance module) will show additional rows for historical GPS-measured trips, changing the reported total km/invoice amount for any date range that includes backfilled rides — this is a real, visible change to what an admin exports and hands to SGI/Knight Archer, not just an internal bookkeeping change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/ride_complete.py` | Period 2 `started_at` reads the real `assigned_at` column instead of a nonexistent `driver_assigned_at` key | Regulatory audit-trail accuracy (CLAUDE.md: Period 2 starts at assignment) |
| `backend/scripts/backfill_period_distances.py` | New one-off backfill script (dry-run default) | Populate historical GPS-measured distance for pre-launch completed rides |
| `backend/tests/test_backfill_period_distances.py` | New — dry-run/apply/failure-handling/estimate-exclusion coverage | Coverage for the new script |

## 7. Before / after

```python
# Before — "driver_assigned_at" never exists on a rides row; always None,
# always silently fell back to driver_accepted_at.
"started_at": ride.get("driver_assigned_at") or ride.get("driver_accepted_at"),

# After — reads the real column.
"started_at": ride.get("assigned_at") or ride.get("driver_accepted_at"),
```

```python
# Backfill: only ever uses GPS-measured actual_distance_km, never estimated.
def _phase_distance_km(ride_metrics: dict, phase_key: str) -> float | None:
    phase = (ride_metrics.get("phases") or {}).get(phase_key) or {}
    value = phase.get("actual_distance_km")   # never estimated_distance_km
    ...
```

## 8. Rollback plan

- `assigned_at` fix: `git revert` — no data written, pure code change.
- Backfill script: **not a `git revert`-only rollback** once run with `--apply` against production, since it writes real rows to an append-only regulatory table (CLAUDE.md: "Never delete or mutate period rows"). If a backfill run needs to be undone, the rows it wrote are identifiable by `source = 'gps_measured_backfill'` (distinct from the live writer's `source = 'gps_measured'`) and by `ride_completed_at` falling before the `--before` cutoff used — an operator could delete specifically those rows via a follow-up SQL statement if the backfill is later found to be wrong, but that is a manual, reviewed action, not an automated rollback path. Recommend running with the default dry-run first and spot-checking the count before any `--apply` run against production.

## 9. Verification performed

- [x] Confirmed live via direct production query (Supabase MCP): 256/258 completed rides finished before the writer's 2026-07-28 launch; the 2 rides completed since launch both have rows (2/2, not 2/258).
- [x] Confirmed via direct production query that `assigned_at` is a real `rides` column (currently `null` on the sampled historical rows, confirming the old code's silent-miss theory) and that `dispatch_service.py` writes it at assignment time for current/future rides.
- [x] `pytest backend/tests/test_backfill_period_distances.py backend/tests/test_period_distance_audit.py` — 9/9 passing.
- [x] Full regression pass on every test file that exercises `complete_ride` (`test_admin_extended.py`, `test_b_p0_2.py`, `test_coverage_rides.py`, `test_drivers_extended.py`, `test_e2e_route_tail_recovery.py`, `test_earnings_snapshot.py`, `test_fare_display_labels.py`, `test_p0_rating_and_payment.py`, `test_quests.py`, `test_ride_state_machine.py`, `test_rides.py`, `test_route_distance.py`, `test_route_distance_osrm.py`, `test_trip_distance.py`) — 467/467 passing, confirming the `assigned_at` fix didn't regress ride completion.
- [x] `ruff check` on all touched/new files — clean.
- [ ] **The backfill script has NOT been run against production** — this change only adds the script and the small fix; running `--apply` against live data is a separate, deliberate operator action the user should take when ready (recommend a dry-run first to confirm the row counts match expectations before writing).
- [ ] No integration test exercises the real `complete_ride` HTTP endpoint end-to-end with a live `assigned_at` value set — the fix is covered by full regression of existing `complete_ride`-adjacent tests (none of which broke) plus direct DB confirmation that the column exists and is populated by `dispatch_service.py`, but not by a new assertion on the exact `started_at` value written during a real completion flow (building that harness was judged not worth the cost for a one-line field-name correction).

## 10. What was NOT verified / deferred

- Whether reconstructing distance from raw `driver_location_history` GPS breadcrumbs (for rides that only have an `estimated_distance_km`, no `actual_distance_km`) is worth doing to close the remaining coverage gap — not attempted; flagged as a possible follow-up if SGI/Knight Archer need full historical coverage.
- Running the backfill script against production — deferred to the user's own operator action.
