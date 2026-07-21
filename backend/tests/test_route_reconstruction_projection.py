"""Pure observed-section projection contracts."""

from datetime import datetime, timedelta, timezone

from backend.utils.route_reconstruction_projection import project_observed_sections
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


def _segmented():
    return segment_route(
        [_point(0, 0, 50.45, -104.62), _point(10, 1, 50.451, -104.621)],
        {
            "ride_started_at": BASE_TIME.isoformat(),
            "ride_completed_at": (BASE_TIME + timedelta(seconds=10)).isoformat(),
        },
        {"lat": 50.451, "lng": -104.621},
    )


def test_projects_matched_geometry_with_observed_provenance_and_distance():
    sections = project_observed_sections(
        _segmented(),
        {
            "segments": [
                {
                    "segment_index": 0,
                    "matched_segments": [
                        {
                            "chunk_index": 0,
                            "provider": "osrm_match",
                            "distance_km": 0.17,
                            "polyline": [[50.45, -104.62], [50.451, -104.621]],
                        }
                    ],
                }
            ],
            "failures": [],
        },
    )

    assert sections == [
        {
            "id": "observed-0-0",
            "source_segment_index": 0,
            "chunk_index": 0,
            "provider": "osrm_match",
            "geometry_kind": "observed",
            "gap_reason": None,
            "distance_km": 0.17,
            "coordinates": [[50.45, -104.62], [50.451, -104.621]],
        }
    ]


def test_matching_failure_falls_back_to_observed_geometry_without_joining():
    sections = project_observed_sections(
        _segmented(),
        {
            "segments": [{"segment_index": 0, "matched_segments": []}],
            "failures": [{"segment_index": 0, "reason": "provider_unavailable"}],
        },
    )

    assert len(sections) == 1
    assert sections[0]["provider"] == "observed_fallback"
    assert sections[0]["geometry_kind"] == "observed"
    assert sections[0]["coordinates"] == [[50.45, -104.62], [50.451, -104.621]]
    assert sections[0]["distance_km"] > 0
