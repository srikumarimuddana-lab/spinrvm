# Actual Route OSRM Gap Reconstruction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconstruct every completed route in chronological order from pickup to the authorized completion point, use OSRM to fill missing spatial intervals, and include inferred geometry in post-trip distance statistics without changing the settled fare.

**Architecture:** The backend remains the sole reconstruction authority. It map-matches continuous GPS evidence, snaps authorized endpoints, inserts bounded OSRM Route connectors for missing start/internal/tail intervals, and publishes an ordered provenance-aware route revision. Shared client utilities preserve section provenance; rider and driver completed-ride maps render observed and inferred sections distinctly without making routing calls.

**Tech Stack:** Python 3.12, FastAPI, httpx, OSRM Nearest/Match/Route, Supabase/Postgres JSONB, TypeScript, React Native, react-native-maps, Pytest, Jest.

## Global Constraints

- Completed-trace calls use only configured `app_settings.osrm_url` or `Settings.OSRM_URL`; never the public OSRM demo fallback.
- OSRM Trip service is forbidden because it can optimize/reorder coordinates.
- Endpoint snaps must be within 75 metres of their authorized guardrail.
- Boundaries within 30 metres are continuous and require no inferred connector.
- A connector distance must be at least its haversine boundary distance and no greater than `max(5 * haversine, haversine + 2 km)`.
- Process at most 20 inferred connectors in one route revision.
- Inferred geometry updates post-trip `actual_distance_km` statistics only; fare, tax, earnings, settlement, and receipt snapshots remain immutable.
- Never draw a straight fallback chord or use the booking-time planned route for a completed v2 ride.
- Never log raw coordinates.
- Every implementation task follows RED → GREEN and is committed before the next task begins.

---

## File Structure

- `backend/utils/route_distance.py`: low-level OSRM Nearest and ordered two-point Route provider calls.
- `backend/utils/route_reconstruction.py`: pure ordering/provenance plus async gap orchestration for one completed ride.
- `backend/utils/route_finalizer.py`: durable revision persistence, quality projection, retry status, and post-trip statistics update.
- `backend/repositories/ride_repo.py`: authorized additive route-section projection.
- `shared/utils/routeSegments.ts`: provenance-preserving normalization and quality copy.
- `shared/constants/routeMapStyle.ts`: observed and inferred route strokes.
- Rider/driver completed screens: render backend sections only.

---

### Task 1: Add bounded OSRM endpoint and gap provider primitives

**Files:**
- Modify: `backend/utils/route_distance.py`
- Modify: `backend/tests/test_route_distance_osrm.py`

**Interfaces:**
- Consumes: `{lat: float, lng: float}` coordinates and an explicit self-hosted OSRM base URL.
- Produces: `snap_endpoint_via_osrm(point, osrm_url) -> Optional[list[float]]` and `compute_gap_route_via_osrm(start, end, osrm_url) -> Optional[RoadMatch]`.

- [ ] **Step 1: Write failing provider contract tests**

Add tests that capture the outgoing URL/params and pin coordinate order, endpoint distance rejection, GeoJSON conversion, and connector sanity rejection:

```python
@pytest.mark.asyncio
async def test_snap_endpoint_uses_nearest_and_rejects_snap_over_75m():
    payload = {"code": "Ok", "waypoints": [{"distance": 76.0, "location": [-104.62, 50.45]}]}
    capture = {}
    with patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=payload), capture=capture)):
        result = await rd.snap_endpoint_via_osrm({"lat": 50.45, "lng": -104.62}, "http://osrm:5000")
    assert result is None
    assert "/nearest/v1/driving/-104.62,50.45" in capture["url"]

@pytest.mark.asyncio
async def test_gap_route_returns_ordered_geojson_and_distance():
    payload = {"code": "Ok", "routes": [{
        "distance": 420.0,
        "geometry": {"coordinates": [[-104.62, 50.45], [-104.625, 50.452]]},
    }]}
    with patch.object(rd.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=payload))):
        result = await rd.compute_gap_route_via_osrm([50.45, -104.62], [50.452, -104.625], "http://osrm:5000")
    assert result == (0.42, [[50.45, -104.62], [50.452, -104.625]])
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
python -m pytest -q --no-cov backend/tests/test_route_distance_osrm.py -k "snap_endpoint or gap_route"
```

