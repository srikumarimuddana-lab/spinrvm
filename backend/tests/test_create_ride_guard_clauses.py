"""POST /rides early guard clauses (TestClient, e2e-ish).

create_ride is a single large handler (routes/rides/booking.py); this file
targets the early validation/guard section — banned/suspended riders, the
card-on-file requirements, and the pickup/dropoff/stop geofence checks —
which had zero direct test coverage. The deeper payment/dispatch internals
further down the function are exercised by other, larger e2e test files
(test_p0_ship_blockers.py, test_corporate_ride_payment.py, etc.) and are
deliberately out of scope here.

Each test satisfies just enough of the prerequisite chain (idempotency
check, ban/suspend read, active/unpaid-ride checks) to reach its target
guard, then asserts the guard actually fires — rather than mocking so much
that the assertion becomes meaningless.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

RIDER_ID = "rider-1"
RIDER = {"id": RIDER_ID, "is_driver": False, "role": "user"}

_VALID_BODY = {
    "vehicle_type_id": "economy",
    "pickup_address": "123 Main St",
    "pickup_lat": 52.13,
    "pickup_lng": -106.67,
    "dropoff_address": "456 Broadway Ave",
    "dropoff_lat": 52.12,
    "dropoff_lng": -106.65,
    "payment_method": "card",
}


def _body(**overrides):
    body = dict(_VALID_BODY)
    body.update(overrides)
    return body


@pytest.fixture
def rider_client(test_client):
    from backend.server import app
    from dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: dict(RIDER)
    yield test_client
    app.dependency_overrides.pop(get_current_user, None)


def _no_active_or_unpaid_rides():
    """db_supabase.get_rows dispatcher: no active ride, no unpaid ride, and —
    if a test's own patch needs a service_areas fetch — returns [] there too
    unless overridden by that test's own get_rows patch."""

    async def _fake(table, filters=None, **kw):
        return []

    return _fake


# ── ban / suspension ──────────────────────────────────────────────────────


def test_banned_rider_gets_403(rider_client):
    with (
        patch("backend.routes.rides.booking._deps.db_supabase.find_one", AsyncMock(return_value=None)),
        patch(
            "backend.routes.rides.booking._deps.db.find_one",
            AsyncMock(return_value={"id": RIDER_ID, "status": "banned", "status_reason": "Fraudulent chargebacks."}),
        ),
    ):
        resp = rider_client.post("/api/v1/rides", json=_body())
    assert resp.status_code == 403
    assert "Fraudulent chargebacks" in resp.json()["detail"]


def test_still_suspended_rider_gets_403(rider_client):
    with (
        patch("backend.routes.rides.booking._deps.db_supabase.find_one", AsyncMock(return_value=None)),
        patch(
            "backend.routes.rides.booking._deps.db.find_one",
            AsyncMock(
                return_value={
                    "id": RIDER_ID,
                    "status": "suspended",
                    "suspended_until": "2099-01-01T00:00:00Z",
                }
            ),
        ),
    ):
        resp = rider_client.post("/api/v1/rides", json=_body())
    assert resp.status_code == 403
    assert "suspended" in resp.json()["detail"].lower()


def test_suspension_with_unparseable_until_defaults_to_still_suspended(rider_client):
    """A malformed suspended_until must fail closed (still suspended), not
    silently let a suspended rider book."""
    with (
        patch("backend.routes.rides.booking._deps.db_supabase.find_one", AsyncMock(return_value=None)),
        patch(
            "backend.routes.rides.booking._deps.db.find_one",
            AsyncMock(return_value={"id": RIDER_ID, "status": "suspended", "suspended_until": "not-a-date"}),
        ),
    ):
        resp = rider_client.post("/api/v1/rides", json=_body())
    assert resp.status_code == 403


# ── card-on-file requirements ─────────────────────────────────────────────


