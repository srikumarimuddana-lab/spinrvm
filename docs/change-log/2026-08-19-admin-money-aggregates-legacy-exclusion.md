# Change Impact & Risk Log — Exclude legacy rides from remaining admin money aggregates

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Surface(s) | backend, admin-dashboard (data only, no frontend file changed) |
| Domain (Sentry tag) | admin |
| Related issue or gap ID | Found by the admin-dashboard review agent during this session's legacy-migration data-quality audit; extends A25/A26/P0-B (migrations 302/303) |

## 1. Issue / gap identified

Migrations 302/303 excluded legacy-imported rides from `admin_ride_money_rollup` and `admin_payouts_overview_aggregates`. Three sibling functions never got the same fix: `admin_earnings_overview_agg` + `admin_earnings_daily_series` (both power `GET /admin/earnings/overview`) and `admin_dashboard_money` (powers the admin-dashboard **homepage**, `GET /admin/analytics/dashboard`). A fourth call site — `get_dashboard_overview`'s raw `count_documents("rides", ...)` calls for the homepage's ride-count cards — had the same gap in Python, not SQL.

## 2. Root cause

Migrations 302/303 were scoped to the two functions the P0-B audit named at the time; nobody re-swept for every other money/count aggregate touching `rides` before or since. The four missed call sites are structurally identical to the two that were fixed.

## 3. Fix / remediation

Migration 341 adds `legacy_import_metadata = '{}'::jsonb` (the same predicate form 302/303 established — `IS NULL` matches zero rows, the column is `NOT NULL DEFAULT '{}'::jsonb`) to the `completed` CTE in `admin_earnings_overview_agg`, to `admin_earnings_daily_series`, and to the ride-money subquery in `admin_dashboard_money`. Deliberately does **not** touch `admin_earnings_overview_agg`'s `cohort`/`cancelled` CTEs or its funnel keys — `booking_import_service.py` only ever imports `status='completed'` rows (the documented 78% cancelled/failed-booking gap), so no legacy row can appear in a cancellation or funnel count; adding the predicate there would be a no-op that invites a wrong assumption later.

`routes/admin/analytics.py`'s `get_dashboard_overview` gains a `_rides_in_range()` helper (wrapping the existing `_in_range()`) that adds `{"legacy_import_metadata": {"$eq": {}}}` to the filter dict — used only for `rides_total` and the per-status breakdown counts, not for `drivers`/`users` counts or `rides_active` (which is status-`$in`-active-statuses with no time window — no legacy row is ever in an active status, since the importer only ever writes `completed`).

## 4. Risk & impact on existing functionality

- **This was live-wrong, not theoretical, at least for roughly the first week post-cutover.** `booking_import_service.py` preserves each imported ride's true historical `ride_completed_at`/`created_at`. The Mongo export's vintage (2026-07-26) put all 186 imported rides' dates only ~3 weeks before this fix — squarely inside a `30d`/`mtd` window on `/earnings/overview` as of this fix, and inside the homepage's `7d`/`24h` windows for roughly the first week after the 2026-07-29 cutover. Any admin who pulled "This Month" on Earnings Overview, or viewed the homepage stat cards in early August, saw GBV/revenue/trip-count inflated by legacy rides with no indication.
- **Blast radius: 3 SQL functions + 1 Python endpoint, all already-existing read paths — no new consumer, no schema change.** Every existing caller (`routes/admin/rides.py`'s `/earnings/overview`, `routes/admin/analytics.py`'s `/dashboard`) gets a more-correct number for the same response shape; nothing changes shape.
- **Not touched**: `admin_ride_money_rollup`/`admin_payouts_overview_aggregates` (already fixed, migrations 302/303, unaffected by this migration), `admin_earnings_refunds` (disputes-based, not a `rides`-money sum, out of scope — not re-verified this session), corporate wallet/allowance code (untouched).
- **Regression risk on the funnel/cancellation keys: none** — deliberately unchanged, per §3's reasoning, with a dedicated test asserting the predicate is *absent* there so a future edit doesn't accidentally add it and silently zero out a legitimate count.

## 5. User-experience effect