Expected: FAIL because both provider functions are absent.

- [ ] **Step 3: Implement the minimal provider calls**

Implement Nearest with `number=1`. Implement Route with `alternatives=false`, `steps=false`, `overview=full`, and `geometries=geojson`. Convert OSRM `[lng, lat]` to stored `[lat, lng]`, require at least two unique coordinates, and validate distance with the global connector bounds.

```python
def _dedupe_coordinates(coordinates: List[List[float]]) -> List[List[float]]:
    deduped: List[List[float]] = []
    for coordinate in coordinates:
        if not deduped or coordinate != deduped[-1]:
            deduped.append(coordinate)
    return deduped

async def snap_endpoint_via_osrm(point: dict, osrm_url: str) -> Optional[List[float]]:
    url = f"{osrm_url.rstrip('/')}/nearest/v1/driving/{point['lng']},{point['lat']}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            response = await client.get(url, params={"number": 1})
        data = response.json() if response.status_code == 200 else {}
    except Exception:
        logger.warning("[route_distance] OSRM endpoint snap failed", exc_info=True)
        return None
    waypoint = ((data.get("waypoints") or [None])[0]) if data.get("code") == "Ok" else None
    if not isinstance(waypoint, dict) or float(waypoint.get("distance") or math.inf) > 75.0:
        return None
    lng, lat = waypoint["location"]
    return [round(float(lat), 6), round(float(lng), 6)]

async def compute_gap_route_via_osrm(
    start: Sequence[float], end: Sequence[float], osrm_url: str
) -> Optional[RoadMatch]:
    url = f"{osrm_url.rstrip('/')}/route/v1/driving/{start[1]},{start[0]};{end[1]},{end[0]}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            response = await client.get(url, params={
                "alternatives": "false", "steps": "false", "overview": "full", "geometries": "geojson"
            })
        data = response.json() if response.status_code == 200 else {}
    except Exception:
        logger.warning("[route_distance] OSRM gap route failed", exc_info=True)
        return None
    route = ((data.get("routes") or [None])[0]) if data.get("code") == "Ok" else None
    if not isinstance(route, dict):
        return None
    distance_km = float(route.get("distance") or 0) / 1000.0
    direct_km = _haversine_km(float(start[0]), float(start[1]), float(end[0]), float(end[1]))
    if distance_km < direct_km or distance_km > max(5 * direct_km, direct_km + 2):
        return None
    polyline = [[round(float(lat), 6), round(float(lng), 6)] for lng, lat in route["geometry"]["coordinates"]]
    return (round(distance_km, 3), _dedupe_coordinates(polyline)) if len(polyline) >= 2 else None
```

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
python -m pytest -q --no-cov backend/tests/test_route_distance_osrm.py
```

Expected: all OSRM provider tests pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/utils/route_distance.py backend/tests/test_route_distance_osrm.py
git commit -m "feat(routes): add OSRM gap provider calls"
```

---

### Task 2: Build the ordered provenance-aware reconstruction service

**Files:**
- Create: `backend/utils/route_reconstruction.py`
- Create: `backend/tests/test_route_reconstruction.py`

**Interfaces:**
- Consumes: `SegmentedRoute`, the existing `compute_segmented_road_route()` result, pickup/completion dictionaries, and configured OSRM settings.
- Produces: `reconstruct_completed_route(segmented, matched_route, pickup_point, completion_point) -> dict` with ordered `segments`, total/observed/inferred distances, ratios, endpoint flags, gap counts, and `failed_gaps`.

- [ ] **Step 1: Write failing reconstruction tests**

Use real segmentation objects and patch only the provider boundaries. Cover missing start, an internal gap, missing tail, 30 m deduplication, unavailable OSRM, connector cap, and preserved chronological ordering:

