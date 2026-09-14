# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (branch `claude/spinr-competitive-positioning-15juag`) |
| Related issue or gap ID | Marketplace-liquidity strategy initiative (this session) |

## 1. Issue / gap identified

CLAUDE.md lists "Weekly active driver retention (week-over-week) ≥ 80%" as a KPI target. Nothing in the codebase computes any retention or cohort concept — confirmed by grep (zero hits for retention/cohort/repeat_rider/churn in an analytics sense) and by direct inspection of `_KPI_TARGETS`, which never included it. CLAUDE.md also references a `/kpi` endpoint that does not exist anywhere in the repo. (The `/kpi` and driver-retention doc corrections are tracked as a separate, smaller follow-up commit in this same session.)

## 2. Root cause

No cohort-retention concept was ever built — this is genuinely new ground, not a regression or an extension of something broken.

## 3. Fix / remediation

- **Migration 423**: new read-only SQL function `admin_retention_cohorts(p_cohort_start, p_cohort_end, p_service_area_id)` computing W1/W4/W12 rider and driver retention, bucketed by signup week. "Retained" = ≥1 completed ride in that later week (explicit, user-confirmed product decision). Two new supporting indexes (`idx_users_rider_created_at`, `idx_drivers_created_at`, both CONCURRENTLY) since neither `users` nor `drivers` had a `created_at` index before.
- **Two real correctness bugs were found and fixed before this was ever committed**, by an adversarial migration-reviewer pass followed by a targeted re-verification pass:
  1. **Off-by-one on "has this horizon elapsed"**: the original `<=` comparison would report the *current, still-in-progress* week as a completed horizon, using only its partial data — the number would then visibly change day-to-day for what should be a stable, closed data point. Fixed to strict `<`.
  2. **Wrong rider-population filter**: originally filtered `users.role = 'rider'`, but migration 101 explicitly retired `role` for rider/driver discrimination in favor of `is_rider`/`is_driver` — `role` is written once at signup and never resynced, so a driver-first dual-role user keeps `role='driver'` forever even while actively riding. This would have silently undercounted the rider cohort. Fixed to `users.is_rider`, with the supporting index predicate updated to match.
  - Two smaller issues were also fixed: a wrong index citation in the migration's own header comment (pointed at an unrelated index), and a missing `ride_completed_at IS NOT NULL` defensive guard (matching the pattern already used in migration 422).
