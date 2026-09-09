# Driver Heatmap Backend Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task by task. This PR authorizes review of the plan; it does not implement or deploy the redesign.

**Goal:** Serve trustworthy, privacy-protected demand snapshots and smooth transparent tiles to all driver surfaces.
**Architecture:** Add opt-in v3 metadata and a scoped tile session route alongside the existing v1/v2 endpoint. Aggregate before rasterizing; authorize before cache access; use bounded shared caches and CPU workers.
**Tech Stack:** Existing Python/FastAPI, Supabase/Postgres, Redis, NumPy and Pillow; all are already declared in backend requirements. Keep existing pinned dependencies.
**Spec:** [Driver heatmap review and design](../specs/2026-09-08-driver-heatmap-review-design.md).

## Global constraints

- Preserve Apple Maps/iOS, Google Maps/Android and the existing Android Auto integration; no CarPlay activation.
- Preserve v1/v2 compatibility, fare calculations, driver availability and ride/insurance state machines.
- v3 privacy: at least 3 distinct contributing riders per cell/component; no raw coordinates or identifiers in outputs/logs. Legacy read-time floor at least 3 rides.
- v3 expiry 180 seconds; refresh 90 seconds ±10%, effective v3 range 30–120 seconds. Legacy refresh remains 30–600.
- Use the repository's dual-import pattern, real logger conventions and append-only migration runner. New flags default false.
- Every implementation commit changes at most 3 files and approximately 200 lines; split larger units without leaving an enabled partial feature. Include the change-impact entry in each implementation PR body.
- Execute mobile Task M0 before production raster infrastructure B4–B6. Hardware capability is a prerequisite, not a claimed result.

## Dependency and ownership map

| Unit | Owner | Produces | Depends on |
|---|---|---|---|
| B0–B1 | Backend | Enforced privacy floor and honest legacy metadata | Existing endpoint/config |
| B2–B3 | Backend/data | Complete aggregate input and versioned v3 contract | B0; repository schema verification |
| B4 | Backend | Deterministic transparent PNG | B3; M0 capability evidence |
| B5–B6 | Backend/security | Scoped tile access and bounded caches | B3–B4 |
| B7 | Backend/mobile QA | Canary-ready service, no public activation | B0–B6; mobile plan |

## B0 — Enforce the existing privacy minimum (P0)

Files: modify `backend/utils/heatmap_config.py`, `backend/tests/test_heatmap_config_resolution.py`; commit separately from admin changes.

- [ ] Add resolver regression tests for global and per-area values 1/2, string JSON, malformed values, fallback, and a stricter override.

```python
def test_area_override_cannot_lower_privacy_floor():
    config = resolve_heatmap_config(
        {"heatmap_config": {"k_floor": 1}}, {"heatmap_k_floor": 3})
    assert config["k_floor"] == 3
```

- [ ] Run `cd backend && pytest tests/test_heatmap_config_resolution.py -q --no-cov`; confirm this new regression fails against the reviewed resolver before fixing it.
- [ ] Change the resolver's lower bound from 1 to 3, preserving higher floors and cache fingerprinting. Re-run and commit `fix(heatmap): enforce minimum privacy floor at read time`.
- [ ] In a second commit modify `backend/routes/admin/settings.py` and `backend/tests/test_admin_settings_heatmap_config.py`: reject global floor 1/2; assert 422 and retain valid 3–50 behavior. Find per-area write validators with `rg -n 'k_floor|heatmap_config' backend/routes/admin`; align their bounds in separate at-most-3-file commits if they have independent bounds. Do not lower migration 397's global CHECK.
- [ ] Run both named test modules. Audit actual global/per-area configuration read-only in staging; document migration applicability separately from code evidence. Do not query or alter production merely to execute unit tests.

## B1 — Correct legacy metadata and preserve compatibility (P0/P1)

Files: modify `backend/routes/drivers/profile.py`, `backend/tests/test_drivers_shared_status_profile_coverage.py`.

- [ ] Add assertions that both v1 and v2 include finite positive `cell_lat_deg`/`cell_lng_deg`, immutable `generated_at`, and an additive `expires_at`; verify cached retrieval does not reset timestamps.
- [ ] Move geometry metadata into the common response without changing v1 points or v2 cells. Derive legacy expiry from its actual configured refresh/retention policy; do not force v3's 180-second lifetime onto legacy 600-second clients.
- [ ] Preserve authorization/area resolution and kill-switch checks before caches. Add unauthenticated, no-driver, wrong-area and disabled tests through the mounted route; 401 for no session, no other area's data in any response. Confirm explicit driver eligibility policy before adding the v3 role gate; do not rely on client flags.
- [ ] Run `cd backend && pytest tests/test_drivers_shared_status_profile_coverage.py -q --no-cov`; commit `fix(heatmap): expose geometry and source freshness consistently`.

## B2 — Build complete, distinct-rider aggregates (P1)

