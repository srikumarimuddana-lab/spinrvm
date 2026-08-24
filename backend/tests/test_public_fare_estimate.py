"""Public fare estimate: the boundary between the shared engine and anonymous callers.

POST /rides/public-estimate reuses compute_ride_estimates so the website quote
cannot drift from the app's. That reuse is the whole point, and also the whole
risk: the engine is built for a signed-in rider, so these pin the three things
the public path must do differently, plus the two that guard cost.

- no surge-lock token is minted for a caller who cannot book
- no price-search funnel row is written for a user who does not exist
- live driver supply (exact counts, closest-driver distance) never leaves
- the flag gates it, and a disabled flag costs nothing
- identical trips are served from cache, because every miss is a paid
  Directions call
"""

from unittest.mock import AsyncMock, patch

import pytest

import backend.routes.rides.estimates as est


def _engine_result(**overrides):
    """What compute_ride_estimates returns internally, before projection."""
    estimate = {
        "vehicle_type": {
            "id": "vt-1",
            "name": "Standard",
            "capacity": 4,
            "image_url": "https://cdn/x.png",
            # An internal column that must not reach a public caller simply by
            # existing on the row.
            "internal_cost_basis": "secret",
        },
        "distance_km": 8.4,
        "duration_minutes": 14,
        "base_fare": "3.50",
        "distance_fare": "10.08",
        "time_fare": "4.20",
        "booking_fee": "1.00",
        "surge_multiplier": 1.0,
        "total_fare": "18.78",
        "grand_total": "20.14",
        "fare_breakdown": [{"label": "Base fare", "amount": "3.50"}],
        "available": True,
        "eta_minutes": 4,
        "closest_driver_km": 1.2,
        "driver_count": 3,
        "wav_available": False,
        "estimate_token": None,
    }
    estimate.update(overrides.pop("estimate", {}))
    return {"estimates": [estimate], "route_polyline": "abc123", **overrides}


SETTINGS_ON = {"public_fare_estimate_enabled": True}
BODY = {"pickup_lat": 52.13, "pickup_lng": -106.67, "dropoff_lat": 52.15, "dropoff_lng": -106.63}


def _req(**over):
    return est.PublicEstimateRequest(**{**BODY, **over})


def _patches(*, settings=None, engine=None, cached=None):
    return {
        "settings": patch.object(_DEPS, "get_app_settings", AsyncMock(return_value=dict(settings or SETTINGS_ON))),
        "engine": patch.object(est, "compute_ride_estimates", AsyncMock(return_value=engine or _engine_result())),
        "get": patch.object(est, "_redis_get", AsyncMock(return_value=cached)),
        "set": patch.object(est, "_redis_set", AsyncMock()),
    }


_DEPS = est._deps


async def _call(**kw):
    p = _patches(**kw)
    with p["settings"], p["engine"] as engine, p["get"] as rget, p["set"] as rset:
        result = await est.public_estimate_ride(_req())
    return result, engine, rget, rset


# ── what an anonymous caller may see ────────────────────────────────────────


@pytest.mark.anyio
async def test_live_driver_supply_never_leaves():
    """Availability and an ETA are fine — every competitor shows them. An exact
    driver count and closest-driver distance are internal operations data."""
    result, _, _, _ = await _call()
    estimate = result["estimates"][0]
    assert "driver_count" not in estimate
    assert "closest_driver_km" not in estimate
    assert estimate["available"] is True
    assert estimate["eta_minutes"] == 4


@pytest.mark.anyio
async def test_vehicle_type_is_whitelisted_not_blocklisted():
    """vehicle_type is the whole DB row. A new internal column must not reach
    the public response just by being added to the table."""
    result, _, _, _ = await _call()
    vt = result["estimates"][0]["vehicle_type"]
    assert set(vt) <= set(est._PUBLIC_VEHICLE_FIELDS)
    assert "internal_cost_basis" not in vt
    assert vt["name"] == "Standard"


