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
            _point(0, 0, 50.4510, -104.6210),
            _point(10, 1, 50.4520, -104.6220),
            _point(110, 2, 50.4550, -104.6250),
            _point(120, 3, 50.4560, -104.6260),
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
    # First→last span still covers the whole trip window...
    assert segmented.quality.span_coverage_ratio == 1.0
    # ...but coverage_ratio is now gap-aware: the deliberate internal gap in this
    # fixture must pull it below 1.0 (that's the signal that hid the incident's
    # missing middle when coverage was span-only).
    assert segmented.quality.coverage_ratio < 1.0
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
    # All three connectors were routed via OSRM (road-following), so the routed
    # split carries the whole inferred distance and none is blind straight-line.
    assert result["routed_connector_distance_km"] == 1.15
    assert result["straight_connector_distance_km"] == 0.0
    assert (
        result["routed_connector_distance_km"] + result["straight_connector_distance_km"]
        == result["inferred_distance_km"]
    )
    assert result["distance_km"] == 1.55
    assert result["observed_distance_ratio"] == pytest.approx(0.258, abs=0.001)
    assert result["inferred_distance_ratio"] == pytest.approx(0.742, abs=0.001)
    assert result["inferred_gap_count"] == 3
    assert result["endpoint_start_verified"] is True
    assert result["endpoint_end_verified"] is True
    assert result["failed_gaps"] == []
    assert gap_route.await_count == 3
    # The whole-segment capture-timestamp fields used internally to time-gate
    # connectors are stripped before segments reach the public output — they
    # aren't part of the persisted/rendered segment shape.
    for section in result["segments"]:
        assert "segment_start_captured_at" not in section
        assert "segment_end_captured_at" not in section


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
async def test_internal_gap_farther_than_the_distance_cap_is_left_unbridged(monkeypatch):
    # Two observed segments ~15 km apart (over MAX_INFERRED_CONNECTOR_KM=10) —
    # not a believable substitute for missing GPS, routed or straight-line.
    completion = {"lat": 50.60, "lng": -104.62, "accuracy": 8}
    segmented = segment_route(
        [
            _point(0, 0, 50.4510, -104.6210),
            _point(10, 1, 50.4520, -104.6220),
            _point(20, 2, 50.60, -104.6220),  # ~16.6 km jump -> new segment
            _point(30, 3, 50.601, -104.6221),
        ],
        _lifecycle(),
        completion,
    )
    matched = {"segments": [], "failures": []}
    monkeypatch.setattr(reconstruction, "get_app_settings", AsyncMock(return_value={}))
    monkeypatch.setattr(reconstruction.settings, "OSRM_URL", "")
    gap_route = AsyncMock()
    monkeypatch.setattr(reconstruction, "compute_gap_route_via_osrm", gap_route)
    monkeypatch.setattr(reconstruction, "compute_gap_route_via_google", gap_route)

    result = await reconstruction.reconstruct_completed_route(
        segmented,
        matched,
        {"lat": 50.4500, "lng": -104.6200},
        completion,
    )

    assert "internal_gap_exceeds_distance_cap" in result["failed_gaps"]
    # No fabricated connector was inserted for the refused gap — only the two
    # observed sections plus (possibly) the plausible start/tail connectors.
    inferred_reasons = [s["gap_reason"] for s in result["segments"] if s["geometry_kind"] == "inferred"]
    assert "internal_gap" not in inferred_reasons


@pytest.mark.asyncio
async def test_internal_gap_longer_than_the_time_cap_is_left_unbridged(monkeypatch):
    # Two observed segments only ~500 m apart (well under the distance cap)
    # but separated by a 400s outage (over MAX_INFERRED_GAP_SECONDS=300) — an
    # outage too long to trust a routed or straight-line guess across.
    completion = {"lat": 50.4560, "lng": -104.6260, "accuracy": 8}
    segmented = segment_route(
        [
            _point(0, 0, 50.4510, -104.6210),
            _point(10, 1, 50.4520, -104.6220),
            _point(410, 2, 50.4555, -104.6255),  # 400s gap from the prior point
            _point(420, 3, 50.4560, -104.6260),
        ],
        _lifecycle(),
        completion,
    )
    matched = {"segments": [], "failures": []}
    monkeypatch.setattr(reconstruction, "get_app_settings", AsyncMock(return_value={}))
    monkeypatch.setattr(reconstruction.settings, "OSRM_URL", "")
    gap_route = AsyncMock()
    monkeypatch.setattr(reconstruction, "compute_gap_route_via_osrm", gap_route)
    monkeypatch.setattr(reconstruction, "compute_gap_route_via_google", gap_route)

    result = await reconstruction.reconstruct_completed_route(
        segmented,
        matched,
        {"lat": 50.4500, "lng": -104.6200},
        completion,
    )

    assert "internal_gap_exceeds_time_cap" in result["failed_gaps"]
    inferred_reasons = [s["gap_reason"] for s in result["segments"] if s["geometry_kind"] == "inferred"]
    assert "internal_gap" not in inferred_reasons


