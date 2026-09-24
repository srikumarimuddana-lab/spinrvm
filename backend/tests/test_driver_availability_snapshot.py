from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend.services import driver_availability_service as service

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


def _driver(**overrides):
    return {
        "id": "drv-1",
        "user_id": "user-1",
        "status": "active",
        "is_online": False,
        "is_available": False,
        "accepting_requests": False,
        "online_epoch": 3,
        "state_version": 9,
        "controller_session_id": None,
        "availability_reason": "go_offline",
        "service_area_id": None,
        **overrides,
    }


def _raw(**overrides):
    return {
        "protocol_enabled": True,
        "driver_count": 1,
        "server_time": NOW.isoformat(),
        "driver": _driver(),
        "active_ride": None,
        "pending_offer": None,
        "offer_reconciliation_required": False,
        **overrides,
    }


@pytest.mark.anyio
async def test_snapshot_preserves_database_ordering_and_serializes_versions():
    with patch.object(
        service.driver_availability_repo, "get_driver_availability_snapshot", AsyncMock(return_value=_raw())
    ):
        snapshot = await service.get_driver_availability("user-1", "session-1")
    assert snapshot["state_version"] == "9"
    assert snapshot["online_epoch"] == "3"
    assert snapshot["snapshot_issued_at"] == NOW.isoformat()
    assert snapshot["availability_state"] == "offline"


@pytest.mark.anyio
async def test_snapshot_lookup_failure_is_not_converted_to_pending():
    with patch.object(
        service.driver_availability_repo,
        "get_driver_availability_snapshot",
        AsyncMock(side_effect=RuntimeError("db down")),
    ):
        with pytest.raises(service.AvailabilityLookupError):
            await service.get_driver_availability("user-1")


@pytest.mark.anyio
async def test_expired_pending_offer_is_not_actionable_and_requires_reconciliation():
    raw = _raw(offer_reconciliation_required=True)
    with patch.object(
        service.driver_availability_repo, "get_driver_availability_snapshot", AsyncMock(return_value=raw)
    ):
        snapshot = await service.get_driver_availability("user-1", "session-1")
    assert snapshot["pending_offer"] is None
    assert snapshot["offer_reconciliation_required"] is True
    assert snapshot["availability_state"] == "reconnecting"
    assert snapshot["reason_code"] == "RECOVERY_REQUIRED"


@pytest.mark.anyio
async def test_eligible_offline_driver_remains_offline():
    with patch.object(
        service.driver_availability_repo, "get_driver_availability_snapshot", AsyncMock(return_value=_raw())
    ):
        snapshot = await service.get_driver_availability("user-1", "session-1")
    assert snapshot["availability_state"] == "offline"
    assert snapshot["reason_code"] == "OFFLINE_INTENT"


@pytest.mark.anyio
async def test_active_trip_survives_reconnect_and_account_block():
    raw = _raw(
        driver=_driver(status="suspended", is_online=True),
        active_ride={"id": "ride-1", "status": "in_progress", "updated_at": NOW.isoformat()},
    )
    with patch.object(
        service.driver_availability_repo, "get_driver_availability_snapshot", AsyncMock(return_value=raw)
    ):
        snapshot = await service.get_driver_availability("user-1", "session-1")
    assert snapshot["availability_state"] == "paused"
    assert snapshot["reason_code"] == "ACTIVE_TRIP"
    assert snapshot["eligibility_reason"] == "ACCOUNT_SUSPENDED"
    assert snapshot["active_ride"]["id"] == "ride-1"


@pytest.mark.anyio
async def test_expired_document_blocks_eligible_driver():
    raw = _raw(driver=_driver(license_expiry_date=(NOW - timedelta(days=1)).date().isoformat()))
    with patch.object(
        service.driver_availability_repo, "get_driver_availability_snapshot", AsyncMock(return_value=raw)
    ):
        snapshot = await service.get_driver_availability("user-1", "session-1")
    assert snapshot["availability_state"] == "blocked"
    assert snapshot["reason_code"] == "LICENSE_EXPIRED"


