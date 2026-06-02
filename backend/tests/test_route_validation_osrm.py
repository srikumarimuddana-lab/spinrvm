from unittest.mock import patch

import pytest

from backend.utils import route_validation as rv


class _FakeResp:
    status_code = 200

    def json(self):
        return {
            "code": "Ok",
            "tracepoints": [{"matchings_index": 0}, {"matchings_index": 0}, None, {"matchings_index": 0}],
            "matchings": [{"confidence": 0.92}],
        }


class _FakeClient:
    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def get(self, *_a, **_kw):
        return _FakeResp()


def _crumb(i):
    return {
        "lat": 50.45 + i * 0.001,
        "lng": -104.62 - i * 0.001,
        "tracking_phase": "trip_in_progress",
        "accuracy": 12,
    }


@pytest.mark.asyncio
async def test_validate_trip_route_prefers_osrm_match():
    async def _settings():
        return {"osrm_url": "http://osrm:5000", "google_maps_api_key": "unused"}

    with patch.object(rv, "get_app_settings", _settings), patch.object(rv.httpx, "AsyncClient", _FakeClient):
        result = await rv.validate_trip_route([_crumb(i) for i in range(6)])

    assert result["provider"] == "osrm_match"
    assert result["snapped_points"] == 3
    assert result["match_confidence"] == 0.92
    assert result["verdict"] == "suspicious"