```python
@pytest.mark.asyncio
async def test_reconstructs_start_internal_and_tail_in_order(monkeypatch):
    base = datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc)
    def point(seconds, sequence, lat, lng):
        return {
            "recording_session_id": "session-a",
            "sequence_number": sequence,
            "captured_at": (base + timedelta(seconds=seconds)).isoformat(),
            "lat": lat,
            "lng": lng,
            "accuracy": 8,
        }
    completion = {"lat": 50.4600, "lng": -104.6300, "accuracy": 8}
    segmented = segment_route(
        [
            point(10, 0, 50.4510, -104.6210),
            point(20, 1, 50.4520, -104.6220),
            point(90, 2, 50.4550, -104.6250),
            point(100, 3, 50.4560, -104.6260),
        ],
        {"ride_started_at": base.isoformat(), "ride_completed_at": (base + timedelta(seconds=120)).isoformat()},
        completion,
    )
    matched = {
        "segments": [
            {"segment_index": 0, "matched_segments": [{
                "provider": "osrm_match", "distance_km": 0.2,
                "polyline": [[50.4510, -104.6210], [50.4520, -104.6220]],
            }]},
            {"segment_index": 1, "matched_segments": [{
                "provider": "osrm_match", "distance_km": 0.2,
                "polyline": [[50.4550, -104.6250], [50.4560, -104.6260]],
            }]},
        ],
        "failures": [],
    }
    monkeypatch.setattr(reconstruction, "get_app_settings", AsyncMock(return_value={"osrm_url": "http://osrm:5000"}))
    monkeypatch.setattr(reconstruction, "snap_endpoint_via_osrm", AsyncMock(side_effect=[[50.45, -104.62], [50.46, -104.63]]))
    gap = AsyncMock(side_effect=[
        (0.15, [[50.4500, -104.6200], [50.4510, -104.6210]]),
        (0.45, [[50.4520, -104.6220], [50.4550, -104.6250]]),
        (0.55, [[50.4560, -104.6260], [50.4600, -104.6300]]),
    ])
    monkeypatch.setattr(reconstruction, "compute_gap_route_via_osrm", gap)

    result = await reconstruction.reconstruct_completed_route(
        segmented,
        matched,
        {"lat": 50.45, "lng": -104.62},
        {"lat": 50.46, "lng": -104.63},
    )

    assert [section["gap_reason"] for section in result["segments"] if section["geometry_kind"] == "inferred"] == [
        "missing_start", "internal_gap", "missing_tail"
    ]
    assert result["distance_km"] == pytest.approx(
        result["observed_distance_km"] + result["inferred_distance_km"]
    )
    assert result["inferred_gap_count"] == 3
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
python -m pytest -q --no-cov backend/tests/test_route_reconstruction.py
```

Expected: FAIL because the reconstruction module does not exist.

- [ ] **Step 3: Implement section projection and ordered connectors**

Create helpers with explicit responsibilities:

```python
CONTINUITY_TOLERANCE_M = 30.0
MAX_ENDPOINT_SNAP_M = 75.0
MAX_INFERRED_CONNECTORS = 20

def project_observed_sections(segmented: SegmentedRoute, matched_route: dict) -> list[dict]:
    """Return ordered observed sections with provider, source index, coordinates, and distance_km."""

async def reconstruct_completed_route(
    segmented: SegmentedRoute,
    matched_route: dict,
    pickup_point: dict,
    completion_point: dict,
) -> dict:
    """Insert only OSRM-inferred connectors between chronological observed boundaries."""
```

Each observed section has `geometry_kind="observed"` and `gap_reason=None`. Each connector has `provider="osrm_inferred"`, `geometry_kind="inferred"`, and one exact gap reason. When a required connector fails, record the reason in `failed_gaps` and leave the sections disconnected. Never fabricate coordinates and never import/use an OSRM Trip function.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
python -m pytest -q --no-cov backend/tests/test_route_reconstruction.py backend/tests/test_route_distance_osrm.py
```

Expected: both suites pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/utils/route_reconstruction.py backend/tests/test_route_reconstruction.py
git commit -m "feat(routes): reconstruct ordered OSRM gaps"
```

---

### Task 3: Integrate reconstruction with finalization and post-trip statistics

**Files:**
- Modify: `backend/utils/route_finalizer.py`
- Modify: `backend/tests/test_route_finalizer.py`

**Interfaces:**
- Consumes: `reconstruct_completed_route()` output from Task 2.
- Produces: persisted provenance-aware `road_matched_segments`, distance-based `route_quality`, complete/incomplete status, revisioned snapshot input, and statistics-only ride updates.

- [ ] **Step 1: Write failing finalizer tests**

