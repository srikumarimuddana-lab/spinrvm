"""Contract tests for timestamp-authoritative ride route analysis."""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

try:
    import backend.utils.ride_route_analyzer as analyzer_module
    from backend.scripts.analyze_ride_route import main
    from backend.utils.ride_route_analyzer import analyze_ride_evidence, project_phase_3_route
except ImportError:
    import utils.ride_route_analyzer as analyzer_module  # type: ignore
    from scripts.analyze_ride_route import main  # type: ignore
    from utils.ride_route_analyzer import analyze_ride_evidence, project_phase_3_route  # type: ignore


BASE = datetime(2026, 7, 21, 17, 0, tzinfo=timezone.utc)


def _ride() -> dict:
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


def _point(
    seconds: int,
    lng: float,
    sequence: int,
    stored_phase: str = "trip_in_progress",
) -> dict:
    return {
        "captured_at": (BASE + timedelta(seconds=seconds)).isoformat(),
        "recording_session_id": "session-1",
        "sequence_number": sequence,
        "lat": 50.45,
        "lng": lng,
        "tracking_phase": stored_phase,
        "accuracy": 8,
    }


def _phase_3_trace(*, first_sequence: int = 10) -> list[dict]:
    """A 120-second eastbound trace with plausible four-second spacing."""
    points = []
    for index, seconds in enumerate(range(60, 181, 4)):
        progress = (seconds - 60) / 120
        points.append(_point(seconds, -104.61 + 0.08 * progress, first_sequence + index))
    return points


def test_lifecycle_timestamps_are_the_phase_authority():
    points = [
        _point(-1, -104.62, 1),
        _point(0, -104.61, 2),
        _point(59, -104.61, 3),
        *_phase_3_trace(),
        _point(181, -104.52, 50),
    ]

    analysis = analyze_ride_evidence(_ride(), points)

    assert analysis.report["phases"]["phase_1"]["point_count"] == 1
    assert analysis.report["phases"]["phase_2"]["point_count"] == 2
    assert analysis.report["phases"]["phase_3"]["point_count"] == 31
    assert analysis.report["excluded_after_completion_count"] == 1
    assert analysis.report["phases"]["phase_2"]["observed_distance_km"] == 0
    assert analysis.report["phases"]["phase_3"]["observed_distance_km"] > 5
    assert analysis.report["phases"]["phase_2"]["duration_seconds"] == 60
    assert analysis.report["phases"]["phase_3"]["duration_seconds"] == 120
    assert analysis.report["stored_phase_disagreement_count"] >= 3
    assert analysis.report["diagnosis"] == "likely_phase_contamination"


def test_no_distance_segment_crosses_a_phase_boundary():
    analysis = analyze_ride_evidence(
        _ride(),
        [
            _point(59, -104.70, 1, "navigating_to_pickup"),
            *_phase_3_trace(first_sequence=2),
        ],
    )

    assert analysis.report["phases"]["phase_2"]["observed_distance_km"] == 0
    assert analysis.report["phases"]["phase_3"]["observed_distance_km"] > 5


def test_capture_timestamp_wins_over_client_tracking_phase():
    points = [
        _point(0, -104.61, 1, "trip_in_progress"),
        _point(59, -104.61, 2, "online_idle"),
        *_phase_3_trace(first_sequence=3),
    ]

    analysis = analyze_ride_evidence(_ride(), points)

    assert analysis.report["phases"]["phase_2"]["point_count"] == 2
    assert analysis.report["stored_phase_disagreement_count"] == 2


def test_invalid_lifecycle_fails_loudly():
    ride = _ride()
    ride["ride_completed_at"] = ride["ride_started_at"]

    with pytest.raises(ValueError, match="completion must be after start"):
        analyze_ride_evidence(ride, [])


