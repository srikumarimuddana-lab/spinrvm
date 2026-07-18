"""Contract tests for durable driver location-batch acknowledgement."""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test_key")
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-ci-only-32chars!!")
os.environ.setdefault("ADMIN_PASSWORD", "TestAdminPass123!")

import pytest
from fastapi import HTTPException

from routes.drivers import location
from routes.drivers.location import LocationBatchRequest
from utils import breadcrumbs
from utils.breadcrumbs import LocationBatchAck, LocationBatchPersistResult


def _run(coroutine):
    return asyncio.run(coroutine)


def _point(sequence_number: int, captured_at: str = "2026-06-01T23:06:00Z") -> dict:
    return {
        "sequence_number": sequence_number,
        "captured_at": captured_at,
        "lat": 50.42,
        "lng": -104.62,
        "accuracy": 8,
    }


def _payload(points: list[dict] | None = None) -> dict:
    return {
        "ride_id": "ride_1",
        "recording_session_id": "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
        "points": points or [_point(1), _point(2)],
    }


def _ride(status: str = "in_progress", **overrides) -> dict:
    ride = {
        "id": "ride_1",
        "driver_id": "driver_1",
        "status": status,
        "driver_accepted_at": "2026-06-01T23:00:00Z",
        "ride_started_at": "2026-06-01T23:05:00Z",
    }
    ride.update(overrides)
    return ride


def _install_driver_and_ride(monkeypatch: pytest.MonkeyPatch, ride: dict) -> AsyncMock:
    async def get_rows(table, filters, **kwargs):
        if table == "drivers":
            return [{"id": "driver_1", "user_id": "user_1", "is_online": False}]
        if table == "rides":
            return [ride]
        raise AssertionError(f"unexpected table: {table}")

    update_one = AsyncMock()
    monkeypatch.setattr(location.db_supabase, "get_rows", get_rows)
    monkeypatch.setattr(location.db_supabase, "update_one", update_one)
    return update_one


def _result(accepted_count: int = 2) -> LocationBatchPersistResult:
    return LocationBatchPersistResult(
        ack=LocationBatchAck(
            recording_session_id="6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
            acked_through=accepted_count,
            accepted_count=accepted_count,
            rejected=(),
        ),
        inserted_count=accepted_count,
    )


def test_v2_batch_persists_before_updating_the_live_marker(monkeypatch: pytest.MonkeyPatch):
    events = []
    update_one = _install_driver_and_ride(monkeypatch, _ride())

    async def persist(driver_id, ride_id, session_id, points, *, active_ride):
        events.append("persist")
        assert driver_id == "driver_1"
        assert ride_id == "ride_1"
        assert active_ride["id"] == "ride_1"
        return _result()

    async def update(*args, **kwargs):
        events.append("marker")

    update_one.side_effect = update
    monkeypatch.setattr("utils.breadcrumbs.persist_trip_location_batch", persist)

    response = _run(location.update_location_batch(_payload(), current_user={"id": "user_1"}))

    assert response == _result().ack.to_dict()
    assert events == ["persist", "marker"]


@pytest.mark.parametrize("payload", [_payload([_point(1), _point(3)]), _payload([_point(i) for i in range(501)])])
def test_v2_batch_rejects_non_contiguous_or_oversized_input(payload):
    with pytest.raises(HTTPException) as exc_info:
        _run(location.update_location_batch(payload, current_user={"id": "user_1"}))

    assert exc_info.value.status_code == 422


def test_v2_batch_returns_503_when_durable_persistence_fails(monkeypatch: pytest.MonkeyPatch):
    _install_driver_and_ride(monkeypatch, _ride())

    async def fail_persist(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("utils.breadcrumbs.persist_trip_location_batch", fail_persist)

    with pytest.raises(HTTPException) as exc_info:
        _run(location.update_location_batch(_payload(), current_user={"id": "user_1"}))

    assert exc_info.value.status_code == 503


def test_completed_ride_accepts_delayed_points_inside_lifecycle_and_retention(monkeypatch: pytest.MonkeyPatch):
    completed_at = datetime.now(timezone.utc) - timedelta(days=1)
    ride = _ride(status="completed", ride_completed_at=completed_at.isoformat())
    _install_driver_and_ride(monkeypatch, ride)
    captured = {}

    async def persist(*args, **kwargs):
        captured["ride"] = kwargs["active_ride"]
        return _result()

    monkeypatch.setattr("utils.breadcrumbs.persist_trip_location_batch", persist)

    response = _run(location.update_location_batch(_payload(), current_user={"id": "user_1"}))

    assert response["acked_through"] == 2
    assert captured["ride"]["status"] == "completed"


def test_active_ride_rejects_future_captured_at_before_persisting(monkeypatch: pytest.MonkeyPatch):
    inserted = AsyncMock(return_value=[])
    monkeypatch.setattr(breadcrumbs.db_supabase, "insert_many_ignore_conflicts", inserted)
    future = (datetime.now(timezone.utc) + timedelta(seconds=31)).isoformat()

    result = _run(
        breadcrumbs.persist_trip_location_batch(
            "driver_1",
            "ride_1",
            "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
            [_point(1, future)],
            active_ride=_ride(),
        )
    )

    assert result.inserted_count == 0
    assert result.ack.rejected[0].reason == "future_capture_time"
    inserted.assert_not_awaited()


def test_legacy_points_remain_compatible(monkeypatch: pytest.MonkeyPatch):
    _install_driver_and_ride(monkeypatch, _ride())

    async def trusted(*args, **kwargs):
        return True, None

    persisted = AsyncMock(return_value=1)
    monkeypatch.setattr("utils.location_integrity.check_location_integrity", trusted)
    monkeypatch.setattr("utils.breadcrumbs.persist_ride_breadcrumbs", persisted)

    response = _run(
        location.update_location_batch(
            {"points": [{"lat": 50.42, "lng": -104.62, "captured_at": "2026-06-01T23:06:00Z"}]},
            current_user={"id": "user_1"},
        )
    )

    assert response == {"success": True}
    persisted.assert_awaited_once()


def test_location_request_contract_uses_a_single_ride_session_and_ordered_points():
    request = LocationBatchRequest.model_validate(_payload())

    assert request.ride_id == "ride_1"
    assert request.points[0].sequence_number == 1
