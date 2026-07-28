"""Tests for the rider-facing Google Maps proxy.

Covers the cost-control contracts that justify the proxy's existence:
  - Places API (New) autocomplete/details billing
  - reverse-geocode Redis cache (avoids paying for repeat lookups)
  - daily-budget circuit breaker (defence in depth on top of GCP budget alerts)

Network access is mocked at the httpx layer; tests run offline.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request


def _fake_request() -> Request:
    """Build a minimal real Request that slowapi's isinstance check accepts."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/maps/test",
        "headers": [],
        "client": ("127.0.0.1", 0),
        "query_string": b"",
        "scheme": "http",
        "server": ("test", 80),
    }
    return Request(scope)


# ── maps_budget primitives ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_record_call_increments_per_sku_counter(mock_redis):
    from utils import maps_budget

    await maps_budget.record_call("autocomplete")
    await maps_budget.record_call("autocomplete")
    await maps_budget.record_call("geocode")

    spent = await maps_budget.estimate_today_usd()
    # 2 * 0.00283 + 1 * 0.005 ≈ 0.01066
    assert 0.010 < spent < 0.012


@pytest.mark.anyio
async def test_text_search_new_sku_counts_toward_budget(mock_redis):
    """B5: the AI booking tool's named-place lookup used to call
    record_call("places_text_search") — a string outside the Sku Literal
    and _PRICE_USD dict — so every such call was silently invisible to
    estimate_today_usd()'s budget total. text_search_new is the real SKU
    now; pin that it actually counts."""
    from utils import maps_budget

    await maps_budget.record_call("text_search_new")

    spent = await maps_budget.estimate_today_usd()
    assert spent == pytest.approx(maps_budget._PRICE_USD["text_search_new"], rel=0.01)


@pytest.mark.anyio
async def test_record_autocomplete_request_counts_abandoned_session_until_close(mock_redis):
    from utils import maps_budget

    for _ in range(13):
        await maps_budget.record_autocomplete_request("session-1")

    spent = await maps_budget.estimate_today_usd()
    assert spent == pytest.approx(13 * 0.00283, rel=0.01)

    await maps_budget.close_autocomplete_session("session-1")

    spent = await maps_budget.estimate_today_usd()
    assert spent == pytest.approx(12 * 0.00283, rel=0.01)


@pytest.mark.anyio
async def test_closed_autocomplete_session_reconciles_only_requests_after_twelve(mock_redis):
    from utils import maps_budget

    for _ in range(16):
        await maps_budget.record_autocomplete_request("session-2")

    await maps_budget.close_autocomplete_session("session-2")

    spent = await maps_budget.estimate_today_usd()
    assert spent == pytest.approx(12 * 0.00283, rel=0.01)


@pytest.mark.anyio
async def test_check_budget_trips_over_threshold(mock_redis, monkeypatch):
    from utils import maps_budget

    monkeypatch.setattr(maps_budget, "_daily_budget_usd", lambda: 0.01)
    # 4 unbatched autocomplete calls = 0.01132 > 0.01 threshold
    for _ in range(4):
        await maps_budget.record_call("autocomplete")

    allowed, spent, budget = await maps_budget.check_budget()
    assert allowed is False
    assert spent > budget


@pytest.mark.anyio
async def test_check_budget_allows_under_threshold(mock_redis, monkeypatch):
    from utils import maps_budget

    monkeypatch.setattr(maps_budget, "_daily_budget_usd", lambda: 1.0)
    await maps_budget.record_call("details")

    allowed, _, _ = await maps_budget.check_budget()
    assert allowed is True


# ── route-level: Places API (New) proxy/billing ──────────────────────────────


def _mock_httpx_response(payload: dict) -> MagicMock:
    """Build an httpx-like response object for unit testing."""
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


@pytest.mark.anyio
async def test_autocomplete_uses_new_api_and_records_per_request_when_token_passed(mock_redis, monkeypatch):
    from routes import maps_proxy
    from utils import maps_budget

    monkeypatch.setattr(maps_proxy, "_maps_key", AsyncMock(return_value="dummy_key"))

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(
        return_value=_mock_httpx_response(
            {
                "suggestions": [
                    {
                        "placePrediction": {
                            "placeId": "ChIJ_123",
                            "text": {"text": "123 Main St, Regina, SK, Canada"},
                            "structuredFormat": {
                                "mainText": {"text": "123 Main St"},
                                "secondaryText": {"text": "Regina, SK, Canada"},
                            },
                            "distanceMeters": 42,
                        }
                    }
                ]
            }
        )
    )

    with patch("routes.maps_proxy.httpx.AsyncClient", return_value=mock_client):
        result = await maps_proxy.places_autocomplete(
            request=_fake_request(),
            input="123 main",
            session_token="abc-uuid",
            location="52.1000,-106.6000",
            radius=50000,
            current_user={"id": "rider_1"},
        )

    assert result == {
        "predictions": [
            {
                "place_id": "ChIJ_123",
                "description": "123 Main St, Regina, SK, Canada",
                "structured_formatting": {
                    "main_text": "123 Main St",
                    "secondary_text": "Regina, SK, Canada",
                },
                "distance_meters": 42,
            }
        ]
    }
    mock_client.post.assert_awaited_once()
    _, kwargs = mock_client.post.await_args
    assert kwargs["json"]["sessionToken"] == "abc-uuid"
    assert kwargs["headers"]["X-Goog-FieldMask"]
    # Places API (New) bills this Essentials-terminating flow per autocomplete request.
    spent = await maps_budget.estimate_today_usd()
    assert spent == pytest.approx(0.00283, rel=0.01)


