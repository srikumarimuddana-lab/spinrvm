"""Tests for the G5 demand-side kill switch (ACTION_ITEMS.md).

new_ride_requests_enabled is checked at the very top of POST /rides
(create_ride), before validate_ride_location or any DB write — distinct
from the E5 flags (test_kill_switch_flags.py), none of which stop new
bookings generally. Follows the same fail-open-on-settings-error
convention as every other kill switch in this codebase (e.g.
services/payment_service.py::settle_corporate's corporate_billing_enabled
check).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio

_RIDER_ID = "rider-g5-1"
_USER = {"id": _RIDER_ID}


def _starlette_request(method="POST", path="/rides"):
    from starlette.requests import Request as SR

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "query_string": b"",
        "root_path": "",
        "client": ("127.0.0.1", 9999),
    }
    return SR(scope)


def _body(**kw):
    from backend.schemas import CreateRideRequest

    defaults = dict(
        pickup_address="100 Main St",
        pickup_lat=52.1,
        pickup_lng=-106.6,
        dropoff_address="200 Broadway Ave",
        dropoff_lat=52.2,
        dropoff_lng=-106.7,
        vehicle_type_id="vt-1",
        payment_method="wallet",
    )
    defaults.update(kw)
    return CreateRideRequest(**defaults)


async def test_flag_off_rejects_new_ride_with_clean_503():
    from fastapi import HTTPException

    from backend.routes.rides import create_ride

    with patch(
        "backend.routes.rides._deps.get_app_settings",
        new_callable=AsyncMock,
        return_value={"new_ride_requests_enabled": False},
    ):
        with pytest.raises(HTTPException) as exc_info:
            await create_ride(request=_starlette_request(), body=_body(), current_user=_USER)

    assert exc_info.value.status_code == 503
    assert "temporarily unavailable" in exc_info.value.detail.lower()


async def test_flag_off_rejects_before_any_db_write():
    """The 503 must fire before validate_ride_location or any DB call --
    confirms the guard is the very first thing in the handler, not just
    somewhere before the response."""
    from fastapi import HTTPException

    from backend.routes.rides import create_ride

    with (
        patch("backend.routes.rides._deps.validate_ride_location") as mock_validate,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch(
            "backend.routes.rides._deps.get_app_settings",
            new_callable=AsyncMock,
            return_value={"new_ride_requests_enabled": False},
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await create_ride(request=_starlette_request(), body=_body(), current_user=_USER)

    assert exc_info.value.status_code == 503
    mock_validate.assert_not_called()
    mock_supabase.get_rows.assert_not_called()


async def test_flag_omitted_defaults_to_enabled():
    """A settings row that predates this flag (or the in-process defaults
    dict) must not silently start rejecting bookings -- default-True is
    the whole point of a kill switch."""
    from backend.routes.rides import create_ride

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides._deps.get_app_settings", new_callable=AsyncMock, return_value={}),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})
        mock_supabase.find_one = AsyncMock(return_value=None)
        # Fails on the next real DB call (service_areas fetch) -- proves we
        # got PAST the kill-switch guard rather than being rejected by it.
        mock_supabase.get_rows = AsyncMock(side_effect=[[], [], RuntimeError("service_areas table down")])

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await create_ride(request=_starlette_request(), body=_body(), current_user=_USER)

    # 503 from the service_areas failure downstream, not from the kill
    # switch (which would also be 503 -- the assertion that matters is
    # that we reached this later guard at all, per mock_validate calls
    # implicitly proven by get_rows having been called 3 times).
    assert exc_info.value.status_code == 503
    assert mock_supabase.get_rows.await_count == 3


async def test_settings_lookup_failure_fails_open():
    """A degraded app_settings read must not itself block booking -- same
    fail-open convention as settle_corporate's corporate_billing_enabled
    check."""
    from backend.routes.rides import create_ride

    with (
        patch("backend.routes.rides._deps.validate_ride_location"),
        patch("backend.routes.rides._deps.db") as mock_db,
        patch("backend.routes.rides._deps.db_supabase") as mock_supabase,
        patch(
            "backend.routes.rides._deps.get_app_settings",
            new_callable=AsyncMock,
            side_effect=RuntimeError("settings db down"),
        ),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": _RIDER_ID, "status": "active"})
        mock_supabase.find_one = AsyncMock(return_value=None)
        mock_supabase.get_rows = AsyncMock(side_effect=[[], [], RuntimeError("service_areas table down")])

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await create_ride(request=_starlette_request(), body=_body(), current_user=_USER)

    # Proves we got past the kill-switch guard (reached the later,
    # unrelated service_areas failure) despite the settings lookup itself
    # raising.
    assert exc_info.value.status_code == 503
    assert mock_supabase.get_rows.await_count == 3