@pytest.mark.anyio
async def test_successful_go_returns_snapshot_for_the_presented_session():
    before = _raw()
    after = _raw(
        driver=_driver(
            is_verified=True,
            is_online=True,
            is_available=True,
            accepting_requests=True,
            controller_session_id="presented-session",
            availability_reason="go_online",
            last_contact_at=NOW.isoformat(),
            ready_until=(NOW + timedelta(minutes=60)).isoformat(),
        )
    )
    with (
        patch.object(
            service.driver_availability_repo,
            "get_driver_availability_snapshot",
            AsyncMock(side_effect=[before, after]),
        ),
        patch.object(
            service.driver_availability_repo,
            "transition_driver_availability",
            AsyncMock(return_value={"code": "OK", "availability_reason": "go_online"}),
        ),
        patch.object(service, "_scoped_dispatch_evidence_fresh", AsyncMock(return_value=(True, None))),
    ):
        result = await service.change_driver_availability(
            "user-1",
            {"action": "go_online", "online_epoch": "3", "request_id": "go-1"},
            "presented-session",
        )
    assert result["code"] == "OK"
    assert result["availability_state"] == "ready"
    assert result["reason_code"] is None


@pytest.mark.anyio
async def test_snapshot_requires_scoped_contact_gps_and_fresh_durable_marker():
    driver = _driver(
        is_verified=True,
        is_online=True,
        is_available=True,
        accepting_requests=True,
        controller_session_id="session-1",
        online_epoch=3,
        last_contact_at=NOW.isoformat(),
        ready_until=(NOW + timedelta(minutes=60)).isoformat(),
        location_captured_at=NOW.isoformat(),
    )
    raw = _raw(driver=driver)
    evidence = {
        "contact_valid_until": NOW + timedelta(seconds=20),
        "location_valid_until": NOW + timedelta(seconds=20),
    }
    with (
        patch.object(service.driver_availability_repo, "get_driver_availability_snapshot", AsyncMock(return_value=raw)),
        patch("backend.utils.driver_presence.get_scoped_driver_presence", AsyncMock(return_value=evidence)) as read,
    ):
        snapshot = await service.get_driver_availability("user-1", "session-1")
    assert snapshot["availability_state"] == "ready"
    read.assert_awaited_once_with("drv-1", "session-1", 3)


@pytest.mark.anyio
async def test_snapshot_does_not_ready_with_fresh_redis_and_stale_stored_coordinates():
    driver = _driver(
        is_verified=True,
        is_online=True,
        is_available=True,
        accepting_requests=True,
        controller_session_id="session-1",
        last_contact_at=NOW.isoformat(),
        ready_until=(NOW + timedelta(minutes=60)).isoformat(),
        location_captured_at=(NOW - timedelta(seconds=61)).isoformat(),
    )
    raw = _raw(driver=driver)
    with (
        patch.object(service.driver_availability_repo, "get_driver_availability_snapshot", AsyncMock(return_value=raw)),
        patch("backend.utils.driver_presence.get_scoped_driver_presence", AsyncMock()),
    ):
        snapshot = await service.get_driver_availability("user-1", "session-1")
    assert snapshot["availability_state"] == "paused"
    assert snapshot["reason_code"] == "LOCATION_STALE"


@pytest.mark.anyio
async def test_snapshot_blocks_online_unverified_driver_with_specific_reason():
    driver = _driver(
        is_verified=False,
        is_online=True,
        is_available=True,
        accepting_requests=True,
        controller_session_id="session-1",
        last_contact_at=NOW.isoformat(),
        ready_until=(NOW + timedelta(minutes=60)).isoformat(),
    )
    with patch.object(
        service.driver_availability_repo, "get_driver_availability_snapshot", AsyncMock(return_value=_raw(driver=driver))
    ):
        snapshot = await service.get_driver_availability("user-1", "session-1")
    assert snapshot["availability_state"] == "blocked"
    assert snapshot["reason_code"] == "DRIVER_UNVERIFIED"
    assert snapshot["eligibility_reason"] == "DRIVER_UNVERIFIED"


