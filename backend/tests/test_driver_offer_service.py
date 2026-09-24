"""Side effects of v2 offer decisions by outcome; replays repeat none."""

from unittest.mock import AsyncMock, patch

import pytest

from backend.services import driver_offer_service as svc

OFFER = "11111111-1111-4111-8111-111111111111"
CLAIM = "22222222-2222-4222-8222-222222222222"
ROW = {"id": OFFER, "status": "pending", "claim_id": CLAIM, "online_epoch": 7, "controller_session_id": "s"}
DRIVER = {"id": "d1", "user_id": "u1"}


@pytest.fixture()
def fx():
    mocks = {
        "resolve": AsyncMock(),
        "rows": AsyncMock(return_value=[ROW]),
        "rate": AsyncMock(),
        "skip": AsyncMock(),
        "ws": AsyncMock(),
        "metric": patch.object(svc, "_metric_inc").start(),
        "redispatch": AsyncMock(),
        "insert": AsyncMock(),
    }
    patches = [
        patch.object(svc.driver_offer_repo, "resolve_offer", mocks["resolve"]),
        patch.object(svc.db_supabase, "get_rows", mocks["rows"]),
        patch.object(svc.db_supabase, "insert_one", mocks["insert"]),
        patch.object(svc, "_update_acceptance_rate", mocks["rate"]),
        patch.object(svc, "_set_offer_skip", mocks["skip"]),
        patch.object(svc.manager, "send_personal_message", mocks["ws"]),
        patch.object(svc, "_redispatch", mocks["redispatch"]),
    ]
    for p in patches:
        p.start()
    yield mocks
    patch.stopall()


def test_is_v2_offer():
    assert svc.is_v2_offer(ROW)
    assert svc.is_v2_offer({**ROW, "online_epoch": 0})
    assert not svc.is_v2_offer({**ROW, "online_epoch": None})
    assert not svc.is_v2_offer({"id": OFFER, "claim_id": None, "online_epoch": 1})
    assert not svc.is_v2_offer(None)


async def test_accept_legacy_offer_returns_none(fx):
    fx["rows"].return_value = [{**ROW, "online_epoch": None}]
    assert await svc.accept_offer_v2("r1", DRIVER, "s") is None
    fx["resolve"].assert_not_awaited()


async def test_accept_without_session_returns_none(fx):
    assert await svc.accept_offer_v2("r1", DRIVER, None) is None


async def test_accept_defaults_to_row_values_and_default_request_id(fx):
    fx["resolve"].return_value = {"code": "OK", "losers": []}
    await svc.accept_offer_v2("r1", DRIVER, "s")
    fx["resolve"].assert_awaited_once_with(
        OFFER, CLAIM, action="accept", request_id=f"accept:{OFFER}:s", expected_epoch=7, actor_session_id="s"
    )
    fx["rate"].assert_awaited_once_with("d1", True)


async def test_accept_uses_client_values(fx):
    fx["resolve"].return_value = {"code": "OK"}
    body = {"offer_id": OFFER, "claim_id": CLAIM, "online_epoch": "9", "request_id": "client-1"}
    await svc.accept_offer_v2("r1", DRIVER, "s", body)
    assert fx["resolve"].await_args.kwargs["expected_epoch"] == 9
    assert fx["resolve"].await_args.kwargs["request_id"] == "client-1"


async def test_accept_replay_and_flag_off(fx):
    fx["resolve"].return_value = {"code": "OK", "replayed": True}
    assert (await svc.accept_offer_v2("r1", DRIVER, "s"))["replayed"] is True
    fx["rate"].assert_not_awaited()
    fx["resolve"].return_value = {"code": "AVAILABILITY_V2_DISABLED"}
    assert await svc.accept_offer_v2("r1", DRIVER, "s") is None


async def test_decline_side_effects_and_redispatch(fx):
    fx["resolve"].return_value = {"code": "OK", "remaining_pending_offers": 0, "ride_status": "searching"}
    await svc.decline_offer_v2("r1", DRIVER, "s")
    fx["rate"].assert_awaited_once_with("d1", False)
    fx["insert"].assert_awaited_once()
    fx["skip"].assert_awaited_once_with("r1", "d1")
    fx["redispatch"].assert_awaited_once_with("r1")


