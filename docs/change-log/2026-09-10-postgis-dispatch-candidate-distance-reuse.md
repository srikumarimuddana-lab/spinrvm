# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (spinr session) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | (branch `mvapps/sleepy-galileo-hqp2xn`) |
| Related issue or gap ID | feat/48 (matching engine proximity query — rescoped, see below) |

## 1. Issue / gap identified

`feat/48` asked to "optimize the existing bounding-box + haversine query for
performance." Investigation found that query was already replaced in
production on 2026-09-04 (migration 404: `dispatch_geo_provider` flipped
`legacy` → `postgis`) — the bounding-box/haversine path is now the fallback
only. The *live* path (`services/dispatch_candidates.py`'s `postgis`
provider) had its own real inefficiency instead: the
`drivers_nearby_location_geog` RPC (migration 398) already computes and
`ORDER BY`s driver distance in Postgres, but `_postgis_ids()` discarded that
`distance_m` column, and `_rows_for_ids()` then recomputed the identical
distance a second time in Python via `haversine_km()` for every candidate,
on every dispatch attempt.

## 2. Root cause

`_postgis_ids()` only ever extracted `row["driver_id"]` from the RPC
response, never `row["distance_m"]`. `_rows_for_ids()` (shared by the
`postgis` and `h3` providers) always recomputes distance from scratch via
haversine, because for the `h3` provider that's the only source of truth —
H3 cell membership carries no distance. Nothing in the `postgis` path told
`_rows_for_ids()` a distance was already known, so it paid for the same
trig computation twice.

## 3. Fix / remediation

- `_postgis_ids()` now returns `(ids, distance_km_by_id)` instead of just
  `ids` — reading the RPC's own `distance_m` column (converted to km).
- `_rows_for_ids()` gained an optional `known_distance_km: dict[str, float]`
  parameter. When a row's id is present in that map, its value is used
  directly instead of calling `haversine_km()`. Sorting and the
  radius safety check are unchanged and still run over every row
  regardless of source — only the redundant trig call is skipped.
- `_postgis_or_fallback()` passes the new distance map through.
- The `h3`, `shadow`, and `legacy` providers are untouched: they never pass
  `known_distance_km`, so `_rows_for_ids()` falls back to computing
  haversine exactly as before for them — zero behavior change.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped the whole backend for every caller of
  `_postgis_ids` and `_rows_for_ids` (both module-private, `_`-prefixed).
  `_postgis_ids` has exactly one caller (`_postgis_or_fallback`, in the
  same file); `_rows_for_ids` has two (`_postgis_or_fallback` and
  `_h3_or_fallback`, both in the same file). No route, service, or test
  imports either helper directly — the only external entry point is the
  public `fetch_dispatch_candidates()`, whose signature and return contract
  (`list[dict]` of driver rows) are unchanged.
- **What else reads/writes the same state:** nothing new. No new table
  reads/writes, no new RPC, no change to the `drivers_nearby_location_geog`
  SQL function itself (migration 398 unchanged) — this only changes how its
  *existing* return columns are used in Python.
- **Could this regress a working flow?** The only way this changes an
  actual dispatch outcome is if the RPC's `distance_m` and a Python-side
  `haversine_km` recomputation over the *same* `pickup_lat`/`pickup_lng`
  could ever disagree materially. They compute the same great-circle
  distance (RPC: PostGIS `ST_Distance` on a geography column; Python:
  haversine) between the same two points, so any divergence would be
  sub-meter floating-point/projection noise, not a behavior change. Order
  and set of returned drivers are unaffected — verified by test
  (`test_postgis_reuses_rpc_distance_without_recomputing_haversine`) using
  eligibility-refetch rows returned in a *different* order than the RPC,
  confirming the sort still lands correctly using the known distances.
- Does not touch the ride state machine, money/wallet deltas, or any of the
  41 background loops.

## 5. User-experience effect