@pytest.mark.anyio
async def test_policy_pause_rechecks_blocking_state_before_one_epoch_retry():
    first = _raw(driver=_driver(status="suspended", online_epoch=4, accepting_requests=True))
    second = _raw(driver=_driver(status="suspended", online_epoch=5, accepting_requests=True))
    with (
        patch.object(service, "_read_snapshot", AsyncMock(side_effect=[first, second])),
        patch.object(service.db_supabase, "get_rows", AsyncMock(return_value=[{"current_session_id": "current"}])),
        patch.object(
            service.driver_availability_repo,
            "transition_driver_availability",
            AsyncMock(side_effect=[{"code": "ONLINE_EPOCH_STALE"}, {"code": "OK"}]),
        ) as transition,
    ):
        result = await service.pause_driver_for_policy(
            "user-1", blocking_statuses={"suspended"}, request_id="admin-pause"
        )
    assert result["code"] == "OK"
    assert transition.await_count == 2
    first_call, retry_call = transition.await_args_list
    assert first_call.args[:4] == ("drv-1", 4, "current", "pause_policy")
    assert retry_call.args[:4] == ("drv-1", 5, "current", "pause_policy")
    assert retry_call.args[4] != "admin-pause"


@pytest.mark.anyio
async def test_policy_pause_does_not_adopt_epoch_after_policy_cleared():
    first = _raw(driver=_driver(status="suspended", online_epoch=4, accepting_requests=True))
    retry = _raw(driver=_driver(status="active", online_epoch=5, accepting_requests=True))
    with (
        patch.object(service, "_read_snapshot", AsyncMock(side_effect=[first, retry])),
        patch.object(service.db_supabase, "get_rows", AsyncMock(return_value=[{"current_session_id": "current"}])),
        patch.object(
            service.driver_availability_repo,
            "transition_driver_availability",
            AsyncMock(return_value={"code": "ONLINE_EPOCH_STALE"}),
        ) as transition,
    ):
        result = await service.pause_driver_for_policy(
            "user-1", blocking_statuses={"suspended"}, request_id="admin-pause"
        )
    assert result["code"] == "POLICY_STATE_CHANGED"
    transition.assert_awaited_once()


@pytest.mark.anyio
async def test_status_snapshot_lookup_failure_maps_to_503_contract():
    from backend.routes.drivers import status

    with patch(
        "backend.services.driver_availability_service.get_driver_availability",
        AsyncMock(side_effect=service.AvailabilityLookupError("db down")),
    ):
        with pytest.raises(HTTPException) as error:
            await status.get_my_availability(current_user={"id": "user-1"}, token_session_id="session-1")
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "ELIGIBILITY_UNAVAILABLE"


@pytest.mark.anyio
async def test_status_snapshot_timeout_maps_to_503_contract():
    from backend.routes.drivers import status

    with patch(
        "backend.services.driver_availability_service.get_driver_availability",
        AsyncMock(side_effect=TimeoutError()),
    ):
        with pytest.raises(HTTPException) as error:
            await status.get_my_availability(current_user={"id": "user-1"}, token_session_id="session-1")
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "ELIGIBILITY_UNAVAILABLE"


@pytest.mark.anyio
async def test_enabled_status_write_requires_explicit_epoch_and_request_id():
    from backend.routes.drivers import status

    with (
        patch.object(status, "_availability_v2_enabled", AsyncMock(return_value=True)),
        patch.object(
            status.db_supabase, "get_driver_by_id", AsyncMock(return_value=_driver(id="drv-1", user_id="user-1"))
        ),
    ):
        with pytest.raises(HTTPException) as error:
            await status.update_driver_status(driver_id="drv-1", is_online=False, current_user={"id": "user-1"})
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "AVAILABILITY_UPGRADE_REQUIRED"


@pytest.mark.anyio
async def test_enabled_stop_uses_presented_token_session_and_keeps_trip_online():
    from backend.routes.drivers import status

    with (
        patch.object(status, "_availability_v2_enabled", AsyncMock(return_value=True)),
        patch.object(
            status.db_supabase, "get_driver_by_id", AsyncMock(return_value=_driver(id="drv-1", user_id="user-1"))
        ),
        patch.object(
            status,
            "_change_availability_status",
            AsyncMock(
                return_value={
                    "code": "OK",
                    "is_online": True,
                    "transition": {"availability_reason": "stop_requests"},
                }
            ),
        ) as change,
    ):
        result = await status.update_driver_status(
            driver_id="drv-1",
            is_online=False,
            current_user={"id": "user-1", "current_session_id": "db-session"},
            token_session_id="presented-session",
            online_epoch="3",
            request_id="request-1",
            availability_action="stop_requests",
        )
    change.assert_awaited_once_with(
        "user-1",
        {"action": "stop_requests", "online_epoch": "3", "request_id": "request-1"},
        "presented-session",
    )
    assert result["is_online"] is True


