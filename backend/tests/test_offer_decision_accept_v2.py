"""accept_ride / decline_ride v2 branches (T5-5, T5-6): atomic RPC vs legacy paths."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.routes.drivers import ride_flow

OFFER = "11111111-1111-4111-8111-111111111111"
DRIVER_V2 = {"id": "d1", "user_id": "u1", "is_online": True, "controller_session_id": "sess", "online_epoch": 4}
RIDE = {"id": "r1", "rider_id": "rider", "status": "searching", "driver_id": None}


@pytest.fixture()
def env():
    m = {
        "get_rows": AsyncMock(return_value=[DRIVER_V2]),
        "get_ride": AsyncMock(return_value=RIDE),
        "accept": AsyncMock(),
        "decline": AsyncMock(),
        "losers": AsyncMock(),
        "update_one": AsyncMock(return_value={"id": "r1"}),
        "find_one": AsyncMock(return_value={**RIDE, "status": "driver_accepted", "driver_id": "d1"}),
        "notify": AsyncMock(),
        "legacy": AsyncMock(),
        "period": AsyncMock(),
        "snapshot": AsyncMock(return_value={"state": "online"}),
    }
    patches = [
        patch.object(ride_flow.db_supabase, "get_rows", m["get_rows"]),
        patch.object(ride_flow.db_supabase, "get_ride", m["get_ride"]),
        patch.object(ride_flow.driver_offer_service, "accept_offer_v2", m["accept"]),
        patch.object(ride_flow.driver_offer_service, "decline_offer_v2", m["decline"]),
        patch.object(ride_flow.driver_offer_service, "release_preempted_losers", m["losers"]),
        patch.object(ride_flow._deps.db, "update_one", m["update_one"]),
        patch.object(ride_flow._deps.db, "find_one", m["find_one"]),
        patch.object(ride_flow._deps, "record_period_transition", m["period"]),
        patch.object(ride_flow, "_after_accept_notify", m["notify"]),
        patch.object(ride_flow, "_legacy_resolve_batch_offers", m["legacy"]),
        patch.object(ride_flow, "_decision_snapshot", m["snapshot"]),
        patch.object(ride_flow, "check_driver_documents_current", AsyncMock()),
        patch.object(ride_flow, "reset_miss_streak", AsyncMock()),
        patch.object(ride_flow, "invalidate_active_rides_cache", AsyncMock()),
        patch("backend.utils.spinr_pass.assert_quota_available", AsyncMock()),
    ]
    for p in patches:
        p.start()
    yield m
    patch.stopall()


async def _accept():
    return await ride_flow.accept_ride("r1", request=None, current_user={"id": "u1"}, token_session_id="sess")


async def test_v2_accept_skips_legacy_cas_and_releases_losers(env):
    losers = [{"offer_id": OFFER, "driver_id": "d2", "claim_id": OFFER}]
    env["accept"].return_value = {"code": "OK", "offer_id": OFFER, "losers": losers, "offered_at": None}
    result = await _accept()
    assert result == {"success": True, "offer_id": OFFER, "already_accepted": False}
    env["update_one"].assert_not_awaited()
    env["period"].assert_not_awaited()
    env["legacy"].assert_not_awaited()
    env["losers"].assert_awaited_once_with(losers, "r1")
    env["notify"].assert_awaited_once()


async def test_v2_replay_returns_already_accepted(env):
    env["accept"].return_value = {"code": "OK", "offer_id": OFFER, "replayed": True}
    result = await _accept()
    assert result == {"success": True, "offer_id": OFFER, "already_accepted": True}
    env["losers"].assert_not_awaited()


async def test_legacy_offer_keeps_cas_path(env):
    env["accept"].return_value = None
    env["get_rows"].side_effect = [[DRIVER_V2], [{"id": "legacy-offer", "status": "pending"}]]
    result = await _accept()
    assert result == {"success": True}
    env["update_one"].assert_awaited()
    env["legacy"].assert_awaited_once()


async def test_v2_conflict_is_409_with_snapshot(env):
    env["accept"].return_value = {"code": "OFFER_EXPIRED", "offer_id": OFFER, "expires_at": "e", "server_time": "t"}
    with pytest.raises(HTTPException) as exc:
        await _accept()
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "OFFER_EXPIRED"
    assert exc.value.detail["snapshot"] == {"state": "online"}


async def test_v2_rpc_failure_is_503(env):
    env["accept"].side_effect = RuntimeError("boom")
    with pytest.raises(HTTPException) as exc:
        await _accept()
    assert exc.value.status_code == 503
    assert exc.value.detail == {"code": "ELIGIBILITY_UNAVAILABLE"}


async def test_v2_offline_driver_gets_structured_409(env):
    env["get_rows"].return_value = [{**DRIVER_V2, "is_online": False}]
    with pytest.raises(HTTPException) as exc:
        await _accept()
    assert exc.value.status_code == 409
    assert exc.value.detail == {"code": "DRIVER_OFFLINE", "online_epoch": "4"}


async def _decline():
    return await ride_flow.decline_ride("r1", request=None, current_user={"id": "u1"}, token_session_id="sess")


async def test_v2_decline_returns_outcome(env):
    env["decline"].return_value = {"code": "OK", "outcome": "declined"}
    assert await _decline() == {"success": True, "outcome": "declined", "already_resolved": False}
    env["decline"].assert_awaited_once_with("r1", DRIVER_V2, "sess", {}, reason=None)


async def test_v2_decline_already_declined_is_200(env):
    env["decline"].return_value = {"code": "OFFER_ALREADY_RESOLVED", "outcome": "declined"}
    assert await _decline() == {"success": True, "outcome": "declined", "already_resolved": True}


async def test_v2_decline_of_preempted_offer_is_ride_taken(env):
    env["decline"].return_value = {"code": "OFFER_ALREADY_RESOLVED", "outcome": "preempted"}
    with pytest.raises(HTTPException) as exc:
        await _decline()
    assert exc.value.status_code == 409
    assert (exc.value.detail["code"], exc.value.detail["reason_code"]) == ("RIDE_STATE_CONFLICT", "RIDE_TAKEN")


async def test_v2_decline_on_cancelled_ride_is_structured(env):
    env["get_ride"].return_value = {**RIDE, "status": "cancelled"}
    with pytest.raises(HTTPException) as exc:
        await _decline()
    assert exc.value.detail == {
        "code": "RIDE_STATE_CONFLICT",
        "reason_code": "RIDE_CANCELLED",
        "ride_status": "cancelled",
    }
    env["decline"].assert_not_awaited()


async def test_legacy_decline_offer_falls_through(env):
    env["decline"].return_value = None
    with patch.object(ride_flow.db_supabase, "run_sync", AsyncMock(side_effect=RuntimeError("legacy path"))):
        with pytest.raises(HTTPException) as exc:
            await _decline()
    # Legacy path ran (its offer update failed, so the ownership guard answers 403).
    assert exc.value.status_code == 403


@pytest.mark.parametrize("raw", [b"{not json", b"[1,2]"])
async def test_v2_decline_rejects_malformed_or_non_object_body_before_rpc(env, raw):
    request = MagicMock()
    if raw.startswith(b"{"):
        request.json = AsyncMock(side_effect=ValueError("bad json"))
    else:
        request.json = AsyncMock(return_value=[1, 2])
    request.body = AsyncMock(return_value=raw)
    with pytest.raises(HTTPException) as exc:
        await ride_flow.decline_ride(
            "r1", request=request, current_user={"id": "u1"}, token_session_id="sess"
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "INVALID_OFFER_DECISION"
    env["decline"].assert_not_awaited()
