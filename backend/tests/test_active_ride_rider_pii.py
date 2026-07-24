"""Regression test for finding 9: get_active_ride must not leak rider secrets.

The endpoint used to build the rider object with a three-field blocklist over a
`select("*")` users row, which shipped every other column — password_hash,
fcm_token, session/auth state — to the driver client. The fix projects the rider
down to an explicit allowlist (_RIDER_PUBLIC_FIELDS). This test asserts the
secrets are gone and the fields the UI renders survive.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


def test_get_active_ride_projects_rider_to_allowlist():
    from backend.routes.drivers import ride_reads

    driver = {"id": "drv-1", "user_id": "user-1"}
    # driver_accepted (not driver_assigned) skips the incentive/quest lookups;
    # service_area_id=None skips the polygon fetch — keeps the test hermetic.
    ride = {
        "id": "ride-1",
        "status": "driver_accepted",
        "driver_id": "drv-1",
        "rider_id": "rider-1",
        "vehicle_type_id": "vt-1",
        "service_area_id": None,
    }
    rider_row = {
        "id": "rider-1",
        "first_name": "Jamie",
        "last_name": "Rider",
        "name": "Jamie Rider",
        "rating": 4.8,
        "profile_image": "https://img.example/p.png",
        # Secrets that must never reach the driver client:
        "password_hash": "$2b$12$SECRETHASHVALUE",
        "fcm_token": "fcm-secret-token",
        "email": "jamie@example.com",
        "phone": "+15551230000",
        "stripe_customer_id": "cus_secret",
        "refresh_token_hash": "rt-secret",
    }

    async def _get_rows(table, *a, **k):
        if table == "drivers":
            return [driver]
        if table == "rides":
            return [ride]
        if table == "vehicle_types":
            return [{"id": "vt-1", "name": "Standard"}]
        return []

    with (
        patch.object(ride_reads.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)),
        patch.object(ride_reads.db_supabase, "get_user_by_id", AsyncMock(return_value=rider_row)),
    ):
        result = asyncio.run(ride_reads.get_active_ride(current_user={"id": "user-1"}))

    rider = result["rider"]
    assert rider is not None

    # No secret / PII column leaks through.
    for secret in (
        "password_hash",
        "fcm_token",
        "email",
        "phone",
        "stripe_customer_id",
        "refresh_token_hash",
    ):
        assert secret not in rider, f"{secret} leaked to driver client"

    # Fields the active-ride UI renders survive the projection.
    assert rider["first_name"] == "Jamie"
    assert rider["last_name"] == "Rider"
    assert rider["rating"] == 4.8
    assert rider["profile_image"] == "https://img.example/p.png"

    # The projection is an allowlist — no unexpected keys beyond the public set.
    assert set(rider).issubset(set(ride_reads._RIDER_PUBLIC_FIELDS))
