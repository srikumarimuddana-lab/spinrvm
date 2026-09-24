"""confirm_driver_ready repo + service wrappers (T12-3)."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from backend.repositories import driver_availability_repo as repo
from backend.services import driver_availability_service as service

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def _raw(enabled=True):
    return {
        "protocol_enabled": enabled,
        "driver_count": 1,
        "server_time": NOW.isoformat(),
        "driver": {"id": "drv-1", "user_id": "user-1", "online_epoch": 5},
    }


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"reason": "bogus"}, "reason"),
        ({"expected_epoch": None}, "expected_epoch"),
        ({"expected_epoch": -1}, "expected_epoch"),
        ({"session": "system:readiness"}, "system"),
        ({"request_id": "x" * 129}, "request_id"),
    ],
)
async def test_repo_confirm_rejects_bad_arguments(kwargs, match):
    args = {"expected_epoch": 5, "session": "sess-1", "request_id": "r-1", "reason": "still_ready", **kwargs}
    with pytest.raises(ValueError, match=match):
        await repo.confirm_driver_ready(
            "drv-1", args["expected_epoch"], args["session"], args["request_id"], reason=args["reason"]
        )


async def test_repo_confirm_trip_completed_allows_null_epoch():
    rpc = AsyncMock(return_value={"code": "OK"})
    with (
        patch.object(repo, "_rpc_object", rpc),
        patch.object(repo, "invalidate_driver_cache", AsyncMock()) as invalidate,
    ):
        result = await repo.confirm_driver_ready("drv-1", None, "sess-1", "trip-completed:r1", reason="trip_completed")
    assert result == {"code": "OK"}
    params = rpc.await_args.args[1]
    assert params["p_expected_epoch"] is None
    assert params["p_reason"] == "trip_completed"
    invalidate.assert_awaited_once()


async def test_repo_reconcile_and_list_validate():
    with pytest.raises(ValueError):
        await repo.reconcile_driver_readiness("drv-1", 5, "bogus", "r")
    with pytest.raises(ValueError):
        await repo.list_availability_reconcile_candidates(0)
    with pytest.raises(ValueError):
        await repo.claim_readiness_prompt("drv-1", -1, "2026-01-01T00:00:00+00:00")


@pytest.mark.parametrize(
    "command",
    [
        {"action": "go_online", "online_epoch": "5", "request_id": "r"},
        {"action": "confirm_ready", "online_epoch": 5, "request_id": "r"},
        {"action": "confirm_ready", "online_epoch": "-5", "request_id": "r"},
        {"action": "confirm_ready", "online_epoch": "5", "request_id": ""},
        {"action": "confirm_ready", "online_epoch": "5", "request_id": "x" * 129},
    ],
)
async def test_service_rejects_invalid_command(command):
    with patch.object(service.driver_availability_repo, "confirm_driver_ready", AsyncMock()) as rpc:
        result = await service.confirm_driver_ready("user-1", command, "sess-1")
    assert result == {"code": "INVALID_AVAILABILITY_COMMAND"}
    rpc.assert_not_awaited()


async def test_service_system_session_is_superseded():
    command = {"action": "confirm_ready", "online_epoch": "5", "request_id": "r"}
    assert (await service.confirm_driver_ready("user-1", command, "system:readiness"))["code"] == "SESSION_SUPERSEDED"
    assert (await service.confirm_driver_ready("user-1", command, None))["code"] == "SESSION_SUPERSEDED"


async def test_service_ok_returns_snapshot_plus_code():
    command = {"action": "confirm_ready", "online_epoch": "5", "request_id": " r-1 "}
    rpc = AsyncMock(return_value={"code": "OK", "ready_until": "later"})
    with (
        patch.object(
            service.driver_availability_repo, "get_driver_availability_snapshot", AsyncMock(return_value=_raw())
        ),
        patch.object(service.driver_availability_repo, "confirm_driver_ready", rpc),
        patch.object(service, "get_driver_availability", AsyncMock(return_value={"online_epoch": "5"})),
    ):
        result = await service.confirm_driver_ready("user-1", command, "sess-1")
    assert result == {"online_epoch": "5", "code": "OK"}
    assert rpc.await_args.args == ("drv-1", 5, "sess-1", "r-1")
    assert rpc.await_args.kwargs == {"reason": "still_ready"}


async def test_service_passes_through_rpc_errors_and_disabled_flag():
    command = {"action": "confirm_ready", "online_epoch": "5", "request_id": "r"}
    with (
        patch.object(
            service.driver_availability_repo, "get_driver_availability_snapshot", AsyncMock(return_value=_raw())
        ),
        patch.object(
            service.driver_availability_repo,
            "confirm_driver_ready",
            AsyncMock(return_value={"code": "READINESS_EXPIRED", "online_epoch": "6"}),
        ),
    ):
        result = await service.confirm_driver_ready("user-1", command, "sess-1")
    assert result == {"code": "READINESS_EXPIRED", "online_epoch": "6"}
    with patch.object(
        service.driver_availability_repo, "get_driver_availability_snapshot", AsyncMock(return_value=_raw(False))
    ):
        assert (await service.confirm_driver_ready("user-1", command, "sess-1"))["code"] == "AVAILABILITY_V2_DISABLED"


async def test_service_rpc_exception_is_lookup_error():
    command = {"action": "confirm_ready", "online_epoch": "5", "request_id": "r"}
    with (
        patch.object(
            service.driver_availability_repo, "get_driver_availability_snapshot", AsyncMock(return_value=_raw())
        ),
        patch.object(service.driver_availability_repo, "confirm_driver_ready", AsyncMock(side_effect=RuntimeError)),
    ):
        with pytest.raises(service.AvailabilityLookupError):
            await service.confirm_driver_ready("user-1", command, "sess-1")