@pytest.mark.anyio
async def test_projection_contains_only_phase_3_and_one_actual_route_feature(monkeypatch):
    analysis = analyze_ride_evidence(
        _ride(),
        [
            _point(0, -104.70, 1, "navigating_to_pickup"),
            *_phase_3_trace(first_sequence=2),
        ],
    )
    matched_point_count = 0

    async def matched(segments):
        nonlocal matched_point_count
        matched_point_count = sum(len(segment.points) for segment in segments)
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

    monkeypatch.setattr(analyzer_module, "_compute_segmented_route", matched)
    monkeypatch.setattr(analyzer_module, "_reconstruct_route", reconstructed)

    projection = await project_phase_3_route(analysis, _ride())

    assert matched_point_count == 31
    assert projection.report["osrm_distance_km"] == 6.2
    assert projection.report["osrm_status"] == "complete"
    feature = projection.geojson["features"][0]
    assert feature["properties"] == {"route_kind": "actual", "style": "uniform-solid"}
    assert feature["geometry"]["type"] == "MultiLineString"


def test_sanitized_report_contains_no_coordinates_or_addresses():
    report = analyze_ride_evidence(_ride(), _phase_3_trace()).report

    encoded = json.dumps(report)

    assert "pickup_lat" not in encoded
    assert "dropoff_lat" not in encoded
    assert "-104." not in encoded
    assert "50.45" not in encoded


