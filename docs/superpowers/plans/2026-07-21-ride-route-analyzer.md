# Ride Route Analyzer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only CLI that proves which GPS samples belong to each ride phase and produces a strict Phase 3 distance and optional OSRM-backed route artifact.

**Architecture:** A pure analyzer derives phases from lifecycle timestamps and returns a sanitized report plus internal accepted points. The CLI owns live/JSON input and local output; an asynchronous projection step reuses the existing segmentation, OSRM matching, and bounded gap-reconstruction helpers without invoking the write-oriented finalizer.

**Tech Stack:** Python 3.12, existing FastAPI backend utilities, pytest/AnyIO, existing Supabase and OSRM helpers.

## Global Constraints

- The implementation is limited to `backend/utils/ride_route_analyzer.py`, `backend/scripts/analyze_ride_route.py`, and `backend/tests/test_ride_route_analyzer.py`.
- The command is read-only: no insert, update, delete, RPC, settlement, or finalization calls.
- Phase 1 is before request; Phase 2 is request inclusive to start exclusive; Phase 3 is start through completion inclusive; later points are excluded.
- Only Phase 3 contributes to passenger-trip distance and route geometry.
- Client `tracking_phase` is comparison evidence, never phase authority.
- Raw coordinates and addresses must never appear in console JSON or logs.
- Route GeoJSON is optional, local, uniformly marked as the actual route, and never joins an unresolved gap with a straight line.
- Fare and booked distance are contextual only and are never recalculated.

---

### Task 1: Timestamp-authoritative evidence analyzer

**Files:**
- Create: `backend/utils/ride_route_analyzer.py`
- Create: `backend/tests/test_ride_route_analyzer.py`

**Interfaces:**
- Consumes: ride dictionaries and `driver_location_history`-shaped dictionaries; `parse_iso_utc`, `segment_route`, and `calculate_distance`.
- Produces: `analyze_ride_evidence(ride: dict, locations: Iterable[dict]) -> RideRouteAnalysis`, where `RideRouteAnalysis.report` is safe to serialize and `phase_points` remains internal coordinate-bearing evidence.

- [ ] **Step 1: Write failing timestamp-boundary and contamination tests**

```python
from datetime import datetime, timedelta, timezone

import pytest

from backend.utils.ride_route_analyzer import analyze_ride_evidence


BASE = datetime(2026, 7, 21, 17, 0, tzinfo=timezone.utc)


def _ride():
    return {
        "id": "ride-test",
        "ride_requested_at": BASE.isoformat(),
        "ride_started_at": (BASE + timedelta(seconds=60)).isoformat(),
        "ride_completed_at": (BASE + timedelta(seconds=180)).isoformat(),
        "pickup_lat": 50.45,
        "pickup_lng": -104.61,
        "dropoff_lat": 50.45,
        "dropoff_lng": -104.53,
        "planned_distance_km": 6.1,
        "actual_distance_km": 21.3,
    }


def _point(seconds, lng, sequence, stored_phase="trip_in_progress"):
    return {
        "captured_at": (BASE + timedelta(seconds=seconds)).isoformat(),
        "recording_session_id": "session-1",
        "sequence_number": sequence,
        "lat": 50.45,
        "lng": lng,
        "tracking_phase": stored_phase,
        "accuracy": 8,
    }


def test_lifecycle_timestamps_are_the_phase_authority():
    points = [
        _point(-1, -104.620, 1),
        _point(0, -104.610, 2),
        _point(59, -104.610, 3),
        _point(60, -104.610, 4),
        _point(180, -104.530, 5),
        _point(181, -104.520, 6),
    ]

    analysis = analyze_ride_evidence(_ride(), points)

    assert analysis.report["phases"]["phase_1"]["point_count"] == 1
    assert analysis.report["phases"]["phase_2"]["point_count"] == 2
    assert analysis.report["phases"]["phase_3"]["point_count"] == 2
    assert analysis.report["excluded_after_completion_count"] == 1
    assert analysis.report["phases"]["phase_2"]["observed_distance_km"] == 0
    assert analysis.report["phases"]["phase_3"]["observed_distance_km"] > 5
    assert analysis.report["stored_phase_disagreement_count"] >= 3


def test_no_distance_segment_crosses_a_phase_boundary():
    analysis = analyze_ride_evidence(
        _ride(),
        [_point(59, -104.70, 1), _point(60, -104.61, 2), _point(180, -104.53, 3)],
    )
    assert analysis.report["phases"]["phase_2"]["observed_distance_km"] == 0
    assert analysis.report["phases"]["phase_3"]["observed_distance_km"] > 5


def test_invalid_lifecycle_fails_loudly():
    ride = _ride()
    ride["ride_completed_at"] = ride["ride_started_at"]
    with pytest.raises(ValueError, match="completion must be after start"):
        analyze_ride_evidence(ride, [])
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `cd backend && pytest tests/test_ride_route_analyzer.py -q`

Expected: collection fails with `ModuleNotFoundError: backend.utils.ride_route_analyzer`.

- [ ] **Step 3: Implement the minimal pure analyzer**

```python
@dataclass(frozen=True)
class RideRouteAnalysis:
    report: dict[str, Any]
    phase_points: dict[str, tuple[dict[str, Any], ...]]
    segmented_phase_3: SegmentedRoute


