"""T11-B1: stop_requests_for_session_end (backend design A1/X5, C8/C9)."""

from unittest.mock import AsyncMock

import pytest

USER = "u1"
DRIVER = "d1"
SESSION = "sess-old"


def _snapshot(**driver_overrides):
    driver = {
        "id": DRIVER,
        "is_online": True,
        "online_epoch": 7,
        "controller_session_id": SESSION,
    }
    driver.update(driver_overrides)
    return {"protocol_enabled": True, "driver": driver}


@pytest.fixture
def svc(monkeypatch):
    from services import driver_session_end_service as mod

    monkeypatch.setattr(mod, "get_app_settings", AsyncMock(return_value={"driver_availability_v2_enabled": True}))
    monkeypatch.setattr(
        mod.driver_availability_repo, "get_driver_availability_snapshot", AsyncMock(return_value=_snapshot())
    )
    monkeypatch.setattr(
        mod.driver_availability_repo,
        "transition_driver_availability",
        AsyncMock(
            return_value={
                "code": "OK",
                "is_online": False,
                "online_epoch": 8,
                "state_version": 3,
                "server_time": "2026-09-24T00:00:00Z",
            }
        ),
    )
    monkeypatch.setattr(mod, "clear_scoped_driver_presence", AsyncMock())
    monkeypatch.setattr(mod.manager, "send_personal_message", AsyncMock())
    return mod


async def _run(mod, cause="logout", ended=SESSION):
    return await mod.stop_requests_for_session_end(USER, cause=cause, ended_session_id=ended)


@pytest.mark.asyncio
async def test_logout_stops_through_system_logout_actor(svc):
    assert await _run(svc) == "stopped"
    svc.driver_availability_repo.transition_driver_availability.assert_awaited_once_with(
        DRIVER, 7, "system:logout", "stop_requests", f"logout:{SESSION}:7"
    )
    svc.clear_scoped_driver_presence.assert_awaited_once_with(DRIVER, SESSION, 7)
    payload, room = svc.manager.send_personal_message.await_args.args
    assert room == f"driver_{USER}"
    assert payload == {
        "type": "availability_changed",
        "online_epoch": 8,
        "state_version": 3,
        "reason_code": "OFFLINE_INTENT",
        "server_time": "2026-09-24T00:00:00Z",
    }


@pytest.mark.asyncio
async def test_obligation_keeps_online_and_reports_requests_stopped(svc):
    svc.driver_availability_repo.transition_driver_availability.return_value = {"code": "OK", "is_online": True}
    assert await _run(svc) == "stopped"
    payload, _room = svc.manager.send_personal_message.await_args.args
    assert payload["reason_code"] == "REQUESTS_STOPPED"


@pytest.mark.asyncio
async def test_logout_all_request_id_uses_all(svc):
    assert await _run(svc, cause="logout-all", ended=None) == "stopped"
    args = svc.driver_availability_repo.transition_driver_availability.await_args.args
    assert args[4] == "logout-all:all:7"


@pytest.mark.asyncio
async def test_request_id_capped_at_128(svc):
    await _run(svc, cause="superseded", ended="x" * 300)
    args = svc.driver_availability_repo.transition_driver_availability.await_args.args
    assert len(args[4]) == 128


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings",
    [{}, {"driver_availability_v2_enabled": False}, None],
)
async def test_flag_off_or_missing_is_legacy(svc, settings):
    svc.get_app_settings.return_value = settings
    assert await _run(svc) == "legacy"
    svc.driver_availability_repo.transition_driver_availability.assert_not_awaited()


@pytest.mark.asyncio
async def test_settings_error_is_legacy(svc):
    svc.get_app_settings.side_effect = RuntimeError("db down")
    assert await _run(svc) == "legacy"


@pytest.mark.asyncio
async def test_snapshot_protocol_disabled_is_legacy(svc):
    svc.driver_availability_repo.get_driver_availability_snapshot.return_value = {
        **_snapshot(),
        "protocol_enabled": False,
    }
    assert await _run(svc) == "legacy"