@pytest.mark.anyio
async def test_no_estimate_token_is_returned():
    result, _, _, _ = await _call()
    assert "estimate_token" not in result["estimates"][0]


@pytest.mark.anyio
async def test_the_priced_fields_do_survive():
    """Stripping must not take the quote with it."""
    result, _, _, _ = await _call()
    estimate = result["estimates"][0]
    for key in ("total_fare", "grand_total", "base_fare", "booking_fee", "surge_multiplier", "fare_breakdown"):
        assert key in estimate, key
    assert result["route_polyline"] == "abc123"


# ── how the engine is called ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_engine_is_called_without_tokens_or_funnel_tracking():
    _, engine, _, _ = await _call()
    kwargs = engine.await_args.kwargs
    assert kwargs["issue_tokens"] is False
    assert kwargs["track_search"] is False
    assert kwargs["include_polyline"] is True
    assert kwargs["rider_id"] == ""


@pytest.mark.anyio
async def test_corporate_and_stops_context_cannot_be_smuggled_in():
    """PublicEstimateRequest is deliberately narrower than the internal one —
    corporate billing context changes the quote and is not a public knob."""
    fields = set(est.PublicEstimateRequest.model_fields)
    for banned in ("corporate_account_id", "work_profile", "payment_method", "stops"):
        assert banned not in fields, banned


# ── the flag ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_flag_off_returns_503():
    from fastapi import HTTPException

    p = _patches(settings={"public_fare_estimate_enabled": False})
    with p["settings"], p["engine"], p["get"], p["set"]:
        with pytest.raises(HTTPException) as exc:
            await est.public_estimate_ride(_req())
    assert exc.value.status_code == 503


@pytest.mark.anyio
async def test_flag_off_never_reaches_the_engine_or_the_maps_bill():
    from fastapi import HTTPException

    p = _patches(settings={"public_fare_estimate_enabled": False})
    with p["settings"], p["engine"] as engine, p["get"], p["set"]:
        with pytest.raises(HTTPException):
            await est.public_estimate_ride(_req())
    engine.assert_not_called()


# ── cost control ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_cache_hit_skips_the_engine_entirely():
    """Every miss is a paid Directions call, so this is the cost control, not
    a nicety."""
    import json

    cached = json.dumps({"estimates": [], "route_polyline": None})
    p = _patches(cached=cached)
    with p["settings"], p["engine"] as engine, p["get"], p["set"]:
        result = await est.public_estimate_ride(_req())
    engine.assert_not_called()
    assert result == {"estimates": [], "route_polyline": None}


@pytest.mark.anyio
async def test_a_priced_result_is_written_to_cache():
    _, _, _, rset = await _call()
    rset.assert_awaited_once()
    assert rset.await_args.kwargs.get("ttl") == est._PUBLIC_ESTIMATE_CACHE_TTL_S


@pytest.mark.anyio
async def test_a_broken_cache_still_serves_a_quote():
    """A Redis blip must cost money, not the visitor's answer."""
    p = _patches()
    with (
        p["settings"],
        p["engine"] as engine,
        patch.object(est, "_redis_get", AsyncMock(side_effect=RuntimeError("redis down"))),
        patch.object(est, "_redis_set", AsyncMock(side_effect=RuntimeError("redis down"))),
    ):
        result = await est.public_estimate_ride(_req())
    engine.assert_awaited_once()
    assert result["estimates"][0]["total_fare"] == "18.78"


def test_nearby_coordinates_share_a_cache_key():
    """~110 m grid: a refresh or a nudged pin reuses one Directions call."""
    a = est._public_estimate_cache_key(_req())
    b = est._public_estimate_cache_key(_req(pickup_lat=BODY["pickup_lat"] + 0.0002))
    assert a == b


def test_a_different_trip_does_not():
    a = est._public_estimate_cache_key(_req())
    b = est._public_estimate_cache_key(_req(dropoff_lat=53.0))
    assert a != b


def test_wav_requests_are_cached_separately():
    assert est._public_estimate_cache_key(_req()) != est._public_estimate_cache_key(_req(requires_wav=True))
