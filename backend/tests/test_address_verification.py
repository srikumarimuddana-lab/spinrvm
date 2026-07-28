"""Unit tests for utils/address_verification.py (B9)."""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.address_verification import verify_address_matches_coordinate  # noqa: E402


def _geocode_response(status="OK", location_type="ROOFTOP", partial_match=False, lat=52.1, lng=-106.0):
    return {
        "status": status,
        "results": [
            {
                "partial_match": partial_match,
                "geometry": {"location_type": location_type, "location": {"lat": lat, "lng": lng}},
            }
        ]
        if status == "OK"
        else [],
    }


@pytest.mark.anyio
async def test_empty_address_passes_open():
    ok, reason = await verify_address_matches_coordinate("", 52.1, -106.0)
    assert ok is True
    assert reason is None


@pytest.mark.anyio
async def test_no_api_key_fails_open():
    with patch("utils.address_verification.get_app_settings", AsyncMock(return_value={})):
        ok, reason = await verify_address_matches_coordinate("123 Main St", 52.1, -106.0)
    assert ok is True
    assert reason is None


@pytest.mark.anyio
async def test_budget_exhausted_fails_open():
    with (
        patch(
            "utils.address_verification.get_app_settings",
            AsyncMock(return_value={"google_maps_api_key": "fake-key"}),
        ),
        patch("utils.address_verification.check_budget", AsyncMock(return_value=(False, 1000, 1000))),
    ):
        ok, reason = await verify_address_matches_coordinate("123 Main St", 52.1, -106.0)
    assert ok is True
    assert reason is None


def _mock_http_client(response_json):
    client = MagicMock()
    resp = MagicMock()
    resp.json.return_value = response_json
    client.get = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


@pytest.mark.anyio
async def test_precise_geocode_far_away_rejects():
    # Address geocodes precisely ~1000km away (different lat) from the supplied coordinate.
    response_json = _geocode_response(location_type="ROOFTOP", lat=45.5, lng=-73.6)
    with (
        patch(
            "utils.address_verification.get_app_settings",
            AsyncMock(return_value={"google_maps_api_key": "fake-key"}),
        ),
        patch("utils.address_verification.check_budget", AsyncMock(return_value=(True, 0, 1000))),
        patch("utils.address_verification.record_call", AsyncMock()),
        patch("utils.address_verification.httpx.AsyncClient", return_value=_mock_http_client(response_json)),
    ):
        ok, reason = await verify_address_matches_coordinate("456 Office Blvd", 52.2, -106.1)
    assert ok is False
    assert "geocodes" in reason


@pytest.mark.anyio
async def test_precise_geocode_nearby_passes():
    response_json = _geocode_response(location_type="ROOFTOP", lat=52.2005, lng=-106.1005)
    with (
        patch(
            "utils.address_verification.get_app_settings",
            AsyncMock(return_value={"google_maps_api_key": "fake-key"}),
        ),
        patch("utils.address_verification.check_budget", AsyncMock(return_value=(True, 0, 1000))),
        patch("utils.address_verification.record_call", AsyncMock()),
        patch("utils.address_verification.httpx.AsyncClient", return_value=_mock_http_client(response_json)),
    ):
        ok, reason = await verify_address_matches_coordinate("456 Office Blvd", 52.2, -106.1)
    assert ok is True
    assert reason is None


@pytest.mark.anyio
async def test_approximate_geocode_fails_open():
    response_json = _geocode_response(location_type="APPROXIMATE", lat=45.5, lng=-73.6)
    with (
        patch(
            "utils.address_verification.get_app_settings",
            AsyncMock(return_value={"google_maps_api_key": "fake-key"}),
        ),
        patch("utils.address_verification.check_budget", AsyncMock(return_value=(True, 0, 1000))),
        patch("utils.address_verification.record_call", AsyncMock()),
        patch("utils.address_verification.httpx.AsyncClient", return_value=_mock_http_client(response_json)),
    ):
        ok, reason = await verify_address_matches_coordinate("Some Business Name", 52.2, -106.1)
    assert ok is True
    assert reason is None


@pytest.mark.anyio
async def test_partial_match_fails_open():
    response_json = _geocode_response(location_type="ROOFTOP", partial_match=True, lat=45.5, lng=-73.6)
    with (
        patch(
            "utils.address_verification.get_app_settings",
            AsyncMock(return_value={"google_maps_api_key": "fake-key"}),
        ),
        patch("utils.address_verification.check_budget", AsyncMock(return_value=(True, 0, 1000))),
        patch("utils.address_verification.record_call", AsyncMock()),
        patch("utils.address_verification.httpx.AsyncClient", return_value=_mock_http_client(response_json)),
    ):
        ok, reason = await verify_address_matches_coordinate("Mistyped Address", 52.2, -106.1)
    assert ok is True
    assert reason is None


@pytest.mark.anyio
async def test_geocode_call_failure_fails_open():
    with (
        patch(
            "utils.address_verification.get_app_settings",
            AsyncMock(return_value={"google_maps_api_key": "fake-key"}),
        ),
        patch("utils.address_verification.check_budget", AsyncMock(return_value=(True, 0, 1000))),
        patch("utils.address_verification.httpx.AsyncClient", side_effect=RuntimeError("network down")),
    ):
        ok, reason = await verify_address_matches_coordinate("123 Main St", 52.1, -106.0)
    assert ok is True
    assert reason is None


@pytest.mark.anyio
async def test_zero_results_fails_open():
    response_json = {"status": "ZERO_RESULTS", "results": []}
    with (
        patch(
            "utils.address_verification.get_app_settings",
            AsyncMock(return_value={"google_maps_api_key": "fake-key"}),
        ),
        patch("utils.address_verification.check_budget", AsyncMock(return_value=(True, 0, 1000))),
        patch("utils.address_verification.record_call", AsyncMock()),
        patch("utils.address_verification.httpx.AsyncClient", return_value=_mock_http_client(response_json)),
    ):
        ok, reason = await verify_address_matches_coordinate("Nonexistent Place XYZ", 52.1, -106.0)
    assert ok is True
    assert reason is None
