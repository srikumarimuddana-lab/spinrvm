# Change Impact & Risk Log — Efficiency + financial aggregates

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | srikumarimuddana@gmail.com (via Claude Code) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/admin-dashboard-analytics-review-xsjyuk` |
| Related issue or gap ID | User request: business-decision metrics, second half of the set begun in 351 |

## 1. Issue / gap identified

No surface existed for rider-experience or driver-economics metrics: nothing
reported how long a rider waited for a match, whether the ETA promised at
offer time held, how much unpaid approach distance drivers absorbed, or how
gross bookings decomposed. Surge penetration and the corporate/consumer mix
were likewise invisible.

## 2. Root cause

Missing capability, not a defect. The underlying columns have existed for a
long time (`assigned_at` mig 54, `ride_started_at` mig 137, `ride_offers.eta_seconds`
mig 100, `pickup_to_driver_km`/`actual_distance_km` mig 15, `surge_multiplier`
mig 08, `corporate_account_id` mig 03) — nothing ever queried them together.

## 3. Fix / remediation

Migration 352 adds two read-only aggregates, exposed as two endpoints:

- `admin_efficiency_metrics` → `GET /analytics/efficiency`
- `admin_financial_metrics` → `GET /analytics/financial`

Both service-area scoped, legacy-excluded, cached 5 min per range+area.

Four decisions worth flagging:

1. **Percentiles ship with their sample size.** `matched_sample`,
   `pickup_sample`, `eta_sample` are returned alongside every P50/P95 so a
   P95 over eleven rides cannot be read as a fleet statistic. A missing
   percentile stays `null` rather than collapsing to `0`, so "no data" never
   renders as "zero seconds".
2. **Unmatched rides are excluded from time-to-match**, not counted as
   infinite. That is only honest because the funnel endpoint (351) reports
   `match_rate` — the omission is measured elsewhere, and the denominator is
   returned so it is never guessed.
3. **`assignment_to_trip_start` is named for exactly what it measures.**
   `rides` has no arrival timestamp, so the span covers driver acceptance
   *and* the drive to pickup. Calling it "time to pickup" would overstate it.
4. **`gross_bookings`, never `revenue`.** Drivers keep 100% of the fare on
   consumer rides (CLAUDE.md: "Not a commission-taking marketplace"), so
   rider-paid volume must not be labelled company revenue. A test asserts the
   response contains no `revenue`/`total_revenue` key.

Deadhead is a ratio of sums, not a mean of per-ride ratios, so one short trip
with a long approach cannot dominate. `repeat_rate_pct` is explicitly tagged
`repeat_rate_basis: "within_window"` — it is a within-window repeat share, not
a retention cohort, and reads lower on short windows by construction.

## 4. Risk & impact on existing functionality

**Blast radius: purely additive, with one shared-module import.**

Two new functions, two new endpoints. No existing function, table, column,
endpoint, or response shape is altered. Nothing is written or migrated.

The one non-additive edit: `routes/admin/analytics.py` now imports `_d`,
`_round`, `_f` from `services/fare_service` via the repo's dual-import
pattern, because CLAUDE.md requires those helpers for any money value
crossing an API boundary. Verified this introduces no circular import — the
full analytics test module (94 tests) imports and exercises the router.
`fare_service` is imported by many modules already; adding one more consumer
does not change its behavior.

**No new index.** Both functions' predicates are the same window+area scan
already covered by `rides(service_area_id, created_at)` (mig 310), and
`ride_offers` is reached by `ride_id` via `idx_ride_offers_ride_id` (mig 100).
An index that buys nothing still costs write throughput on the ride path, so
none was added.

No ride state machine, wallet, Stripe, dispatch, or background-loop path is
touched. `/financial` performs a second RPC (`admin_supply_utilization`) for
bookings-per-online-hour; a failure there is logged at error level and nulls
only that derived field rather than failing the endpoint — the financial
figures remain valid, and `null` is returned instead of `0` so "unknown" is
not mistaken for "zero".

## 5. User-experience effect

**Backend-only in this commit.** No admin, rider, driver, or corporate
surface changes; nothing visible mid-session. The tabs consuming these land
in the following commit.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/352_efficiency_and_financial_fns.sql` | New. Two read-only aggregates | Postgres-side rollup, per repo convention |
| `backend/routes/admin/analytics.py` | `+/efficiency`, `+/financial`; money helpers imported via dual-import | Expose the aggregates; Decimal-only money per CLAUDE.md |
| `backend/tests/test_admin_analytics_coverage.py` | +21 tests | Endpoint behavior + static migration assertions |

