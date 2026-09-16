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
        assert result["accepted"] == enabled
        asyncio.run(tasks())
    assert apply.await_count == int(status is None and enabled)
    guard.assert_awaited_once_with("session-1")
