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


# ── route-level: Directions proxy (R7, docs/audit/ride-experience/ROADMAP.md) ──

_DIRECTIONS_OK_PAYLOAD = {
    "status": "OK",
    "routes": [
        {
            "legs": [
                {"distance": {"value": 5000}, "duration": {"value": 600}},
            ],
            "overview_polyline": {"points": "_p~iF~ps|U_ulLnnqC_mqNvxq`@"},
        }
    ],
}


@pytest.mark.anyio
async def test_directions_decodes_route_and_records_budget_call(mock_redis, monkeypatch):
    from routes import maps_proxy
    from utils import maps_budget

    monkeypatch.setattr(maps_proxy, "_maps_key", AsyncMock(return_value="dummy_key"))

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_mock_httpx_response(_DIRECTIONS_OK_PAYLOAD))

    with patch("routes.maps_proxy.httpx.AsyncClient", return_value=mock_client):
        result = await maps_proxy.get_directions(
            request=_fake_request(),
            origin="38.5,-120.2",
            destination="43.252,-126.453",
            waypoints=None,
            current_user={"id": "rider_1"},
        )

    assert result == {
        "coordinates": [[38.5, -120.2], [40.7, -120.95], [43.252, -126.453]],
        "distance_km": 5.0,
        "duration_minutes": 10.0,
        "cached": False,
    }
    mock_client.get.assert_awaited_once()
    _, kwargs = mock_client.get.await_args
    assert kwargs["params"]["origin"] == "38.5,-120.2"
    assert kwargs["params"]["destination"] == "43.252,-126.453"
    assert "waypoints" not in kwargs["params"]
    # Shares the same "directions" SKU bucket as _fetch_directions_route /
    # route_distance.py's live-route fallback -- not a new bucket.
    spent = await maps_budget.estimate_today_usd()
    assert spent == pytest.approx(maps_budget._PRICE_USD["directions"], rel=0.01)


@pytest.mark.anyio
async def test_directions_preserves_waypoint_order_unoptimized(mock_redis, monkeypatch):
    """Rider-entered stops are priced/dispatched in the given order -- this
    proxy must never let Google reorder them (no optimizeWaypoints)."""
    from routes import maps_proxy

    monkeypatch.setattr(maps_proxy, "_maps_key", AsyncMock(return_value="dummy_key"))

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_mock_httpx_response(_DIRECTIONS_OK_PAYLOAD))

    with patch("routes.maps_proxy.httpx.AsyncClient", return_value=mock_client):
        await maps_proxy.get_directions(
            request=_fake_request(),
            origin="38.5,-120.2",
            destination="43.252,-126.453",
            waypoints="40.0,-121.0|41.0,-122.0",
            current_user={"id": "rider_1"},
        )

    _, kwargs = mock_client.get.await_args
    assert kwargs["params"]["waypoints"] == "40.0,-121.0|41.0,-122.0"
    assert "optimizeWaypoints" not in kwargs["params"]