Add tests proving the finalizer passes pickup/completion into reconstruction, publishes its ordered segments, marks failed connectors incomplete, updates `actual_distance_km`, and never writes fare fields:

```python
def test_finalizer_uses_reconstructed_distance_without_touching_fare(monkeypatch):
    reconstructed = {
        "segments": [{
            "coordinates": [[50.45, -104.62], [50.46, -104.63]],
            "provider": "osrm_inferred",
            "geometry_kind": "inferred",
            "gap_reason": "missing_start",
            "distance_km": 1.25,
        }],
        "distance_km": 1.25,
        "observed_distance_km": 0.75,
        "inferred_distance_km": 0.50,
        "observed_distance_ratio": 0.6,
        "inferred_distance_ratio": 0.4,
        "inferred_gap_count": 2,
        "endpoint_start_verified": True,
        "endpoint_end_verified": True,
        "failed_gaps": [],
    }
    updates = []
    async def capture_update(table, filters, payload, **kwargs):
        updates.append((table, filters, payload))
        return {**filters, **payload}
    monkeypatch.setattr(route_finalizer.db_supabase, "update_one", capture_update)
    monkeypatch.setattr(route_finalizer, "reconstruct_completed_route", AsyncMock(return_value=reconstructed))
    result = _run(route_finalizer.finalize_route("ride_1"))
    ride_update = next(payload for table, _filters, payload in updates if table == "rides")
    route_update = next(payload for table, _filters, payload in updates if table == "ride_routes")
    assert result["processing_status"] == "complete"
    assert ride_update["actual_distance_km"] == 1.25
    assert "total_fare" not in ride_update
    assert "fare_breakdown_snapshot" not in ride_update
    assert route_update["route_quality"]["observed_distance_ratio"] == 0.6
```

Also add a regression fixture matching the screenshot pattern: timestamps span the lifecycle, but the first GPS section is away from pickup. Expected quality must report inferred start distance rather than `100% GPS coverage`.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
python -m pytest -q --no-cov backend/tests/test_route_finalizer.py
```

Expected: new assertions fail because finalization still persists isolated matched sections and recomputes statistics from breadcrumbs alone.

- [ ] **Step 3: Wire reconstruction into the durable revision**

Replace `_matched_projection()` use with:

```python
reconstructed = await reconstruct_completed_route(
    segmented,
    matched_route,
    {"lat": ride.get("pickup_lat"), "lng": ride.get("pickup_lng")},
    (route_row or {}).get("completion_point") or {},
)
display_segments = reconstructed["segments"]
```

Merge reconstruction fields into `_quality_projection()`. `_final_status()` returns incomplete when `failed_gaps` is non-empty or either endpoint is unresolved. Change `_recompute_ride_distance_stats()` to accept the reconstructed distance and use it for trip `actual_distance_km`; retain breadcrumb-derived phase durations and pickup-to-driver statistics. Keep the completed-status compare-and-set and append-only audit insert. Do not add any fare, tax, earning, payment, or settlement field to the update payload.

- [ ] **Step 4: Verify GREEN and nearby regressions**

Run:

```powershell
python -m pytest -q --no-cov backend/tests/test_route_finalizer.py backend/tests/test_route_reconstruction.py backend/tests/test_route_segments.py backend/tests/test_trip_distance.py
```

Expected: all selected backend suites pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/utils/route_finalizer.py backend/tests/test_route_finalizer.py
git commit -m "feat(routes): finalize pickup-to-completion geometry"
```

---

### Task 4: Expose additive section provenance in the authorized ride contract

**Files:**
- Modify: `backend/repositories/ride_repo.py`
- Modify: `backend/tests/test_ride_route_contract.py`
- Modify: `shared/types/api/route.ts`

**Interfaces:**
- Consumes: stored route sections containing `coordinates`, `provider`, `geometry_kind`, and `gap_reason`.
- Produces: the same authorized additive fields in `actual_route_segments`; shared `ActualRouteSegment` and `RouteQuality` definitions include the new quality metrics.

- [ ] **Step 1: Write failing contract tests**

