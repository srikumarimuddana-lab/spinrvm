"""Online-idle live delivery must not depend on durable history being enabled."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError

from routes.drivers import location


def test_live_position_rejects_zero_sentinel():
    with pytest.raises(ValidationError):
        location.LiveLocationRequest(lat=0, lng=0, captured_at=datetime.now(timezone.utc))


@pytest.mark.parametrize(
    "online,age,enabled,status",
    [
        (True, 0, True, None),
        (False, 0, True, 409),
        (True, 61, True, 422),
        (True, -10, True, 422),
        (True, 0, False, None),
        (False, 0, False, 409),
        (True, 61, False, 422),
        (True, -10, False, 422),
    ],
)
def test_live_position_without_idle_history(monkeypatch, online, age, enabled, status):
    async def rows(table, filters, **kwargs):
        if table == "drivers":
            assert filters == {"user_id": "user-1"}
            return [{"id": "driver-1", "is_online": online}]
        return []

    monkeypatch.setattr(location.db_supabase, "get_rows", rows)
    monkeypatch.setattr(
        "settings_loader.get_app_settings",
        AsyncMock(return_value={"background_location_fanout_enabled": enabled, "idle_location_v2_enabled": False}),
    )
    apply = AsyncMock()
    presence = AsyncMock()
    monkeypatch.setattr(location._deps, "mark_present", presence)
    monkeypatch.setattr(location, "_apply_v2_live_marker_update", apply)
    guard = AsyncMock()
    monkeypatch.setattr(location, "_guard_revoked_session", guard)
    tasks = BackgroundTasks()
    point = location.LiveLocationRequest(
        lat=50.45, lng=-104.6, captured_at=datetime.now(timezone.utc) - timedelta(seconds=age)
    )
    call = location.update_live_location(point, tasks, current_user={"id": "user-1"}, token_session_id="session-1")
    if status:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(call)
        assert exc.value.status_code == status
    else:
        result = asyncio.run(call)
        assert result["accepted"] is True
        asyncio.run(tasks())
    assert apply.await_count == int(status is None)
    assert presence.await_count == int(status is None)
    if status is None:
        presence.assert_awaited_once_with("driver-1")
    guard.assert_awaited_once_with("session-1")


@pytest.mark.parametrize("revoked,status", [(False, 403), (True, 401)])
def test_live_presence_rejects_missing_driver_or_revoked_session(monkeypatch, revoked, status):
    monkeypatch.setattr(location.db_supabase, "get_rows", AsyncMock(return_value=[]))
    monkeypatch.setattr("settings_loader.get_app_settings", AsyncMock(return_value={}))
    monkeypatch.setattr(
        location,
        "_guard_revoked_session",
        AsyncMock(side_effect=HTTPException(status_code=401) if revoked else None),
    )
    presence = AsyncMock()
    monkeypatch.setattr(location._deps, "mark_present", presence)
    tasks = BackgroundTasks()
    point = location.LiveLocationRequest(lat=50.45, lng=-104.6, captured_at=datetime.now(timezone.utc))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            location.update_live_location(
                point,
                tasks,
                current_user={"id": "user-1"},
                token_session_id="session-1",
            )
        )
    assert exc.value.status_code == status
    presence.assert_not_awaited()
    assert not tasks.tasks


def test_live_endpoint_updates_marker_with_fanout_disabled(monkeypatch):
    async def rows(table, filters, **kwargs):
        return [{"id": "driver-1", "is_online": True}] if table == "drivers" else []

    monkeypatch.setattr(location.db_supabase, "get_rows", rows)
    monkeypatch.setattr("settings_loader.get_app_settings", AsyncMock(return_value={}))
    monkeypatch.setattr(location, "_guard_revoked_session", AsyncMock())
    presence = AsyncMock()
    monkeypatch.setattr(location._deps, "mark_present", presence)
    monkeypatch.setattr("utils.location_integrity.check_location_integrity", AsyncMock(return_value=(True, "ok")))
    monkeypatch.setattr(location, "_newer_than_last_written_marker", AsyncMock(return_value=True))
    write = AsyncMock()
    send = AsyncMock()
    monkeypatch.setattr(location, "_write_marker_if_due", write)
    monkeypatch.setattr(location._deps.manager, "send_personal_message", send)
    tasks = BackgroundTasks()
    point = location.LiveLocationRequest(lat=50.45, lng=-104.6, captured_at=datetime.now(timezone.utc))
    result = asyncio.run(
        location.update_live_location(
            point,
            tasks,
            current_user={"id": "user-1"},
            token_session_id="session-1",
        )
    )
    assert result["accepted"] is True
    asyncio.run(tasks())
    write.assert_awaited_once()
    assert write.await_args.args[1]["lat"] == point.lat
    assert write.await_args.args[1]["lng"] == point.lng
    send.assert_not_awaited()
    presence.assert_awaited_once_with("driver-1")


def test_v2_live_location_requires_presented_epoch_without_legacy_renewal(monkeypatch):
    monkeypatch.setattr(location, "_guard_revoked_session", AsyncMock())
    monkeypatch.setattr(location, "_availability_v2_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(location, "_current_online_epoch", AsyncMock(return_value="12"))
    monkeypatch.setattr(
        location.db_supabase,
        "get_rows",
        AsyncMock(return_value=[{"id": "driver-1", "is_online": True}]),
    )
    legacy_presence = AsyncMock()
    monkeypatch.setattr(location._deps, "mark_present", legacy_presence)
    point = location.LiveLocationRequest(lat=50.45, lng=-104.6, captured_at=datetime.now(timezone.utc))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            location.update_live_location(
                point,
                BackgroundTasks(),
                current_user={"id": "user-1"},
                token_session_id="session-1",
            )
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == {
        "code": "AVAILABILITY_UPGRADE_REQUIRED",
        "reason_code": "ONLINE_EPOCH_REQUIRED",
        "online_epoch": "12",
    }
    legacy_presence.assert_not_awaited()


def test_active_trip_location_keeps_uploading_after_epoch_reconcile_conflict(monkeypatch):
    async def rows(table, filters, **kwargs):
        if table == "drivers":
            return [{"id": "driver-1", "is_online": True}]
        if table == "rides":
            return [{"id": "ride-1", "status": "in_progress"}]
        return [{"driver_availability_v2_enabled": True}]

    monkeypatch.setattr(location.db_supabase, "get_rows", rows)
    monkeypatch.setattr(location, "_guard_revoked_session", AsyncMock())
    monkeypatch.setattr(location, "_availability_v2_enabled", AsyncMock(return_value=True))
    renewal = AsyncMock(return_value={"status": "stale_epoch", "code": "CONTACT_GAP", "online_epoch": "13"})
    monkeypatch.setattr(location, "renew_scoped_presence", renewal)
    apply = AsyncMock()
    monkeypatch.setattr(location, "_apply_v2_live_marker_update", apply)
    point = location.LiveLocationRequest(
        lat=50.45,
        lng=-104.6,
        captured_at=datetime.now(timezone.utc),
        online_epoch="12",
    )
    tasks = BackgroundTasks()
    result = asyncio.run(
        location.update_live_location(
            point,
            tasks,
            current_user={"id": "user-1"},
            token_session_id="session-1",
        )
    )
    assert result == {"accepted": True, "online_epoch": "13", "presence_code": "ONLINE_EPOCH_STALE"}
    renewal.assert_awaited_once_with("driver-1", "session-1", 12)
    asyncio.run(tasks())
    apply.assert_awaited_once()
    assert apply.await_args.kwargs["availability_v2"] is True
    assert apply.await_args.kwargs["online_epoch"] == 12


def test_v2_offline_live_location_uses_structured_conflict(monkeypatch):
    monkeypatch.setattr(location, "_guard_revoked_session", AsyncMock())
    monkeypatch.setattr(location, "_availability_v2_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(location, "_current_online_epoch", AsyncMock(return_value="13"))
    monkeypatch.setattr(
        location.db_supabase,
        "get_rows",
        AsyncMock(return_value=[{"id": "driver-1", "is_online": False}]),
    )
    presence = AsyncMock()
    monkeypatch.setattr(location._deps, "mark_present", presence)
    point = location.LiveLocationRequest(lat=50.45, lng=-104.6, captured_at=datetime.now(timezone.utc))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            location.update_live_location(
                point,
                BackgroundTasks(),
                current_user={"id": "user-1"},
                token_session_id="session-1",
            )
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == {"code": "DRIVER_OFFLINE", "online_epoch": "13"}
    presence.assert_not_awaited()


def test_presence_epoch_rejects_oversized_unicode_and_negative_values(monkeypatch):
    monkeypatch.setattr(location, "_availability_v2_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(location, "_current_online_epoch", AsyncMock(return_value="12"))
    for value in ("9" * 5000, "١٢", -1):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(location._require_presence_epoch("user-1", "session-1", value))
        assert exc.value.status_code == 409
        assert exc.value.detail == {
            "code": "AVAILABILITY_UPGRADE_REQUIRED",
            "reason_code": "ONLINE_EPOCH_REQUIRED",
            "online_epoch": "12",
        }


def test_delayed_v2_callback_cannot_write_marker_or_fanout_after_epoch_changes(monkeypatch):
    captured_at = datetime.now(timezone.utc)
    monkeypatch.setattr(
        "utils.location_integrity.check_location_integrity",
        AsyncMock(return_value=(True, "ok")),
    )
    renewal = AsyncMock(return_value={"status": "stale_epoch", "code": "ONLINE_EPOCH_STALE", "online_epoch": "13"})
    monkeypatch.setattr(location, "renew_scoped_presence", renewal)
    marker = AsyncMock()
    monkeypatch.setattr(location, "_write_marker_if_due", marker)
    delivery = AsyncMock()
    monkeypatch.setattr(location._deps.manager, "send_personal_message", delivery)

    asyncio.run(
        location._apply_v2_live_marker_update(
            "driver-1",
            "ride-1",
            50.45,
            -104.6,
            None,
            None,
            8,
            False,
            True,
            captured_at,
            token_session_id="session-1",
            online_epoch=12,
            availability_v2=True,
        )
    )

    renewal.assert_awaited_once_with("driver-1", "session-1", 12, location_captured_at=captured_at)
    marker.assert_not_awaited()
    delivery.assert_not_awaited()


def test_v2_marker_writer_passes_atomic_session_epoch_fence(monkeypatch):
    monkeypatch.setattr(location, "should_write_marker", AsyncMock(return_value=True))
    write = AsyncMock(return_value=True)
    monkeypatch.setattr(location.db_supabase, "update_driver_location", write)
    captured_at = datetime.now(timezone.utc)
    result = asyncio.run(
        location._write_marker_if_due(
            {"id": "driver-1"},
            {"lat": 50.45, "lng": -104.6, "location_captured_at": captured_at},
            "driver-1",
            "rest_v2_trip",
            presence_session_id="session-1",
            presence_epoch=12,
        )
    )
    assert result is True
    assert write.await_args.kwargs["authenticated_session_id"] == "session-1"
    assert write.await_args.kwargs["online_epoch"] == 12


def test_redis_outage_does_not_drop_current_active_trip_live_marker(monkeypatch):
    captured_at = datetime.now(timezone.utc)
    monkeypatch.setattr(
        "utils.location_integrity.check_location_integrity",
        AsyncMock(return_value=(True, "ok")),
    )
    monkeypatch.setattr(
        location,
        "renew_scoped_presence",
        AsyncMock(return_value={"status": "unavailable", "code": "PRESENCE_UNAVAILABLE"}),
    )
    marker = AsyncMock(return_value=True)
    monkeypatch.setattr(location, "_write_marker_if_due", marker)
    monkeypatch.setattr(
        "settings_loader.get_app_settings",
        AsyncMock(return_value={"background_location_fanout_enabled": True}),
    )
    async def rows(table, filters, **kwargs):
        if table == "rides":
            return [{"id": "ride-1", "driver_id": "driver-1", "rider_id": "rider-1", "status": "in_progress"}]
        return []

    monkeypatch.setattr(location.db_supabase, "get_rows", rows)
    delivery = AsyncMock()
    monkeypatch.setattr(location._deps.manager, "send_personal_message", delivery)

    asyncio.run(
        location._apply_v2_live_marker_update(
            "driver-1",
            "ride-1",
            50.45,
            -104.6,
            None,
            None,
            8,
            False,
            True,
            captured_at,
            token_session_id="session-1",
            online_epoch=12,
            availability_v2=True,
        )
    )

    marker.assert_awaited_once()
    assert marker.await_args.kwargs["presence_session_id"] == "session-1"
    assert marker.await_args.kwargs["presence_epoch"] == 12
    delivery.assert_awaited_once()
