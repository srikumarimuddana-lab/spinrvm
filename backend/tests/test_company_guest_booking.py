"""Corporate guest booking — services layer.

Covers services/guest_user_service.py (phone-keyed identity) and
services/company_booking_service.py (fare/policy/ride assembly). Unit-level
against the service functions with patches on each service module's own
references — same rationale as the settle_corporate section of
test_corporate_ride_payment.py.

Run:
    pytest backend/tests/test_company_guest_booking.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.tests._factories import close_spawned_coro

_COMPANY_ID = "company_gb"
_BOOKER = {
    "id": "member_booker_gb",
    "company_id": _COMPANY_ID,
    "user_id": "user_booker_gb",
    "role": "member",
    "status": "active",
    "policy_override": False,
}


def _payload(**overrides):
    body = SimpleNamespace(
        customer_name="Pat Customer",
        customer_phone="+13065550123",
        pickup_address="123 Showroom Rd, Saskatoon",
        pickup_lat=52.13,
        pickup_lng=-106.67,
        dropoff_address="456 Home St, Saskatoon",
        dropoff_lat=52.15,
        dropoff_lng=-106.65,
        distance_km=5.2,
        duration_minutes=12,
        vehicle_type_id="vt_standard",
        scheduled_time=None,
        rider_notes=None,
    )
    for k, v in overrides.items():
        setattr(body, k, v)
    return body


def _fare_dict():
    return {
        "base_fare": 5.0,
        "distance_fare": 6.24,
        "time_fare": 2.4,
        "booking_fee": 2.0,
        "surge_multiplier": 1.0,
        "subtotal": 15.64,
        "area_fees": [],
        "area_fees_total": 0.0,
        "tax_amount": 0.78,
        "tax_breakdown": {"gst": 0.78},
        "grand_total": 16.42,
        "service_area": "Saskatoon",
        "service_area_id": "area_sask",
        "driver_earnings": 13.64,
        "admin_earnings": 2.0,
    }


def _policy_result(passed=True, failed_rules=None, allowance=None, policy=None):
    return SimpleNamespace(
        passed=passed,
        failed_rules=failed_rules or [],
        allowance=allowance if allowance is not None else {"type": "unlimited"},
        policy=policy if policy is not None else {},
    )


# ─────────────────────────────────────────────────────────────────────────────
#  guest_user_service
# ─────────────────────────────────────────────────────────────────────────────

_GUS = "backend.services.guest_user_service."


@pytest.mark.anyio
async def test_existing_app_user_is_returned_not_recreated():
    from backend.services.guest_user_service import find_or_create_guest_by_phone

    existing = {"id": "u1", "phone": "+13065550123", "is_guest": False, "status": "active"}
    with (
        patch(_GUS + "db_supabase.get_user_by_phone", AsyncMock(return_value=existing)),
        patch(_GUS + "db_supabase.create_user", AsyncMock()) as create,
    ):
        user, has_app = await find_or_create_guest_by_phone("+1 306 555 0123", "Pat")
    assert user["id"] == "u1"
    assert has_app is True
    create.assert_not_called()


@pytest.mark.anyio
async def test_existing_guest_row_reused_without_app_flag():
    from backend.services.guest_user_service import find_or_create_guest_by_phone

    existing = {"id": "u1", "phone": "+13065550123", "is_guest": True}
    with (
        patch(_GUS + "db_supabase.get_user_by_phone", AsyncMock(return_value=existing)),
        patch(_GUS + "db_supabase.create_user", AsyncMock()) as create,
    ):
        user, has_app = await find_or_create_guest_by_phone("+13065550123", "Pat")
    assert has_app is False
    create.assert_not_called()


@pytest.mark.anyio
async def test_new_guest_row_created_with_is_guest():
    from backend.services.guest_user_service import find_or_create_guest_by_phone

    with (
        patch(_GUS + "db_supabase.get_user_by_phone", AsyncMock(return_value=None)),
        patch(_GUS + "db_supabase.create_user", AsyncMock()) as create,
    ):
        user, has_app = await find_or_create_guest_by_phone("+13065550123", "  Pat  ")
    assert has_app is False
    created = create.call_args.args[0]
    assert created["is_guest"] is True
    assert created["first_name"] == "Pat"
    assert created["role"] == "rider"
    assert created["phone"] == "+13065550123"
    assert user["id"] == created["id"]


@pytest.mark.anyio
async def test_deactivated_phone_is_409():
    from backend.services.guest_user_service import find_or_create_guest_by_phone

    for blocked in (
        {"id": "u1", "status": "pending_deletion"},
        {"id": "u2", "status": "deleted"},
        {"id": "u3", "deleted_at": "2026-01-01T00:00:00Z"},
    ):
        with patch(_GUS + "db_supabase.get_user_by_phone", AsyncMock(return_value=blocked)):
            with pytest.raises(HTTPException) as exc_info:
                await find_or_create_guest_by_phone("+13065550123", "Pat")
        assert exc_info.value.status_code == 409


# ─────────────────────────────────────────────────────────────────────────────
#  company_booking_service
# ─────────────────────────────────────────────────────────────────────────────

_CBS = "backend.services.company_booking_service."


def _booking_patches(
    *,
    guest=None,
    has_app=False,
    fare=None,
    policy_result=None,
    insert_result=None,
):
    guest = guest or {"id": "guest_u1", "phone": "+13065550123", "is_guest": True}

    async def _insert(ride_data, rider_id):
        return (insert_result or ride_data), False

    return {
        _CBS + "find_or_create_guest_by_phone": AsyncMock(return_value=(guest, has_app)),
        _CBS + "compute_fare_estimate": AsyncMock(return_value=fare or _fare_dict()),
        _CBS + "evaluate_policy_for_ride": AsyncMock(return_value=policy_result or _policy_result()),
        "backend.routes.rides.booking._insert_ride_with_code": AsyncMock(side_effect=_insert),
        "backend.routes.rides.booking._prep_and_dispatch": MagicMock(name="prep"),
        # side_effect closes the coroutine handed to spawn() instead of
        # leaking it un-awaited (A8) -- doesn't affect call_count assertions.
        _CBS + "spawn": MagicMock(name="spawn", side_effect=close_spawned_coro),
    }


def _start_patches(patch_dict):
    patchers, mocks = [], {}
    for target, mock_obj in patch_dict.items():
        p = patch(target, mock_obj)
        mocks[target] = p.start()
        patchers.append(p)
    return patchers, mocks


@pytest.mark.anyio
async def test_guest_booking_happy_path_stamps_corporate_fields():
    from backend.services.company_booking_service import create_company_guest_booking

    patchers, mocks = _start_patches(_booking_patches())
    try:
        result = await create_company_guest_booking(_COMPANY_ID, _BOOKER, _payload())
    finally:
        for p in patchers:
            p.stop()

    ride = result["ride"]
    assert ride["corporate_account_id"] == _COMPANY_ID
    assert ride["corporate_member_id"] == _BOOKER["id"]
    assert ride["guest_booking"] is True
    assert ride["payment_method"] == "company_allowance"
    assert ride["surge_multiplier"] == 1.0
    assert ride["status"] == "searching"
    assert len(str(ride["pickup_otp"])) == 4
    assert ride["shared_trip_token"], "immediate rides carry the tracking token from booking"
    assert result["tracking_url"] and ride["shared_trip_token"] in result["tracking_url"]
    assert result["customer_has_app"] is False
    # Fare pinned to 1.0x via the canonical pipeline
    fare_call = mocks[_CBS + "compute_fare_estimate"].call_args.kwargs
    assert str(fare_call["surge_override"]) == "1"
    # Two background tasks: dispatch + customer notification
    assert mocks[_CBS + "spawn"].call_count == 2
    # Policy evaluated against the BOOKER, not the guest
    policy_call = mocks[_CBS + "evaluate_policy_for_ride"].call_args.kwargs
    assert policy_call["rider_id"] == _BOOKER["user_id"]


@pytest.mark.anyio
async def test_scheduled_guest_booking_parks_and_defers_token():
    from backend.services.company_booking_service import create_company_guest_booking

    when = datetime.now(timezone.utc) + timedelta(hours=30)
    patchers, mocks = _start_patches(_booking_patches())
    try:
        result = await create_company_guest_booking(_COMPANY_ID, _BOOKER, _payload(scheduled_time=when))
    finally:
        for p in patchers:
            p.stop()

    ride = result["ride"]
    assert ride["status"] == "scheduled"
    assert ride["is_scheduled"] is True
    assert "shared_trip_token" not in ride, "scheduled rides mint the token at driver-accept (24h expiry)"
    assert result["tracking_url"] is None
    # Only the customer notification is spawned — no dispatch until the
    # scheduled-ride loop flips it to searching.
    assert mocks[_CBS + "spawn"].call_count == 1


@pytest.mark.anyio
async def test_policy_violation_is_403_with_failed_rules():
    from backend.services.company_booking_service import create_company_guest_booking

    patchers, _ = _start_patches(
        _booking_patches(policy_result=_policy_result(passed=False, failed_rules=["max_fare_per_ride"]))
    )
    try:
        with pytest.raises(HTTPException) as exc_info:
            await create_company_guest_booking(_COMPANY_ID, _BOOKER, _payload())
    finally:
        for p in patchers:
            p.stop()
    assert exc_info.value.status_code == 403
    assert "max_fare_per_ride" in exc_info.value.detail["failed_rules"]


@pytest.mark.anyio
async def test_allowance_low_without_master_fallback_is_403():
    from backend.services.company_booking_service import create_company_guest_booking

    low = _policy_result(
        allowance={"type": "fixed_recurring", "amount": 20, "used": 10},  # $10 left, fare ~16.42*1.5
        policy={"allowed_payment_source": "allowance_only"},
    )
    patchers, _ = _start_patches(_booking_patches(policy_result=low))
    try:
        with pytest.raises(HTTPException) as exc_info:
            await create_company_guest_booking(_COMPANY_ID, _BOOKER, _payload())
    finally:
        for p in patchers:
            p.stop()
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["reason"] == "allowance_low"


@pytest.mark.anyio
async def test_active_ride_conflict_is_reworded_409():
    from backend.services.company_booking_service import create_company_guest_booking

    deps = _booking_patches()
    deps["backend.routes.rides.booking._insert_ride_with_code"] = AsyncMock(
        side_effect=HTTPException(status_code=409, detail="You already have an active ride")
    )
    patchers, _ = _start_patches(deps)
    try:
        with pytest.raises(HTTPException) as exc_info:
            await create_company_guest_booking(_COMPANY_ID, _BOOKER, _payload())
    finally:
        for p in patchers:
            p.stop()
    assert exc_info.value.status_code == 409
    assert "customer" in str(exc_info.value.detail).lower()