@pytest.mark.asyncio
async def test_unconfigured_osrm_fills_gaps_with_straight_lines_without_network(monkeypatch):
    # With neither OSRM nor a Google key configured, reconstruction must never
    # reach out to a network provider (no silent public-OSRM fallback). The
    # 4-tier fill still succeeds by dropping to haversine straight-line
    # connectors, so gaps are DISCLOSED as straight-connector distance rather
    # than "failed" — and the measured-distance resolver excludes them.
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

    # No network provider was contacted.
    snap.assert_not_awaited()
    gap_route.assert_not_awaited()
    # The 4-tier fill always succeeds; gaps are straight-line, not failures.
    assert result["failed_gaps"] == []
    assert result["straight_connector_distance_km"] > 0
    assert result["routed_connector_distance_km"] == 0
    # Anchors still resolve from the raw pickup/completion coordinates.
    assert result["endpoint_start_verified"] is True
    assert result["endpoint_end_verified"] is True


def _tight_gap_evidence():
    """Two due-north observed segments split by a ~389 m / 20 s hole.

    Short enough to clear both plausibility caps (MAX_INFERRED_CONNECTOR_KM,
    MAX_INFERRED_GAP_SECONDS) so the gap genuinely reaches the routed tier —
    which is the only place the detour in ride 0c24901f could be produced.
    """
    completion = {"lat": 50.4565, "lng": -104.6210, "accuracy": 8}
    segmented = segment_route(
        [
            _point(0, 0, 50.4510, -104.6210),
            _point(10, 1, 50.4520, -104.6210),
            # 389 m from the previous fix: splits on displacement, not time, so
            # the gap keeps a short real elapsed interval on both sides.
            _point(30, 2, 50.4555, -104.6210),
            _point(40, 3, 50.4565, -104.6210),
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
                        "distance_km": 0.111,
                        "polyline": [[50.4510, -104.6210], [50.4520, -104.6210]],
                    }
                ],
            },
            {
                "segment_index": 1,
                "matched_segments": [
                    {
                        "provider": "osrm_match",
                        "distance_km": 0.111,
                        "polyline": [[50.4555, -104.6210], [50.4565, -104.6210]],
                    }
                ],
            },
        ],
        "failures": [],
    }
    return segmented, matched, completion


def _patch_providers(monkeypatch, gap_route):
    monkeypatch.setattr(
        reconstruction,
        "get_app_settings",
        AsyncMock(return_value={"osrm_url": "http://osrm:5000"}),
    )
    # Anchors resolve to the raw pickup/completion coordinates, which coincide
    # with the observed ends — so no start/tail connector is attempted and the
    # internal gap is the only routed call.
    monkeypatch.setattr(reconstruction, "snap_endpoint_via_osrm", AsyncMock(return_value=None))
    monkeypatch.setattr(reconstruction, "compute_gap_route_via_osrm", gap_route)


@pytest.mark.asyncio
async def test_refuses_routed_connector_nobody_could_have_driven(monkeypatch):
    """A 2.33 km road answer across a 389 m / 20 s hole implies ~419 km/h.

    Regression for ride 0c24901f: the router bridged a short hole spanning a
    divided road by driving to the next legal turnaround and back, adding ~2 km
    of phantom distance to a finalized trip (8.96 km published against a 6.99 km
    booking — 1.28x, just under resolve_measured_distance_km's 1.3x ceiling, so
    nothing downstream caught it).
    """
    segmented, matched, completion = _tight_gap_evidence()
    gap_route = AsyncMock(return_value=(2.33, [[50.4520, -104.6210], [50.4530, -104.6180], [50.4555, -104.6210]]))
    _patch_providers(monkeypatch, gap_route)

    result = await reconstruction.reconstruct_completed_route(
        segmented, matched, {"lat": 50.4510, "lng": -104.6210}, completion
    )

    assert result["failed_gaps"] == ["internal_gap_implausible_detour"]
    # Refused outright — not swapped for a straight line, which would still read
    # as real evidence on the audit map even though its km is excluded.
    assert result["routed_connector_distance_km"] == 0.0
    assert result["straight_connector_distance_km"] == 0.0
    assert result["inferred_gap_count"] == 0
    assert result["distance_km"] == result["observed_distance_km"]
    assert all(section["geometry_kind"] == "observed" for section in result["segments"])


