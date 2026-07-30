"""Coverage for backend/routes/corporate_company_bookings.py booking endpoints
(create/list/cancel/fare-estimate + the company-active gate + row projection).

Sections CRUD is already covered by test_corporate_sections.py — this file
targets the remaining branches: guest booking creation, tenancy-scoped
listing (member vs owner/admin, filters), pre-trip cancellation authz, the
fare-estimate passthrough, and the `_require_company_active` guard.

Handlers are called directly with an explicit ctx (what the company guards
return), matching the pattern in test_corporate_sections.py.

Run:
    pytest backend/tests/test_corporate_company_bookings_coverage.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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
    "member": {"id": "member_1", "company_id": _COMPANY_ID, "role": "member", "display_name": "Employee One"},
}

_ADMIN_CTX = {
    "user": {"id": "user_admin"},
    "company_id": _COMPANY_ID,
    "role": "admin",
    "member_id": "member_admin",
    "member": {"id": "member_admin", "company_id": _COMPANY_ID, "role": "admin", "display_name": "Admin One"},
}


def _booking_request(**overrides):
    from backend.routes.corporate_company_bookings import CompanyGuestBookingRequest

    payload = {
        "customer_name": "Jamie Rider",
        "customer_phone": "+13065551234",
        "pickup_address": "123 Main St, Regina, SK",
        "pickup_lat": 50.4452,
        "pickup_lng": -104.6189,
        "dropoff_address": "456 Elm St, Regina, SK",
        "dropoff_lat": 50.45,
        "dropoff_lng": -104.62,
        "distance_km": 5.2,
        "duration_minutes": 12,
        "vehicle_type_id": "standard",
    }
    payload.update(overrides)
    return CompanyGuestBookingRequest(**payload)


# ── _require_company_active ─────────────────────────────────────────────


@pytest.mark.anyio
async def test_require_company_active_passes_for_active_company():
    from backend.routes.corporate_company_bookings import _require_company_active

    with patch(
        _CCB + "db_supabase.get_corporate_account_by_id",
        AsyncMock(return_value={"id": _COMPANY_ID, "status": "active"}),
    ):
        await _require_company_active(_COMPANY_ID)  # no raise


@pytest.mark.anyio
async def test_require_company_active_rejects_pending_verification():
    from backend.routes.corporate_company_bookings import _require_company_active

    with patch(
        _CCB + "db_supabase.get_corporate_account_by_id",
        AsyncMock(return_value={"id": _COMPANY_ID, "status": "pending_verification"}),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await _require_company_active(_COMPANY_ID)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "company_not_active"


@pytest.mark.anyio
async def test_require_company_active_rejects_missing_company():
    """No row at all (deleted/bad id) must still gate — not a 500 from None.get()."""
    from backend.routes.corporate_company_bookings import _require_company_active

    with patch(_CCB + "db_supabase.get_corporate_account_by_id", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await _require_company_active(_COMPANY_ID)
    assert exc_info.value.status_code == 403


# ── _booking_row projection ─────────────────────────────────────────────


def test_booking_row_projects_first_name_only_no_otp():
    from backend.routes.corporate_company_bookings import _booking_row

    ride = {
        "id": "ride1",
        "ride_code": "RC1",
        "status": "searching",
        "guest_booking": True,
        "corporate_member_id": "member_1",
        "pickup_address": "123 Main",
        "dropoff_address": "456 Elm",
        "scheduled_time": None,
        "created_at": "2026-07-01T00:00:00Z",
        "grand_total": "12.50",
        "payment_status": "pending",
        "cancelled_reason": None,
        "pickup_otp": "999999",  # must never surface
    }
    member = {"id": "member_1", "invited_email": "e@x.com", "display_name": "E", "section_id": "sec1"}
    guest = {"id": "rider1", "first_name": "Jamie", "last_name": "Rider"}

    row = _booking_row(ride, member, guest)

    assert row["customer_first_name"] == "Jamie"
    assert "pickup_otp" not in row
    assert "last_name" not in row
    assert row["booked_by_name"] == "e@x.com"
    assert row["section_id"] == "sec1"


def test_booking_row_handles_missing_member_and_guest():
    from backend.routes.corporate_company_bookings import _booking_row

    ride = {"id": "ride1", "status": "searching"}
    row = _booking_row(ride, None, None)
    assert row["customer_first_name"] is None
    assert row["booked_by_name"] is None
    assert row["section_id"] is None


# ── create_booking ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_create_booking_rejects_inactive_company():
    from backend.routes.corporate_company_bookings import create_booking

    request = MagicMock()
    with patch(
        _CCB + "db_supabase.get_corporate_account_by_id",
        AsyncMock(return_value={"id": _COMPANY_ID, "status": "suspended"}),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await create_booking(request, _booking_request(), _MEMBER_CTX)
    assert exc_info.value.status_code == 403


@pytest.mark.anyio
async def test_create_booking_success_returns_tracking_and_no_otp():
    from backend.routes.corporate_company_bookings import create_booking

    request = MagicMock()
    ride = {
        "id": "ride1",
        "ride_code": "RC1",
        "status": "searching",
        "guest_booking": True,
        "corporate_member_id": "member_1",
        "is_scheduled": False,
        "pickup_otp": "123456",
    }
    guest_user = {"id": "rider1", "first_name": "Jamie"}
    service_result = {
        "ride": ride,
        "tracking_url": "https://spinr.ca/t/abc",
        "customer_has_app": False,
        "guest_user": guest_user,
    }
    with (
        patch(
            _CCB + "db_supabase.get_corporate_account_by_id",
            AsyncMock(return_value={"id": _COMPANY_ID, "status": "active"}),
        ),
        patch(_CCB + "create_company_guest_booking", AsyncMock(return_value=service_result)) as create_mock,
        patch(_CCB + "_metric_inc") as metric_mock,
    ):
        result = await create_booking(request, _booking_request(), _MEMBER_CTX)

    create_mock.assert_awaited_once()
    assert create_mock.call_args.args[0] == _COMPANY_ID
    assert create_mock.call_args.args[1] == _MEMBER_CTX["member"]
    assert result["success"] is True
    assert result["tracking_url"] == "https://spinr.ca/t/abc"
    assert result["customer_has_app"] is False
    assert "pickup_otp" not in result["booking"]
    metric_mock.assert_called_once_with("spinr_corporate_guest_booking_total", {"scheduled": "false"})


@pytest.mark.anyio
async def test_create_booking_scheduled_metric_tag():
    from backend.routes.corporate_company_bookings import create_booking

    request = MagicMock()
    ride = {"id": "ride1", "status": "scheduled", "is_scheduled": True}
    service_result = {"ride": ride, "tracking_url": None, "customer_has_app": True, "guest_user": None}
    with (
        patch(
            _CCB + "db_supabase.get_corporate_account_by_id",
            AsyncMock(return_value={"id": _COMPANY_ID, "status": "active"}),
        ),
        patch(_CCB + "create_company_guest_booking", AsyncMock(return_value=service_result)),
        patch(_CCB + "_metric_inc") as metric_mock,
    ):
        await create_booking(request, _booking_request(), _MEMBER_CTX)

    metric_mock.assert_called_once_with("spinr_corporate_guest_booking_total", {"scheduled": "true"})


def test_booking_request_phone_normalized_to_e164():
    req = _booking_request(customer_phone="3065551234")
    assert req.customer_phone.startswith("+")


# ── list_bookings ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_list_bookings_member_scoped_to_own():
    from backend.routes.corporate_company_bookings import list_bookings

    async def _rows(table, filters=None, **kwargs):
        if table == "rides":
            assert filters["corporate_member_id"] == "member_1"
            return [{"id": "r1", "corporate_member_id": "member_1", "rider_id": "rider1"}]
        return []

    with patch(_CCB + "db_supabase.get_rows", AsyncMock(side_effect=_rows)):
        result = await list_bookings(
            _MEMBER_CTX,
            status=None,
            member_id=None,
            section_id=None,
            date_from=None,
            date_to=None,
            skip=0,
            limit=50,
        )

    assert len(result["bookings"]) == 1
    assert result["skip"] == 0
    assert result["limit"] == 50


@pytest.mark.anyio
async def test_list_bookings_admin_sees_all_unless_member_filter():
    from backend.routes.corporate_company_bookings import list_bookings

    async def _rows(table, filters=None, **kwargs):
        if table == "rides":
            assert "corporate_member_id" not in filters
            return []
        return []

    with patch(_CCB + "db_supabase.get_rows", AsyncMock(side_effect=_rows)):
        await list_bookings(_ADMIN_CTX, status=None, member_id=None, section_id=None, date_from=None, date_to=None)


@pytest.mark.anyio
async def test_list_bookings_admin_member_id_filter():
    from backend.routes.corporate_company_bookings import list_bookings

    async def _rows(table, filters=None, **kwargs):
        if table == "rides":
            assert filters["corporate_member_id"] == "member_other"
            return []
        return []

    with patch(_CCB + "db_supabase.get_rows", AsyncMock(side_effect=_rows)):
        await list_bookings(
            _ADMIN_CTX,
            status=None,
            member_id="member_other",
            section_id=None,
            date_from=None,
            date_to=None,
        )


@pytest.mark.anyio
async def test_list_bookings_status_and_date_filters():
    from backend.routes.corporate_company_bookings import list_bookings

    captured = {}

    async def _rows(table, filters=None, **kwargs):
        if table == "rides":
            captured.update(filters)
            return []
        return []

    with patch(_CCB + "db_supabase.get_rows", AsyncMock(side_effect=_rows)):
        await list_bookings(
            _ADMIN_CTX,
            status="completed",
            member_id=None,
            section_id=None,
            date_from="2026-07-01",
            date_to="2026-07-31",
        )

    assert captured["status"] == "completed"
    assert captured["created_at"] == {"$gte": "2026-07-01", "$lte": "2026-07-31"}


@pytest.mark.anyio
async def test_list_bookings_batch_joins_members_and_guests_no_n_plus_1():
    from backend.routes.corporate_company_bookings import list_bookings

    calls = []

    async def _rows(table, filters=None, **kwargs):
        calls.append(table)
        if table == "rides":
            return [
                {"id": "r1", "corporate_member_id": "m1", "rider_id": "u1"},
                {"id": "r2", "corporate_member_id": "m2", "rider_id": "u2"},
            ]
        if table == "corporate_members":
            assert filters["id"] == {"$in": ["m1", "m2"]}
            return [{"id": "m1", "section_id": "sec1"}, {"id": "m2", "section_id": "sec2"}]
        if table == "users":
            assert filters["id"] == {"$in": ["u1", "u2"]}
            return [{"id": "u1", "first_name": "A"}, {"id": "u2", "first_name": "B"}]
        return []

    with patch(_CCB + "db_supabase.get_rows", AsyncMock(side_effect=_rows)):
        result = await list_bookings(
            _ADMIN_CTX, status=None, member_id=None, section_id=None, date_from=None, date_to=None
        )

    assert calls.count("rides") == 1
    assert calls.count("corporate_members") == 1
    assert calls.count("users") == 1
    assert len(result["bookings"]) == 2


@pytest.mark.anyio
async def test_list_bookings_section_filter_excludes_other_sections():
    from backend.routes.corporate_company_bookings import list_bookings

    async def _rows(table, filters=None, **kwargs):
        if table == "rides":
            return [
                {"id": "r1", "corporate_member_id": "m1", "rider_id": "u1"},
                {"id": "r2", "corporate_member_id": "m2", "rider_id": "u2"},
            ]
        if table == "corporate_members":
            return [{"id": "m1", "section_id": "sec1"}, {"id": "m2", "section_id": "sec2"}]
        if table == "users":
            return []
        return []

    with patch(_CCB + "db_supabase.get_rows", AsyncMock(side_effect=_rows)):
        result = await list_bookings(
            _ADMIN_CTX, status=None, member_id=None, section_id="sec1", date_from=None, date_to=None
        )

    assert len(result["bookings"]) == 1
    assert result["bookings"][0]["ride_id"] == "r1"


@pytest.mark.anyio
async def test_list_bookings_empty_rides_short_circuits_joins():
    from backend.routes.corporate_company_bookings import list_bookings

    with patch(_CCB + "db_supabase.get_rows", AsyncMock(return_value=[])) as get_rows:
        result = await list_bookings(
            _ADMIN_CTX, status=None, member_id=None, section_id=None, date_from=None, date_to=None
        )

    get_rows.assert_awaited_once()  # only the rides query — no member/guest joins fired
    assert result["bookings"] == []


# ── cancel_booking ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_cancel_booking_not_found_returns_404():
    from backend.routes.corporate_company_bookings import cancel_booking

    request = MagicMock()
    with patch(_CCB + "db_supabase.get_ride", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await cancel_booking("ride1", request, _MEMBER_CTX)
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_cancel_booking_cross_company_returns_404():
    from backend.routes.corporate_company_bookings import cancel_booking

    request = MagicMock()
    ride = {"id": "ride1", "corporate_account_id": "other_company", "guest_booking": True}
    with patch(_CCB + "db_supabase.get_ride", AsyncMock(return_value=ride)):
        with pytest.raises(HTTPException) as exc_info:
            await cancel_booking("ride1", request, _MEMBER_CTX)
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_cancel_booking_non_guest_booking_returns_404():
    """A regular (non-guest) ride under the same company id must not be
    cancellable through the company-portal cancel path."""
    from backend.routes.corporate_company_bookings import cancel_booking

    request = MagicMock()
    ride = {"id": "ride1", "corporate_account_id": _COMPANY_ID, "guest_booking": False}
    with patch(_CCB + "db_supabase.get_ride", AsyncMock(return_value=ride)):
        with pytest.raises(HTTPException) as exc_info:
            await cancel_booking("ride1", request, _MEMBER_CTX)
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_cancel_booking_member_cannot_cancel_others():
    from backend.routes.corporate_company_bookings import cancel_booking

    request = MagicMock()
    ride = {
        "id": "ride1",
        "corporate_account_id": _COMPANY_ID,
        "guest_booking": True,
        "corporate_member_id": "member_other",
    }
    with patch(_CCB + "db_supabase.get_ride", AsyncMock(return_value=ride)):
        with pytest.raises(HTTPException) as exc_info:
            await cancel_booking("ride1", request, _MEMBER_CTX)
    assert exc_info.value.status_code == 403


@pytest.mark.anyio
async def test_cancel_booking_admin_can_cancel_any_member():
    from backend.routes.corporate_company_bookings import cancel_booking

    request = MagicMock()
    ride = {
        "id": "ride1",
        "corporate_account_id": _COMPANY_ID,
        "guest_booking": True,
        "corporate_member_id": "member_other",
        "rider_id": "rider1",
    }
    guest_user = {"id": "rider1", "first_name": "Jamie"}
    cancel_result = {"success": True, "status": "cancelled"}

    with (
        patch(_CCB + "db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch(_CCB + "db_supabase.get_user_by_id", AsyncMock(return_value=guest_user)),
        patch("backend.routes.rides.cancel_ride_rider", AsyncMock(return_value=cancel_result)) as cancel_mock,
        patch("backend.services.guest_notification_service.notify_guest_cancelled", AsyncMock()),
        # side_effect closes the coroutine handed to spawn() instead of
        # leaking it un-awaited (A8) -- doesn't affect call_count assertions.
        patch("backend.utils.background.spawn", side_effect=close_spawned_coro) as spawn_mock,
    ):
        result = await cancel_booking("ride1", request, _ADMIN_CTX)

    cancel_mock.assert_awaited_once()
    assert cancel_mock.call_args.args[0] == "ride1"
    assert cancel_mock.call_args.kwargs["current_user"] == guest_user
    assert result == cancel_result
    spawn_mock.assert_called_once()


@pytest.mark.anyio
async def test_cancel_booking_missing_guest_user_returns_404():
    from backend.routes.corporate_company_bookings import cancel_booking

    request = MagicMock()
    ride = {
        "id": "ride1",
        "corporate_account_id": _COMPANY_ID,
        "guest_booking": True,
        "corporate_member_id": "member_1",
        "rider_id": "rider_gone",
    }
    with (
        patch(_CCB + "db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch(_CCB + "db_supabase.get_user_by_id", AsyncMock(return_value=None)),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await cancel_booking("ride1", request, _MEMBER_CTX)
    assert exc_info.value.status_code == 404


# ── booking_fare_estimate ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_booking_fare_estimate_pins_surge_to_one():
    from decimal import Decimal

    from backend.routes.corporate_company_bookings import booking_fare_estimate

    with patch(_CCB + "compute_fare_estimate", AsyncMock(return_value={"total": "10.00"})) as fare_mock:
        result = await booking_fare_estimate(
            _MEMBER_CTX,
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
            distance_km=5.0,
            duration_minutes=10,
            vehicle_type_id="standard",
        )

    assert result == {"total": "10.00"}
    assert fare_mock.call_args.kwargs["surge_override"] == Decimal("1")