- **New endpoint** `GET /api/admin/analytics/retention-cohorts`, following the same pattern as `/marketplace-funnel`/`/supply-utilization`/`/dispatch-latency`. Default window is 90d (wider than other endpoints' 30d) since a cohort needs up to 12 weeks to elapse before a W12 reading can exist.
- **New KPI** `driver_retention_w1_pct` — explicitly documented as a *different* metric from CLAUDE.md's "week-over-week active" phrasing (signup-cohort retention vs. rolling active-user retention). Not claimed as satisfying that KPI literally; the doc-correction follow-up commit addresses this distinction directly.
- **New "Retention" tab** on the admin analytics page, with a per-cohort-week table (rows = signup week, columns = W1/W4/W12) for riders and drivers separately. A horizon with no reading yet renders as an em-dash, never a misleading 0%.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface, additive.** `admin_retention_cohorts` has exactly one caller (the new endpoint). No existing table, column, or function is altered.
- **Read-only** against `users`, `drivers`, `rides` — no write path touched.
- **Two new CONCURRENTLY index builds on high-write tables** (`users`: OTP/profile writes; `drivers`: location pings, online-status toggles — very high frequency). `CREATE INDEX CONCURRENTLY` takes a `SHARE UPDATE EXCLUSIVE` lock, which blocks other DDL but not normal INSERT/UPDATE/DELETE — confirmed against the same reasoning used for migration 422's index build.
- **Deploy-ordering note** (same as migration 422, learned on migration 419 earlier this session): merging does not auto-apply the migration to production; `run_migrations.py` must be run separately or the new endpoint 500s until it is.
- **No interaction** with fare calculation, wallet/allowance deltas, dispatch, or the ride state machine.

## 5. User-experience effect

- **Internal admin only.** A new tab on an existing admin-dashboard page (`/dashboard/analytics`), visible only to admins with dashboard access. No rider/driver/corporate-facing change.
- Not visible mid-session to any end user.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/423_retention_cohorts.sql` | New SQL function + 2 supporting indexes | Durable-storage source for the KPI |
| `backend/routes/admin/analytics.py` | New `/retention-cohorts` endpoint, new `driver_retention_w1_pct` KPI target | HTTP surface |
| `backend/tests/test_admin_analytics_coverage.py` | New `TestRetentionCohorts` (9 tests) + `TestRetentionCohortsMigration423` (7 static tests, including regression tests for both fixed bugs) | Coverage, including the two bugs caught during review |
| `admin-dashboard/src/lib/api/analytics-payouts.ts` | New `getRetentionCohorts()` API call | Frontend API surface |
| `admin-dashboard/src/lib/api.ts` | Re-exported the new function | Existing barrel-file convention |
| `admin-dashboard/src/components/analytics/retention-cohorts-panel.tsx` | New panel component (cohort tables for riders/drivers) | UI |
| `admin-dashboard/src/app/dashboard/analytics/page.tsx` | New "Retention" tab wired in | UI |

## 7. Before / after

```
-- Before (migration 423, first draft — two real bugs)
FROM users u
WHERE u.role = 'rider'
  AND u.created_at >= p_cohort_start ...
...
WHERE rc.cohort_week + (h.weeks || ' weeks')::interval <= (SELECT d FROM current_week)
```

```
-- After — both caught by spinr-migration-reviewer before commit
FROM users u
WHERE u.is_rider
  AND u.created_at >= p_cohort_start ...
...
WHERE rc.cohort_week + (h.weeks || ' weeks')::interval < (SELECT d FROM current_week)
```

## 8. Rollback plan

`git-revert-safe` — purely additive, read-only. If reverting past the migration too: `DROP FUNCTION IF EXISTS public.admin_retention_cohorts(timestamptz, timestamptz, text); DROP INDEX CONCURRENTLY IF EXISTS idx_users_rider_created_at; DROP INDEX CONCURRENTLY IF EXISTS idx_drivers_created_at;` (stated in the migration's own header). No data was ever written by this change.

## 9. Verification performed

- [x] Unit tests: `pytest tests/test_admin_analytics_coverage.py` — 150/150 passed (16 new: 9 endpoint + 7 migration-static, including two tests that specifically regression-guard the two bugs found). `ruff check`/`ruff format --check` clean.
- [x] `spinr-migration-reviewer` subagent audit, **twice**: first pass found 2 blockers + 2 warnings; targeted re-verification pass after fixes confirmed all four resolved correctly with nothing new introduced. Final verdict: **SAFE TO APPLY**.
- [x] Frontend: real `npm run build` (exit 0) and `tsc --noEmit` (clean). `eslint`: 0 errors; 2 warnings match the exact pre-existing pattern already present in `supply-panel.tsx` (not new); 1 real new warning (unescaped quote) found and fixed before this commit.
- [ ] Not verified against a real Postgres instance — correctness rests on two independent structural reviews (including direct verification of `is_rider`'s existence/nullability and the cited indexes' real names against their source migration files), not execution.
- [ ] Not manually verified in a running admin-dashboard browser session — no staging environment exists.

## What was NOT verified

- The SQL function has not actually been run against production data.
- Real-world retention percentages for any cohort are unknown until this ships and data accumulates.
- Whether `driver_retention_w1_pct`'s reused 80% target is the right bar for this specific (signup-cohort, not rolling-active) definition of retention — flagged as a placeholder in the code comment, pending a human decision.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (pure revert; no data written)
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change — new, additive surface only; two real bugs were caught and fixed pre-commit rather than shipped