def _boundaries(ride: dict[str, Any]) -> tuple[datetime, datetime, datetime]:
    requested = parse_iso_utc(ride.get("ride_requested_at"))
    started = parse_iso_utc(ride.get("ride_started_at"))
    completed = parse_iso_utc(ride.get("ride_completed_at"))
    if requested is None or started is None or completed is None:
        raise ValueError("ride request, start, and completion timestamps are required")
    if started < requested:
        raise ValueError("ride start must not precede request")
    if completed <= started:
        raise ValueError("ride completion must be after start")
    return requested, started, completed


def _phase_for(captured_at, requested, started, completed):
    if captured_at < requested:
        return "phase_1"
    if captured_at < started:
        return "phase_2"
    if captured_at <= completed:
        return "phase_3"
    return "after_completion"


def _observed_distance(segmented: SegmentedRoute) -> float:
    total = 0.0
    for segment in segmented.observed_segments:
        for left, right in zip(segment.points, segment.points[1:]):
            total += calculate_distance(left["lat"], left["lng"], right["lat"], right["lng"])
    return round(total, 3)


def analyze_ride_evidence(ride, locations):
    requested, started, completed = _boundaries(ride)
    buckets = {"phase_1": [], "phase_2": [], "phase_3": []}
    excluded_after = invalid_time = disagreements = 0
    stored_names = {
        "phase_1": {"online_idle", "available"},
        "phase_2": {"navigating_to_pickup"},
        "phase_3": {"trip_in_progress"},
    }
    for point in locations:
        captured = parse_iso_utc(point.get("captured_at") or point.get("timestamp")) if isinstance(point, dict) else None
        if captured is None:
            invalid_time += 1
            continue
        phase = _phase_for(captured, requested, started, completed)
        if phase == "after_completion":
            excluded_after += 1
            continue
        buckets[phase].append(point)
        if point.get("tracking_phase") not in stored_names[phase]:
            disagreements += 1

    completion_point = {
        "lat": ride.get("dropoff_lat"),
        "lng": ride.get("dropoff_lng"),
        "captured_at": completed.isoformat(),
        "recording_session_id": "diagnostic-anchor",
        "sequence_number": 1,
        "accuracy": 0,
    }
    segmented = {
        name: segment_route(points, ride, completion_point if name == "phase_3" else None)
        for name, points in buckets.items()
    }
    accepted = {
        name: tuple(point for section in value.observed_segments for point in section.points)
        for name, value in segmented.items()
    }
    phase_report = {
        name: {
            "point_count": value.quality.point_count,
            "segment_count": value.quality.segment_count,
            "rejected_point_count": value.quality.rejected_point_count,
            "observed_distance_km": _observed_distance(value),
            "max_gap_seconds": value.quality.max_gap_seconds,
            "duration_seconds": (
                int((started - requested).total_seconds()) if name == "phase_2"
                else int((completed - started).total_seconds()) if name == "phase_3"
                else None
            ),
        }
        for name, value in segmented.items()
    }
    strict = phase_report["phase_3"]["observed_distance_km"]
    stored = float(ride.get("actual_distance_km") or 0)
    ratio = round(stored / strict, 3) if strict > 0 else None
    report = {
        "ride_id": str(ride.get("id") or ""),
        "lifecycle": {
            "requested_at": requested.isoformat(),
            "started_at": started.isoformat(),
            "completed_at": completed.isoformat(),
        },
        "phases": phase_report,
        "excluded_after_completion_count": excluded_after,
        "invalid_capture_time_count": invalid_time,
        "stored_phase_disagreement_count": disagreements,
        "stored_actual_distance_km": stored,
        "strict_phase_3_observed_km": strict,
        "contamination_delta_km": round(stored - strict, 3),
        "contamination_ratio": ratio,
        "diagnosis": (
            "likely_phase_contamination" if ratio is not None and ratio >= 1.25
            else "insufficient_phase_3_evidence" if strict == 0
            else "distance_consistent_with_phase_3"
        ),
    }
    return RideRouteAnalysis(report, accepted, segmented["phase_3"])
