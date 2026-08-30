"""Tests for the signed offer-card image endpoint (routes/offer_card.py).

Exercises the inner ``_build_offer_card_response`` helper directly so we skip
the SlowAPI/request plumbing while still covering auth, state-gating, PNG
output, and the PIPEDA guarantee that no exact address leaks into the image.
"""

from unittest.mock import AsyncMock, patch

import pytest

import routes.offer_card as oc
from utils.offer_card_token import sign_offer_card_token

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

_RIDE = {
    "id": "ride-1",
    "rider_id": "rider-1",
    "status": "searching",
    "driver_earnings": 17.20,
    "distance_km": 4.2,
    "duration_minutes": 12,
    "pickup_address": "1855 Victoria Ave #3, Regina",
    "dropoff_address": "320 4th Ave N, Saskatoon",
    "surge_multiplier": 1.5,
}
_RIDER = {"id": "rider-1", "first_name": "Sarah", "last_name": "Johnson", "rating": 4.9}


def _token(ride_id="ride-1", driver_id="driver-1"):
    return sign_offer_card_token(ride_id=ride_id, driver_id=driver_id)


async def test_valid_token_renders_png():
    with (
        patch.object(oc.db_supabase, "get_ride", AsyncMock(return_value=_RIDE)),
        patch.object(oc.db_supabase, "get_user_by_id", AsyncMock(return_value=_RIDER)),
    ):
        resp = await oc._build_offer_card_response("ride-1", _token())
    assert resp.status_code == 200
    assert resp.media_type == "image/png"
    assert resp.body[:8] == _PNG_MAGIC


async def test_no_exact_address_in_image_bytes():
    # The street number must never appear — but PNG bytes aren't text, so the
    # real guarantee is enforced at the data layer: coarsen_address strips it
    # before it reaches the renderer (asserted in test_offer_card.py). Here we
    # just confirm the endpoint passes coarsened values through.
    captured = {}

    def _fake_render(**kwargs):
        captured.update(kwargs)
        return _PNG_MAGIC + b"x"

    with (
        patch.object(oc.db_supabase, "get_ride", AsyncMock(return_value=_RIDE)),
        patch.object(oc.db_supabase, "get_user_by_id", AsyncMock(return_value=_RIDER)),
        patch.object(oc, "render_offer_card", _fake_render),
    ):
        await oc._build_offer_card_response("ride-1", _token())

    assert "1855" not in (captured["pickup_area"] or "")
    assert captured["rider_first_name"] == "Sarah"  # full name never passed


async def test_bad_token_returns_403():
    resp = await oc._build_offer_card_response("ride-1", "garbage.token")
    assert resp.status_code == 403


async def test_token_for_other_ride_returns_403():
    # Token minted for a different ride must not unlock this one.
    resp = await oc._build_offer_card_response("ride-1", _token(ride_id="ride-99"))
    assert resp.status_code == 403


async def test_completed_ride_returns_404():
    done = {**_RIDE, "status": "completed"}
    with patch.object(oc.db_supabase, "get_ride", AsyncMock(return_value=done)):
        resp = await oc._build_offer_card_response("ride-1", _token())
    assert resp.status_code == 404


async def test_missing_ride_returns_404():
    with patch.object(oc.db_supabase, "get_ride", AsyncMock(return_value=None)):
        resp = await oc._build_offer_card_response("ride-1", _token())
    assert resp.status_code == 404


async def test_db_error_returns_503():
    with patch.object(oc.db_supabase, "get_ride", AsyncMock(side_effect=RuntimeError("boom"))):
        resp = await oc._build_offer_card_response("ride-1", _token())
    assert resp.status_code == 503


# ── Area boost / incentives ─────────────────────────────────────────────


class _IncChain:
    """Minimal supabase query-builder stand-in for the ride_incentives read."""

    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **kw):
        return self

    def eq(self, *a, **kw):
        return self

    def or_(self, *a, **kw):
        return self

    def is_(self, *a, **kw):
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


def _patch_incentives(rows):
    fake = type("S", (), {"table": staticmethod(lambda name: _IncChain(rows))})()
    return (
        patch.object(oc.db_supabase, "supabase", fake),
        patch.object(oc.db_supabase, "run_sync", AsyncMock(side_effect=lambda fn: fn())),
    )


async def _render_kwargs(ride, incentive_rows):
    """Run the endpoint with a stubbed renderer and return its kwargs."""
    captured = {}

    def _fake_render(**kwargs):
        captured.update(kwargs)
        return _PNG_MAGIC + b"x"

    inc_supabase, inc_run_sync = _patch_incentives(incentive_rows)
    with (
        patch.object(oc.db_supabase, "get_ride", AsyncMock(return_value=ride)),
        patch.object(oc.db_supabase, "get_user_by_id", AsyncMock(return_value=_RIDER)),
        patch.object(oc, "render_offer_card", _fake_render),
        inc_supabase,
        inc_run_sync,
    ):
        await oc._build_offer_card_response("ride-1", _token())
    return captured


async def test_banner_carries_area_boost_bonus():
    """The banner must show the boost the driver actually earns — the fare
    headline plus a '+$X BONUS' pill — not the bare base fare."""
    kwargs = await _render_kwargs(
        {**_RIDE, "service_area_id": "area-1", "vehicle_type_id": "vt-std"},
        [
            {"bonus_amount": "5.00", "service_area_id": "area-1", "vehicle_type_id": None},
            {"bonus_amount": "1.50", "service_area_id": None, "vehicle_type_id": "vt-std"},
        ],
    )
    assert kwargs["fare"] == 17.20
    assert kwargs["total_bonus"] == 6.5


async def test_banner_skips_incentive_for_other_vehicle_type():
    kwargs = await _render_kwargs(
        {**_RIDE, "vehicle_type_id": "vt-std"},
        [{"bonus_amount": "5.00", "service_area_id": None, "vehicle_type_id": "vt-xl"}],
    )
    assert kwargs["total_bonus"] is None


async def test_banner_still_renders_when_incentive_lookup_fails():
    """A failed boost lookup must cost the pill, never the banner."""
    captured = {}

    def _fake_render(**kwargs):
        captured.update(kwargs)
        return _PNG_MAGIC + b"x"

    boom = type("S", (), {"table": staticmethod(lambda name: (_ for _ in ()).throw(RuntimeError("boom")))})()
    with (
        patch.object(oc.db_supabase, "get_ride", AsyncMock(return_value=_RIDE)),
        patch.object(oc.db_supabase, "get_user_by_id", AsyncMock(return_value=_RIDER)),
        patch.object(oc, "render_offer_card", _fake_render),
        patch.object(oc.db_supabase, "supabase", boom),
    ):
        resp = await oc._build_offer_card_response("ride-1", _token())

    assert resp.status_code == 200
    assert captured["total_bonus"] is None
