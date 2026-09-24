"""driver_readiness_reconciler loop (T12-5)."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.utils import driver_readiness_reconciler as rec

pytestmark = pytest.mark.anyio

ROW = {"driver_id": "d1", "user_id": "u1", "online_epoch": "5", "ready_until": "2026-09-24T12:02:00+00:00"}


def _patches(settings=None, listed=None, reconcile=None, claim=None, settings_error=None, list_error=None):
    return (
        patch.object(
            rec,
            "get_app_settings",
            AsyncMock(
                return_value=settings if settings is not None else {"driver_availability_v2_enabled": True},
                side_effect=settings_error,
            ),
        ),
        patch.object(
            rec.driver_availability_repo,
            "list_availability_reconcile_candidates",
            AsyncMock(return_value=listed or {}, side_effect=list_error),
        ),
        patch.object(
            rec.driver_availability_repo,
            "reconcile_driver_readiness",
            AsyncMock(return_value=reconcile or {"code": "OK"}),
        ),
        patch.object(rec.driver_availability_repo, "claim_readiness_prompt", AsyncMock(return_value=claim)),
        patch.object(rec.manager, "send_personal_message", AsyncMock()),
        patch.object(rec, "send_push_notification", AsyncMock()),
    )


async def _tick(**kwargs):
    p = _patches(**kwargs)
    with p[0], p[1] as lst, p[2] as rpc, p[3] as claim, p[4] as ws, p[5] as push:
        stats = await rec.reconcile_tick()
    return stats, {"list": lst, "rpc": rpc, "claim": claim, "ws": ws, "push": push}


async def test_settings_failure_skips_tick():
    stats, m = await _tick(settings_error=RuntimeError("down"))
    assert stats["skipped"] == 1
    m["list"].assert_not_awaited()
    m["rpc"].assert_not_awaited()


async def test_list_failure_skips_tick_without_pausing():
    stats, m = await _tick(list_error=RuntimeError("down"))
    assert stats["skipped"] == 1
    m["rpc"].assert_not_awaited()


async def test_flag_off_does_nothing():
    stats, m = await _tick(settings={"driver_availability_v2_enabled": False})
    assert stats == {"paused": 0, "prompted": 0, "skipped": 0}
    m["list"].assert_not_awaited()


async def test_contact_gap_and_readiness_pauses_notify():
    result = {"code": "OK", "online_epoch": "6", "state_version": "9", "server_time": "t", "user_id": "u1"}
    stats, m = await _tick(listed={"contact_gap": [ROW], "readiness_due": [ROW]}, reconcile=result)
    assert stats["paused"] == 2
    calls = m["rpc"].await_args_list
    assert calls[0].args == ("d1", 5, "contact_gap", "contact-gap:d1:5")
    assert calls[1].args == ("d1", 5, "readiness", "readiness:d1:5")
    payloads = [c.args[0] for c in m["ws"].await_args_list]
    assert [p["reason_code"] for p in payloads] == ["PRESENCE_UNAVAILABLE", "READY_TIMEOUT"]
    assert payloads[0] == {
        "type": "availability_changed",
        "online_epoch": "6",
        "state_version": "9",
        "reason_code": "PRESENCE_UNAVAILABLE",
        "server_time": "t",
    }
    assert all(c.kwargs["priority"] == "normal" for c in m["push"].await_args_list)


@pytest.mark.parametrize("result", [{"code": "OK", "replayed": True}, {"code": "BUSY"}, {"code": "NOT_DUE"}])
async def test_replay_or_non_ok_sends_nothing(result):
    stats, m = await _tick(listed={"readiness_due": [ROW]}, reconcile=result)
    assert stats["paused"] == 0
    m["ws"].assert_not_awaited()
    m["push"].assert_not_awaited()


async def test_prompt_claimed_sends_ws_and_dispatch_push():
    stats, m = await _tick(listed={"prompt_due": [ROW], "server_time": "now"}, claim="u1")
    assert stats["prompted"] == 1
    m["claim"].assert_awaited_once_with("d1", 5, ROW["ready_until"])
    assert m["ws"].await_args.args[0] == {
        "type": "availability_readiness_prompt",
        "online_epoch": "5",
        "ready_until": ROW["ready_until"],
        "server_time": "now",
    }
    assert m["push"].await_args.kwargs["priority"] == "dispatch"


async def test_prompt_not_claimed_sends_nothing():
    stats, m = await _tick(listed={"prompt_due": [ROW]}, claim=None)
    assert stats["prompted"] == 0
    m["ws"].assert_not_awaited()


async def test_loop_runs_when_redis_lock_errors_and_records_heartbeat():
    calls = {"n": 0}

    async def _sleep(_s):
        calls["n"] += 1
        raise asyncio.CancelledError

    with (
        patch.object(rec, "try_acquire_leader_lock", AsyncMock(return_value=True)) as lock,
        patch.object(rec, "reconcile_tick", AsyncMock()) as tick,
        patch.object(rec, "_record_heartbeat") as hb,
        patch.object(rec.asyncio, "sleep", _sleep),
    ):
        with pytest.raises(asyncio.CancelledError):
            await rec.driver_readiness_reconciler_loop()
    lock.assert_awaited_once_with("driver_readiness_reconciler", 17)
    tick.assert_awaited_once()
    hb.assert_called_once_with("driver_readiness_reconciler (20s)")
