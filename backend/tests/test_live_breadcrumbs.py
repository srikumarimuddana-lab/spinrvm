"""Tests for utils.live_breadcrumbs — gap-filled breadcrumb trail for live map."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.mark.anyio
async def test_get_gap_filled_breadcrumbs_empty():
    """Returns empty list when no GPS points exist."""
    from utils.live_breadcrumbs import get_gap_filled_breadcrumbs

    with patch("utils.live_breadcrumbs.get_rows", return_value=[]):
        result = await get_gap_filled_breadcrumbs("ride123")
        assert result == []


@pytest.mark.anyio
async def test_get_gap_filled_breadcrumbs_insufficient_points():
    """Returns empty list when only one GPS point exists."""
    from utils.live_breadcrumbs import get_gap_filled_breadcrumbs

    mock_rows = [
        {"lat": 50.45, "lng": -104.62, "captured_at": "2024-01-01T10:00:00Z", "tracking_phase": "trip_in_progress"}
    ]

    with patch("utils.live_breadcrumbs.get_rows", return_value=mock_rows):
        result = await get_gap_filled_breadcrumbs("ride123")
        assert result == []


@pytest.mark.anyio
async def test_get_gap_filled_breadcrumbs_small_gap_skipped():
    """Tiny gaps (< 30m, < 30s) are skipped without connector calls."""
    from utils.live_breadcrumbs import get_gap_filled_breadcrumbs

    mock_rows = [
        {"lat": 50.45, "lng": -104.62, "captured_at": "2024-01-01T10:00:00Z", "tracking_phase": "trip_in_progress"},
        {
            "lat": 50.45001,
            "lng": -104.62001,
            "captured_at": "2024-01-01T10:00:10Z",
            "tracking_phase": "trip_in_progress",
        },
        {
            "lat": 50.45002,
            "lng": -104.62002,
            "captured_at": "2024-01-01T10:00:20Z",
            "tracking_phase": "trip_in_progress",
        },
    ]

    with patch("utils.live_breadcrumbs.get_rows", return_value=mock_rows):
        with patch("utils.live_breadcrumbs.compute_gap_route_via_osrm") as mock_osrm:
            with patch("utils.live_breadcrumbs.compute_gap_route_via_google") as mock_google:
                result = await get_gap_filled_breadcrumbs("ride123")

                # Should have 3 points (no gap filling needed)
                assert len(result) == 3
                # No connector calls for tiny gaps
                mock_osrm.assert_not_called()
                mock_google.assert_not_called()


@pytest.mark.anyio
async def test_get_gap_filled_breadcrumbs_large_gap_uses_osrm():
    """Large gaps trigger OSRM /route connector."""
    from utils.live_breadcrumbs import get_gap_filled_breadcrumbs

    # Two points ~500m apart (large gap)
    mock_rows = [
        {"lat": 50.45, "lng": -104.62, "captured_at": "2024-01-01T10:00:00Z", "tracking_phase": "trip_in_progress"},
        {"lat": 50.455, "lng": -104.615, "captured_at": "2024-01-01T10:02:00Z", "tracking_phase": "trip_in_progress"},
    ]

    mock_connector = (0.5, [[50.45, -104.62], [50.451, -104.619], [50.455, -104.615]])

    with patch("utils.live_breadcrumbs.get_rows", return_value=mock_rows):
        with patch("utils.live_breadcrumbs.compute_gap_route_via_osrm", return_value=mock_connector) as mock_osrm:
            with patch("utils.live_breadcrumbs.compute_gap_route_via_google") as mock_google:
                with patch("utils.live_breadcrumbs.get_app_settings", return_value={}):
                    with patch("utils.live_breadcrumbs.settings.OSRM_URL", "http://osrm:5000"):
                        result = await get_gap_filled_breadcrumbs("ride123")

                        # Should have original points + interpolated connector points
                        assert len(result) >= 3
                        mock_osrm.assert_called_once()
                        mock_google.assert_not_called()


@pytest.mark.anyio
async def test_get_gap_filled_breadcrumbs_osrm_fails_uses_google():
    """Falls back to Google Directions when OSRM fails."""
    from utils.live_breadcrumbs import get_gap_filled_breadcrumbs

    mock_rows = [
        {"lat": 50.45, "lng": -104.62, "captured_at": "2024-01-01T10:00:00Z", "tracking_phase": "trip_in_progress"},
        {"lat": 50.455, "lng": -104.615, "captured_at": "2024-01-01T10:02:00Z", "tracking_phase": "trip_in_progress"},
    ]

    mock_connector = (0.5, [[50.45, -104.62], [50.451, -104.619], [50.455, -104.615]])

    with patch("utils.live_breadcrumbs.get_rows", return_value=mock_rows):
        with patch("utils.live_breadcrumbs.compute_gap_route_via_osrm", return_value=None) as mock_osrm:
            with patch(
                "utils.live_breadcrumbs.compute_gap_route_via_google", return_value=mock_connector
            ) as mock_google:
                with patch("utils.live_breadcrumbs.get_app_settings", return_value={"google_maps_api_key": "test"}):
                    with patch("utils.live_breadcrumbs.settings.OSRM_URL", "http://osrm:5000"):
                        result = await get_gap_filled_breadcrumbs("ride123")

                        assert len(result) >= 3
                        mock_osrm.assert_called_once()
                        mock_google.assert_called_once()


@pytest.mark.anyio
async def test_get_gap_filled_breadcrumbs_both_fail_uses_haversine():
    """Falls back to Haversine interpolation when both providers fail."""
    from utils.live_breadcrumbs import get_gap_filled_breadcrumbs

    mock_rows = [
        {"lat": 50.45, "lng": -104.62, "captured_at": "2024-01-01T10:00:00Z", "tracking_phase": "trip_in_progress"},
        {"lat": 50.455, "lng": -104.615, "captured_at": "2024-01-01T10:02:00Z", "tracking_phase": "trip_in_progress"},
    ]

    with patch("utils.live_breadcrumbs.get_rows", return_value=mock_rows):
        with patch("utils.live_breadcrumbs.compute_gap_route_via_osrm", return_value=None):
            with patch("utils.live_breadcrumbs.compute_gap_route_via_google", return_value=None):
                with patch("utils.live_breadcrumbs.get_app_settings", return_value={}):
                    with patch("utils.live_breadcrumbs.settings.OSRM_URL", ""):
                        result = await get_gap_filled_breadcrumbs("ride123")

                        # Should have interpolated points via Haversine
                        assert len(result) >= 3
                        # First and last should match original
                        assert result[0] == [50.45, -104.62]
                        assert result[-1] == [50.455, -104.615]


@pytest.mark.anyio
async def test_build_breadcrumb_trail_adds_current_position():
    """Trail includes connector from last breadcrumb to current driver position."""
    from utils.live_breadcrumbs import build_breadcrumb_trail

    mock_trail = [[50.45, -104.62], [50.451, -104.619]]
    current_pos = [50.455, -104.615]  # ~500m away
    destination = [50.46, -104.61]

    with patch("utils.live_breadcrumbs.get_gap_filled_breadcrumbs", return_value=mock_trail):
        with patch("utils.live_breadcrumbs.compute_gap_route_via_osrm", return_value=None):
            with patch("utils.live_breadcrumbs.compute_gap_route_via_google", return_value=None):
                with patch("utils.live_breadcrumbs.get_app_settings", return_value={}):
                    with patch("utils.live_breadcrumbs.settings.OSRM_URL", ""):
                        result = await build_breadcrumb_trail("ride123", current_pos, destination)

                        # Should include current position
                        assert len(result) >= 3
                        assert result[-1] == current_pos


@pytest.mark.anyio
async def test_build_breadcrumb_trail_caps_points():
    """Trail is downsampled to MAX_BREADCRUMB_POINTS."""
    from utils.live_breadcrumbs import MAX_BREADCRUMB_POINTS, build_breadcrumb_trail

    # Create a trail longer than max
    long_trail = [[50.45 + i * 0.001, -104.62 + i * 0.001] for i in range(300)]
    current_pos = [50.75, -104.32]
    destination = [50.46, -104.61]

    with patch("utils.live_breadcrumbs.get_gap_filled_breadcrumbs", return_value=long_trail):
        with patch("utils.live_breadcrumbs.compute_gap_route_via_osrm", return_value=None):
            with patch("utils.live_breadcrumbs.compute_gap_route_via_google", return_value=None):
                with patch("utils.live_breadcrumbs.get_app_settings", return_value={}):
                    with patch("utils.live_breadcrumbs.settings.OSRM_URL", ""):
                        result = await build_breadcrumb_trail("ride123", current_pos, destination)

                        assert len(result) <= MAX_BREADCRUMB_POINTS
