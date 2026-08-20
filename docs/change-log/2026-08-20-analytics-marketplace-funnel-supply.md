# Change Impact & Risk Log — Marketplace funnel + supply utilization

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | srikumarimuddana@gmail.com (via Claude Code) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/admin-dashboard-analytics-review-xsjyuk` |
| Related issue or gap ID | User request: "metrics that help business decisions, like Uber and Lyft" |

## 1. Issue / gap identified

Four of CLAUDE.md's own KPI targets had **no surface anywhere in the
dashboard**: match rate (≥85%), rider cancellation rate (≤8%), driver
cancellation rate (≤3%), and driver utilization (≥55%). The Analytics page
showed a single blended `cancellation_rate` that maps to neither cancellation
target, and no funnel or supply view existed at all — so there was no way to
tell whether a low completion rate was a demand problem, a supply problem, or
a driver-behaviour problem.

## 2. Root cause

Not a defect — a missing capability. The page was built as failure triage
("which ride broke and why") and never grew the marketplace-health view that
answers "is the business working". The data to compute all of it already
existed (`rides` stage timestamps, `ride_offers`, `driver_insurance_periods`);
nothing queried it.

## 3. Fix / remediation

Migration 351 adds two read-only aggregates, plus two endpoints:

- `admin_marketplace_funnel` → `GET /analytics/marketplace-funnel`
  requested → matched → accepted → completed, per-stage drop-off, unmet
  demand (`no_drivers_found`), and an attributed cancellation split.
- `admin_supply_utilization` → `GET /analytics/supply-utilization`
  online / en-route / on-trip hours and utilization, derived from the
  append-only `driver_insurance_periods` ledger.

Both are service-area scoped, Regina-bucketed, legacy-excluded, and cached
5 min per range+area. Both return a `kpis` array pairing each actual with its
CLAUDE.md target and a `meeting_target` verdict, so the UI renders
actual-vs-target rather than a bare number.

Three deliberate accuracy decisions:

1. **Stages come from durable timestamps, not `status`.** `rides` stores only
   the current status, so a ride matched then cancelled still counts as
   matched. `accepted` is the union of (recorded accepted offer ∨
   `ride_started_at` ∨ `status='completed'`) so a dispatch path that does not
   write `ride_offers` cannot silently undercount acceptance.
2. **Cancellation attribution prefers the structured `cancelled_by` /
   `cancellation_type` columns** that migration 38 added expressly "so reports
   can aggregate without parsing reason strings", falling back to the legacy
   string heuristic only for pre-38 rows — and returning
   `cancels_unattributed_fallback` so an operator can see how much of the
   split rests on string matching.
3. **Utilization is reported twice** — `utilization_pct` (P3/online, the
   CLAUDE.md definition) and `engaged_pct` ((P2+P3)/online, which counts
   committed-but-unpaid Period 2 as working). Publishing one number under an
   ambiguous name invites the two readings to be conflated.

Insurance periods are **clamped** to the window (`GREATEST(started_at,
p_start)` / `LEAST(COALESCE(ended_at, p_end), p_end)`); without it a driver
online since last month would add a month of online time to a 7-day window.
Period 0 (app off) is excluded from online time.

## 4. Risk & impact on existing functionality

**Blast radius: purely additive.** Two new functions, two new endpoints, one
new index. No existing function, table, column, constraint, endpoint, or
response shape is altered. Nothing is written or migrated. No existing caller
of anything changes behavior.

`driver_insurance_periods` is a **regulatory audit table with an append-only
contract** (migration 64: INSERT allowed, UPDATE only to close `ended_at`,
DELETE blocked). The new function is `STABLE` and read-only — it cannot write,
and the append-only trigger is untouched. The new index is created
`CONCURRENTLY` specifically so it cannot take a lock that would block an
insurance-period transition on the live `go_online`/ride-state path.

No ride state machine, money, wallet, Stripe, dispatch, or background-loop
path is involved.

**New load:** two more RPCs on dashboard load. Both aggregate Postgres-side
(the repo's established pattern) rather than streaming rows into Python, and
both are cached 5 min per range+area.

## 5. User-experience effect

**Backend-only in this commit** — the endpoints exist but nothing renders them
yet (the tabs land in the next commit). No admin, rider, driver, or corporate
surface changes. Nothing visible mid-session to anyone.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/351_marketplace_funnel_and_supply_fns.sql` | New. Two aggregates + `idx_dip_started_at` | Postgres-side rollup; index for the new time-window scan |
| `backend/routes/admin/analytics.py` | `+/marketplace-funnel`, `+/supply-utilization`, `_KPI_TARGETS`, `_pct()`, `_kpi()` | Expose the aggregates with target comparison |
| `backend/tests/test_admin_analytics_coverage.py` | +22 tests | Endpoint behavior + static migration assertions |