```

- [ ] **Step 4: Run tests and verify GREEN**

Run: `cd backend && pytest tests/test_ride_route_analyzer.py tests/test_route_segments.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add backend/utils/ride_route_analyzer.py backend/tests/test_ride_route_analyzer.py
git commit -m "feat(routes): analyze ride GPS by lifecycle timestamps"
```

### Task 2: Read-only CLI and OSRM-backed Phase 3 artifact

**Files:**
- Modify: `backend/utils/ride_route_analyzer.py`
- Create: `backend/scripts/analyze_ride_route.py`
- Modify: `backend/tests/test_ride_route_analyzer.py`

**Interfaces:**
- Consumes: `RideRouteAnalysis`, `db_supabase.get_rows`, `compute_segmented_road_route`, and `reconstruct_completed_route`.
- Produces: `project_phase_3_route(analysis, ride) -> RouteProjection`; CLI flags `--ride-id`, `--ride-json`, `--locations-json`, `--route-output`, and `--overwrite`.

- [ ] **Step 1: Add failing projection, privacy, and offline CLI tests**

```python
@pytest.mark.anyio
async def test_projection_contains_only_phase_3_and_one_actual_route_feature(monkeypatch):
    analysis = analyze_ride_evidence(_ride(), [_point(0, -104.61, 1), _point(60, -104.61, 2), _point(180, -104.53, 3)])

    async def matched(_segments):
        return {"segments": [], "distance_km": 6.2, "provider": "osrm_match", "failures": []}

    async def reconstructed(*_args):
        return {
            "segments": [{"coordinates": [[50.45, -104.61], [50.45, -104.53]]}],
            "distance_km": 6.2,
            "observed_distance_km": 6.0,
            "inferred_distance_km": 0.2,
            "observed_distance_ratio": 0.968,
            "inferred_distance_ratio": 0.032,
            "inferred_gap_count": 1,
            "endpoint_start_verified": True,
            "endpoint_end_verified": True,
            "failed_gaps": [],
        }

    monkeypatch.setattr("backend.utils.ride_route_analyzer.compute_segmented_road_route", matched)
    monkeypatch.setattr("backend.utils.ride_route_analyzer.reconstruct_completed_route", reconstructed)
    projection = await project_phase_3_route(analysis, _ride())
    assert projection.report["osrm_distance_km"] == 6.2
    assert projection.geojson["features"][0]["properties"]["route_kind"] == "actual"
    assert projection.geojson["features"][0]["geometry"]["type"] == "MultiLineString"


def test_sanitized_report_contains_no_coordinates_or_addresses():
    report = analyze_ride_evidence(_ride(), [_point(60, -104.61, 1), _point(180, -104.53, 2)]).report
    encoded = json.dumps(report)
    assert "pickup_lat" not in encoded
    assert "dropoff_lat" not in encoded
    assert "-104." not in encoded


def test_cli_offline_json_prints_sanitized_report(tmp_path, capsys):
    ride_path = tmp_path / "ride.json"
    points_path = tmp_path / "points.json"
    ride_path.write_text(json.dumps(_ride()))
    points_path.write_text(json.dumps([_point(60, -104.61, 1), _point(180, -104.53, 2)]))
    exit_code = main(["--ride-json", str(ride_path), "--locations-json", str(points_path), "--no-osrm"])
    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"strict_phase_3_observed_km"' in output
    assert "-104." not in output
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `cd backend && pytest tests/test_ride_route_analyzer.py -q`

Expected: imports fail because `project_phase_3_route` and the CLI module do not exist.

- [ ] **Step 3: Add provider projection and uniform GeoJSON**

```python
@dataclass(frozen=True)
class RouteProjection:
    report: dict[str, Any]
    geojson: dict[str, Any] | None


async def project_phase_3_route(analysis: RideRouteAnalysis, ride: dict[str, Any]) -> RouteProjection:
    matched = await compute_segmented_road_route(list(analysis.segmented_phase_3.observed_segments))
    pickup = {"lat": ride.get("pickup_lat"), "lng": ride.get("pickup_lng")}
    completion = {"lat": ride.get("dropoff_lat"), "lng": ride.get("dropoff_lng")}
    reconstructed = await reconstruct_completed_route(
        analysis.segmented_phase_3, matched, pickup, completion
    )
    lines = []
    for section in reconstructed.get("segments") or []:
        coordinates = section.get("coordinates") or []
        if len(coordinates) >= 2:
            lines.append([[float(lng), float(lat)] for lat, lng in coordinates])
    complete = not reconstructed.get("failed_gaps") and bool(lines)
    provider_report = {
        "osrm_status": "complete" if complete else "incomplete",
        "osrm_distance_km": reconstructed.get("distance_km"),
        "observed_distance_km": reconstructed.get("observed_distance_km"),
        "inferred_distance_km": reconstructed.get("inferred_distance_km"),
        "observed_distance_ratio": reconstructed.get("observed_distance_ratio"),
        "inferred_distance_ratio": reconstructed.get("inferred_distance_ratio"),
        "unresolved_gap_count": len(reconstructed.get("failed_gaps") or []),
    }
    geojson = None if not lines else {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"route_kind": "actual", "style": "uniform-solid"},
            "geometry": {"type": "MultiLineString", "coordinates": lines},
        }],
    }
    return RouteProjection(provider_report, geojson)
```