None directly visible. This reduces backend CPU work per dispatch attempt
under the `postgis` provider (the current production default); it does not
change which drivers get offered a ride, in what order, or how fast an
offer reaches a driver's phone beyond a (unmeasured, expected-small)
latency improvement from removing one redundant per-row trig computation.
No copy, notification, or UI change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/dispatch_candidates.py` | `_postgis_ids()` now returns `(ids, distance_km)`; `_rows_for_ids()` accepts optional `known_distance_km` and uses it instead of recomputing haversine when present; `_postgis_or_fallback()` wires the two together | Eliminate a redundant per-driver distance (re)computation on the live (`postgis`) dispatch path — the RPC already computed and ordered by the same value |
| `backend/tests/test_dispatch_candidates.py` | Added `test_postgis_reuses_rpc_distance_without_recomputing_haversine` and `test_postgis_falls_back_to_haversine_when_rpc_omits_distance` | Prove haversine is skipped when the RPC provides distance (including when the eligibility re-fetch returns rows in a different order), and that behavior still falls back correctly when it doesn't |

## 7. Before / after

```python
# Before — services/dispatch_candidates.py
async def _postgis_ids(db, lat, lng, radius_km, limit, dispatch_filter=None) -> list[str]:
    ...
    rows = await db.rpc(POSTGIS_RPC, params)
    ...
    out: list[str] = []
    for row in rows:
        if isinstance(row, dict) and row.get("driver_id"):
            out.append(str(row["driver_id"]))
    return out  # distance_m discarded

async def _rows_for_ids(db, dispatch_filter, ids, columns, limit, *, pickup_lat, pickup_lng, radius_km) -> list[dict]:
    ...
    for row in rows or []:
        dist = haversine_km(pickup_lat, pickup_lng, float(row["lat"]), float(row["lng"]))  # always recomputed
        ...
```

```python
# After
async def _postgis_ids(db, lat, lng, radius_km, limit, dispatch_filter=None) -> tuple[list[str], dict[str, float]]:
    ...
    ids: list[str] = []
    distance_km: dict[str, float] = {}
    for row in rows:
        ...
        dist_m = row.get("distance_m")
        if isinstance(dist_m, (int, float)):
            distance_km[driver_id] = dist_m / 1000.0
    return ids, distance_km

async def _rows_for_ids(..., known_distance_km: Optional[dict[str, float]] = None) -> list[dict]:
    ...
    for row in rows or []:
        driver_id = row.get("id")
        dist = known_distance_km.get(str(driver_id)) if known_distance_km and driver_id is not None else None
        if dist is None:
            dist = haversine_km(pickup_lat, pickup_lng, float(row["lat"]), float(row["lng"]))  # only when unknown
        ...
```

## 8. Rollback plan

`git revert` is sufficient and complete here — no data was written, no
migration applied, no flag flipped. This is a pure code-level change to an
internal computation path with no persisted state of its own. Reverting the
commit restores the exact prior behavior (always recompute via haversine)
with no follow-up cleanup needed.

## 9. Verification performed

- [x] Automated tests run (unit): `pytest tests/test_dispatch_candidates.py` (17/17 passed, including 2 new), plus the full dispatch surface — `test_dispatch_cascade.py`, `test_dispatch_claim_parity.py`, `test_dispatch_db_errors.py`, `test_dispatch_match_attempt_branches.py`, `test_dispatch_metrics.py`, `test_dispatch_notify_loop_branches.py`, `test_dispatch_presence_failopen.py`, `test_cross_service_area_dispatch.py`, `test_rides_matching_coverage.py`, `test_e2e_wav_dispatch.py`, `tests/services/test_dispatch_service.py`, `test_wav_dispatch.py` — 192/192 passed.
- [ ] Manual repro steps followed in staging — **not done**, no staging Supabase/PostGIS access from this session.
- [x] Blast-radius grep performed: `grep -rn "_postgis_ids\(" backend --include=*.py` and the same for `_rows_for_ids` — one caller each, both inside `dispatch_candidates.py`.
- [x] Reviewed against relevant CLAUDE.md convention(s): dispatch domain rules (`.claude/context/domain-dispatch.md`), N+1/query-perf guidance — no new query added, no new N+1.
- [x] Feature-flagged if user-visible and non-trivial — not applicable; not user-visible, no flag needed (pure internal computation, same inputs/outputs).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-level cleanup required)
- [x] Blast radius is stated, not assumed: isolated to `backend/services/dispatch_candidates.py` (2 call sites, both internal to the file) and its test file
- [x] No silent behavior change to an already-shipped flow: no UX field needed — this is backend-internal, no observable behavior difference