Files: create `backend/services/demand_snapshot.py`, `backend/tests/test_demand_snapshot.py`; add schema/query work in a separate commit below.
Interface: `build_demand_snapshot(rows, *, area_id, layer, now, config) -> dict`; rows include ride id, rider id, pickup coordinates, timestamps and status internally. Output follows DemandV3, with zones but no token and no private rows.

- [ ] Write pure tests using synthetic distinct rider/ride IDs: 2 riders/20 repeated rides suppresses; 3 riders/3 valid requests qualifies; duplicate ride IDs count once; scheduled/past/current windows do not mix; invalid/nonfinite coordinates are rejected and measured; cancelled retries cannot create false activity.
- [ ] Normalize source times to UTC and choose the service area's timezone for hourly history. Keep recent request activity distinct from unserved demand; do not count an ongoing ride as an available new request. Configure bands in requests per ten-minute-equivalent window: initial low 3–5, medium 6–11, high 12+, only after privacy suppression. Store thresholds/window in `scale_version` inputs; counts here are proposed initial tuning, not measured city demand.
- [ ] Construct stable zone IDs from area/layer/coarse-cell identity, never array rank. Select geographically distinct peaks with at least 600 m separation. Keep server metadata to 20 candidate zones; clients collision-filter to their own screen limits. `wait_minutes` is null.
- [ ] Run `cd backend && pytest tests/test_demand_snapshot.py -q --no-cov`; commit the pure aggregate builder.
- [ ] Separately create `backend/repositories/demand_repo.py` and `backend/tests/test_demand_repo.py`: `load_demand_rows(area_id, window_start, window_end, cursor) -> (rows, next_cursor)`. Use deterministic `(created_at,id)` keyset paging and explicit area/time predicates; aggregate in streaming pages, not a 5,000-row truncation. Tests must place qualifying cells after row 5,000 and across equal-timestamp page boundaries.
- [ ] Inspect indexes with read-only schema tools. Prefer a DB aggregate query/RPC if it materially reduces transfer; before adding it, verify actual columns/types and query plans. Generate a new sequence-numbered migration through the repo runner conventions, not edits to migration 397. Any internal RPC must revoke PUBLIC/anon/authenticated execution, set a fixed search_path, and authorize area at the API boundary. Avoid an exposed unrestricted SECURITY DEFINER endpoint.
- [ ] Set a 2-second source-build timeout and configured row/work ceiling. If completeness cannot be established, return `availability=unavailable`, no zones/tiles, and a metric; never publish partial scans as representative demand. Record `EXPLAIN (ANALYZE, BUFFERS)` in staging and a >5,000-row integration result.

## B3 — Add versioned metadata behind a dark flag (P1)

Files: create `backend/models/demand_heatmap.py`; modify `backend/routes/drivers/profile.py`; create `backend/tests/test_demand_v3_contract.py`.

- [ ] Define Pydantic models matching DemandV3 in the spec, with enum layers, finite/ranged coordinates, bounded wait ranges, UTC timestamps, and fixed tile limits. Unknown layer/schema receives 422. Preserve legacy shape for callers that do not request `schema=3`.
- [ ] Test flag off, eligible driver, unauthenticated/rider/suspended cases, service-area switch, v1/v2 coexistence, empty privacy-suppressed area and backend failure. `schema=3` flag-off returns disabled metadata with no tile handle. Never accept a client-supplied area as authority.
- [ ] Wire the v3 builder through the repository adapter. Add `driver_heatmap_v3_enabled` plus per-platform/internal-driver/area targeting to the existing settings mechanism in a separate at-most-3-file commit (new migration, settings model, settings test). Determine the next migration sequence from the tree at execution time; include flag-off rollback, do not overwrite existing migrations. Read `backend/migrations/CLAUDE.md` first.
- [ ] Run `cd backend && pytest tests/test_demand_v3_contract.py tests/test_demand_snapshot.py -q --no-cov`; commit `feat(heatmap): add opt-in demand snapshot contract`.

## B4 — Rasterize only safe aggregates (P1; gated on M0)

Files: create `backend/services/demand_tiles.py`, `backend/tests/test_demand_tiles.py`.
Interface: `render_demand_tile(snapshot, *, z, x, y, palette) -> bytes` returns a 256×256 RGBA PNG. Input is immutable suppressed aggregate cells with stable scale; never raw ride rows.

- [ ] Write deterministic PNG tests: zero cells means transparent; center/edge continuity; neighboring tiles match at shared world coordinates; no alpha above .32; negative tile indices/out-of-range zoom rejected; separate light/night palettes. Use golden images produced from synthetic cells, not customer coordinates.
- [ ] Project aggregated cell centers into Web Mercator. Use a Gaussian kernel with sigma 180 m and support radius 540 m as initial tuning; sample tile plus that geographic halo, combine field values on a fixed scale, map amber/orange/red, crop only at the end. Apply a service-area mask; do not spread apparent demand outside the authorized area. Cap contribution and alpha so overlapping cells do not saturate to opaque red.
- [ ] Include kernel/palette/scale versions in snapshot identity. Keep native zoom 10–16; above-16 scaling is client rendering, not more granular source data. Render with existing NumPy/Pillow in a bounded worker pool, never inline CPU work on FastAPI's event loop.
- [ ] Run `cd backend && pytest tests/test_demand_tiles.py -q --no-cov`; inspect golden PNGs at adjacent tile seams and zoom transitions; commit `feat(heatmap): render seamless transparent demand tiles`.