@pytest.mark.anyio
async def test_v2_idempotent_go_does_not_write_unfenced_coordinates():
    """Go Online coordinates must go through captured-time fenced location APIs."""
    from backend.routes.drivers import status
    from backend.utils import driver_presence

    driver = _driver(is_online=True, lat=51.0, lng=-105.0)
    committed = {
        "code": "OK",
        "is_online": True,
        "is_available": True,
        "accepting_requests": True,
        "online_epoch": "3",
        "state_version": "10",
        "transition": {
            "online_epoch": "3",
            "state_version": "10",
            "availability_reason": "go_online",
            "is_online": True,
        },
    }
    with (
        patch.object(status, "_availability_v2_enabled", AsyncMock(return_value=True)),
        patch.object(status.db_supabase, "get_driver_by_id", AsyncMock(return_value=driver)),
        patch.object(status.db_supabase, "get_rows", AsyncMock(return_value=[])),
        patch.object(status.db_supabase, "update_one", AsyncMock()) as raw_update,
        patch("backend.settings_loader.get_app_settings", AsyncMock(return_value={})),
        patch("backend.utils.spinr_pass.assert_quota_available", AsyncMock()),
        patch.object(status, "_change_availability_status", AsyncMock(return_value=committed)),
        patch.object(driver_presence, "renew_driver_presence", AsyncMock(return_value={"status": "renewed"})),
        patch.object(status, "reset_miss_streak", AsyncMock()),
    ):
        await status.update_driver_status(
            driver_id="drv-1",
            is_online=True,
            lat=52.0,
            lng=-106.0,
            current_user={"id": "user-1"},
            token_session_id="session-1",
            online_epoch="3",
            request_id="retry-1",
            availability_action="go_online",
        )
    raw_update.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "snapshot",
    [
        {"online_epoch": "6", "state_version": "15", "is_online": False, "accepting_requests": False},
        {
            "online_epoch": "5",
            "state_version": "15",
            "is_online": True,
            "accepting_requests": True,
            "is_available": False,
            "pending_offer": {"id": "offer-1"},
        },
    ],
    ids=["newer-stop", "newer-offer"],
)
async def test_replayed_go_does_not_restore_stale_presence_or_miss_streak(snapshot):
    from backend.routes.drivers import status

    result = {
        "code": "OK",
        "transition": {
            "availability_reason": "go_online",
            "online_epoch": "5",
            "state_version": "14",
            "is_online": True,
            "accepting_requests": True,
        },
        "accepting_requests": True,
        "is_available": True,
        "active_ride": None,
        "pending_offer": None,
        **snapshot,
    }
    with (
        patch.object(status._deps, "mark_present", AsyncMock()) as mark_present,
        patch.object(status, "reset_miss_streak", AsyncMock()) as reset_misses,
        patch.object(status._deps, "clear_presence", AsyncMock()) as clear_presence,
    ):
        await status._finish_v2_status(result, "drv-1")
    mark_present.assert_not_awaited()
    reset_misses.assert_not_awaited()
    clear_presence.assert_not_awaited()


@pytest.mark.anyio
async def test_matching_go_transition_still_publishes_fresh_presence():
    from backend.routes.drivers import status

    result = {
        "code": "OK",
        "online_epoch": "5",
        "state_version": "14",
        "is_online": True,
        "accepting_requests": True,
        "is_available": True,
        "active_ride": None,
        "pending_offer": None,
        "offer_reconciliation_required": False,
        "transition": {
            "availability_reason": "go_online",
            "online_epoch": "5",
            "state_version": "14",
            "is_online": True,
            "accepting_requests": True,
        },
    }
    with (
        patch.object(status._deps, "mark_present", AsyncMock()) as mark_present,
        patch(
            "backend.utils.driver_presence.renew_driver_presence", AsyncMock(return_value={"status": "renewed"})
        ) as renew,
        patch.object(status, "reset_miss_streak", AsyncMock()) as reset_misses,
    ):
        await status._finish_v2_status(result, "drv-1", "session-1")
    mark_present.assert_not_awaited()
    renew.assert_awaited_once_with("drv-1", "session-1", 5)
    reset_misses.assert_awaited_once_with("drv-1")