```python
def test_authorized_route_preserves_safe_section_provenance():
    ride = project_v2_route([
        {
            "coordinates": [[50.45, -104.62], [50.451, -104.621]],
            "provider": "osrm_inferred",
            "geometry_kind": "inferred",
            "gap_reason": "missing_start",
        }
    ])
    assert ride["actual_route_segments"][0]["geometry_kind"] == "inferred"
    assert ride["actual_route_segments"][0]["gap_reason"] == "missing_start"
```

Add TypeScript fields:

```ts
export type RouteGeometryKind = 'observed' | 'inferred';
export type RouteGapReason = 'missing_start' | 'internal_gap' | 'missing_tail';
```

- [ ] **Step 2: Run the backend contract test and verify RED**

```powershell
python -m pytest -q --no-cov backend/tests/test_ride_route_contract.py
```

Expected: FAIL if the repository sanitization does not preserve the additive provenance.

- [ ] **Step 3: Implement the safe additive projection and types**

Normalize each section to coordinates plus enumerated safe provenance fields. Ignore unknown provider/kind/reason values rather than exposing arbitrary stored metadata. Extend `RouteQuality` with:

```ts
observed_distance_km?: number;
inferred_distance_km?: number;
observed_distance_ratio?: number;
inferred_distance_ratio?: number;
inferred_gap_count?: number;
endpoint_start_verified?: boolean;
endpoint_end_verified?: boolean;
temporal_coverage_ratio?: number;
```

- [ ] **Step 4: Verify GREEN**

```powershell
python -m pytest -q --no-cov backend/tests/test_ride_route_contract.py
```

Expected: contract tests pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/repositories/ride_repo.py backend/tests/test_ride_route_contract.py shared/types/api/route.ts
git commit -m "feat(routes): expose inferred route provenance"
```

---

### Task 5: Preserve provenance and distance-based quality in shared client utilities

**Files:**
- Modify: `shared/utils/routeSegments.ts`
- Modify: `shared/constants/routeMapStyle.ts`
- Modify: `admin-dashboard/src/lib/__tests__/route-segments.test.ts`

**Interfaces:**
- Consumes: additive route sections and quality from Task 4.
- Produces: `normalizeActualRouteSegments()`, `toReactNativeRouteSections()`, `routeQualityLabel()`, and `INFERRED_ROUTE_STROKE`.

- [ ] **Step 1: Write failing shared utility tests**

```ts
it('preserves inferred provenance for native rendering', () => {
  expect(toReactNativeRouteSections([{
    coordinates: [[50.45, -104.62], [50.46, -104.63]],
    provider: 'osrm_inferred',
    geometry_kind: 'inferred',
    gap_reason: 'missing_tail',
  }])).toEqual([expect.objectContaining({
    geometryKind: 'inferred',
    gapReason: 'missing_tail',
  })]);
});

it('labels distance-based observed and inferred coverage', () => {
  expect(routeQualityLabel({ observed_distance_ratio: 0.72, inferred_distance_ratio: 0.28 }))
    .toBe('Route reconstructed · 72% GPS observed · 28% inferred');
});
```

- [ ] **Step 2: Run and verify RED**

```powershell
npm test -- --run src/lib/__tests__/route-segments.test.ts
```

Run from `admin-dashboard`. Expected: FAIL because provenance is discarded and copy uses temporal coverage.

- [ ] **Step 3: Implement normalized sections, compatibility adapter, copy, and style**

```ts
export interface ReactNativeRouteSection {
  id: string;
  coordinates: ReactNativeRouteCoordinate[];
  geometryKind: 'observed' | 'inferred';
  provider?: string;
  gapReason?: 'missing_start' | 'internal_gap' | 'missing_tail';
}