- **Internal-admin-facing only.** An admin viewing Earnings Overview or the dashboard homepage now sees numbers that no longer include the 186 legacy rides' fare/revenue/trip-count contribution for any window that still overlaps their historical dates (a shrinking window going forward, since those dates are fixed in the past). No rider/driver/corporate-facing surface reads any of these three functions.
- Not mid-session-relevant — these are periodic admin dashboard views, not something a rider/driver has open.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/341_exclude_legacy_from_remaining_admin_money_aggregates.sql` | Amends `admin_earnings_overview_agg`, `admin_earnings_daily_series`, `admin_dashboard_money` to exclude legacy-imported rides from their money/count aggregates | Closes the live double-counting gap |
| `backend/routes/admin/analytics.py` | Adds `_rides_in_range()`, used for `rides_total` and the per-status breakdown counts in `get_dashboard_overview` | Same fix for the one Python-side (non-SQL) count call site |
| `backend/tests/test_migration_341_admin_money_legacy_exclusion.py` | New — SQL-migration-text assertions (CI has no Postgres), same convention as `test_step_h_driver_rides_guard_migration.py` | Pins the predicate is present in the 3 functions and absent from the funnel/cancelled CTEs |
| `backend/tests/test_admin_analytics_coverage.py` | Added `test_ride_counts_exclude_legacy_imported_rides` | Regression coverage for the Python-side fix |

## 7. Before / after

```sql
-- Before (admin_dashboard_money's ride-money subquery, migration 194)
FROM rides r
WHERE r.status = 'completed'
  AND r.created_at >= p_start AND r.created_at < p_end
  AND (p_service_area_id IS NULL OR r.service_area_id = p_service_area_id)
```

```sql
-- After (migration 341)
FROM rides r
WHERE r.status = 'completed'
  AND r.created_at >= p_start AND r.created_at < p_end
  AND (p_service_area_id IS NULL OR r.service_area_id = p_service_area_id)
  AND r.legacy_import_metadata = '{}'::jsonb
```

```python
# Before (analytics.py get_dashboard_overview)
_count("rides", _in_range(area)),
...
*[_count("rides", _in_range({**area, "status": s})) for s in _DASH_BREAKDOWN_STATUSES],

# After
_count("rides", _rides_in_range(area)),
...
*[_count("rides", _rides_in_range({**area, "status": s})) for s in _DASH_BREAKDOWN_STATUSES],
```

## 8. Rollback plan

- **Migration 341**: re-run 227's `admin_earnings_overview_agg` body, 163's `admin_earnings_daily_series` body, and 194's `admin_dashboard_money` body verbatim (each restores the unfiltered version — no new index/column exists to drop). Stated in the migration's own header.
- **`analytics.py`**: `git-revert-safe` — pure filter-dict change, no data written.
- Neither fix mutates any stored data — both are read-path corrections, so there is no data-level remediation needed regardless of direction.

## 9. Verification performed

- [x] Automated tests: `pytest backend/tests/test_migration_341_admin_money_legacy_exclusion.py backend/tests/test_admin_analytics_coverage.py backend/tests/test_admin_extended.py` → all pass (11 + 28 + 60 = 99). `ruff check` clean on both modified/new Python files.
- [ ] Manual repro / staging — not performed, no live DB access this session; the "live-wrong" claim in §4 is derived from the known import batch dates and window logic, not a live query against production.
- [x] Blast-radius grep performed — confirmed no other admin money/count aggregate reads `rides` without an existing legacy exclusion beyond the 4 fixed here and the 2 already fixed by 302/303 (not exhaustively re-verified beyond what the admin-dashboard review agent covered this session).
- [x] Reviewed against CLAUDE.md: migration numbering (341 is next-free, confirmed via `ls | sort -V | tail`), money-function safety (STABLE/SECURITY DEFINER/search_path/REVOKE-GRANT preserved verbatim, checked by `TestSecurityPropertiesUnchanged`), query-filter convention (`{"$eq": {}}`, not `IS NULL`, matching A26's established fix).

## 10. Sign-off

- [x] Rollback plan concrete and stated per-fix
- [x] Blast radius stated (3 SQL functions + 1 Python endpoint, all pre-existing read paths)
- [x] No silent behavior change to a shipped flow without the UX field filled in — §5 states plainly this is an accuracy correction visible only to admins viewing historical-overlapping windows
