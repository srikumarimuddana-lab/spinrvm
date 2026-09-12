"""Roadmap R4 (docs/audit/ride-experience/ROADMAP.md): `distance_matrix` was
not a registered SKU in `utils/maps_budget.py` at all, so `estimate_today_usd()`
structurally could not total spend on this call site regardless of volume —
the breaker was blind, not merely unwired. These pin: a budget-exhausted
state skips the Google call entirely (haversine fallback, same as no-api-key),
and a successful/attempted call records against the "distance_matrix" SKU.
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.utils import maps_eta as me


class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class _FakeClient:
    def __init__(self, resp=None):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def get(self, url, params=None):
        return self._resp


def _client_factory(resp=None):
    return lambda *a, **kw: _FakeClient(resp=resp)


def _no_cache():
    return (
        patch.object(me, "redis_get", AsyncMock(return_value=None)),
        patch.object(me, "redis_set", AsyncMock(return_value=None)),
    )


@pytest.mark.asyncio
async def test_get_ride_eta_budget_exceeded_skips_google_entirely():
    google_called = {"hit": False}

    def _google_client_factory(*a, **kw):
        google_called["hit"] = True
        raise AssertionError("Google Distance Matrix must not be called when the daily budget is exhausted")

    cache_get, cache_set = _no_cache()
    with (
        cache_get,
        cache_set,
        patch.object(me, "_osrm_eta_seconds", AsyncMock(return_value=None)),
        patch.object(me, "check_budget", AsyncMock(return_value=(False, 5.5, 5.0))),
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

    assert google_called["hit"] is False
    # haversine floor is 60s for this short hop
    assert isinstance(eta, int) and eta >= 60


@pytest.mark.asyncio
async def test_get_ride_eta_budget_allowed_records_spend_on_success():
    dm_payload = {"rows": [{"elements": [{"status": "OK", "duration": {"value": 540}}]}]}
    record_mock = AsyncMock()
    cache_get, cache_set = _no_cache()
    with (
        cache_get,
        cache_set,
        patch.object(me, "_osrm_eta_seconds", AsyncMock(return_value=None)),
        patch.object(me, "check_budget", AsyncMock(return_value=(True, 0.0, 5.0))),
        patch.object(me, "record_call", record_mock),
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
    record_mock.assert_awaited_once_with("distance_matrix")


@pytest.mark.asyncio
async def test_batch_get_etas_budget_exceeded_skips_google_entirely():
    def _google_client_factory(*a, **kw):
        raise AssertionError("Google Distance Matrix must not be called when the daily budget is exhausted")

    drivers = [{"id": "d1", "lat": 50.45, "lng": -104.62}, {"id": "d2", "lat": 50.46, "lng": -104.63}]
    with (
        patch.object(me, "check_budget", AsyncMock(return_value=(False, 5.5, 5.0))),
        patch.object(me.httpx, "AsyncClient", _google_client_factory),
    ):
        result = await me.batch_get_etas(drivers, dest_lat=50.44, dest_lng=-104.64, maps_api_key="test-google-key")

    assert set(result.keys()) == {"d1", "d2"}
    assert all(isinstance(v, int) and v >= 60 for v in result.values())


@pytest.mark.asyncio
async def test_batch_get_etas_budget_allowed_records_spend():
    dm_payload = {
        "rows": [
            {"elements": [{"status": "OK", "duration": {"value": 300}}]},
            {"elements": [{"status": "OK", "duration": {"value": 420}}]},
        ]
    }
    record_mock = AsyncMock()
    drivers = [{"id": "d1", "lat": 50.45, "lng": -104.62}, {"id": "d2", "lat": 50.46, "lng": -104.63}]
    with (
        patch.object(me, "check_budget", AsyncMock(return_value=(True, 0.0, 5.0))),
        patch.object(me, "record_call", record_mock),
        patch.object(me.httpx, "AsyncClient", _client_factory(resp=_FakeResp(payload=dm_payload))),
    ):
        result = await me.batch_get_etas(drivers, dest_lat=50.44, dest_lng=-104.64, maps_api_key="test-google-key")

    assert result == {"d1": 300, "d2": 420}
    record_mock.assert_awaited_once_with("distance_matrix")


@pytest.mark.asyncio
async def test_batch_get_etas_no_api_key_never_checks_budget():
    drivers = [{"id": "d1", "lat": 50.45, "lng": -104.62}]
    with patch.object(me, "check_budget") as budget_mock:
        result = await me.batch_get_etas(drivers, dest_lat=50.44, dest_lng=-104.64, maps_api_key="")

    assert set(result.keys()) == {"d1"}
    budget_mock.assert_not_called()
