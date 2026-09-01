# Change Impact & Risk Log — driver heatmap v2 hard-down on a non-existent column

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-01 |
| Author | Claude Code session (with mkkreddy52) |
| Surface(s) | backend, driver-app (behaviour only — no client code change) |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/heap-map-display-comparison-2o8mv9` |
| Related issue or gap ID | none filed — found during live driver-app testing in Regina |

## 1. Issue / gap identified

With `driver_heatmap_v2_enabled` on (it was on globally in production), every call to
`GET /drivers/demand-heatmap` returned a 500. On the driver app the legend pill appeared
for a few seconds (loading shimmer) and then vanished, and no demand cells were ever
drawn — even in Regina, where two grid cells clear the k-anonymity floor. The app hides
the pill on the first failed fetch, so the feature looked simply absent rather than broken.

## 2. Root cause

The v2 "scheduled" component query filtered `rides` on `scheduled_pickup_time`. That
column does not exist — the real column is `scheduled_time` (the one
`utils/scheduled_rides.py` and `create_ride` use). PostgREST answers 400
(`column rides.scheduled_pickup_time does not exist`), `get_rows` raises, the query is not
wrapped in any `try`, so the whole endpoint 500s. Confirmed from Supabase edge logs: every
`rides?...&status=eq.scheduled&...&scheduled_pickup_time=gte...` request on 2026-09-01 is a 400.

It never surfaced in tests because `mock_supabase_client` / the `get_rows` `AsyncMock`
side-effects do not validate column names.

## 3. Fix / remediation

Rename the filter key to `scheduled_time`. Add a regression test that captures the
scheduled-rides query's filters and pins the column name.

Immediate production mitigation (config only, no deploy): `driver_heatmap_v2_enabled` set
to `false` in `settings`, and the one UUID that had just been added to
`heatmap_internal_driver_ids` removed, so every driver falls back to the v1 payload path,
which does not run the broken query. Re-enable v2 after this fix is deployed.

## 4. Risk & impact on existing functionality

- Only caller of this query is `get_demand_heatmap` in `backend/routes/drivers/profile.py`
  (`grep scheduled_pickup_time backend/` → 1 file). Blast radius: isolated, single endpoint.
- The corrected query now actually returns rows: `status='scheduled'` rides whose
  `scheduled_time` is within the next `scheduled_lookahead_hours` (default 2 h). Those feed
  the `scheduled` component of each v2 cell, subject to the same per-component k-floor
  as `live`. No ride state, dispatch, money, or background-loop interaction — the endpoint
  is read-only.
- The v2 payload is Redis-cached for 60 s per `(area, version, config-fingerprint)`; the
  cache key already includes `v2`, so no stale v1 payload can be served to a v2 client.

## 5. User-experience effect

- Drivers: with the config mitigation, every online, idle driver in an area with
  `show_demand_heatmap` on now sees the v1 heatmap (decayed 7-day pickup density) and the
  legend pill instead of nothing. Visible mid-session on the next poll (≤ 90 s) or on
  offline→online toggle. Once v2 is re-enabled post-deploy, they get the layer selector,
  hotspot chips and forecast strip back — and the "Soon" layer will show scheduled pickups
  for the first time.
- No copy changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/profile.py` | `scheduled_pickup_time` → `scheduled_time` in the v2 scheduled-rides filter | column did not exist; endpoint 500'd |
| `backend/tests/test_drivers_shared_status_profile_coverage.py` | new `test_v2_scheduled_query_filters_on_real_column` | pins the column name so the mock cannot hide a rename again |

## 7. Before / after

```python
# Before
"scheduled_pickup_time": {"$gte": now.isoformat(), "$lte": cutoff_scheduled},
```

```python
# After
"scheduled_time": {"$gte": now.isoformat(), "$lte": cutoff_scheduled},
```

## 8. Rollback plan

Config only, no deploy: set `settings.driver_heatmap_v2_enabled = false` (and clear
`heatmap_internal_driver_ids`) — that is exactly the mitigation already applied, and it
routes every driver to the v1 path which does not execute this query. `driver_heatmap_enabled = false`
is the fleet-wide kill switch for the whole feature if needed.

## 9. Verification performed

- [x] Root cause confirmed against production: `information_schema.columns` shows no
  `scheduled_pickup_time` on `rides`; Supabase edge logs show the 400s.
- [x] Blast-radius grep: `scheduled_pickup_time` appears only in `profile.py`.
- [x] `ruff check` / `ruff format --check` on both files — clean for the new code
  (one pre-existing F841 at `test_drivers_shared_status_profile_coverage.py:1459` is untouched).
- [ ] `pytest` could NOT be run in this session: the remote container has no backend
  venv and PyPI was unreachable through the proxy (`aiohappyeyeballs==2.6.1` — "from versions: none").
  CI must run `tests/test_drivers_shared_status_profile_coverage.py::TestGetDemandHeatmapV2`.
- [x] Not feature-flagged separately: the existing `driver_heatmap_v2_enabled` flag already
  gates the only code path touched.

## 10. What was NOT verified

- The new test was not executed locally (see above).
- The corrected v2 payload was not exercised against production: v2 is currently off by
  the mitigation. After deploy, re-enable v2 and confirm `spinr_drivers_heatmap_requests_total`
  climbs with no 5xx on `/drivers/demand-heatmap`.
- No visual check of the driver app's "Soon" layer with real scheduled rides.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] UX effect stated for the already-shipped heatmap flow
