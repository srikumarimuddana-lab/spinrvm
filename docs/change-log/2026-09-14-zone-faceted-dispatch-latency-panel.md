# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | dispatch |
| PR / commit link | (branch `claude/spinr-competitive-positioning-15juag`) |
| Related issue or gap ID | Marketplace-liquidity strategy initiative (this session) |

## 1. Issue / gap identified

CLAUDE.md's Performance SLA table names "Dispatch offer → driver phone notification < 2s (P95)" as a KPI, and it *is* measured today (`spinr_dispatch_offer_to_accept_duration_ms`, `routes/drivers/ride_flow.py`) — but only into Prometheus/Grafana. It never reaches the admin dashboard and is never broken out by service area, so an operator watching the heatmap page's existing per-zone "Live Demand Pressure" panel (demand, idle supply, surge tier — all already per-zone) has no way to see whether a *specific* zone is actually dispatching fast.

## 2. Root cause

The metric was built as an in-process Prometheus observation only, at the one call site that resolves an offer (`accept_ride`). No corresponding durable-storage aggregate or admin-facing endpoint was ever built for it, unlike the marketplace funnel / supply utilization KPIs (migration 351), which already have both.

## 3. Fix / remediation

- **Migration 420**: new read-only SQL function `admin_dispatch_latency_by_zone(p_start, p_end, p_service_area_id)` computing P50/P95 offer→accept latency (ms) from `ride_offers.offered_at`/`responded_at` (already durably stored — no new metrics pipeline needed), overall and per service area. Same conventions as migration 351 (STABLE, SECURITY DEFINER, pinned search_path, EXECUTE revoked from PUBLIC/anon/authenticated, granted to service_role only). Adds one supporting partial index (`idx_ride_offers_accepted_responded`, CONCURRENTLY) since no existing index covered a `(status='accepted', responded_at range)` scan.
- **New endpoint** `GET /api/admin/analytics/dispatch-latency` (same file/pattern as the existing `/marketplace-funnel`, `/supply-utilization` endpoints), Redis-cached 5 min.
- **New KPI target** `dispatch_p95_ms` (2000ms ceiling) added to the existing `_KPI_TARGETS` dict.
- **New admin-dashboard panel**: a 5th summary card ("Dispatch P95") on the heatmap page's existing Live Demand Pressure section, plus a per-zone "Dispatch P95 (offer→accept)" row inside each area's existing demand card — extends what's already there rather than building a new page.
- **Null-safety fix caught by the migration reviewer before commit**: a window with zero accepted offers returns SQL `NULL` percentiles, not `0`. The endpoint now omits the `kpis` entry entirely when `sample_count == 0` (matching the existing `by_zone` convention of omitting empty zones) instead of reporting a false-positive "0ms, meeting target."

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface, additive.** Grepped: `admin_dispatch_latency_by_zone` has exactly one caller (the new endpoint); the new endpoint has no other caller. No existing table, column, or function is altered.
- **Read-only against a live dispatch table** (`ride_offers`) — no write path touched. The `CREATE INDEX CONCURRENTLY` takes a `SHARE UPDATE EXCLUSIVE` lock, which blocks other DDL but not the `accept_ride` row UPDATE that writes `ride_offers.responded_at` — confirmed by the migration reviewer against the actual accept-path code.
- **No interaction** with fare calculation, wallet/allowance deltas, or the ride state machine.
- **Deploy-ordering note** (learned the hard way earlier this session on migration 419): merging this PR does **not** auto-apply the migration to production — `backend/scripts/run_migrations.py` must be run separately, or the new endpoint 500s until it is. Isolated blast radius if that happens (only this one endpoint, not the rest of the dashboard).

## 5. User-experience effect

- **Internal admin only.** A new card and a new per-zone row on an existing admin-dashboard page (`/dashboard/heatmap`), visible only to admins with dashboard access. No rider/driver/corporate-facing change.
- Not visible mid-session to any end user.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/420_dispatch_latency_by_zone.sql` | New SQL function + supporting index | Durable-storage source for the KPI |
| `backend/routes/admin/analytics.py` | New `/dispatch-latency` endpoint, new `dispatch_p95_ms` KPI target | HTTP surface |
| `backend/tests/test_admin_analytics_coverage.py` | New `TestDispatchLatency` (9 tests) + `TestDispatchLatencyMigration420` (4 static tests) | Coverage for the new endpoint and migration, including the null-vs-zero case |
| `admin-dashboard/src/lib/api/analytics-payouts.ts` | New `getDispatchLatency()` API call | Frontend API surface |
| `admin-dashboard/src/lib/api.ts` | Re-exported the new function | Existing barrel-file convention |
| `admin-dashboard/src/app/dashboard/heatmap/page.tsx` | New summary card + per-zone row, fetched independently alongside the existing demand/forecast calls | UI |

## 7. Before / after

```
# Before (routes/admin/analytics.py, first draft of this endpoint)
p95_ms = float(dl.get("p95_ms") or 0)
...
"kpis": [_kpi("dispatch_p95_ms", p95_ms)],
```

```
# After — caught by spinr-migration-reviewer before commit
sample_count = int(dl.get("sample_count") or 0)
p95_ms = float(dl.get("p95_ms") or 0)
...
"kpis": [_kpi("dispatch_p95_ms", p95_ms)] if sample_count > 0 else [],
```

## 8. Rollback plan

`git-revert-safe` — purely additive. If reverting past the migration too: `DROP FUNCTION IF EXISTS public.admin_dispatch_latency_by_zone(timestamptz, timestamptz, text); DROP INDEX CONCURRENTLY IF EXISTS idx_ride_offers_accepted_responded;` (stated in the migration's own header). No data was ever written by this change — it's read-only reporting.

## 9. Verification performed

- [x] Unit tests: `pytest tests/test_admin_analytics_coverage.py` — 133/133 passed (13 new). `ruff check`/`ruff format --check` clean.
- [x] `spinr-migration-reviewer` subagent audit on migration 420: **SAFE TO APPLY**, no blockers. One real warning (the null-vs-zero percentile issue) was found and fixed before this commit, not after.
- [x] Frontend: real `npm run build` (exit 0) and `tsc --noEmit` (clean), not just the dev server. `eslint` on all three touched files: 0 errors, 0 new warnings (5 pre-existing warnings on lines this diff didn't touch).
- [ ] Not verified against a real Postgres instance — the SQL function's correctness was verified by the reviewer subagent by inspection against migration 351's known-working pattern and by checking actual production schema (columns/indexes confirmed to exist via direct query), not by executing it.
- [ ] Not manually verified in a running admin-dashboard browser session — no staging environment exists (see the 2026-09-13 corporate-billing change logs for the fuller context on that gap).

## What was NOT verified

- The SQL function has not actually been run against production data — its correctness rests on structural review + schema verification, not execution.
- Real-world P95 values for any zone are unknown until this ships and accumulates accepted offers.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (pure revert; no data written)
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow — this is new, additive surface only