def test_card_ride_without_card_on_file_gets_400_when_stripe_configured(rider_client):
    with (
        patch("backend.routes.rides.booking._deps.db_supabase.find_one", AsyncMock(return_value=None)),
        patch(
            "backend.routes.rides.booking._deps.db.find_one",
            AsyncMock(return_value={"id": RIDER_ID, "status": "active"}),  # no stripe_customer_id
        ),
        patch(
            "backend.routes.rides.booking._deps.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test_x"}),
        ),
        patch("backend.routes.rides.booking._deps.db_supabase.get_rows", AsyncMock(side_effect=_no_active_or_unpaid_rides())),
    ):
        resp = rider_client.post("/api/v1/rides", json=_body(payment_method="card"))
    assert resp.status_code == 400
    assert "payment method" in resp.json()["detail"].lower()


def test_card_ride_without_explicit_card_id_gets_400_even_with_card_on_file(rider_client):
    """Defense-in-depth: even a rider WITH a Stripe customer must name the
    exact card (payment_method_id or preauthorized_payment_intent_id) —
    otherwise settle_card() would fall back to the customer's stored
    default, which can be a stale/forgotten card."""
    with (
        patch("backend.routes.rides.booking._deps.db_supabase.find_one", AsyncMock(return_value=None)),
        patch(
            "backend.routes.rides.booking._deps.db.find_one",
            AsyncMock(return_value={"id": RIDER_ID, "status": "active", "stripe_customer_id": "cus_x"}),
        ),
        patch(
            "backend.routes.rides.booking._deps.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test_x"}),
        ),
        patch("backend.routes.rides.booking._deps.db_supabase.get_rows", AsyncMock(side_effect=_no_active_or_unpaid_rides())),
    ):
        resp = rider_client.post("/api/v1/rides", json=_body(payment_method="card"))
    assert resp.status_code == 400
    assert "select a payment card" in resp.json()["detail"].lower()


def test_card_ride_skips_card_checks_when_stripe_unconfigured(rider_client):
    """Demo/local mode (no Stripe secret key) must not lock riders out of
    booking a card ride just because there's no real Stripe customer yet —
    charge_ride's unconfigured path marks it paid without a real charge."""
    with (
        patch("backend.routes.rides.booking._deps.db_supabase.find_one", AsyncMock(return_value=None)),
        patch(
            "backend.routes.rides.booking._deps.db.find_one",
            AsyncMock(return_value={"id": RIDER_ID, "status": "active"}),  # no stripe_customer_id
        ),
        patch("backend.routes.rides.booking._deps.get_app_settings", AsyncMock(return_value={})),
        patch("backend.routes.rides.booking._deps.db_supabase.get_rows", AsyncMock(side_effect=_no_active_or_unpaid_rides())),
    ):
        resp = rider_client.post("/api/v1/rides", json=_body(payment_method="card"))
    # Not the 400 from either card check — whatever happens next (likely a
    # 503/500 from unmocked downstream fare/dispatch calls) is out of scope;
    # the assertion is specifically that these two guards did NOT fire.
    if resp.status_code == 400:
        detail = str(resp.json().get("detail", "")).lower()
        assert "payment method" not in detail
        assert "select a payment card" not in detail


# ── unpaid-ride block ──────────────────────────────────────────────────────