@pytest.mark.anyio
async def test_autocomplete_records_per_call_without_token(mock_redis, monkeypatch):
    from routes import maps_proxy
    from utils import maps_budget

    monkeypatch.setattr(maps_proxy, "_maps_key", AsyncMock(return_value="dummy_key"))

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=_mock_httpx_response({"suggestions": []}))

    with patch("routes.maps_proxy.httpx.AsyncClient", return_value=mock_client):
        await maps_proxy.places_autocomplete(
            request=_fake_request(),
            input="abc",
            session_token=None,
            location=None,
            radius=None,
            current_user={"id": "rider_1"},
        )

    spent = await maps_budget.estimate_today_usd()
    # Per-call autocomplete is $0.00283; never the legacy $0.017 session rate.
    assert spent == pytest.approx(0.00283, rel=0.01)


@pytest.mark.anyio
async def test_details_uses_new_api_and_records_essentials_charge(mock_redis, monkeypatch):
    from routes import maps_proxy
    from utils import maps_budget

    monkeypatch.setattr(maps_proxy, "_maps_key", AsyncMock(return_value="dummy_key"))

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(
        return_value=_mock_httpx_response(
            {
                "location": {"latitude": 52.1, "longitude": -106.6},
                "formattedAddress": "123 Main St",
            }
        )
    )

    with patch("routes.maps_proxy.httpx.AsyncClient", return_value=mock_client):
        result = await maps_proxy.places_details(
            request=_fake_request(),
            place_id="ChIJ_123",
            session_token="abc-uuid",
            current_user={"id": "rider_1"},
        )

    assert result == {"lat": 52.1, "lng": -106.6, "formatted_address": "123 Main St"}
    mock_client.get.assert_awaited_once()
    _, kwargs = mock_client.get.await_args
    assert kwargs["params"] == {"sessionToken": "abc-uuid"}
    assert kwargs["headers"]["X-Goog-FieldMask"] == "location,formattedAddress"
    spent = await maps_budget.estimate_today_usd()
    assert spent == pytest.approx(0.005, rel=0.01)


# ── route-level: reverse-geocode caching ──────────────────────────────────────


@pytest.mark.anyio
async def test_reverse_geocode_returns_cached_without_network(mock_redis, monkeypatch):
    from routes import maps_proxy
    from utils.redis_client import redis_set

    cache_key = "maps:revgeo:52.1333:-106.6667"
    await redis_set(cache_key, "Cached Address, Saskatoon", ttl=3600)

    # If the route incorrectly hits the network the test will fail loudly:
    # AsyncClient's get is wired to raise.
    bad_client = MagicMock()
    bad_client.__aenter__ = AsyncMock(return_value=bad_client)
    bad_client.__aexit__ = AsyncMock(return_value=False)
    bad_client.get = AsyncMock(side_effect=AssertionError("must not call Google on cache hit"))

    with patch("routes.maps_proxy.httpx.AsyncClient", return_value=bad_client):
        result = await maps_proxy.reverse_geocode(
            request=_fake_request(),
            lat=52.1333,
            lng=-106.6667,
            current_user={"id": "rider_1"},
        )

    assert result == {"formatted_address": "Cached Address, Saskatoon", "cached": True}


@pytest.mark.anyio
async def test_reverse_geocode_caches_on_miss(mock_redis, monkeypatch):
    from routes import maps_proxy
    from utils.redis_client import redis_get

    monkeypatch.setattr(maps_proxy, "_maps_key", AsyncMock(return_value="dummy_key"))

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(
        return_value=_mock_httpx_response({"status": "OK", "results": [{"formatted_address": "1 Main St"}]})
    )

    with patch("routes.maps_proxy.httpx.AsyncClient", return_value=mock_client):
        result = await maps_proxy.reverse_geocode(
            request=_fake_request(),
            lat=52.1234,
            lng=-106.5678,
            current_user={"id": "rider_1"},
        )

    assert result["formatted_address"] == "1 Main St"
    assert result["cached"] is False

    cached = await redis_get("maps:revgeo:52.1234:-106.5678")
    assert cached == "1 Main St"


# ── route-level: budget breaker ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_autocomplete_503s_when_budget_exhausted(mock_redis, monkeypatch):
    from routes import maps_proxy
    from utils import maps_budget

    # Pin budget tiny and pre-spend over it.
    monkeypatch.setattr(maps_budget, "_daily_budget_usd", lambda: 0.001)
    await maps_budget.record_call("autocomplete")  # 0.00283 spent

    monkeypatch.setattr(maps_proxy, "_maps_key", AsyncMock(return_value="dummy_key"))

    with pytest.raises(HTTPException) as exc:
        await maps_proxy.places_autocomplete(
            request=_fake_request(),
            input="abc",
            session_token=None,
            current_user={"id": "rider_1"},
        )
    assert exc.value.status_code == 503
    assert "budget" in exc.value.detail.lower()