## 7. Before / after

Not applicable — purely additive; no existing behavior changed.

## 8. Rollback plan

Executable without a code deploy:

```sql
DROP FUNCTION IF EXISTS public.admin_efficiency_metrics(timestamptz, timestamptz, text);
DROP FUNCTION IF EXISTS public.admin_financial_metrics(timestamptz, timestamptz, text);
```

The two new endpoints then return 503; nothing else regresses, since nothing
else calls them. `git revert` removes the endpoints too. Additive-only, so
there is no partially-applied state and no live-data remediation.

## 9. Verification performed

- [x] Automated tests — `pytest tests/test_admin_analytics_coverage.py`: **94 passed** (73 prior + 21 new). Covers percentile/sample pairing, null-not-zero for missing percentiles, negative ETA error preserved, deadhead block, Decimal money round-trip, the `bookings_per_online_hour` join, supply-failure degradation, zero-hour division guard, surge/mix blocks, repeat-rate basis tag, empty-window zeros, and 503 on RPC failure.
- [x] A test asserts the financial payload exposes no `revenue`/`total_revenue` key — a naming regression here would misrepresent the business model.
- [x] Static migration assertions — legacy exclusion on both functions, both `SECURITY DEFINER` + pinned `search_path` + `REVOKE EXECUTE`, no UTC bucketing, deadhead as ratio-of-sums, sample counts present, money divisions stay in `numeric`, ETA error uses the accepted offer's promise.
- [x] `ruff check` + `ruff format` clean.
- [x] Circular-import check — the router module loads under the test harness with the new `fare_service` import.
- [x] Reviewed against CLAUDE.md money conventions: all money values pass through `_d()` → `_round()` → `_f()`; no float arithmetic on fare columns; SQL divisions stay in `numeric`. Pre-commit money-arithmetic hook passed.
- [x] Observability: RPC failures `logger.error(..., exc_info=True, extra={"domain": "admin"})` then 503 — never warned-and-continued. No PII in logs or payloads.

## 10. What was NOT verified

- **Neither function has been executed.** No database was available. ~230 lines of new SQL — including `percentile_cont(...) WITHIN GROUP`, a join to `ride_offers`, and several `FILTER` clauses — has never been parsed by Postgres. **Dry-run with `run_migrations.py --dry-run` before merge.**
- **No test validates the metrics against real rides.** Tests feed fixture dicts into the endpoints' arithmetic; they prove the Python and the response contract, not that the SQL selects the right rows or computes the right percentile.
- **ETA accuracy depends on `ride_offers.eta_seconds` being populated in production.** If it is frequently null, `eta_sample` will be small and the on-time percentage unrepresentative. The endpoint surfaces the sample size so this is visible, but the actual production fill rate of that column was not checked.
- **`pickup_to_driver_km` fill rate likewise unverified** — if it is sparse, the deadhead ratio understates. Not checked against real data.
- **Query plans not measured.** No `EXPLAIN`; the `ride_offers` join and the percentile sorts are reasoned to be acceptable, not proven. `percentile_cont` sorts the full matched set in memory — on a 1-year window at scale this is the most expensive query added in this whole branch and is the first thing to check if the endpoint is slow.
- No frontend build in this commit — no frontend file changed here.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] Additive-only; no silent behavior change
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
