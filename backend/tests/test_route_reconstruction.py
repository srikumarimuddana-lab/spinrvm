"""Completed-route reconstruction contracts for ordered OSRM gap filling."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from backend.utils import route_reconstruction as reconstruction
from backend.utils.route_segments import segment_route

BASE_TIME = datetime(2026, 7, 21, 14, 0, tzinfo=timezone.utc)


def _point(seconds: int, sequence: int, lat: float, lng: float) -> dict:
    return {
        "recording_session_id": "session-a",
        "sequence_number": sequence,
        "captured_at": (BASE_TIME + timedelta(seconds=seconds)).isoformat(),
        "lat": lat,
        "lng": lng,
        "accuracy": 8,
    }


def _lifecycle() -> dict:
    return {
        "ride_started_at": BASE_TIME.isoformat(),
        "ride_completed_at": (BASE_TIME + timedelta(seconds=120)).isoformat(),
    }


def _two_segment_evidence():
    completion = {"lat": 50.4600, "lng": -104.6300, "accuracy": 8}
    segmented = segment_route(
        [
            _point(10, 0, 50.4510, -104.6210),
            _point(20, 1, 50.4520, -104.6220),
            _point(90, 2, 50.4550, -104.6250),
            _point(100, 3, 50.4560, -104.6260),
        ],
        _lifecycle(),
        completion,
    )
    matched = {
        "segments": [
            {
                "segment_index": 0,
                "matched_segments": [
                    {
                        "provider": "osrm_match",
                        "distance_km": 0.2,
                        "polyline": [[50.4510, -104.6210], [50.4520, -104.6220]],
                    }
                ],
            },
            {
                "segment_index": 1,
                "matched_segments": [
                    {
                        "provider": "osrm_match",
                        "distance_km": 0.2,
                        "polyline": [[50.4550, -104.6250], [50.4560, -104.6260]],
                    }
                ],
            },
        ],
        "failures": [],
    }
    return segmented, matched, completion


@pytest.mark.asyncio
async def test_reconstructs_missing_start_internal_gap_and_tail_in_order(monkeypatch):
    segmented, matched, completion = _two_segment_evidence()
    monkeypatch.setattr(
        reconstruction,
        "get_app_settings",
        AsyncMock(return_value={"osrm_url": "http://osrm:5000"}),
    )
    monkeypatch.setattr(
        reconstruction,
        "snap_endpoint_via_osrm",
        AsyncMock(side_effect=[[50.4500, -104.6200], [50.4600, -104.6300]]),
    )
    gap_route = AsyncMock(
        side_effect=[
            (0.15, [[50.4500, -104.6200], [50.4510, -104.6210]]),
            (0.45, [[50.4520, -104.6220], [50.4550, -104.6250]]),
            (0.55, [[50.4560, -104.6260], [50.4600, -104.6300]]),
        ]
    )
    monkeypatch.setattr(reconstruction, "compute_gap_route_via_osrm", gap_route)

    result = await reconstruction.reconstruct_completed_route(
        segmented,
        matched,
        {"lat": 50.4500, "lng": -104.6200},
        completion,
    )

    assert [section["geometry_kind"] for section in result["segments"]] == [
        "inferred",
        "observed",
        "inferred",
        "observed",
        "inferred",
    ]
    assert [section["gap_reason"] for section in result["segments"] if section["geometry_kind"] == "inferred"] == [
        "missing_start",
        "internal_gap",
        "missing_tail",
    ]
    assert result["observed_distance_km"] == 0.4
    assert result["inferred_distance_km"] == 1.15
    assert result["distance_km"] == 1.55
    assert result["observed_distance_ratio"] == pytest.approx(0.258, abs=0.001)
    assert result["inferred_distance_ratio"] == pytest.approx(0.742, abs=0.001)
    assert result["inferred_gap_count"] == 3
    assert result["endpoint_start_verified"] is True
    assert result["endpoint_end_verified"] is True
    assert result["failed_gaps"] == []
    assert gap_route.await_count == 3


@pytest.mark.asyncio
async def test_does_not_route_boundaries_already_within_30_metres(monkeypatch):
    completion = {"lat": 50.4502, "lng": -104.6200, "accuracy": 8}
    segmented = segment_route(
        [_point(0, 0, 50.4500, -104.6200), _point(120, 1, 50.4502, -104.6200)],
        _lifecycle(),
        completion,
    )
    matched = {
        "segments": [
            {
                "segment_index": 0,
                "matched_segments": [
                    {
                        "provider": "osrm_match",
                        "distance_km": 0.022,
                        "polyline": [[50.4500, -104.6200], [50.4502, -104.6200]],
                    }
                ],
            }
        ],
        "failures": [],
    }
    monkeypatch.setattr(
        reconstruction,
        "get_app_settings",
        AsyncMock(return_value={"osrm_url": "http://osrm:5000"}),
    )
    monkeypatch.setattr(
        reconstruction,
        "snap_endpoint_via_osrm",
        AsyncMock(side_effect=[[50.4500, -104.6200], [50.4502, -104.6200]]),
    )
    gap_route = AsyncMock()
    monkeypatch.setattr(reconstruction, "compute_gap_route_via_osrm", gap_route)

    result = await reconstruction.reconstruct_completed_route(
        segmented,
        matched,
        {"lat": 50.4500, "lng": -104.6200},
        completion,
    )

    assert result["inferred_gap_count"] == 0
    assert result["observed_distance_ratio"] == 1.0
    assert result["segments"][0]["geometry_kind"] == "observed"
    gap_route.assert_not_awaited()


@pytest.mark.asyncio
async def test_unconfigured_osrm_never_uses_public_fallback_and_reports_gaps(monkeypatch):
    segmented, matched, completion = _two_segment_evidence()
    monkeypatch.setattr(reconstruction, "get_app_settings", AsyncMock(return_value={}))
    monkeypatch.setattr(reconstruction.settings, "OSRM_URL", "")
    snap = AsyncMock()
    gap_route = AsyncMock()
    monkeypatch.setattr(reconstruction, "snap_endpoint_via_osrm", snap)
    monkeypatch.setattr(reconstruction, "compute_gap_route_via_osrm", gap_route)

    result = await reconstruction.reconstruct_completed_route(
        segmented,
        matched,
        {"lat": 50.4500, "lng": -104.6200},
        completion,
    )

    assert result["endpoint_start_verified"] is False
    assert result["endpoint_end_verified"] is False
    assert result["failed_gaps"] == ["missing_start", "internal_gap", "missing_tail"]
    snap.assert_not_awaited()
    gap_route.assert_not_awaited()