@pytest.mark.anyio
async def test_directions_rejects_malformed_origin(mock_redis, monkeypatch):
    from routes import maps_proxy

    monkeypatch.setattr(maps_proxy, "_maps_key", AsyncMock(return_value="dummy_key"))

    with pytest.raises(HTTPException) as exc:
        await maps_proxy.get_directions(
            request=_fake_request(),
            origin="not-a-latlng",
            destination="43.252,-126.453",
            waypoints=None,
            current_user={"id": "rider_1"},
        )
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_directions_rejects_out_of_range_coordinates(mock_redis, monkeypatch):
    from routes import maps_proxy

    monkeypatch.setattr(maps_proxy, "_maps_key", AsyncMock(return_value="dummy_key"))

    with pytest.raises(HTTPException) as exc:
        await maps_proxy.get_directions(
            request=_fake_request(),
            origin="200,-120.2",
            destination="43.252,-126.453",
            waypoints=None,
            current_user={"id": "rider_1"},
        )
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_directions_rejects_malformed_waypoint(mock_redis, monkeypatch):
    """`_parse_latlng` is reused for each waypoint pair -- a bad one must 400
    the same way a bad origin/destination does, not 500 or silently drop."""
    from routes import maps_proxy

    monkeypatch.setattr(maps_proxy, "_maps_key", AsyncMock(return_value="dummy_key"))

    with pytest.raises(HTTPException) as exc:
        await maps_proxy.get_directions(
            request=_fake_request(),
            origin="38.5,-120.2",
            destination="43.252,-126.453",
            waypoints="40.0,-121.0|bad-data",
            current_user={"id": "rider_1"},
        )
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_directions_502s_on_malformed_polyline(mock_redis, monkeypatch):
    """decode_polyline raises ValueError on a truncated/malformed encoded
    string -- must surface as a clean 502, not an unhandled 500."""
    from routes import maps_proxy

    monkeypatch.setattr(maps_proxy, "_maps_key", AsyncMock(return_value="dummy_key"))

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    bad_payload = {
        "status": "OK",
        "routes": [
            {
                "legs": [{"distance": {"value": 5000}, "duration": {"value": 600}}],
                # Truncated mid-varint -- decode_polyline can't terminate cleanly.
                "overview_polyline": {"points": "_p~iF~ps|U_ulL"},
            }
        ],
    }
    mock_client.get = AsyncMock(return_value=_mock_httpx_response(bad_payload))

    with patch("routes.maps_proxy.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(HTTPException) as exc:
            await maps_proxy.get_directions(
                request=_fake_request(),
                origin="38.5,-120.2",
                destination="43.252,-126.453",
                waypoints=None,
                current_user={"id": "rider_1"},
            )
    assert exc.value.status_code == 502


@pytest.mark.anyio
async def test_directions_502s_on_non_ok_status(mock_redis, monkeypatch):
    from routes import maps_proxy

    monkeypatch.setattr(maps_proxy, "_maps_key", AsyncMock(return_value="dummy_key"))

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_mock_httpx_response({"status": "ZERO_RESULTS", "routes": []}))

    with patch("routes.maps_proxy.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(HTTPException) as exc:
            await maps_proxy.get_directions(
                request=_fake_request(),
                origin="38.5,-120.2",
                destination="43.252,-126.453",
                waypoints=None,
                current_user={"id": "rider_1"},
            )
    assert exc.value.status_code == 502


@pytest.mark.anyio
async def test_directions_502s_when_google_request_fails(mock_redis, monkeypatch):
    from routes import maps_proxy

    monkeypatch.setattr(maps_proxy, "_maps_key", AsyncMock(return_value="dummy_key"))

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=RuntimeError("network down"))

    with patch("routes.maps_proxy.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(HTTPException) as exc:
            await maps_proxy.get_directions(
                request=_fake_request(),
                origin="38.5,-120.2",
                destination="43.252,-126.453",
                waypoints=None,
                current_user={"id": "rider_1"},
            )
    assert exc.value.status_code == 502


# ── route-level: Directions proxy result cache (C105, ACTION_ITEMS.md) ───────


@pytest.mark.anyio
async def test_directions_cache_hit_skips_google_call(mock_redis, monkeypatch):
    import json

    from routes import maps_proxy
    from utils import maps_budget
    from utils.redis_client import redis_set

    cache_key = maps_proxy._directions_cache_key(38.5, -120.2, 43.252, -126.453, [])
    cached_payload = {
        "coordinates": [[38.5, -120.2], [43.252, -126.453]],
        "distance_km": 12.3,
        "duration_minutes": 4.5,
    }
    await redis_set(cache_key, json.dumps(cached_payload), ttl=30)

    monkeypatch.setattr(maps_proxy, "_maps_key", AsyncMock(return_value="dummy_key"))
    bad_client = MagicMock()
    bad_client.__aenter__ = AsyncMock(return_value=bad_client)
    bad_client.__aexit__ = AsyncMock(return_value=False)
    bad_client.get = AsyncMock(side_effect=AssertionError("must not call Google on cache hit"))

    with patch("routes.maps_proxy.httpx.AsyncClient", return_value=bad_client):
        result = await maps_proxy.get_directions(
            request=_fake_request(),
            origin="38.5,-120.2",
            destination="43.252,-126.453",
            waypoints=None,
            current_user={"id": "rider_1"},
        )

    assert result == {**cached_payload, "cached": True}
    # No budget consumed -- the Google call (and its record_call) never happened.
    spent = await maps_budget.estimate_today_usd()
    assert spent == 0.0


@pytest.mark.anyio
async def test_directions_cache_miss_populates_cache(mock_redis, monkeypatch):
    import json

    from routes import maps_proxy
    from utils.redis_client import redis_get

    monkeypatch.setattr(maps_proxy, "_maps_key", AsyncMock(return_value="dummy_key"))

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_mock_httpx_response(_DIRECTIONS_OK_PAYLOAD))

    with patch("routes.maps_proxy.httpx.AsyncClient", return_value=mock_client):
        result = await maps_proxy.get_directions(
            request=_fake_request(),
            origin="38.5,-120.2",
            destination="43.252,-126.453",
            waypoints=None,
            current_user={"id": "rider_1"},
        )

    assert result["cached"] is False
    cache_key = maps_proxy._directions_cache_key(38.5, -120.2, 43.252, -126.453, [])
    cached = await redis_get(cache_key)
    assert cached is not None
    stored = json.loads(cached)
    assert stored["distance_km"] == 5.0
    assert stored["coordinates"]