@pytest.mark.asyncio
async def test_no_controller_is_legacy(svc):
    svc.driver_availability_repo.get_driver_availability_snapshot.return_value = _snapshot(controller_session_id=None)
    assert await _run(svc) == "legacy"


@pytest.mark.asyncio
async def test_no_driver_or_offline_is_skipped(svc):
    svc.driver_availability_repo.get_driver_availability_snapshot.return_value = {
        "protocol_enabled": True,
        "driver": None,
    }
    assert await _run(svc) == "skipped"
    svc.driver_availability_repo.get_driver_availability_snapshot.return_value = _snapshot(is_online=False)
    assert await _run(svc) == "skipped"
    svc.driver_availability_repo.transition_driver_availability.assert_not_awaited()


@pytest.mark.asyncio
async def test_logout_from_non_controller_session_is_skipped(svc):
    assert await _run(svc, ended="other-session") == "skipped"
    svc.driver_availability_repo.transition_driver_availability.assert_not_awaited()


@pytest.mark.asyncio
async def test_superseded_stops_even_if_not_controller(svc):
    assert await _run(svc, cause="superseded", ended="other-session") == "stopped"


@pytest.mark.asyncio
async def test_stale_epoch_rereads_and_retries_once(svc):
    repo = svc.driver_availability_repo
    repo.get_driver_availability_snapshot.side_effect = [_snapshot(), _snapshot(online_epoch=9)]
    repo.transition_driver_availability.side_effect = [
        {"code": "ONLINE_EPOCH_STALE"},
        {"code": "OK", "is_online": False},
    ]
    assert await _run(svc) == "stopped"
    second = repo.transition_driver_availability.await_args_list[1].args
    assert second[1] == 9 and second[4] == f"logout:{SESSION}:9"


@pytest.mark.asyncio
async def test_stale_retry_rechecks_step_two(svc):
    repo = svc.driver_availability_repo
    repo.get_driver_availability_snapshot.side_effect = [_snapshot(), _snapshot(is_online=False)]
    repo.transition_driver_availability.return_value = {"code": "ONLINE_EPOCH_STALE"}
    assert await _run(svc) == "skipped"
    assert repo.transition_driver_availability.await_count == 1


@pytest.mark.asyncio
async def test_second_stale_is_failed(svc):
    svc.driver_availability_repo.transition_driver_availability.return_value = {"code": "ONLINE_EPOCH_STALE"}
    assert await _run(svc) == "failed"
    assert svc.driver_availability_repo.transition_driver_availability.await_count == 2


@pytest.mark.asyncio
async def test_other_code_or_exception_is_failed(svc):
    svc.driver_availability_repo.transition_driver_availability.return_value = {"code": "INVALID_SYSTEM_ACTOR"}
    assert await _run(svc) == "failed"
    svc.driver_availability_repo.transition_driver_availability.side_effect = RuntimeError("boom")
    assert await _run(svc) == "failed"
    svc.driver_availability_repo.get_driver_availability_snapshot.side_effect = RuntimeError("boom")
    assert await _run(svc) == "failed"
    svc.manager.send_personal_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_presence_and_ws_failures_do_not_change_result(svc):
    svc.clear_scoped_driver_presence.side_effect = RuntimeError("redis down")
    svc.manager.send_personal_message.side_effect = RuntimeError("ws down")
    assert await _run(svc) == "stopped"


@pytest.mark.asyncio
async def test_replayed_stop_does_not_renotify(svc):
    svc.driver_availability_repo.transition_driver_availability.return_value = {"code": "OK", "replayed": True}
    assert await _run(svc) == "stopped"
    svc.manager.send_personal_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_cause_rejected(svc):
    with pytest.raises(ValueError):
        await svc.stop_requests_for_session_end(USER, cause="bogus", ended_session_id=None)
