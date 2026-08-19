# Phase 2 — scheduled Regina daily rollup (Distance Travelled data layer)

**Date:** 2026-08-19
**Surface:** backend drivers/admin (live-tested), 7 commits
**Trigger:** tracking-overhaul roadmap Phase 2. The Distance Travelled table needs trustworthy per-driver per-day km + duration per phase; the existing rollup was manual-only (cron hitting an admin endpoint), UTC-bucketed, and its SQL function summed GPS teleports raw.

## Issue/gap identified
(1) `driver_daily_stats` was only written when something called the admin endpoint — days without a call simply never rolled up; (2) days were UTC buckets, splitting Saskatchewan evenings across two business days; (3) `compute_driver_phase_distances` had no anomaly filter — one tower-handoff teleport inflated a day by ~230 km (pinned as a "known divergence" from settlement); (4) no per-phase duration; (5) driver discovery scanned 10k raw breadcrumbs and missed drivers past the cap.

## Root cause
The rollup was built as a one-off admin maintenance tool, not a product data layer; settlement's anomaly-filter learnings never propagated to the SQL function.

## Fix/remediation (by commit)
1. `ddf1340` — migration 347 (idle/navigating/trip **seconds** + `day_tz` on driver_daily_stats), migration 348 (function v2: settlement's exact caps 5 km/300 s/150 km/h, per-phase seconds, `arrived_at_pickup` folded into the pickup-way bucket, always-one-row); parity suite rewritten — spike-filter parity now **asserted**, verified on a real Postgres 16.
2. `aae36c8` — migration-review blocker: `REVOKE EXECUTE ... FROM PUBLIC, anon, authenticated` on the SECURITY DEFINER function (it takes an arbitrary driver_id over RLS-protected GPS data; v1 was exposed via `/rest/v1/rpc/` since migration 54) + `day_tz` CHECK constraint.
3. `613b4d8` — `utils/driver_daily_rollup.py`: shared Regina-day core; discovery via insurance-period overlap ∪ rides-that-day; scheduler (2 completed days per 30-min tick, 7-day sweep nightly after 02:00 Regina, never a partial today).
4. `dc13272` — loop registered in lifespan + `_WATCHDOG_LOOP_NAMES` + `LOOP_THRESHOLDS` (90 min).
5. `8b64ba1` — admin endpoint delegates to the shared core; completed-day guard made Regina-aware.
6. `9e9bc31` — leaderboard freshness top-up boundary = covered day's **Regina end** (was UTC midnight → would have double-counted 6 evening hours of rides/earnings daily once Regina rows landed).
7. `7d73f28` — nightly sweep converts up to 14 legacy `day_tz='utc'` days per night (newest first) within the 90-day GPS retention window; older rows are left untouched (breadcrumbs purged — re-deriving would zero km). `9857c29` — daily-activity endpoint merges the rollup summary for closed days (`day_source='rollup'`, additive `gps_km`/`gps_seconds`).

## Risk & impact on existing functionality
- **driver_daily_stats readers (full blast radius, each verified):** `routes/drivers/earnings.py` weekly/monthly (re-buckets whole rows — no boundary math, a day's rides may shift ±6h between adjacent weeks under the Regina definition, which is the intended business-day correction); `routes/drivers/referrals.py` leaderboard (boundary fixed in this batch — see commit 6; transient direction chosen is *undercount-until-heal*, never inflation); admin `drivers/{id}/stats` endpoint (passes rows through — new fields appear additively in its JSON); leaderboard RPC migration 204 (aggregates named columns — unaffected).
- **`compute_driver_phase_distances` callers:** the admin endpoint and the new loop, both updated in-batch; the function swap is atomic (single-transaction DROP+CREATE).
- **Km values will drop for spike-affected days** when a legacy day is re-derived — that is the correction, not a regression (settlement always rejected those segments; only the rollup counted them).
- **Insurance/period data:** untouched. Fare/money paths: untouched (earnings sums still come from ride rows).
- **Load:** loop adds ~2 RPC sweeps per driver-day per 30 min for two days — bounded by active drivers; the RPC returns 11 scalars.

## User experience effect
Driver-facing weekly/monthly earnings *buckets* shift to Saskatchewan business days as history converts (totals identical; day/week attribution more accurate). Leaderboard totals stop being inflatable by GPS teleports. Admin daily-activity gains idle-roaming km for closed days. Nothing rider-facing.

## Before/after (behavior-changing)
Leaderboard top-up boundary:
```
before: MAX(stat_date)=D → top-up counts rides created >= D+1 00:00 UTC
        (re-counting [D+1 00:00, 06:00) already inside Regina row D)
after:  top-up counts rides created >= D+1 06:00 UTC (row D's true end)
```
Daily rollup for one driver-day:
```
before: manual endpoint, UTC bounds, raw haversine sum (spikes included), km only
after:  30-min loop + endpoint on one Regina core, settlement-filtered, km + seconds
```

## Rollback plan
Loop: it has no flag, but is inert-safe to stop — remove is a one-line lifespan revert; rows already written are data. Migration 347 columns: additive, `DROP COLUMN` per rollback comment. Migration 348: re-apply 54's body (documented in-file). Boundary fix (commit 6): revert restores UTC-midnight top-up — only correct if the loop is also stopped; revert the two together. No live-data mutation beyond recomputed stat rows (always re-derivable from retained GPS/rides).

## Verification performed
- New/updated suites: test_driver_daily_rollup.py (7), test_phase_distance_parity.py (2, against a **real Postgres 16** applying 54→348 in order), test_rollup_partial_day_guard.py (4), test_admin_maintenance_coverage.py (26 total), test_referrals_coverage.py (21, incl. explicit Regina-boundary pin), test_driver_activity.py (6).
- Full fast backend suite run before push (see PR/commit thread for count).
- Migrations 347/348 reviewed by `spinr-migration-reviewer`; the one blocker (EXECUTE lockdown) and both actionable warnings (CHECK constraint, mixed-boundary seam plan) addressed in-batch.

## What was NOT verified
- Not run against live Supabase; PostgREST behavior mocked except the parity tests' real-Postgres function checks.
- The leaderboard RPC (migration 204) was read, not executed, to confirm named-column safety.
- Load profile of the 30-min loop estimated (bounded per active driver), not measured against production fleet size.