@pytest.mark.anyio
async def test_directions_degenerate_result_never_cached(mock_redis, monkeypatch):
    """A result with no billable distance and no coordinates must never be
    cached -- a stale bad entry would keep returning it for the whole TTL
    window (mirrors the R8 money-auditor finding in _shared.py)."""
    from routes import maps_proxy
    from utils.redis_client import redis_get

    monkeypatch.setattr(maps_proxy, "_maps_key", AsyncMock(return_value="dummy_key"))

    degenerate_payload = {
        "status": "OK",
        "routes": [
            {
                "legs": [{"distance": {"value": 0}, "duration": {"value": 0}}],
                "overview_polyline": {"points": ""},
            }
        ],
    }
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_mock_httpx_response(degenerate_payload))

    with patch("routes.maps_proxy.httpx.AsyncClient", return_value=mock_client):
        result = await maps_proxy.get_directions(
            request=_fake_request(),
            origin="38.5,-120.2",
            destination="43.252,-126.453",
            waypoints=None,
            current_user={"id": "rider_1"},
        )

    assert result["distance_km"] is None
    assert result["coordinates"] == []

    cache_key = maps_proxy._directions_cache_key(38.5, -120.2, 43.252, -126.453, [])
    assert await redis_get(cache_key) is None


@pytest.mark.anyio
async def test_directions_waypoint_order_changes_cache_key(mock_redis, monkeypatch):
    """Two different waypoint orderings for the same origin/destination must
    not collide -- each is billed and cached independently (waypoint order
    is never optimized, so it's a different route)."""
    from routes import maps_proxy

    monkeypatch.setattr(maps_proxy, "_maps_key", AsyncMock(return_value="dummy_key"))

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_mock_httpx_response(_DIRECTIONS_OK_PAYLOAD))

    with patch("routes.maps_proxy.httpx.AsyncClient", return_value=mock_client):
        await maps_proxy.get_directions(
            request=_fake_request(),
            origin="38.5,-120.2",
            destination="43.252,-126.453",
            waypoints="40.0,-121.0|41.0,-122.0",
            current_user={"id": "rider_1"},
        )
        await maps_proxy.get_directions(
            request=_fake_request(),
            origin="38.5,-120.2",
            destination="43.252,-126.453",
            waypoints="41.0,-122.0|40.0,-121.0",
            current_user={"id": "rider_1"},
        )

    # Both requests hit Google -- the second ordering did not read the
    # first's cache entry.
    assert mock_client.get.await_count == 2

    key_a = maps_proxy._directions_cache_key(38.5, -120.2, 43.252, -126.453, [(40.0, -121.0), (41.0, -122.0)])
    key_b = maps_proxy._directions_cache_key(38.5, -120.2, 43.252, -126.453, [(41.0, -122.0), (40.0, -121.0)])
    assert key_a != key_b


@pytest.mark.anyio
async def test_directions_cache_get_failure_falls_through_to_google(mock_redis, monkeypatch):
    """A Redis read error must fail open -- the request still succeeds via
    Google, it just can't benefit from the cache."""
    from routes import maps_proxy

    monkeypatch.setattr(maps_proxy, "_maps_key", AsyncMock(return_value="dummy_key"))
    monkeypatch.setattr(maps_proxy, "redis_get", AsyncMock(side_effect=RuntimeError("redis down")))

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_mock_httpx_response(_DIRECTIONS_OK_PAYLOAD))

    with patch("routes.maps_proxy.httpx.AsyncClient", return_value=mock_client):
        result = await maps_proxy.get_directions(
            request=_fake_request(),
            origin="38.5,-120.2",
            destination="43.252,-126.453",
            waypoints=None,
            current_user={"id": "rider_1"},
        )

    assert result["distance_km"] == 5.0
    assert result["cached"] is False


@pytest.mark.anyio
async def test_directions_cache_set_failure_does_not_break_request(mock_redis, monkeypatch):
    """A Redis write error after a successful Google call must not surface
    to the caller -- the response is still returned normally."""
    from routes import maps_proxy

    monkeypatch.setattr(maps_proxy, "_maps_key", AsyncMock(return_value="dummy_key"))
    monkeypatch.setattr(maps_proxy, "redis_set", AsyncMock(side_effect=RuntimeError("redis down")))

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_mock_httpx_response(_DIRECTIONS_OK_PAYLOAD))

    with patch("routes.maps_proxy.httpx.AsyncClient", return_value=mock_client):
        result = await maps_proxy.get_directions(
            request=_fake_request(),
            origin="38.5,-120.2",
            destination="43.252,-126.453",
            waypoints=None,
            current_user={"id": "rider_1"},
        )

    assert result["distance_km"] == 5.0
    assert result["cached"] is False


@pytest.mark.anyio
async def test_directions_503s_when_budget_exhausted(mock_redis, monkeypatch):
    from routes import maps_proxy
    from utils import maps_budget

    monkeypatch.setattr(maps_budget, "_daily_budget_usd", lambda: 0.001)
    await maps_budget.record_call("directions")  # $0.005 spent, over budget

    monkeypatch.setattr(maps_proxy, "_maps_key", AsyncMock(return_value="dummy_key"))

    with pytest.raises(HTTPException) as exc:
        await maps_proxy.get_directions(
            request=_fake_request(),
            origin="38.5,-120.2",
            destination="43.252,-126.453",
            waypoints=None,
            current_user={"id": "rider_1"},
        )
    assert exc.value.status_code == 503
    assert "budget" in exc.value.detail.lower()


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
