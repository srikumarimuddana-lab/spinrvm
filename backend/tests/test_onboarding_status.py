"""
Regression tests for backend/onboarding_status.py.

Covers the drivers.status-based suspension check (see the "Step 3" comment
in derive_driver_onboarding_status) — previously read the dead
`drivers.is_suspended` boolean, which no code path ever set True, so a
suspended/banned driver silently fell through to the documents/verified
checks below and could still be routed to /driver.
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend import onboarding_status

pytestmark = pytest.mark.unit

USER = {
    "id": "user-1",
    "first_name": "Jamie",
    "last_name": "Smith",
    "email": "jamie@example.com",
    "is_driver": True,
}

VEHICLE_FIELDS = {
    "vehicle_make": "Honda",
    "vehicle_model": "Civic",
    "license_plate": "ABC123",
    "vehicle_type_id": "vt-1",
}


def _driver(status: str) -> dict:
    return {
        "id": "driver-1",
        "user_id": "user-1",
        "status": status,
        "is_verified": True,
        **VEHICLE_FIELDS,
    }


@pytest.mark.anyio
async def test_suspended_status_routes_to_suspended():
    driver = _driver("suspended")
    with patch.object(onboarding_status.db_supabase, "get_rows", AsyncMock(return_value=[driver])):
        status, detail, next_screen = await onboarding_status.derive_driver_onboarding_status(USER)

    assert status == "suspended"
    assert next_screen == "/driver"


@pytest.mark.anyio
async def test_banned_status_routes_to_suspended():
    driver = _driver("banned")
    with patch.object(onboarding_status.db_supabase, "get_rows", AsyncMock(return_value=[driver])):
        status, _detail, _next_screen = await onboarding_status.derive_driver_onboarding_status(USER)

    assert status == "suspended"


@pytest.mark.anyio
async def test_legacy_is_suspended_flag_alone_no_longer_triggers_suspension():
    """A driver with the dead is_suspended=True flag but status='active' must
    NOT be treated as suspended — that flag is never set by any writer, so
    trusting it would be trusting a field nothing maintains."""
    driver = _driver("active")
    driver["is_suspended"] = True
    with patch.object(onboarding_status.db_supabase, "get_rows", AsyncMock(return_value=[driver, []])) as mock_get_rows:
        # documents lookup will also go through get_rows; return no documents
        # and no service-area requirements so the driver reads as pending_review.
        mock_get_rows.side_effect = [
            [driver],  # drivers row
            [],  # driver_documents
        ]
        status, _detail, _next_screen = await onboarding_status.derive_driver_onboarding_status(USER)

    assert status != "suspended"


@pytest.mark.anyio
async def test_active_status_does_not_short_circuit_as_suspended():
    driver = _driver("active")
    with patch.object(onboarding_status.db_supabase, "get_rows", AsyncMock()) as mock_get_rows:
        mock_get_rows.side_effect = [
            [driver],  # drivers row
            [],  # driver_documents
        ]
        status, _detail, _next_screen = await onboarding_status.derive_driver_onboarding_status(USER)

    assert status != "suspended"