## B5 — Secure metadata-issued tile sessions (P1)

Files: create `backend/services/demand_tile_sessions.py`, `backend/tests/test_demand_tile_sessions.py`.
Interface: `issue_tile_session(driver_id, auth_session_id, snapshot_id, area_id, layer, palette) -> handle`; `resolve_tile_session(handle, z, x, y) -> authorized_snapshot`.

- [ ] Test expired/revoked/random handle, area reassignment, suspended driver, global/per-area flag off, unauthorized bounds/zoom, session replacement and key/token leakage. Session handles use cryptographically random 32-byte values; store only a hash as the Redis lookup key.
- [ ] TTL is min(180 seconds, snapshot remaining lifetime, auth session remaining validity). Scope to a coarse service-area tile bounding range and one palette/layer/snapshot. Verify current driver/session eligibility and current switches before cache delivery; coalesce repeated eligibility reads only within a documented maximum 15-second revocation window. Logout/session revocation deletes applicable handles immediately.
- [ ] Return an opaque handle in the native URL template, never a JWT. Treat possession as a scoped bearer credential: redact it in application, reverse-proxy and client error logs; set Referrer-Policy and Cache-Control private/no-store on credential-bearing responses. Do not send tile URLs to analytics/Sentry. If this redaction cannot be verified at the serving proxy, do not enable tiles.
- [ ] Run `cd backend && pytest tests/test_demand_tile_sessions.py -q --no-cov`; commit `feat(heatmap): scope native tile sessions to authorized snapshots`.

## B6 — Route, cache and request budgets (P1)

Files: create `backend/routes/drivers/demand_tiles.py`, `backend/tests/test_demand_tile_route.py`; modify `backend/routes/drivers/__init__.py` to mount it. Wire metadata token issuance in a subsequent ≤3-file commit with profile.py and contract tests.

- [ ] Endpoint: `GET /drivers/demand-tiles/{handle}/{z}/{x}/{y}.png`. Authorize first, then retrieve/render. Successful content is image/png. Missing/invalid credentials 401; unauthorized area/zoom 403; exhausted budget 429; source unavailable 503. Empty valid geographic coverage is transparent, not an authentication bypass.
- [ ] Share raster bytes using immutable snapshot/layer/palette/z/x/y keys, never handle-specific credentials. Snapshot builds cache for 60 seconds; immutable raster/source lifetime at most 180 seconds. Use a Redis owner-token lock with 3-second TTL, 500 ms bounded wait and compare-and-delete release. A follower that cannot obtain a complete snapshot returns unavailable; it does not launch another unbounded scan.
- [ ] Default limits: metadata existing 20/minute per driver; tiles 240/minute per driver across sessions, burst 48; at most 4 simultaneous raster jobs per backend replica and 2 per tile session. Validate z before computing `2**z`, x/y range, service-area bounds and snapshot size. Enforce a 2,000-cell snapshot cap via privacy-safe coarsening before publication; never truncate randomly. Record coarsening in geometry/scale version.
- [ ] Redis unavailable: do not mint unresolved tile sessions or bypass access control. Return explicit unavailable v3 data and operational error metrics. Preserve independent ride/navigation flows. A stale cached raster never bypasses expiry or authorization.
- [ ] Run route/session/contract tests together; exercise simultaneous same-key misses and fake Redis failures. Load-test a connected phone+car viewport and rapid panning, with dispatch traffic running concurrently. Commit `feat(heatmap): serve bounded authenticated demand tiles`.

## B7 — Review and backend release evidence

- [ ] Verify HM26-01/07/10/11/13 against code and tests; document source/doc drift without claiming old change-log assertions remain true.
- [ ] Record aggregate completeness, suppressed-cell counts, cache hits, lock contention, source/raster durations, 401/403/429/503 counts. Only coarse area and version tags, no tile handles, rider IDs or raw coordinates.
- [ ] Run the relevant backend unit/integration suites and normal repository CI gates. Include actual output and production-image build status in the implementation PR; never infer production readiness from this plan.
- [ ] Rehearse disabling global/area/platform v3 flags: metadata and existing handles stop serving, phone/car clear overlays within their documented expiry/revocation bounds, and installed v1/v2 clients continue working.
- [ ] Hand off to [mobile/Auto plan](2026-09-08-driver-heatmap-mobile.md). Numeric wait-time modeling and financial incentives remain a separate opt-in follow-up with the spec's data gates.