async def test_decline_already_resolved_has_no_side_effects(fx):
    fx["resolve"].return_value = {"code": "OFFER_ALREADY_RESOLVED", "outcome": "declined"}
    result = await svc.decline_offer_v2("r1", DRIVER, "s")
    assert result["code"] == "OFFER_ALREADY_RESOLVED"
    fx["rate"].assert_not_awaited()
    fx["redispatch"].assert_not_awaited()


async def test_expire_nonresponse_counts_miss_and_notifies(fx):
    fx["resolve"].return_value = {
        "code": "OK",
        "outcome": "expired_nonresponse",
        "miss_counted": True,
        "paused": False,
        "driver_id": "d1",
        "driver_user_id": "u1",
        "ride_id": "r1",
        "claim_id": CLAIM,
    }
    assert await svc.expire_offer_v2(ROW, 3) is True
    assert fx["resolve"].await_args.kwargs == {"action": "expire", "request_id": f"expire:{OFFER}", "miss_threshold": 3}
    fx["rate"].assert_awaited_once_with("d1", False)
    fx["metric"].assert_called_once_with("spinr_dispatch_offer_terminal_total", {"outcome": "expired_nonresponse"})
    payload = fx["ws"].await_args.args[0]
    assert payload == {
        "type": "ride_offer_expired",
        "ride_id": "r1",
        "offer_id": OFFER,
        "claim_id": CLAIM,
        "outcome": "expired_nonresponse",
    }


async def test_expire_uncounted_outcome_skips_acceptance_rate(fx):
    fx["resolve"].return_value = {"code": "OK", "outcome": "expired_availability_changed", "miss_counted": False}
    assert await svc.expire_offer_v2(ROW, 3) is True
    fx["rate"].assert_not_awaited()


async def test_expire_pause_sends_availability_changed_then_auto_offline(fx):
    fx["resolve"].return_value = {
        "code": "OK",
        "outcome": "expired_nonresponse",
        "miss_counted": True,
        "paused": True,
        "miss_streak": 3,
        "driver_id": "d1",
        "driver_user_id": "u1",
        "ride_id": "r1",
        "availability": {"online_epoch": "8", "state_version": "4", "server_time": "t"},
    }
    await svc.expire_offer_v2(ROW, 3)
    types = [c.args[0]["type"] for c in fx["ws"].await_args_list]
    assert types == ["availability_changed", "auto_offline"]
    assert fx["ws"].await_args_list[1].args[0]["reason"] == "missed_offers"


@pytest.mark.parametrize("result", [{"code": "OK", "replayed": True}, {"code": "OFFER_ALREADY_RESOLVED"}])
async def test_expire_lost_or_replayed_has_no_side_effects(fx, result):
    fx["resolve"].return_value = result
    assert await svc.expire_offer_v2(ROW, 3) is False
    fx["rate"].assert_not_awaited()
    fx["ws"].assert_not_awaited()
    fx["metric"].assert_not_called()


async def test_release_preempted_losers(fx):
    fx["resolve"].side_effect = [{"code": "OK", "driver_user_id": "u2"}, {"code": "OK", "replayed": True}]
    losers = [{"offer_id": OFFER, "claim_id": CLAIM, "driver_id": "d2"}, {"offer_id": CLAIM, "claim_id": OFFER}]
    await svc.release_preempted_losers(losers, "r1")
    assert fx["resolve"].await_args_list[0].kwargs == {
        "action": "cancel_unaccepted",
        "request_id": f"preempted:{OFFER}",
    }
    fx["ws"].assert_awaited_once_with({"type": "ride_taken", "ride_id": "r1"}, "driver_u2")


async def test_release_cancelled_offer(fx):
    fx["resolve"].return_value = {"code": "OK"}
    await svc.release_cancelled_offer_v2(ROW)
    fx["resolve"].assert_awaited_once_with(OFFER, CLAIM, action="cancel_unaccepted", request_id=f"cancel:{OFFER}")
