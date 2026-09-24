"""Stored-channel v2 offer envelope (T6-7): ride offer GET and snapshot pending_offer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services import driver_availability_service as service
from backend.tests.test_driver_ride_flow_coverage import _RIDE_ID, _USER_ID, _chain, _driver, _Patches, _ride

pytestmark = pytest.mark.anyio

OFFER_ID = "0b6a2b8e-3c1f-4a55-9d3e-1f2a3b4c5d6e"
CLAIM_ID = "9f8e7d6c-5b4a-4321-8fed-cba987654321"
EXPIRES = "2026-01-01T00:00:15+00:00"
OFFERED = "2026-01-01T00:00:00+00:00"


async def _get_offer(offer_row):
    from backend.routes.drivers.ride_reads import get_ride_offer

    fake_supabase = MagicMock()
    fake_supabase.table.side_effect = lambda name: _chain([offer_row] if name == "ride_offers" else [])
    with _Patches(
        patch("backend.routes.drivers.ride_reads.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
        patch(
            "backend.routes.drivers.ride_reads.db_supabase.get_ride",
            AsyncMock(return_value=_ride(status="searching", driver_id=None)),
        ),
        patch("backend.routes.drivers.ride_reads.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
        patch("backend.routes.drivers.ride_reads.db_supabase.supabase", fake_supabase),
        patch("backend.routes.drivers.ride_reads.db_supabase.run_sync", AsyncMock(side_effect=lambda fn: fn())),
        patch("backend.settings_loader.get_app_settings", AsyncMock(return_value={"ride_offer_timeout_seconds": 15})),
    ):
        return await get_ride_offer(ride_id=_RIDE_ID, current_user={"id": _USER_ID})


async def test_v2_offer_row_adds_envelope():
    result = await _get_offer(
        {
            "id": OFFER_ID,
            "status": "pending",
            "expires_at": EXPIRES,
            "offered_at": OFFERED,
            "claim_id": CLAIM_ID,
            "online_epoch": 42,
        }
    )
    assert result["offer_protocol"] == "v2"
    assert result["offer_id"] == OFFER_ID
    assert result["claim_id"] == CLAIM_ID
    assert result["online_epoch"] == "42"
    assert result["server_time"] == OFFERED
    assert result["expires_at"] == EXPIRES
    assert result["offer_expires_at"] == EXPIRES
    assert result["countdown_seconds"] == 15


async def test_legacy_offer_row_has_no_envelope():
    result = await _get_offer({"id": OFFER_ID, "status": "pending", "expires_at": EXPIRES, "online_epoch": None})
    for key in ("offer_protocol", "offer_id", "claim_id", "online_epoch", "server_time", "expires_at"):
        assert key not in result
    assert result["offer_expires_at"] == EXPIRES


def _raw(pending_offer):
    return {
        "protocol_enabled": True,
        "driver_count": 1,
        "server_time": "2026-01-01T00:00:05+00:00",
        "driver": {
            "id": "drv-1",
            "user_id": "user-1",
            "status": "active",
            "is_online": True,
            "is_available": False,
            "accepting_requests": True,
            "online_epoch": 42,
            "state_version": 3,
            "controller_session_id": "sess-A",
            "is_verified": True,
        },
        "current_session_id": "sess-A",
        "active_ride": None,
        "pending_offer": pending_offer,
        "offer_reconciliation_required": False,
    }


async def _snapshot(pending_offer):
    with (
        patch.object(
            service.driver_availability_repo,
            "get_driver_availability_snapshot",
            AsyncMock(return_value=_raw(pending_offer)),
        ),
        patch.object(service, "_eligibility_reason", AsyncMock(return_value=None)),
    ):
        return await service.get_driver_availability("user-1", "sess-A")


async def test_snapshot_pending_offer_carries_v2_envelope():
    snap = await _snapshot(
        {
            "id": OFFER_ID,
            "offer_id": OFFER_ID,
            "ride_id": "ride-1",
            "offered_at": OFFERED,
            "expires_at": EXPIRES,
            "ride_status": "searching",
            "claim_id": CLAIM_ID,
            "online_epoch": "42",
        }
    )
    offer = snap["pending_offer"]
    assert offer == {
        "id": OFFER_ID,
        "ride_id": "ride-1",
        "offered_at": OFFERED,
        "expires_at": EXPIRES,
        "ride_status": "searching",
        "offer_protocol": "v2",
        "offer_id": OFFER_ID,
        "claim_id": CLAIM_ID,
        "online_epoch": "42",
        "server_time": OFFERED,
    }


async def test_snapshot_legacy_pending_offer_keeps_original_keys():
    snap = await _snapshot(
        {
            "id": OFFER_ID,
            "offer_id": OFFER_ID,
            "ride_id": "ride-1",
            "offered_at": OFFERED,
            "expires_at": EXPIRES,
            "ride_status": "searching",
            "claim_id": None,
            "online_epoch": None,
        }
    )
    assert set(snap["pending_offer"]) == {"id", "ride_id", "offered_at", "expires_at", "ride_status"}