def test_cli_offline_json_prints_sanitized_report(tmp_path, capsys):
    ride_path = tmp_path / "ride.json"
    points_path = tmp_path / "points.json"
    ride_path.write_text(json.dumps(_ride()), encoding="utf-8")
    points_path.write_text(json.dumps(_phase_3_trace()), encoding="utf-8")

    exit_code = main(
        [
            "--ride-json",
            str(ride_path),
            "--locations-json",
            str(points_path),
            "--no-osrm",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"strict_phase_3_observed_km"' in captured.out
    assert "-104." not in captured.out
    assert "50.45" not in captured.out
    assert captured.err == ""


def test_cli_requires_locations_json_with_offline_ride(tmp_path, capsys):
    ride_path = tmp_path / "ride.json"
    ride_path.write_text(json.dumps(_ride()), encoding="utf-8")

    exit_code = main(["--ride-json", str(ride_path), "--no-osrm"])

    assert exit_code == 2
    assert "--locations-json is required" in capsys.readouterr().err


def test_module_cli_no_osrm_runs_from_repository_root(tmp_path):
    ride_path = tmp_path / "ride.json"
    points_path = tmp_path / "points.json"
    ride_path.write_text(json.dumps(_ride()), encoding="utf-8")
    points_path.write_text(json.dumps(_phase_3_trace()), encoding="utf-8")
    repository_root = Path(__file__).resolve().parents[2]

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "backend.scripts.analyze_ride_route",
            "--ride-json",
            str(ride_path),
            "--locations-json",
            str(points_path),
            "--no-osrm",
        ],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert '"strict_phase_3_observed_km"' in completed.stdout
    assert "-104." not in completed.stdout


# --- Incident report (P4) ----------------------------------------------------

def _incident_point(seconds, lng, sequence, *, accuracy=8.0, speed=12.0):
    return {
        "captured_at": (BASE + timedelta(seconds=seconds)).isoformat(),
        "recording_session_id": "session-1",
        "sequence_number": sequence,
        "lat": 50.45,
        "lng": lng,
        "tracking_phase": "trip_in_progress",
        "accuracy": accuracy,
        "speed": speed,
    }


def test_build_incident_report_distance_ladder_and_filter_effect():
    from backend.utils.ride_route_analyzer import build_incident_report

    ride = _ride()
    # Phase-3 window is 60..180s. Build: clean eastbound movement, two garbage
    # low-accuracy fixes, and a stationary jitter cluster.
    points = [_incident_point(60 + i * 4, -104.61 + i * 0.001, 10 + i) for i in range(6)]
    points.append(_incident_point(90, -104.20, 30, accuracy=90.0))   # low-accuracy
    points.append(_incident_point(94, -104.90, 31, accuracy=120.0))  # low-accuracy
    base_lng = -104.60
    for k in range(5):  # stationary cluster (speed 0, tight)
        points.append(_incident_point(120 + k * 4, base_lng + k * 0.00003, 40 + k, speed=0.0))

    report = build_incident_report(ride, points, gap_events=[], recomputes=[])

    assert report["incident_report"] is True
    ladder = report["distance_ladder"]
    # All rungs present and populated.
    for key in ("booked_planned_km", "straight_line_pickup_dropoff_km", "raw_gps_phase3_km", "filtered_gps_phase3_km"):
        assert key in ladder
    assert ladder["booked_planned_km"] == 6.1
    assert ladder["straight_line_pickup_dropoff_km"] is not None

    hist = report["accuracy_histogram_phase3"]
    assert hist["over_gate_count"] == 2  # the two >50 m fixes

    effect = report["gps_filter_effect_phase3"]
    assert effect["dropped_low_accuracy"] == 2
    assert effect["collapsed_stationary"] >= 3   # 5-fix cluster → 2
    # Filtering removes the garbage-fix excursions, so filtered < raw.
    assert effect["filtered_km"] < effect["raw_km"]


def test_build_incident_report_includes_recompute_audit_and_gaps():
    from backend.utils.ride_route_analyzer import build_incident_report

    ride = _ride()
    # A trace with a long mid-window silence → a gap in the timeline.
    points = [
        _incident_point(60, -104.61, 10),
        _incident_point(64, -104.60, 11),
        _incident_point(170, -104.54, 12),  # ~106 s gap
        _incident_point(174, -104.53, 13),
    ]
    recomputes = [
        {"route_revision": 3, "previous_actual_distance_km": 2.6, "new_actual_distance_km": 1.8, "trigger": "reconstructed_distance"},
    ]
    report = build_incident_report(ride, points, gap_events=[], recomputes=recomputes)

    gaps = report["gap_timeline_phase3"]
    assert any(g["source"] == "breadcrumb_delta" and g["gap_seconds"] > 100 for g in gaps)
    assert report["distance_recompute_audit"][0]["trigger"] == "reconstructed_distance"
    assert report["distance_recompute_audit"][0]["new_actual_distance_km"] == 1.8


# --- Outlier table (per-fix spike detection) ---------------------------------

def test_build_outlier_table_flags_spikes_and_matches_despike():
    from backend.utils.ride_route_analyzer import build_outlier_table

    # forward, then a stale fix jumping ~1.3 km BACK in 0.3 s, then forward again.
    ordered = [
        {"lat": 50.4100, "lng": -104.6500, "timestamp": (BASE + timedelta(seconds=0)).isoformat()},
        {"lat": 50.4200, "lng": -104.6600, "timestamp": (BASE + timedelta(seconds=0.3)).isoformat()},  # spike
        {"lat": 50.4101, "lng": -104.6499, "timestamp": (BASE + timedelta(seconds=12)).isoformat()},
        {"lat": 50.4102, "lng": -104.6498, "timestamp": (BASE + timedelta(seconds=24)).isoformat()},
    ]
    table = build_outlier_table(ordered)
    assert table["outlier_count"] == 1
    verdicts = [r["verdict"] for r in table["rows"]]
    assert verdicts == ["start", "spike_outlier", "ok", "ok"]
    spike = table["rows"][1]
    assert spike["implied_speed_impossible"] is True
    # coordinate-free: no lat/lng leaks into the table rows
    assert "lat" not in spike and "lng" not in spike


def test_build_outlier_table_clean_trace_has_no_outliers():
    from backend.utils.ride_route_analyzer import build_outlier_table

    ordered = [
        {"lat": 50.4100 + i * 0.0005, "lng": -104.6500, "timestamp": (BASE + timedelta(seconds=i * 5)).isoformat()}
        for i in range(6)
    ]
    table = build_outlier_table(ordered)
    assert table["outlier_count"] == 0
    assert all(r["verdict"] in ("start", "ok") for r in table["rows"])