- [ ] **Step 4: Implement the CLI with explicit read-only loaders**

```python
async def _load_live(ride_id: str) -> tuple[dict, list[dict]]:
    rides = await db_supabase.get_rows("rides", {"id": ride_id}, limit=1)
    if not rides:
        raise ValueError("ride not found")
    points = []
    offset = 0
    while True:
        page = await db_supabase.get_rows(
            "driver_location_history", {"ride_id": ride_id},
            order="captured_at", limit=1000, offset=offset,
        )
        points.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    return rides[0], points


async def _run(args):
    if args.ride_id:
        ride, points = await _load_live(args.ride_id)
    else:
        ride = json.loads(Path(args.ride_json).read_text(encoding="utf-8"))
        points = json.loads(Path(args.locations_json).read_text(encoding="utf-8"))
    analysis = analyze_ride_evidence(ride, points)
    report = dict(analysis.report)
    projection = None if args.no_osrm else await project_phase_3_route(analysis, ride)
    if projection:
        report["route_projection"] = projection.report
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.route_output:
        if not projection or not projection.geojson:
            raise RuntimeError("no drawable Phase 3 route is available")
        output = Path(args.route_output)
        if output.exists() and not args.overwrite:
            raise FileExistsError(f"refusing to overwrite {output}")
        output.write_text(json.dumps(projection.geojson, indent=2), encoding="utf-8")
        print("Privacy warning: route output contains precise GPS coordinates.", file=sys.stderr)
```

The parser must require exactly one input mode, require `--locations-json` with
`--ride-json`, return exit code 2 for input errors, and return exit code 1 for
database/provider failures. Exception messages must not interpolate input rows
or raw coordinates.

- [ ] **Step 5: Run focused and neighboring route tests**

Run: `cd backend && pytest tests/test_ride_route_analyzer.py tests/test_route_segments.py tests/test_route_reconstruction.py tests/test_route_distance_osrm.py -q`

Expected: all tests pass with no warnings caused by the analyzer.

- [ ] **Step 6: Exercise the offline CLI with synthetic evidence**

Run: `cd backend && pytest tests/test_ride_route_analyzer.py::test_cli_offline_json_prints_sanitized_report -q`

Expected: the test passes after invoking the real CLI with temporary JSON files;
captured JSON includes Phase 2 and Phase 3 metrics and no latitude/longitude
values.

- [ ] **Step 7: Commit Task 2**

```bash
git add backend/utils/ride_route_analyzer.py backend/scripts/analyze_ride_route.py backend/tests/test_ride_route_analyzer.py
git commit -m "feat(routes): add read-only ride route diagnostic CLI"
```

### Task 3: Repository verification and graph refresh

**Files:**
- Modify generated tracked graph outputs only if graphify reports changes.

**Interfaces:**
- Consumes: completed analyzer and CLI commits.
- Produces: verified test results and an up-to-date graphify report.

- [ ] **Step 1: Run the complete relevant backend test slice**

Run: `cd backend && pytest tests/test_ride_route_analyzer.py tests/test_trip_distance.py tests/test_route_segments.py tests/test_route_reconstruction.py tests/test_route_finalizer.py -q`

Expected: all tests pass.

- [ ] **Step 2: Run lint on the three implementation files**

Run: `cd backend && ruff check utils/ride_route_analyzer.py scripts/analyze_ride_route.py tests/test_ride_route_analyzer.py`

Expected: `All checks passed!`

- [ ] **Step 3: Refresh graphify after code changes**

Run from repository root: `python -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"`

Expected: graph outputs rebuild successfully. If tracked graph outputs change,
commit them separately with `chore(graphify): refresh route analyzer graph`.

- [ ] **Step 4: Confirm final worktree state**

Run: `git status --short && git log -4 --oneline`

Expected: no uncommitted implementation files; the design, plan, analyzer, and
CLI commits are visible.