## 7. Before / after

Not applicable — purely additive. No existing behavior changed, so there is no
before state to diff. (Included per template; the fields it exists to protect
are covered in §4.)

## 8. Rollback plan

Executable without a code deploy, stated in the migration header:

```sql
DROP FUNCTION IF EXISTS public.admin_marketplace_funnel(timestamptz, timestamptz, text);
DROP FUNCTION IF EXISTS public.admin_supply_utilization(timestamptz, timestamptz, text);
DROP INDEX CONCURRENTLY IF EXISTS idx_dip_started_at;
```

Dropping the functions makes the two new endpoints return 503; nothing else
regresses, because nothing else calls them. To remove the endpoints too,
`git revert` the backend commit. Additive-only, so there is no partially
applied state and no live-data remediation.

## 9. Verification performed

- [x] Automated tests — `pytest tests/test_admin_analytics_coverage.py`: **73 passed** (51 prior + 22 new). Covers stage counts, drop-off arithmetic, rate denominators, KPI target verdicts in both directions (min and max), empty-window division-by-zero, area forwarding, 503 on RPC failure, cache-hit short-circuit, and seconds→hours conversion.
- [x] Static migration assertions — legacy exclusion present, no UTC bucketing, both functions `SECURITY DEFINER` + pinned `search_path` + `REVOKE EXECUTE`, the `started_at` index ships with the query pattern that needs it, periods are clamped, Period 0 excluded, structured attribution preferred.
- [x] `ruff check` + `ruff format` clean.
- [x] Blast-radius check — both function names and both route paths grepped across the repo; no pre-existing references, confirming additive-only.
- [x] Reviewed against `backend/migrations/CLAUDE.md`: append-only, rollback in header, index ships with its query pattern, `CONCURRENTLY` for a table on a live regulatory write path, no new table so no RLS needed. Numbering checked with `ls migrations | sort -V | tail`.
- [x] Reviewed against CLAUDE.md observability: errors `logger.error(..., exc_info=True, extra={"domain": "admin"})` then re-raised as 503 — never warned-and-continued, never softened into a half-valid payload. No PII in logs or payloads (ids and counts only; no names, GPS, phone, or email).

## 10. What was NOT verified

- **The migration has never been executed.** No Postgres was available in this session. Both functions are verified by static assertion only — never parsed by Postgres, so a syntax error would not have been caught. **Dry-run with `run_migrations.py --dry-run` before merge.** This one carries more risk than migration 350: it is ~200 lines of new SQL with CTEs, `FILTER` clauses, and an `EXISTS` correlated subquery, none of it executed.
- **No test asserts the funnel numbers are *correct* against real rides** — the tests feed a fixture dict straight into the endpoint's arithmetic. They prove the Python maths and the response contract; they prove nothing about whether the SQL classifies a real ride into the right stage.
- **`utilization_pct` has never been compared against a known-good figure.** If `driver_insurance_periods` has gaps in production (a missed period close would leave `ended_at` NULL and clamp to `p_end`, inflating online time), utilization would read low and this change would not detect it. Worth sanity-checking the ledger's open-row count before trusting the number.
- **Index-creation cost unknown** — `driver_insurance_periods` row count in production was not checked. `CONCURRENTLY` avoids the lock but the build still takes time proportional to table size.
- **Query plans not measured.** No `EXPLAIN` was run; the `EXISTS` on `ride_offers` and the `driver_id IN (SELECT ...)` area filter are reasoned to use existing indexes, not proven to.
- No frontend build in this commit — no frontend file changed.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] Additive-only; no silent behavior change to any shipped flow
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