def test_unpaid_ride_blocks_new_booking_with_402(rider_client):
    unpaid = {"id": "ride-unpaid-1"}

    async def _get_rows(table, filters=None, **kw):
        if table == "rides" and (filters or {}).get("payment_status") == "failed":
            return [unpaid]
        return []

    with (
        patch("backend.routes.rides.booking._deps.db_supabase.find_one", AsyncMock(return_value=None)),
        patch(
            "backend.routes.rides.booking._deps.db.find_one",
            AsyncMock(return_value={"id": RIDER_ID, "status": "active", "stripe_customer_id": "cus_x"}),
        ),
        patch("backend.routes.rides.booking._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
    ):
        resp = rider_client.post("/api/v1/rides", json=_body(payment_method="wallet"))
    assert resp.status_code == 402
    assert resp.json()["error"]["details"]["unpaid_ride_id"] == "ride-unpaid-1"


# ── geofence: pickup / dropoff / stop ──────────────────────────────────────


_ACTIVE_AREA = {"id": "area-1", "is_active": True}


def test_pickup_outside_service_area_gets_400(rider_client):
    async def _get_rows(table, filters=None, **kw):
        if table == "service_areas":
            return [_ACTIVE_AREA]
        return []

    with (
        patch("backend.routes.rides.booking._deps.db_supabase.find_one", AsyncMock(return_value=None)),
        patch(
            "backend.routes.rides.booking._deps.db.find_one",
            AsyncMock(return_value={"id": RIDER_ID, "status": "active", "stripe_customer_id": "cus_x"}),
        ),
        patch(
            "backend.routes.rides.booking._deps.get_app_settings",
            AsyncMock(return_value={}),  # Stripe unconfigured — skip card checks
        ),
        patch("backend.routes.rides.booking._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
        patch(
            "backend.routes.rides.booking._get_active_service_area_for_point",
            AsyncMock(return_value=None),  # pickup never matches
        ),
    ):
        resp = rider_client.post("/api/v1/rides", json=_body(payment_method="wallet"))
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "OUTSIDE_SERVICE_AREA"
    assert "pickup" in resp.json()["detail"]["message"].lower()


def test_dropoff_outside_service_area_gets_400(rider_client):
    async def _get_rows(table, filters=None, **kw):
        if table == "service_areas":
            return [_ACTIVE_AREA]
        return []

    call_count = {"n": 0}

    async def _area_lookup(lat, lng, all_areas):
        # 1st call: pickup (must match to reach the dropoff check).
        # 2nd call: dropoff (must fail to trigger this guard).
        call_count["n"] += 1
        return _ACTIVE_AREA if call_count["n"] == 1 else None

    with (
        patch("backend.routes.rides.booking._deps.db_supabase.find_one", AsyncMock(return_value=None)),
        patch(
            "backend.routes.rides.booking._deps.db.find_one",
            AsyncMock(return_value={"id": RIDER_ID, "status": "active", "stripe_customer_id": "cus_x"}),
        ),
        patch("backend.routes.rides.booking._deps.get_app_settings", AsyncMock(return_value={})),
        patch("backend.routes.rides.booking._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
        patch("backend.routes.rides.booking._get_active_service_area_for_point", AsyncMock(side_effect=_area_lookup)),
    ):
        resp = rider_client.post("/api/v1/rides", json=_body(payment_method="wallet"))
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "OUTSIDE_SERVICE_AREA"
    assert "dropoff" in resp.json()["detail"]["message"].lower()


def test_stop_outside_service_area_gets_400(rider_client):
    async def _get_rows(table, filters=None, **kw):
        if table == "service_areas":
            return [_ACTIVE_AREA]
        return []

    call_count = {"n": 0}

    async def _area_lookup(lat, lng, all_areas):
        # 1st: pickup (match), 2nd: dropoff (match), 3rd: the one stop (fail).
        call_count["n"] += 1
        return None if call_count["n"] == 3 else _ACTIVE_AREA

    with (
        patch("backend.routes.rides.booking._deps.db_supabase.find_one", AsyncMock(return_value=None)),
        patch(
            "backend.routes.rides.booking._deps.db.find_one",
            AsyncMock(return_value={"id": RIDER_ID, "status": "active", "stripe_customer_id": "cus_x"}),
        ),
        patch("backend.routes.rides.booking._deps.get_app_settings", AsyncMock(return_value={})),
        patch("backend.routes.rides.booking._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
        patch("backend.routes.rides.booking._get_active_service_area_for_point", AsyncMock(side_effect=_area_lookup)),
    ):
        resp = rider_client.post(
            "/api/v1/rides",
            json=_body(payment_method="wallet", stops=[{"lat": 52.14, "lng": -106.68}]),
        )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "OUTSIDE_SERVICE_AREA"
    assert "stop" in resp.json()["detail"]["message"].lower()