@pytest.mark.asyncio
async def test_keeps_routed_connector_the_driver_could_have_driven(monkeypatch):
    """Control for the refusal above: 0.42 km over the same 20 s is ~76 km/h."""
    segmented, matched, completion = _tight_gap_evidence()
    gap_route = AsyncMock(return_value=(0.42, [[50.4520, -104.6210], [50.4555, -104.6210]]))
    _patch_providers(monkeypatch, gap_route)

    result = await reconstruction.reconstruct_completed_route(
        segmented, matched, {"lat": 50.4510, "lng": -104.6210}, completion
    )

    assert result["failed_gaps"] == []
    assert result["routed_connector_distance_km"] == 0.42
    assert result["inferred_gap_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("routed_km", "refused"),
    [
        (round(reconstruction.MAX_CONNECTOR_IMPLIED_SPEED_KPH * 20 / 3600, 3), False),
        (round((reconstruction.MAX_CONNECTOR_IMPLIED_SPEED_KPH + 1) * 20 / 3600, 3), True),
    ],
)
async def test_implied_speed_cap_boundary(monkeypatch, routed_km, refused):
    """At the cap is kept; one km/h over it is refused."""
    segmented, matched, completion = _tight_gap_evidence()
    gap_route = AsyncMock(return_value=(routed_km, [[50.4520, -104.6210], [50.4555, -104.6210]]))
    _patch_providers(monkeypatch, gap_route)

    result = await reconstruction.reconstruct_completed_route(
        segmented, matched, {"lat": 50.4510, "lng": -104.6210}, completion
    )

    assert bool(result["failed_gaps"]) is refused


@pytest.mark.asyncio
async def test_gap_router_is_given_observed_headings_and_the_strict_slack(monkeypatch):
    """The gap is routed along the roads actually travelled, not the fastest path.

    Without a heading on each side, a router is free to answer a gap spanning a
    divided road with a drive to the next turnaround and back. Both fixture
    segments run due north, so both bearings must come back as 0 degrees.
    """
    segmented, matched, completion = _tight_gap_evidence()
    gap_route = AsyncMock(return_value=(0.42, [[50.4520, -104.6210], [50.4555, -104.6210]]))
    _patch_providers(monkeypatch, gap_route)

    await reconstruction.reconstruct_completed_route(segmented, matched, {"lat": 50.4510, "lng": -104.6210}, completion)

    assert gap_route.await_count == 1
    kwargs = gap_route.await_args.kwargs
    assert kwargs["start_bearing"] == pytest.approx(0.0, abs=0.5)
    assert kwargs["end_bearing"] == pytest.approx(0.0, abs=0.5)
    # A finalized route asks for the strict slack, not route_distance's
    # live-trail default, because this distance is audited and billed.
    assert kwargs["max_extra_km"] == reconstruction.GAP_MAX_EXTRA_KM == 0.5


@pytest.mark.asyncio
async def test_missing_start_and_tail_connectors_are_not_speed_gated(monkeypatch):
    """Anchor connectors carry no device clock, so the speed gate cannot apply.

    Only an internal gap has a real captured_at on both sides. A start/tail
    connector is bounded by the distance cap alone — gating it on a speed
    derived from a timestamp it does not have would refuse valid geometry.
    """
    segmented, matched, completion = _tight_gap_evidence()
    gap_route = AsyncMock(return_value=(0.42, [[50.4520, -104.6210], [50.4555, -104.6210]]))
    monkeypatch.setattr(reconstruction, "get_app_settings", AsyncMock(return_value={"osrm_url": "http://osrm:5000"}))
    # Anchors far from the observed ends force real start/tail connectors.
    monkeypatch.setattr(reconstruction, "snap_endpoint_via_osrm", AsyncMock(return_value=None))
    monkeypatch.setattr(reconstruction, "compute_gap_route_via_osrm", gap_route)

    result = await reconstruction.reconstruct_completed_route(
        segmented, matched, {"lat": 50.4400, "lng": -104.6210}, {"lat": 50.4700, "lng": -104.6210}
    )

    reasons = [section["gap_reason"] for section in result["segments"] if section["geometry_kind"] == "inferred"]
    assert "missing_start" in reasons and "missing_tail" in reasons
    # The start anchor has no preceding geometry, so no heading is asserted for it.
    assert gap_route.await_args_list[0].kwargs["start_bearing"] is None
    assert gap_route.await_args_list[0].kwargs["end_bearing"] is not None
