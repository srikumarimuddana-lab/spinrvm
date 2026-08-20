# Change Impact & Risk Log — Analytics Regina bucketing + service-area scope

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | srikumarimuddana@gmail.com (via Claude Code) |
| Surface(s) | backend (+ admin-dashboard consumes the new fields in a later commit) |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/admin-dashboard-analytics-review-xsjyuk` |
| Related issue or gap ID | Operational Analytics review, findings P1-3 and P3-area |

## 1. Issue / gap identified

Two silent defects in `/dashboard/analytics`:

1. `admin_analytics_overview` (166) and `admin_cancellation_breakdown` (165)
   bucketed day and hour on `AT TIME ZONE 'UTC'`. Saskatchewan is
   America/Regina, UTC−6 year-round. The "Cancellations by Hour" axis was
   shifted six hours, and the daily chart's day boundary fell at 18:00 local
   the previous day — splitting the evening peak across two bars.
2. `/analytics/overview` took no `service_area_id`, so the page's headline
   KPI cards always blended every market together. The sibling endpoints
   (`cancellation-reasons`, `driver-acceptance`) already accepted one.

## 2. Root cause

(1) The functions were written UTC-first and never revisited. The repo had
already recognised this class of bug elsewhere — migration 347 added
`driver_daily_stats.day_tz` and documents `day_tz='regina'` as "a deliberate
correction to the business day", and `utils/auto_payout.py`,
`utils/quest_tracker.py`, `utils/legacy_rides.py` all use
`ZoneInfo("America/Regina")`. These two functions were the outliers.

(2) The overview predates the service-area dimension; the parameter was added
to the other two functions and never backfilled here.

## 3. Fix / remediation

Migration 350 rewrites both functions:
- Day/hour buckets now use `AT TIME ZONE 'America/Regina'`.
- `admin_analytics_overview` gains `p_service_area_id text DEFAULT NULL` and
  the same area predicate the other two use.
- Endpoint forwards `service_area_id`, and both payloads now report
  `timezone: "America/Regina"` so the UI can label axes instead of rendering
  an ambiguous bare `14:00`.
- Redis cache keys bumped to `:v2:` and made per-area, so entries written
  under UTC bucketing are not served after deploy.

## 4. Risk & impact on existing functionality

**Blast radius: two Postgres functions and one endpoint, read-path only.**

Grepped every caller of both functions: `admin_analytics_overview` is called
only from `get_analytics_overview`; `admin_cancellation_breakdown` only from
`get_cancellation_breakdown`. Both live in `backend/routes/admin/analytics.py`.
No other route, service, background loop, or migration references them.

No table, column, index, or RLS policy is touched. Nothing is written. No ride
state, money, wallet, dispatch, or insurance-period path is involved.

**Preserved deliberately** (verified by assertion tests, not by eye): migration
349's `legacy_import_metadata = '{}'::jsonb` exclusion on both functions.
Dropping it while rewriting would have silently re-admitted legacy-imported
historical bookings into live KPIs — the exact regression 349 existed to fix.

**Signature-change deploy safety.** `CREATE OR REPLACE` cannot change a
signature, so the 1-arg `admin_analytics_overview(timestamptz)` is DROPped and
recreated as `(timestamptz, text DEFAULT NULL)`. Because the new parameter has
a DEFAULT, a backend still running the old code and calling with only
`{"p_start": ...}` resolves to the new function and behaves identically. Safe
to apply before or after the backend deploy. Without the DROP, Postgres would
have kept both overloads and PostgREST could have picked either — a
regression test asserts the DROP is present.

**Numbers will move.** Any saved screenshot or externally-recorded figure of
the hourly/daily charts will not match after this ships. That is the fix, not
a regression, but it is a visible discontinuity in a live-tested surface.

## 5. User-experience effect

**Internal admin only.** No rider, driver, or corporate-admin surface changes;
nothing is visible mid-session to anyone using the rider or driver app.

Admins will see the hourly cancellation chart shift by six hours and daily
bars re-attribute evening rides to the correct day. Values that were being
read as local time were previously wrong; they are now correct.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/350_analytics_regina_buckets_and_area_scope.sql` | New. Rewrites both RPCs: Regina buckets, area scope on overview | Fix the six-hour shift and enable per-market KPIs |
| `backend/routes/admin/analytics.py` | Forward `service_area_id`; report `timezone`; bump cache keys to `:v2:`, per-area | Wire the new capability; avoid serving stale UTC buckets |
| `backend/tests/test_admin_analytics_coverage.py` | +10 tests (area forwarding, per-area cache keys, tz reporting, migration assertions) | Regression cover, incl. that 349's exclusion survives |

## 7. Before / after

```sql
-- Before (migration 349)
(created_at AT TIME ZONE 'UTC')::date                    AS d,
EXTRACT(HOUR FROM (created_at AT TIME ZONE 'UTC'))::int  AS hr
FROM rides
WHERE created_at >= p_start
  AND legacy_import_metadata = '{}'::jsonb
```

```sql
-- After (migration 350)
(created_at AT TIME ZONE 'America/Regina')::date                    AS d,
EXTRACT(HOUR FROM (created_at AT TIME ZONE 'America/Regina'))::int  AS hr
FROM rides
WHERE created_at >= p_start
  AND legacy_import_metadata = '{}'::jsonb
  AND (p_service_area_id IS NULL OR service_area_id::text = p_service_area_id)
```