export const INFERRED_ROUTE_STROKE = {
  strokeColor: '#F59E0B',
  strokeWidth: 4,
  lineDashPattern: [8, 6] as number[],
} as const;
```

Keep `toReactNativeSegments()` as a compatibility adapter over the new normalized result. `routeQualityLabel()` prefers distance-based ratios, falls back to legacy temporal coverage only when new metrics are absent, and returns `Route incomplete · OSRM reconstruction pending` when connector failures remain.

- [ ] **Step 4: Verify GREEN**

```powershell
npm test -- --run src/lib/__tests__/route-segments.test.ts
```

Expected: shared utility tests pass.

- [ ] **Step 5: Commit**

```powershell
git add shared/utils/routeSegments.ts shared/constants/routeMapStyle.ts admin-dashboard/src/lib/__tests__/route-segments.test.ts
git commit -m "feat(routes): normalize inferred route sections"
```

---

### Task 6: Render inferred sections on the rider completion screen

**Files:**
- Modify: `rider-app/app/ride-completed.tsx`
- Modify: `rider-app/__tests__/ride-completed-route.test.tsx`

**Interfaces:**
- Consumes: `toReactNativeRouteSections()` and both shared route strokes.
- Produces: a complete backend-authored pickup-to-completion map with solid observed and dashed inferred sections.

- [ ] **Step 1: Write a failing screen contract test**

```ts
expect(screenSource).toContain('toReactNativeRouteSections');
expect(screenSource).toContain("section.geometryKind === 'inferred'");
expect(screenSource).toContain('INFERRED_ROUTE_STROKE');
expect(screenSource).not.toContain('MapViewDirections');
expect(screenSource).not.toContain('/route/v1/');
```

- [ ] **Step 2: Run and verify RED**

```powershell
yarn test __tests__/ride-completed-route.test.tsx --runInBand
```

Expected: FAIL because the screen currently receives coordinate arrays without provenance.

- [ ] **Step 3: Render each section with its correct stroke**

Replace `actualSegments` with `actualSections`, flatten only for viewport fitting, and select stroke props per section:

```tsx
{actualSections.map((section) => (
  <Polyline
    key={section.id}
    coordinates={section.coordinates}
    {...(section.geometryKind === 'inferred' ? INFERRED_ROUTE_STROKE : ACTUAL_ROUTE_STROKE)}
    lineCap="round"
    lineJoin="round"
  />
))}
```

Keep pickup, destination, and completion markers independent. Keep planned geometry legacy-only. Do not add any client-side OSRM or Directions request.

- [ ] **Step 4: Verify GREEN**

```powershell
yarn test __tests__/ride-completed-route.test.tsx __tests__/useCompletedRouteRefresh.test.tsx --runInBand
```

Expected: both suites pass.

- [ ] **Step 5: Commit**

```powershell
git add rider-app/app/ride-completed.tsx rider-app/__tests__/ride-completed-route.test.tsx
git commit -m "feat(rider): show reconstructed completion route"
```

---

### Task 7: Render inferred sections in rider history

**Files:**
- Modify: `rider-app/app/ride-details.tsx`
- Modify: `rider-app/__tests__/ride-details-route.test.tsx`

**Interfaces:**
- Consumes: `toReactNativeRouteSections()`, `ACTUAL_ROUTE_STROKE`, and `INFERRED_ROUTE_STROKE`.
- Produces: provenance-aware rider history map and distance-based route quality copy.

- [ ] **Step 1: Write failing assertions**

```ts
expect(source).toContain('toReactNativeRouteSections');
expect(source).toContain("section.geometryKind === 'inferred'");
expect(source).toContain('INFERRED_ROUTE_STROKE');
expect(source).not.toContain('MapViewDirections');
```

- [ ] **Step 2: Run and verify RED**

```powershell
yarn test __tests__/ride-details-route.test.tsx --runInBand
```

Expected: FAIL on the new provenance assertions.

- [ ] **Step 3: Apply the section rendering contract**

Render each backend section explicitly while retaining map-ready refitting, markers, PDF snapshot generation, actual-distance statistics, and legacy-only planned geometry:

```tsx
{actualSections.map((section) => (
  <Polyline
    key={section.id}
    coordinates={section.coordinates}
    {...(section.geometryKind === 'inferred' ? INFERRED_ROUTE_STROKE : ACTUAL_ROUTE_STROKE)}
    lineCap="round"
    lineJoin="round"
  />
))}
```

PDF snapshot behavior remains server-artifact based and does not call routing services.

- [ ] **Step 4: Verify GREEN**

```powershell
yarn test __tests__/ride-details-route.test.tsx __tests__/useCompletedRouteRefresh.test.tsx --runInBand
```

Expected: both suites pass.

- [ ] **Step 5: Commit**

```powershell
git add rider-app/app/ride-details.tsx rider-app/__tests__/ride-details-route.test.tsx
git commit -m "feat(rider): show reconstructed history route"
```

---

### Task 8: Render inferred sections in driver history

**Files:**
- Modify: `driver-app/app/driver/ride-detail.tsx`
- Modify: `driver-app/__tests__/screens/ride-detail-route.test.tsx`

**Interfaces:**
- Consumes: `toReactNativeRouteSections()`, `ACTUAL_ROUTE_STROKE`, and `INFERRED_ROUTE_STROKE`.
- Produces: the driver-facing complete route shown in the reported screenshot, with truthful observed/inferred copy.

- [ ] **Step 1: Write failing assertions**

```ts
expect(source).toContain('toReactNativeRouteSections');
expect(source).toContain("section.geometryKind === 'inferred'");
expect(source).toContain('INFERRED_ROUTE_STROKE');
expect(source).toContain('routeQualityLabel');
expect(source).not.toContain('MapViewDirections');
expect(source).not.toContain('/trip/v1/');
```

- [ ] **Step 2: Run and verify RED**

```powershell
yarn test __tests__/screens/ride-detail-route.test.tsx --runInBand
```

Expected: FAIL on the provenance/stroke assertions.

- [ ] **Step 3: Apply the section rendering contract**

Replace coordinate-only arrays with provenance-aware sections and flatten only for `fitToCoordinates`:

```tsx
{actualSections.map((section) => (
  <Polyline
    key={section.id}
    coordinates={section.coordinates}
    {...(section.geometryKind === 'inferred' ? INFERRED_ROUTE_STROKE : ACTUAL_ROUTE_STROKE)}
    lineCap="round"
    lineJoin="round"
  />
))}
```

Preserve the road-snapped pickup marker, destination marker, authorized completion marker, and statistics-only distance display.

- [ ] **Step 4: Verify GREEN**

```powershell
yarn test __tests__/screens/ride-detail-route.test.tsx store/__tests__/driverStore.test.ts --runInBand
```

Expected: both suites pass.

- [ ] **Step 5: Commit**

```powershell
git add driver-app/app/driver/ride-detail.tsx driver-app/__tests__/screens/ride-detail-route.test.tsx
git commit -m "feat(driver): show reconstructed history route"
```

---

### Task 9: Verify the full route story and refresh the code graph

**Files:**
- Verify only; Graphify may update tracked `graphify-out` artifacts.

**Interfaces:**
- Consumes: all prior committed tasks.
- Produces: fresh test evidence, clean diffs, current graph, and a push-ready `main` branch.

- [ ] **Step 1: Run backend verification**

```powershell
python -m pytest -q --no-cov backend/tests/test_route_distance_osrm.py backend/tests/test_route_reconstruction.py backend/tests/test_route_finalizer.py backend/tests/test_route_segments.py backend/tests/test_ride_route_contract.py backend/tests/test_trip_distance.py backend/tests/test_ride_completion_location.py
```

Expected: all selected backend tests pass.

- [ ] **Step 2: Run rider and driver verification**

```powershell
Set-Location rider-app
yarn test __tests__/ride-completed-route.test.tsx __tests__/ride-details-route.test.tsx __tests__/useCompletedRouteRefresh.test.tsx --runInBand
Set-Location ..\driver-app
yarn test __tests__/screens/ride-detail-route.test.tsx store/__tests__/driverStore.test.ts --runInBand
Set-Location ..
```

Expected: all selected Jest suites pass.

- [ ] **Step 3: Run shared utility and static checks**

```powershell
Set-Location admin-dashboard
npm test -- --run src/lib/__tests__/route-segments.test.ts
Set-Location ..
git diff --check
```

Expected: utility tests pass and `git diff --check` is silent. Run rider and driver `yarn tsc --noEmit`; report only independently reproduced pre-existing failures and fix any error in changed files before continuing.

- [ ] **Step 4: Rebuild Graphify**

```powershell
python -m graphify update .
```

Expected: rebuild completes. If tracked graph artifacts change, commit them as one graph-maintenance commit after inspecting the diff.

- [ ] **Step 5: Confirm repository state**

```powershell
git status --short
git log --oneline -12
git rev-list --left-right --count origin/main...HEAD
```

Expected: no uncommitted implementation changes. Do not push until explicitly authorized by the user.
