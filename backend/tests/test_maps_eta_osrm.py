"""OSRM-first ETA selection in utils/maps_eta.py.

get_ride_eta_seconds provider order: Redis cache → OSRM /route (free) →
Google Distance Matrix (metered) → haversine. These pin: OSRM short-circuits
Google entirely, an OSRM failure still reaches the Google fallback, and the
OSRM helper parses the /route duration correctly (lng,lat request order).
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.utils import maps_eta as me
from backend.utils import route_distance as rd


class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class _FakeClient:
    def __init__(self, resp=None, exc=None, capture=None):
        self._resp = resp
        self._exc = exc
        self._capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def get(self, url, params=None):
        if self._capture is not None:
            self._capture["url"] = url
            self._capture["params"] = params
        if self._exc:
            raise self._exc
        return self._resp


def _client_factory(resp=None, exc=None, capture=None):
    return lambda *a, **kw: _FakeClient(resp=resp, exc=exc, capture=capture)


async def _empty_settings():
    return {}


def _no_cache():
    # Cache miss + ignore writes so provider order is what's under test.
    return (
        patch.object(me, "redis_get", AsyncMock(return_value=None)),
        patch.object(me, "redis_set", AsyncMock(return_value=None)),
    )


@pytest.mark.asyncio
async def test_osrm_eta_parses_route_duration():
    payload = {"code": "Ok", "routes": [{"duration": 412.6}]}
    cap = {}
    with (
        patch.object(rd, "get_app_settings", _empty_settings),
        patch("backend.settings_loader.get_app_settings", _empty_settings),
        patch.object(rd.settings, "OSRM_URL", "http://osrm:5000"),
        patch.object(me.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=payload), capture=cap)),
    ):
        eta = await me._osrm_eta_seconds(50.45, -104.62, 50.44, -104.63)

    assert eta == 413
    assert "/route/v1/driving/-104.62,50.45;-104.63,50.44" in cap["url"]
    assert cap["params"]["overview"] == "false"


@pytest.mark.asyncio
async def test_osrm_eta_none_when_no_url_resolves():
    with (
        patch("backend.settings_loader.get_app_settings", _empty_settings),
        patch.object(rd.settings, "OSRM_URL", ""),
        patch.object(rd.settings, "OSRM_FALLBACK_URL", "", create=True),
    ):
        assert await me._osrm_eta_seconds(50.45, -104.62, 50.44, -104.63) is None


@pytest.mark.asyncio
async def test_get_ride_eta_prefers_osrm_and_skips_google():
    google_called = {"hit": False}

    def _google_client_factory(*a, **kw):
        google_called["hit"] = True
        raise AssertionError("Google Distance Matrix must not be called when OSRM succeeds")

    cache_get, cache_set = _no_cache()
    with (
        cache_get,
        cache_set,
        patch.object(me, "_osrm_eta_seconds", AsyncMock(return_value=300)),
        patch.object(me.httpx, "AsyncClient", _google_client_factory),
    ):
        eta = await me.get_ride_eta_seconds(
            driver_lat=50.45,
            driver_lng=-104.62,
            dest_lat=50.44,
            dest_lng=-104.63,
            maps_api_key="test-google-key",
            driver_id="d1",
            ride_id="r1",
        )

    assert eta == 300
    assert google_called["hit"] is False


@pytest.mark.asyncio
async def test_get_ride_eta_falls_back_to_google_when_osrm_fails():
    dm_payload = {"rows": [{"elements": [{"status": "OK", "duration": {"value": 540}}]}]}
    cache_get, cache_set = _no_cache()
    with (
        cache_get,
        cache_set,
        patch.object(me, "_osrm_eta_seconds", AsyncMock(return_value=None)),
        patch.object(me.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=dm_payload))),
    ):
        eta = await me.get_ride_eta_seconds(
            driver_lat=50.45,
            driver_lng=-104.62,
            dest_lat=50.44,
            dest_lng=-104.63,
            maps_api_key="test-google-key",
            driver_id="d1",
            ride_id="r1",
        )

    assert eta == 540


@pytest.mark.asyncio
async def test_get_ride_eta_haversine_when_all_providers_unavailable():
    cache_get, cache_set = _no_cache()
    with (
        cache_get,
        cache_set,
        patch.object(me, "_osrm_eta_seconds", AsyncMock(return_value=None)),
    ):
        eta = await me.get_ride_eta_seconds(
            driver_lat=50.45,
            driver_lng=-104.62,
            dest_lat=50.44,
            dest_lng=-104.63,
            maps_api_key="",  # no Google key either
            driver_id="d1",
            ride_id="r1",
        )

    # haversine floor is 60s; this short hop clamps to it
    assert isinstance(eta, int) and eta >= 60
