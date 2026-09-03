# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude, at user request — "check the settings page and monitoring for other bugs too" |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/admin-portal-heatmaps-audit-gm8fbn` |
| Related issue or gap ID | Likely root cause of the live-reported "Live data paused ... snapshot_failed" issue on `/dashboard/monitoring`, which earlier fixes this session made diagnosable but did not root-cause |

## 1. Issue / gap identified

`backend/routes/admin/monitoring.py`'s `fetch_monitoring_drivers()` and `fetch_monitoring_rides()` — the shared fetchers behind both the REST `/monitoring/drivers`/`/monitoring/rides` endpoints and the WebSocket `get_drivers_snapshot`/`get_rides_snapshot` handlers that feed the Live Ride Monitoring map — built their id-list lookups (`users` by id, `rides` by driver_id, `drivers` by id) with a raw, unbatched `.in_(column, ids).execute()` call. As the online-fleet or active-ride count grows, that id list is serialized directly into the request URL by PostgREST (`col=in.(id1,id2,...)`), and an edge proxy in front of Supabase rejects sufficiently long URLs before the request ever reaches PostgREST.

## 2. Root cause

This is not a new or theoretical failure mode in this codebase — it's a previously-observed, documented production incident. `backend/tests/test_oversized_in_batching.py`'s own header records: *"Observed in production on 2026-08-31: 207 rejected requests in 24h across `/rest/v1/users` (admin drivers/stats), `/rest/v1/driver_documents` (the document-expiry sweep) and `/rest/v1/rides` (the stale-intent reconciler)."* That incident led to a shared, purpose-built async helper — `repositories/_base.py`'s `get_rows_batched_in()` (re-exported as `db_supabase.get_rows_batched_in`) — that chunks any `column IN (values)` lookup into requests small enough to stay under the edge proxy's URL-length ceiling (150 ids per batch, ~6 KB encoded, well under both the edge's ~32 KB ceiling and the 8 KB request-line default many fronting proxies use).

`fetch_monitoring_drivers`/`fetch_monitoring_rides` were never migrated to use it — they still built raw `supabase.table(...).in_(...)` calls via `run_sync`. At `_MONITORING_ROW_CAP` (2000), both `user_ids`/`driver_ids` (drivers fetcher) and `rider_ids`/`driver_ids` (rides fetcher) can comfortably exceed the batch-size that trips this failure. When it does, the exception propagates to the WS handler's `except Exception as _snap_exc` (`routes/websocket.py`), which — after this session's earlier fix — now correctly logs it at `error` level and reports it to Sentry, but the underlying query was still broken; it would fail identically on both the WS path (what the user saw) and the REST fallback (`GET /monitoring/drivers`/`/monitoring/rides`, used by the page's own poll loop and initial load), since both call the exact same fetchers.

Found via a systematic audit of `monitoring.py`/`use-monitoring-socket.ts`/`websocket.py`, specifically tasked with digging for the `snapshot_failed` root cause after this session's earlier fixes (surfacing the real error label, upgrading its log level + Sentry reporting) made the failure diagnosable but did not themselves explain *why* the snapshot fetch was failing.

## 3. Fix / remediation

Replaced every raw `.in_()` id-list lookup in both fetchers with `db_supabase.get_rows_batched_in(table, column, values, extra_filters=..., columns=...)`, matching the exact idiom already used by ~10 other call sites across `routes/admin/drivers.py` and elsewhere in this codebase:

- `fetch_monitoring_drivers`: `users` by `id` (was `user_ids`), and `rides` by `driver_id` with `status` folded into `extra_filters={"status": {"$in": ON_RIDE_STATUSES}}` (was `.in_("status", ...).in_("driver_id", ...)`).
- `fetch_monitoring_rides`: `users` (riders) by `id`, `drivers` by `id`, and `users` (driver-users) by `id`.

The initial base queries (`drivers`/`rides` themselves, bounded by `_MONITORING_ROW_CAP` with no growing `.in_()` list) are untouched — only the id-list joins that scale with fleet/ride count were changed.

## 4. Risk & impact on existing functionality

- **Blast radius**: isolated to these two functions. `get_rows_batched_in` itself is a pre-existing, unmodified, already-battle-tested helper (its own dedicated regression suite in `test_oversized_in_batching.py` covers its internal correctness — batch sizing, result concatenation, the empty-input short-circuit). Grepped for other callers of `fetch_monitoring_drivers`/`fetch_monitoring_rides` — both are called only by their REST endpoints and the WS snapshot handler, all of which pass through unchanged (same function signature, same return shape).
- **Behavioral equivalence for small fleets**: at low id-list sizes (below the 150-per-batch threshold), `get_rows_batched_in` makes exactly one batch — functionally identical to the single unbatched call it replaces, just async-native instead of `run_sync`-wrapped. No change in results for the common case; the fix only changes behavior once a list is large enough to need more than one batch, which is exactly the case that used to fail outright.
- **No schema or API contract change** — same tables, same columns, same response shape to the REST/WS clients.

## 5. User-experience effect

Admin-facing only (`/dashboard/monitoring`). Once the online fleet or active-ride count is large enough to have been tripping the URL-length rejection, the live map/ride list should load instead of showing "Live data paused — snapshot_failed". This is the most direct fix yet for the user's reported symptom, but — consistent with this session's other fixes — I have no live Supabase/production-traffic access to confirm the fleet size at the time of the report actually crossed the failure threshold; the fix is justified by the mechanism (verified directly against `postgrest`'s request-building and this codebase's own documented, previously-observed incident) rather than a live reproduction.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/monitoring.py` | `fetch_monitoring_drivers`/`fetch_monitoring_rides`: replaced 4 raw `.in_()` lookups with `db_supabase.get_rows_batched_in` | Avoid the known URL-length rejection failure mode on `.in_()` lookups that scale with fleet size |
| `backend/tests/test_admin_monitoring_coverage.py` | Updated the 3 existing unit tests for these two functions to mock `db_supabase.get_rows_batched_in` (returning plain lists) instead of `run_sync` for the affected calls | Match the new implementation; still exercises the same fetchers end-to-end |

## 7. Before / after

```python
# Before — fetch_monitoring_drivers, raw .in_() via run_sync
users_res, rides_res, present_ids = await asyncio.gather(
    run_sync(lambda: supabase.table("users").select(...).in_("id", user_ids).execute()),
    run_sync(lambda: supabase.table("rides").select(...).in_("status", ON_RIDE_STATUSES).in_("driver_id", driver_ids).execute()),
    present_driver_ids(driver_ids),
)
users_by_id = {u["id"]: u for u in _rows_from_res(users_res)}
active_ride_by_driver = {r["driver_id"]: r["id"] for r in _rows_from_res(rides_res)}

# After — batched, avoids the URL-length rejection past ~840 ids
users_list, active_rides, present_ids = await asyncio.gather(
    db_supabase.get_rows_batched_in("users", "id", user_ids, columns="id, first_name, last_name, phone, profile_image"),
    db_supabase.get_rows_batched_in("rides", "driver_id", driver_ids, extra_filters={"status": {"$in": ON_RIDE_STATUSES}}, columns="id, driver_id"),
    present_driver_ids(driver_ids),
)
users_by_id = {u["id"]: u for u in users_list}
active_ride_by_driver = {r["driver_id"]: r["id"] for r in active_rides}
```

## 8. Rollback plan

Plain `git revert` — no data, no migration, no schema change. `get_rows_batched_in` is a read-only query helper; reverting restores the prior (occasionally-failing) unbatched queries with no other side effects.

## 9. Verification performed

- [x] Read `repositories/_base.py`'s `get_rows_batched_in` and its docstring in full, and cross-referenced `test_oversized_in_batching.py`'s documented production incident (2026-08-31, 207 rejected requests) to confirm this is a real, previously-observed failure mode in this exact system — not a theoretical concern.
- [x] Read ~10 other real call sites of `get_rows_batched_in` across `routes/admin/drivers.py` to confirm the exact idiom (positional `table, column, values`, keyword `extra_filters`/`columns`/`limit`) before applying it here.
- [x] Verified `run_sync`/`_rows_from_res`/`supabase` imports are still used elsewhere in `monitoring.py` (4 remaining call sites each) — no orphaned imports.
- [x] `python3 -c "import ast; ast.parse(...)"` — syntax valid.
- [x] `ruff check` on both changed files — clean.
- [x] Updated and ran the 3 existing unit tests covering these two functions (`test_admin_monitoring_coverage.py`) — all pass against the new implementation.
- [x] Ran the full `test_admin_monitoring_coverage.py` + `test_websocket_coverage.py` suites together (61 tests) — all pass.

## What was NOT verified

- **No live Supabase/production traffic access** — I could not confirm the actual fleet size at the moment of the user's report, or directly reproduce the URL-length rejection against a real edge proxy. The fix is justified by the documented mechanism and this exact codebase's own prior incident with the identical failure shape, not a live reproduction of this specific report.
- **A new dedicated large-id-list regression test was not added for `monitoring.py` specifically** — `get_rows_batched_in`'s own correctness (batch sizing, concatenation, empty-input handling) is already thoroughly covered by the existing `test_oversized_in_batching.py` suite; adding a second, redundant large-list test here would only re-verify the shared primitive, not anything specific to `monitoring.py`'s usage of it. The updated unit tests confirm the wiring (the two fetchers correctly call the helper and consume its results) but use small fixture lists, not a list large enough to actually exercise multi-batch behavior end-to-end.
