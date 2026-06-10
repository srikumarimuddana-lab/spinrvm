"""Tests for utils.route_distance.compute_route — OSRM-first, Google Directions fallback.

The live trip map polls GET /rides/{id}/live-route; compute_route must keep
returning a road-snapped line when OSRM is unconfigured or down (Google
Directions, budget-gated + cached) so the rider's route line follows a
deviating driver instead of freezing on the planned polyline.
"""

import json
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

import pytest

from backend.utils import route_distance as rd

# Google's documented encoded-polyline reference vector (3 points).
_ENC = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
_ENC_DECODED = [[38.5, -120.2], [40.7, -120.95], [43.252, -126.453]]

_OSRM_BODY = {
    "code": "Ok",
    "routes": [
        {
            "geometry": {"coordinates": [[-104.62, 50.45], [-104.63, 50.44]]},
            "duration": 300,
            "distance": 2500,
        }
    ],
}

_DIRECTIONS_BODY = {
    "status": "OK",
    "routes": [
        {
            "overview_polyline": {"points": _ENC},
            "legs": [
                {"duration": {"value": 300}, "distance": {"value": 2500}},
                {"duration": {"value": 120}, "distance": {"value": 800}},
            ],
        }
    ],
}


class _Resp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


def _fake_client(handler, calls):
    """AsyncClient stand-in: routes each GET through handler(url), records URLs."""

    class _C:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def get(self, url, params=None):
            calls.append(url)
            return handler(url)

    return _C


@contextmanager
def _common_patches(app_settings, handler, calls, *, budget_allowed=True, cached=None, recorded=None):
    """Patch settings/budget/redis/httpx so only `app_settings` drives provider choice.

    OSRM_URL and OSRM_FALLBACK_URL (public demo, non-empty by default) are
    blanked so "OSRM unconfigured" in a test really means no OSRM provider.
    """

    async def _settings():
        return app_settings

    async def _budget():
        return (budget_allowed, 5.0 if not budget_allowed else 0.0, 5.0)

    async def _record(sku):
        if recorded is not None:
            recorded.append(sku)

    async def _redis_get(_key):
        return cached

    async def _redis_set(*_a, **_kw):
        return None

    with ExitStack() as stack:
        for p in (
            patch.object(rd.settings, "OSRM_URL", ""),
            patch.object(rd.settings, "OSRM_FALLBACK_URL", ""),
            patch.object(rd, "get_app_settings", _settings),
            patch.object(rd, "check_budget", _budget),
            patch.object(rd, "record_call", _record),
            patch.object(rd, "redis_get", _redis_get),
            patch.object(rd, "redis_set", _redis_set),
            patch.object(rd.httpx, "AsyncClient", _fake_client(handler, calls)),
        ):
            stack.enter_context(p)
        yield


def test_decode_encoded_polyline_reference_vector():
    assert rd._decode_encoded_polyline(_ENC) == _ENC_DECODED
    assert rd._decode_encoded_polyline("") == []
    # Truncated input keeps only cleanly decoded pairs, never raises.
    assert rd._decode_encoded_polyline(_ENC[:7]) == []


@pytest.mark.asyncio
async def test_osrm_preferred_when_configured():
    calls = []

    def handler(url):
        assert "/route/v1/driving/" in url
        return _Resp(body=_OSRM_BODY)

    with _common_patches({"osrm_url": "http://osrm:5000", "google_maps_api_key": "gk"}, handler, calls):
        out = await rd.compute_route(50.45, -104.62, 50.44, -104.63)

    assert out == {
        "polyline": [[50.45, -104.62], [50.44, -104.63]],
        "eta_seconds": 300,
        "distance_km": 2.5,
    }
    # OSRM answered — Google never contacted.
    assert len(calls) == 1 and "googleapis" not in calls[0]


@pytest.mark.asyncio
async def test_falls_back_to_google_when_osrm_fails():
    calls, recorded = [], []

    def handler(url):
        if "/route/v1/driving/" in url:
            return _Resp(status_code=503)
        assert url == rd._DIRECTIONS_URL
        return _Resp(body=_DIRECTIONS_BODY)

    with _common_patches(
        {"osrm_url": "http://osrm:5000", "google_maps_api_key": "gk"}, handler, calls, recorded=recorded
    ):
        out = await rd.compute_route(50.45, -104.62, 50.44, -104.63)

    assert out is not None
    assert out["polyline"] == _ENC_DECODED
    assert out["eta_seconds"] == 420  # legs summed
    assert out["distance_km"] == 3.3
    assert recorded == ["directions"]  # metered under the daily budget
    assert len(calls) == 2  # OSRM attempt, then Directions


@pytest.mark.asyncio
async def test_google_used_when_osrm_unconfigured():
    calls = []

    def handler(url):
        assert url == rd._DIRECTIONS_URL
        return _Resp(body=_DIRECTIONS_BODY)

    with _common_patches({"google_maps_api_key": "gk"}, handler, calls):
        out = await rd.compute_route(50.45, -104.62, 50.44, -104.63)

    assert out is not None and out["polyline"] == _ENC_DECODED
    assert calls == [rd._DIRECTIONS_URL]


@pytest.mark.asyncio
async def test_budget_breaker_blocks_google():
    calls = []

    def handler(_url):  # pragma: no cover - must never be reached
        raise AssertionError("HTTP call attempted with budget exhausted")

    with _common_patches({"google_maps_api_key": "gk"}, handler, calls, budget_allowed=False):
        out = await rd.compute_route(50.45, -104.62, 50.44, -104.63)

    assert out is None
    assert calls == []


@pytest.mark.asyncio
async def test_cache_hit_skips_google_call():
    calls = []
    cached_result = {"polyline": [[1.0, 2.0], [3.0, 4.0]], "eta_seconds": 60, "distance_km": 0.5}

    def handler(_url):  # pragma: no cover - must never be reached
        raise AssertionError("HTTP call attempted on cache hit")

    with _common_patches({"google_maps_api_key": "gk"}, handler, calls, cached=json.dumps(cached_result)):
        out = await rd.compute_route(50.45, -104.62, 50.44, -104.63)

    assert out == cached_result
    assert calls == []


@pytest.mark.asyncio
async def test_none_when_no_providers_configured():
    calls = []

    def handler(_url):  # pragma: no cover - must never be reached
        raise AssertionError("HTTP call attempted with no providers")

    with _common_patches({}, handler, calls):
        out = await rd.compute_route(50.45, -104.62, 50.44, -104.63)

    assert out is None
    assert calls == []
