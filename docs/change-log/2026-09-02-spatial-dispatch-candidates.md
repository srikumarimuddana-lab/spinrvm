# Change Impact & Risk Log — WS-C: spatial dispatch candidates (audit C4)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-02 |
| Author | Claude Code session (branch `claude/topology-remediation-plan-g516e0`) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | commits `3c7f5b3`, `3d773b3`, `029e62d` on `claude/topology-remediation-plan-g516e0` |
| Related issue or gap ID | Critical issue **C4** in `docs/audit/2026-09-01-engineering-director-teardown.md`; WS-C of `plans/2026-09-01-critical-topology-remediation-plan.md` |

## 1. Issue / gap identified

The dispatch candidate fetch reads up to 500 driver rows through a lat/lng
bounding box and then computes haversine distance in Python. `drivers` has
carried a trigger-maintained PostGIS `location_geog` column and a partial GiST
index since migration 170, and neither is used by dispatch.

Two costs. The obvious one is that the geo predicates filter *within* an
online-fleet index walk rather than being an indexed lookup. The one that
actually hurts riders is that `LIMIT 500` truncates **arbitrarily**: on a dense
night the nearest driver can be in row 501, and dispatch reports "no drivers
available" with a driver two blocks away.

## 2. Root cause

Deliberate, and documented in `matching.py` itself (PR #2028 review): no
dedicated `(lat, lng)` index was added because
`idx_drivers_online_available_recency` already bounds the scan, and the comment
records the intended next step — *"a future radius query should go through an
RPC on that column rather than a second btree that every location heartbeat
would have to maintain."* This change is that step; nothing was overlooked, the
work was simply deferred.

## 3. Fix / remediation

Migration 395 adds `dispatch_candidate_drivers(...)`, a `SECURITY DEFINER`,
service-role-only SQL function that filters on the same predicates and orders by
the GiST KNN operator before applying the limit. `DISPATCH_SPATIAL_CANDIDATES`
(default **off**) routes both `matching.py` candidate fetches through it; any
exception falls back to the existing bounding-box query, loudly.

## 4. Risk & impact on existing functionality

**Blast radius: dispatch — the single most latency- and revenue-sensitive path
in the product. Mitigated to zero by the default flag state.** With the flag
unset (every environment today) the new branch is not entered and the box query
runs byte-for-byte as before.

| Consumer | Assessment |
|---|---|
| `filter_and_rank_drivers` | Unchanged, and deliberately still the exact distance gate. The RPC over-fetches by the same 10% + 1 km the box does — the padding is now a shared `_padded_radius_km()` so the two cannot drift. A tighter spatial fetch would have silently shrunk the ranked pool with no error anywhere. |
| Presence filter, subscription gate, service-area guard, `_is_dispatchable_driver` | All unchanged and all still applied in Python after the fetch. No logic moved into SQL. |
| Cascade (vehicle-type upgrade) pool | Also routed through the RPC. This forced the signature to take `text[]` rather than a scalar, since that path filters on a set of upgrade types. |
| `services/dispatch_service.find_candidate_drivers` | **Not touched.** A caller sweep found only test callers — no production path — so it is left as-is, the same treatment the plan prescribes for the unpopulated `find_nearby_drivers` RPC. Noted, not deleted. |
| `drivers` table / write path | No schema change. `location_geog` is already maintained by migration 170's trigger; this only reads it. |
| `find_nearby_drivers()` (migration 55) | Still reads the unpopulated `location` column and is still bypassed. Unchanged by this work; remains a follow-up. |
| RLS / PIPEDA | The function returns driver locations, so `EXECUTE` is revoked from `PUBLIC`/`anon`/`authenticated` and granted only to `service_role`, matching `drivers_available_in_polygon`. The projection is exactly the columns `matching.py` already requested — no encrypted PII. |

Residual risk, stated plainly: **no query in this change has ever been executed
against a Postgres.** The semantic parity between the SQL predicate and the
Python filter is verified by modelling both (see §9), which catches a logic
divergence but cannot catch a SQL-level error — a planner choosing a Seq Scan,
or a PostgREST return-type mismatch. That is what the staging step in §9 is for,
and it must happen before the flag is enabled anywhere.

## 5. User-experience effect

**None today** — the flag is off, so no rider, driver or admin sees a
difference, mid-session or otherwise.

When enabled, the intended rider-visible effect is *fewer false "no drivers
available" results* in dense areas, because the 500-row cap keeps the nearest
drivers instead of an arbitrary slice. No copy changes, no new notifications.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/395_dispatch_candidate_drivers_rpc.sql` | new RPC | The indexed candidate query. |
| `backend/repositories/driver_repo.py` | +`dispatch_candidate_drivers()` | Async wrapper; raises on DB error rather than returning `[]`. |
| `backend/db_supabase.py` | re-export, both dual-import halves | Call sites reach it via `_deps.db_supabase`. |
| `backend/services/dispatch_service.py` | +`_padded_radius_km`, `dispatch_radius_m`, `DISPATCH_SPATIAL_CANDIDATES` | Shared padding so the two paths cannot drift; the flag. |
| `backend/routes/rides/matching.py` | +`_fetch_candidate_drivers`, both call sites, cap-warning wording | The branch and its fallback. |
| `backend/tests/test_dispatch_spatial_candidates.py` | new | Parity, superset discipline, migration contract, fallback. |
| `.claude/context/domain-dispatch.md` | candidate-fetch section | The context doc described only the box path. |

## 7. Before / after

```python
# Before — routes/rides/matching.py (both sites)
all_drivers = await _deps.db_supabase.get_rows(
    "drivers", _dispatch_filter, columns="id,user_id,lat,lng,...", limit=500,
)
```

```python
# After — flag off, this still runs verbatim inside _fetch_candidate_drivers
all_drivers = await _fetch_candidate_drivers(
    box_filter=_dispatch_filter, lat=_box_lat, lng=_box_lng,
    radius_km=search_radius, vehicle_type_ids=[ride["vehicle_type_id"]],
    requires_wav=bool(ride.get("requires_wav")),
    area_ids=_area_ids, allow_unassigned_area=_allow_unassigned_area,
)
```

Concrete scenario — 50 online economy drivers in Saskatoon, 12 km radius:

| | Before | After (flag on) |
|---|---|---|
| DB work | 1 box query, index walk over the online fleet, arbitrary 500 | 1 RPC, GiST index scan, KNN-ordered 500 |
| Python | haversine over every returned row | unchanged (`filter_and_rank_drivers` still the exact gate) |
| Pool at the cap | arbitrary — nearest driver may be dropped | the 500 nearest |

## 8. Rollback plan

Three independent levels, none needing a deploy:

1. **Flag off** — `DISPATCH_SPATIAL_CANDIDATES` unset/false restores the box
   query. This is the primary rollback and is where the flag sits today.
2. **Automatic** — any RPC exception already falls back to the box query per
   call, so a broken function degrades rather than failing dispatch.
3. **Drop the function** — `DROP FUNCTION IF EXISTS dispatch_candidate_drivers(
   double precision, double precision, double precision, text[], boolean,
   text[], boolean, integer);` (in the migration header). Safe at any time; with
   the flag off nothing calls it, and with the flag on it degrades to level 2.

No data is written or migrated, so there is no data-level remediation to plan.

## 9. Verification performed

- [x] **Semantic parity, executed.** The SQL `WHERE` clause and the Python
      filter dict were modelled side by side and compared over an 11-driver
      fixture covering offline / unavailable / unverified / suspended / wrong
      vehicle type / WAV / wrong area / unassigned area / no geography / out of
      range, across 6 filter combinations. **0 divergences.**
- [x] **Column types checked against the real table, not assumed.** This found
      two genuine defects in the first draft: `drivers.rating` is `FLOAT` (I had
      written `numeric`) and `drivers.destination_mode` is `BOOLEAN` (I had
      written `text` with a `::text` cast). The second was the dangerous one —
      `dispatch_service` does `if not driver.get("destination_mode")`, and the
      string `'false'` is truthy in Python, so every driver would have looked
      like they were in destination mode and the pool would have silently
      collapsed. Both fixed and pinned by a test.
- [x] **KNN index usability reviewed.** The pickup point is written inline at
      each use rather than built in a CTE: `<->` is only index-answerable when
      one side is constant for the scan, and a CTE join risks a materialise plus
      a full sort — which would have cost exactly the speed-up this migration
      exists for while still returning correct rows.
- [x] Caller sweep for `find_candidate_drivers` (test-only) and `_deps.db_supabase`
      binding confirmed.
- [x] Migration prefix 395 confirmed free (`migration-check.yml` CHECK B).
- [x] `ruff check` + `ruff format --check` + `py_compile` clean on all touched files.
- [ ] **`pytest` not run; no SQL ever executed** — see §10.

## 10. What was NOT verified

- **No Postgres was involved at any point.** The function has never been
  created, called, or planned. Specifically unverified: that PostgREST accepts
  the `RETURNS TABLE` signature against the live `drivers` schema, and that the
  planner actually chooses `idx_drivers_location_geog_available`. The parity
  modelling in §9 is a check of the *predicate logic*, and deliberately claims
  nothing about SQL validity.
- **`pytest` was never executed** — PyPI egress is blocked in this environment
  (403 at the gateway), so `fastapi`/`pydantic`/`pytest` could not be installed.
  The parity and migration-contract classes were executed standalone under
  stdlib; the flag-reload tests (which need `monkeypatch`) were not.
- `python -m backend.scripts.run_migrations --dry-run` not run (needs
  `DATABASE_URL` and `psycopg2`, neither available here).
- No staging `EXPLAIN ANALYZE`, and therefore **no evidence the index is used**.
- No before/after P95 comparison of `spinr_dispatch_offer_to_accept_duration_ms`.

## 11. Required before enabling the flag anywhere (GATE C5)

The plan gates production enablement on the user's decision. That gate is not
reached yet — these must happen first, in order:

1. Apply migration 395 to staging.
2. `EXPLAIN ANALYZE SELECT * FROM dispatch_candidate_drivers(52.1332, -106.6700,
   14300, ARRAY['<vehicle_type_id>'], false, NULL, true, 500);` — confirm an
   **Index Scan using `idx_drivers_location_geog_available`**. A Seq Scan means
   the partial index's `WHERE is_online AND is_available` no longer lines up
   with the function's predicates, and the change is pointless until fixed.
3. Confirm the returned rows are byte-comparable to the box query's for the same
   inputs — in particular that `destination_mode` arrives as a boolean.
4. Staging with the flag on for 24 h; compare
   `spinr_dispatch_offer_to_accept_duration_ms` P95 and
   `spinr_dispatch_spatial_fallback_total` (which must be ~0) against 24 h off.
5. Only then present the numbers and ask about production.

## 12. Sign-off

- [x] Rollback plan is concrete and layered (flag / automatic fallback / drop)
- [x] Blast radius stated, and bounded to zero by the default flag state
- [x] No silent behavior change — the new path is unreachable until the flag is set
- [ ] **Not signed off on execution** — no test run and no SQL ever executed (§10)
