"""Coverage for the company guest-booking endpoints in
routes/corporate_company_bookings.py: create/list/cancel a booking and the
fare-estimate passthrough.

Handlers are called directly with an explicit ctx (what the company guards
return), mirroring the style in test_corporate_sections.py, so these tests
pin the tenancy/authz rules without needing the full HTTP dependency chain.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend.tests._factories import close_spawned_coro

_CCB = "backend.routes.corporate_company_bookings."
_COMPANY_ID = "company_bk"

_MEMBER_CTX = {
    "user": {"id": "user_member"},
    "company_id": _COMPANY_ID,
    "role": "member",
    "member_id": "member_1",
    "member": {"id": "member_1", "company_id": _COMPANY_ID, "role": "member", "invited_email": "m1@co.com"},
}

_ADMIN_CTX = {
    "user": {"id": "user_admin"},
    "company_id": _COMPANY_ID,
    "role": "admin",
    "member_id": "member_admin",
    "member": {"id": "member_admin", "company_id": _COMPANY_ID, "role": "admin"},
}

_BOOKING_BODY_KWARGS = dict(
    customer_name="Sam",
    customer_phone="+13065550123",
    pickup_address="123 A St",
    pickup_lat=52.13,
    pickup_lng=-106.67,
    dropoff_address="456 B St",
    dropoff_lat=52.14,
    dropoff_lng=-106.66,
    distance_km=3.2,
    duration_minutes=9,
    vehicle_type_id="standard",
)


def _fake_request():
    """A minimal stand-in for the FastAPI Request the handlers accept —
    the handlers under test never read attributes off it directly (the
    slowapi rate-limit decorator does, but we call the undecorated function)."""
    from unittest.mock import MagicMock

    return MagicMock()


# ── create_booking ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_create_booking_success_returns_tracking_url():
    from backend.routes.corporate_company_bookings import CompanyGuestBookingRequest, create_booking

    body = CompanyGuestBookingRequest(**_BOOKING_BODY_KWARGS)
    ride = {
        "id": "ride1",
        "ride_code": "RC1",
        "status": "searching",
        "guest_booking": True,
        "corporate_member_id": "member_1",
        "is_scheduled": False,
    }
    with (
        patch(_CCB + "db_supabase.get_corporate_account_by_id", AsyncMock(return_value={"status": "active"})),
        patch(
            _CCB + "create_company_guest_booking",
            AsyncMock(
                return_value={
                    "ride": ride,
                    "tracking_url": "https://track/abc",
                    "customer_has_app": False,
                    "guest_user": {"first_name": "Sam"},
                }
            ),
        ) as m_create,
        patch(_CCB + "_metric_inc") as m_metric,
    ):
        result = await create_booking(_fake_request(), body, _MEMBER_CTX)

    m_create.assert_awaited_once_with(_COMPANY_ID, _MEMBER_CTX["member"], body)
    m_metric.assert_called_once_with("spinr_corporate_guest_booking_total", {"scheduled": "false"})
    assert result["success"] is True
    assert result["tracking_url"] == "https://track/abc"
    assert result["booking"]["ride_id"] == "ride1"
    assert result["booking"]["customer_first_name"] == "Sam"
    # The OTP must never appear in the booker-facing response.
    assert "otp" not in result["booking"]


@pytest.mark.anyio
async def test_create_booking_scheduled_metric_tag():
    from backend.routes.corporate_company_bookings import CompanyGuestBookingRequest, create_booking

    body = CompanyGuestBookingRequest(**_BOOKING_BODY_KWARGS)
    ride = {"id": "ride2", "is_scheduled": True, "status": "scheduled"}
    with (
        patch(_CCB + "db_supabase.get_corporate_account_by_id", AsyncMock(return_value={"status": "active"})),
        patch(
            _CCB + "create_company_guest_booking",
            AsyncMock(return_value={"ride": ride, "tracking_url": None, "customer_has_app": True, "guest_user": None}),
        ),
        patch(_CCB + "_metric_inc") as m_metric,
    ):
        result = await create_booking(_fake_request(), body, _MEMBER_CTX)

    m_metric.assert_called_once_with("spinr_corporate_guest_booking_total", {"scheduled": "true"})
    assert result["customer_has_app"] is True


@pytest.mark.anyio
async def test_create_booking_blocked_for_non_active_company_direct():
    from backend.routes.corporate_company_bookings import CompanyGuestBookingRequest, create_booking

    body = CompanyGuestBookingRequest(**_BOOKING_BODY_KWARGS)
    with patch(
        _CCB + "db_supabase.get_corporate_account_by_id",
        AsyncMock(return_value={"status": "suspended"}),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await create_booking(_fake_request(), body, _MEMBER_CTX)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "company_not_active"


@pytest.mark.anyio
async def test_require_company_active_missing_company_is_blocked():
    """No row at all (falsy dict fallback) must be treated the same as an
    explicitly inactive company, not raise an unrelated error."""
    from backend.routes.corporate_company_bookings import _require_company_active

    with patch(_CCB + "db_supabase.get_corporate_account_by_id", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await _require_company_active("company_missing")
    assert exc_info.value.status_code == 403


# ── list_bookings ──────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_list_bookings_member_scoped_to_own_rides():
    from backend.routes.corporate_company_bookings import list_bookings

    with patch(_CCB + "db_supabase.get_rows", AsyncMock(return_value=[])) as m_rows:
        result = await list_bookings(_MEMBER_CTX, status=None, member_id=None, section_id=None, date_from=None, date_to=None, skip=0, limit=50)

    filters = m_rows.call_args.args[1]
    assert filters["corporate_member_id"] == "member_1"
    assert result == {"bookings": [], "skip": 0, "limit": 50}


@pytest.mark.anyio
async def test_list_bookings_admin_sees_all_unless_member_filter_given():
    from backend.routes.corporate_company_bookings import list_bookings

    with patch(_CCB + "db_supabase.get_rows", AsyncMock(return_value=[])) as m_rows:
        await list_bookings(_ADMIN_CTX, status=None, member_id=None, section_id=None, date_from=None, date_to=None, skip=0, limit=50)
    filters = m_rows.call_args.args[1]
    assert "corporate_member_id" not in filters

    with patch(_CCB + "db_supabase.get_rows", AsyncMock(return_value=[])) as m_rows:
        await list_bookings(_ADMIN_CTX, status=None, member_id="member_9", section_id=None, date_from=None, date_to=None, skip=0, limit=50)
    filters = m_rows.call_args.args[1]
    assert filters["corporate_member_id"] == "member_9"


@pytest.mark.anyio
async def test_list_bookings_status_and_date_filters():
    from backend.routes.corporate_company_bookings import list_bookings

    with patch(_CCB + "db_supabase.get_rows", AsyncMock(return_value=[])) as m_rows:
        await list_bookings(
            _ADMIN_CTX,
            status="completed",
            member_id=None,
            section_id=None,
            date_from="2026-07-01",
            date_to="2026-07-31",
            skip=0,
            limit=50,
        )
    filters = m_rows.call_args.args[1]
    assert filters["status"] == "completed"
    assert filters["created_at"] == {"$gte": "2026-07-01", "$lte": "2026-07-31"}


@pytest.mark.anyio
async def test_list_bookings_joins_members_and_guests_no_n_plus_1():
    from backend.routes.corporate_company_bookings import list_bookings

    rides = [
        {
            "id": "r1",
            "corporate_member_id": "member_1",
            "rider_id": "rider_1",
            "status": "completed",
        }
    ]
    member_rows = [{"id": "member_1", "section_id": "sec_a", "invited_email": "m1@co.com"}]
    guest_rows = [{"id": "rider_1", "first_name": "Sam", "is_guest": True}]

    async def _get_rows(table, filters, **kwargs):
        if table == "rides":
            return rides
        if table == "corporate_members":
            return member_rows
        if table == "users":
            return guest_rows
        return []

    with patch(_CCB + "db_supabase.get_rows", AsyncMock(side_effect=_get_rows)):
        result = await list_bookings(_ADMIN_CTX, status=None, member_id=None, section_id=None, date_from=None, date_to=None, skip=0, limit=50)

    assert len(result["bookings"]) == 1
    row = result["bookings"][0]
    assert row["customer_first_name"] == "Sam"
    assert row["booked_by_name"] == "m1@co.com"
    assert row["section_id"] == "sec_a"


@pytest.mark.anyio
async def test_list_bookings_section_filter_excludes_other_sections():
    from backend.routes.corporate_company_bookings import list_bookings

    rides = [
        {"id": "r1", "corporate_member_id": "member_1", "rider_id": None},
        {"id": "r2", "corporate_member_id": "member_2", "rider_id": None},
    ]
    member_rows = [
        {"id": "member_1", "section_id": "sec_a"},
        {"id": "member_2", "section_id": "sec_b"},
    ]

    async def _get_rows(table, filters, **kwargs):
        if table == "rides":
            return rides
        if table == "corporate_members":
            return member_rows
        return []

    with patch(_CCB + "db_supabase.get_rows", AsyncMock(side_effect=_get_rows)):
        result = await list_bookings(_ADMIN_CTX, status=None, member_id=None, section_id="sec_a", date_from=None, date_to=None, skip=0, limit=50)

    assert [b["ride_id"] for b in result["bookings"]] == ["r1"]


# ── cancel_booking ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_cancel_booking_not_found_404():
    from backend.routes.corporate_company_bookings import cancel_booking

    with patch(_CCB + "db_supabase.get_ride", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await cancel_booking("ride_missing", _fake_request(), _MEMBER_CTX)
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_cancel_booking_wrong_company_is_404():
    from backend.routes.corporate_company_bookings import cancel_booking

    ride = {"id": "r1", "corporate_account_id": "other_company", "guest_booking": True}
    with patch(_CCB + "db_supabase.get_ride", AsyncMock(return_value=ride)):
        with pytest.raises(HTTPException) as exc_info:
            await cancel_booking("r1", _fake_request(), _MEMBER_CTX)
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_cancel_booking_self_booked_employee_ride_is_cancellable():
    """Finding #19: a self-booked employee ride (guest_booking=False) must
    NOT 404 through this endpoint -- it appears in the same company booking
    list this cancel button lives on, so a company admin must be able to
    act on it. Previously this exact case 404'd."""
    from backend.routes.corporate_company_bookings import cancel_booking

    ride = {
        "id": "r1",
        "corporate_account_id": _COMPANY_ID,
        "guest_booking": False,
        "corporate_member_id": "member_1",
        "rider_id": "rider_1",
        "is_scheduled": False,
    }
    employee_user = {"id": "rider_1", "phone": "+13065551234"}
    with (
        patch(_CCB + "db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch(_CCB + "db_supabase.get_user_by_id", AsyncMock(return_value=employee_user)),
        patch("backend.routes.rides.cancel_ride_rider", AsyncMock(return_value={"success": True})) as m_cancel,
        patch("backend.services.guest_notification_service.notify_guest_cancelled", AsyncMock()),
        patch("backend.utils.background.spawn", side_effect=close_spawned_coro),
    ):
        result = await cancel_booking("r1", _fake_request(), _MEMBER_CTX)

    assert result == {"success": True}
    m_cancel.assert_awaited_once()
    assert m_cancel.call_args.kwargs["current_user"] == employee_user


@pytest.mark.anyio
async def test_cancel_booking_scheduled_ride_delegates_to_cancel_scheduled_ride():
    """A not-yet-dispatched scheduled booking (guest or self-booked) isn't
    in cancel_ride_rider's cancellable-states list at all -- it must go
    through cancel_scheduled_ride instead, mirroring the rider-app's own
    DELETE /rides/scheduled/{id} route."""
    from backend.routes.corporate_company_bookings import cancel_booking

    ride = {
        "id": "r1",
        "corporate_account_id": _COMPANY_ID,
        "guest_booking": True,
        "corporate_member_id": "member_1",
        "rider_id": "rider_1",
        "is_scheduled": True,
        "status": "scheduled",
    }
    guest_user = {"id": "rider_1", "phone": "+13065551234"}
    with (
        patch(_CCB + "db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch(_CCB + "db_supabase.get_user_by_id", AsyncMock(return_value=guest_user)),
        patch(
            "backend.routes.rides.cancellation.cancel_scheduled_ride",
            AsyncMock(return_value={"success": True}),
        ) as m_cancel_scheduled,
        patch("backend.routes.rides.cancel_ride_rider", AsyncMock()) as m_cancel_generic,
        patch("backend.services.guest_notification_service.notify_guest_cancelled", AsyncMock()),
        patch("backend.utils.background.spawn", side_effect=close_spawned_coro),
    ):
        result = await cancel_booking("r1", _fake_request(), _MEMBER_CTX)

    assert result == {"success": True}
    m_cancel_scheduled.assert_awaited_once_with("r1", request=m_cancel_scheduled.call_args.kwargs["request"], current_user=guest_user)
    m_cancel_generic.assert_not_awaited()


@pytest.mark.anyio
async def test_cancel_booking_member_cannot_cancel_others_booking():
    from backend.routes.corporate_company_bookings import cancel_booking

    ride = {
        "id": "r1",
        "corporate_account_id": _COMPANY_ID,
        "guest_booking": True,
        "corporate_member_id": "member_other",
    }
    with patch(_CCB + "db_supabase.get_ride", AsyncMock(return_value=ride)):
        with pytest.raises(HTTPException) as exc_info:
            await cancel_booking("r1", _fake_request(), _MEMBER_CTX)
    assert exc_info.value.status_code == 403


@pytest.mark.anyio
async def test_cancel_booking_customer_missing_404():
    from backend.routes.corporate_company_bookings import cancel_booking

    ride = {
        "id": "r1",
        "corporate_account_id": _COMPANY_ID,
        "guest_booking": True,
        "corporate_member_id": "member_1",
        "rider_id": "rider_1",
    }
    with (
        patch(_CCB + "db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch(_CCB + "db_supabase.get_user_by_id", AsyncMock(return_value=None)),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await cancel_booking("r1", _fake_request(), _MEMBER_CTX)
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_cancel_booking_admin_can_cancel_any_members_booking():
    """Owner/admin roles bypass the own-booking restriction (per docstring)."""
    from backend.routes.corporate_company_bookings import cancel_booking

    ride = {
        "id": "r1",
        "corporate_account_id": _COMPANY_ID,
        "guest_booking": True,
        "corporate_member_id": "member_other",
        "rider_id": "rider_1",
    }
    guest_user = {"id": "rider_1", "phone": "+13065551234"}
    with (
        patch(_CCB + "db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch(_CCB + "db_supabase.get_user_by_id", AsyncMock(return_value=guest_user)),
        patch("backend.routes.rides.cancel_ride_rider", AsyncMock(return_value={"success": True})) as m_cancel,
        patch("backend.services.guest_notification_service.notify_guest_cancelled", AsyncMock()),
        # side_effect closes the coroutine handed to spawn() instead of
        # leaking it un-awaited (A8) -- doesn't affect call_count assertions.
        patch("backend.utils.background.spawn", side_effect=close_spawned_coro) as m_spawn,
    ):
        result = await cancel_booking("r1", _fake_request(), _ADMIN_CTX)

    assert result == {"success": True}
    m_cancel.assert_awaited_once()
    assert m_cancel.call_args.kwargs["current_user"] == guest_user
    m_spawn.assert_called_once()


@pytest.mark.anyio
async def test_cancel_booking_notifies_guest_and_delegates_to_rider_cancel():
    from backend.routes.corporate_company_bookings import cancel_booking

    ride = {
        "id": "r1",
        "corporate_account_id": _COMPANY_ID,
        "guest_booking": True,
        "corporate_member_id": "member_1",
        "rider_id": "rider_1",
    }
    guest_user = {"id": "rider_1", "phone": "+13065551234"}
    with (
        patch(_CCB + "db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch(_CCB + "db_supabase.get_user_by_id", AsyncMock(return_value=guest_user)),
        patch(
            "backend.routes.rides.cancel_ride_rider",
            AsyncMock(return_value={"success": True, "status": "cancelled"}),
        ) as m_cancel,
        patch("backend.services.guest_notification_service.notify_guest_cancelled", AsyncMock()) as m_notify,
        # side_effect closes the coroutine handed to spawn() instead of
        # leaking it un-awaited (A8) -- doesn't affect call_count assertions.
        patch("backend.utils.background.spawn", side_effect=close_spawned_coro) as m_spawn,
    ):
        result = await cancel_booking("r1", _fake_request(), _MEMBER_CTX)

    assert result["status"] == "cancelled"
    m_cancel.assert_awaited_once_with(
        "r1",
        reason="Cancelled by company",
        request=m_cancel.call_args.kwargs["request"],
        current_user=guest_user,
    )
    m_spawn.assert_called_once()
    # spawn was given a coroutine built from notify_guest_cancelled(dict(ride))
    m_notify.assert_called_once_with(dict(ride))


# ── booking_fare_estimate ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_booking_fare_estimate_pins_surge_to_one():
    from decimal import Decimal

    from backend.routes.corporate_company_bookings import booking_fare_estimate

    with patch(_CCB + "compute_fare_estimate", AsyncMock(return_value={"grand_total": "12.50"})) as m_fare:
        result = await booking_fare_estimate(
            _MEMBER_CTX,
            pickup_lat=52.13,
            pickup_lng=-106.67,
            dropoff_lat=52.14,
            dropoff_lng=-106.66,
            distance_km=3.2,
            duration_minutes=9,
            vehicle_type_id="standard",
        )

    assert result == {"grand_total": "12.50"}
    assert m_fare.call_args.kwargs["surge_override"] == Decimal("1")