## 8. Rollback plan

Stated in the migration header and executable without a code deploy:

```sql
DROP FUNCTION IF EXISTS public.admin_analytics_overview(timestamptz, text);
-- then re-run the two CREATE OR REPLACE blocks from
-- 349_exclude_legacy_cancelled_from_cancellation_analytics.sql
-- (lines 139-195 and 205-247) verbatim.
```

Restoring the 1-arg overview while the new backend is deployed would break it
(it sends `p_service_area_id`), so a full rollback is: apply the SQL above,
then revert the backend commit. If only the bucketing needs reverting, replace
`America/Regina` with `UTC` in migration 350's two functions and re-apply —
no signature change, no code deploy needed.

No data migration to undo: the change is read-path only, so there is no
partially-applied state and no live-data remediation.

## 9. Verification performed

- [x] Automated tests — `pytest tests/test_admin_analytics_coverage.py`: **51 passed** (41 prior + 10 new). Includes assertion tests that migration 350 keeps 349's legacy exclusion on both functions, leaves no UTC bucketing, keeps both `REVOKE EXECUTE`s, and DROPs the old arity before recreating.
- [x] `ruff check` + `ruff format` clean.
- [x] Blast-radius grep — searched `admin_analytics_overview` and `admin_cancellation_breakdown` across `backend/`; only the two endpoints in `routes/admin/analytics.py` call them.
- [x] Index check — `rides(service_area_id, created_at)` already exists (migration 310), matching the new predicate. Bucketing on an expression of `created_at` does not affect the range scan, which still filters bare `created_at`.
- [x] Reviewed against `backend/migrations/CLAUDE.md`: append-only (new file, no edit to a merged migration), rollback in a top comment, `SECURITY DEFINER` + pinned `search_path` preserved, `REVOKE EXECUTE` from `anon`/`authenticated` preserved, no new table so no RLS needed.
- [x] Migration numbering — `ls migrations | sort -V | tail` confirmed 349 is highest; 350 is free and does not collide.

## 10. What was NOT verified

- **The migration has not been executed anywhere.** No Postgres instance was available in this session — not production, not staging, not a local throwaway. Its SQL is verified by static assertion only. It has never been parsed by Postgres, so a syntax error would not have been caught here. **This must be run with `run_migrations.py --dry-run` against a real database before merge.**
- **PostgREST default-argument resolution is reasoned, not tested.** The claim that a 1-arg `{"p_start": ...}` call resolves to the 2-arg function via its DEFAULT is standard PostgREST behavior but was not exercised against a live PostgREST.
- **No timezone correctness test against real data** — no fixture proves a 02:00 Regina cancellation now lands in bucket 2. The change is verified as "the SQL says America/Regina", not as "the numbers moved by six hours".
- **No admin-dashboard build in this commit** — no frontend file changed here; `timezone`/`service_area_id` are returned but not yet consumed. The UI wiring lands in the next commit and will be built then.
- Query-plan impact of the area predicate not measured (no EXPLAIN run).

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change — §5 documents the deliberate, visible shift in chart values
- [x] **Gate closed** — migration applied and its functions executed against a real PostgreSQL 16 instance; see §12.

## 12. Migration verification — gate CLOSED (2026-08-20)

The open gate above ("migration has never been executed") is now closed. A
local PostgreSQL 16.13 instance was stood up, a minimal schema built from the
column definitions in the migrations that created them, and **all three
migrations (350, 351, 352) applied successfully with `ON_ERROR_STOP=1`**.

Verified by executing the functions against seeded data, not just by parsing:

| Claim | Result |
|---|---|
| Regina bucketing | A ride created `2026-08-20T04:00Z` buckets to Regina day **2026-08-19**, hour **22**. Under the old UTC bucketing it was day 20, hour 4. |
| Legacy-import exclusion (349's guarantee) | A `legacy_import_metadata`-tagged ride with `total_fare` 999 is absent from revenue (115.00, not 1114.00) and from every count. |
| Service-area scope | Saskatoon 4 rides / Regina 1 — correctly partitioned. |
| Backward-compatible 1-arg call | `admin_analytics_overview(timestamptz)` still resolves via the new parameter's DEFAULT. |
| Funnel stages | requested 5 → matched 4 → accepted 3 → completed 3, cancelled 2, no_supply 1. |
| Structured vs fallback attribution | `cancels_by_party {rider:1, system:1}` with `cancels_unattributed_fallback: 1` — exactly the one row lacking `cancelled_by`. |
| Period 0 excluded from online time | P1 1h + P2 0.5h + P3 1h = 2.5h online, utilization 40%, engaged 60%. The 10h Period-0 row is correctly ignored (it would have driven utilization to 8%). |
| Insurance-period clamping | Periods clamp to the window as designed. |
| Deadhead ratio | 7.0 unpaid / 35.0 paid = 20.0% — ratio of sums, completed rides only. |
| ETA error | Promised 120s vs actual 240s → +120s; second ride exactly on time → p50 60s, on-time 50%. |
| Financial arithmetic | gross 115.00, avg fare 38.33, surge revenue 20.00 (= 60 − 60/1.5), corporate 60.00 / consumer 55.00, repeat rate 50%. |

**A real defect was found by this verification and fixed** — see the grant
issue in `docs/change-log/2026-08-20-analytics-function-grants.md`. Static
assertions had passed on the original text; only executing it revealed the
`REVOKE` was a no-op.
