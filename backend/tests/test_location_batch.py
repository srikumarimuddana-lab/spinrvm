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


def test_completed_ride_batch_does_not_move_the_live_driver_marker(monkeypatch: pytest.MonkeyPatch):
    # M-B: a delayed offline outbox flush for a COMPLETED ride (inside the 90-day
    # retention window) must persist breadcrumbs but must NOT advance the live
    # driver marker — the driver may now be online on a new trip and would be
    # teleported to these stale coordinates.
    completed_at = datetime.now(timezone.utc) - timedelta(days=1)
    ride = _ride(status="completed", ride_completed_at=completed_at.isoformat())
    update_one = _install_driver_and_ride(monkeypatch, ride)

    async def persist(*args, **kwargs):
        return _result()

    monkeypatch.setattr("utils.breadcrumbs.persist_trip_location_batch", persist)

    response = _run(location.update_location_batch(_payload(), current_user={"id": "user_1"}))

    assert response["acked_through"] == 2
    update_one.assert_not_awaited()  # the driver marker was never advanced


def test_active_ride_batch_still_moves_the_live_driver_marker(monkeypatch: pytest.MonkeyPatch):
    # M-B: the ACTIVE trip still advances the live marker to the newest point.
    update_one = _install_driver_and_ride(monkeypatch, _ride(status="in_progress"))

    async def persist(*args, **kwargs):
        return _result()

    monkeypatch.setattr("utils.breadcrumbs.persist_trip_location_batch", persist)

    _run(location.update_location_batch(_payload(), current_user={"id": "user_1"}))

    update_one.assert_awaited_once()
    table, filters, payload = update_one.await_args.args[:3]
    assert table == "drivers"
    assert filters == {"id": "driver_1"}
    assert payload["lat"] == 50.42
    assert payload["lng"] == -104.62


def test_active_ride_batch_does_not_rewind_a_fresher_marker(monkeypatch: pytest.MonkeyPatch):
    # M-B: an out-of-order late chunk (captured before the driver's current
    # position time) must not rewind the marker even on the active ride.
    driver_row = {"id": "driver_1", "user_id": "user_1", "is_online": False, "updated_at": "2026-06-01T23:10:00Z"}

    async def get_rows(table, filters, **kwargs):
        if table == "drivers":
            return [driver_row]
        if table == "rides":
            return [_ride(status="in_progress")]
        raise AssertionError(f"unexpected table: {table}")

    update_one = AsyncMock()
    monkeypatch.setattr(location.db_supabase, "get_rows", get_rows)
    monkeypatch.setattr(location.db_supabase, "update_one", update_one)

    async def persist(*args, **kwargs):
        return _result()

    monkeypatch.setattr("utils.breadcrumbs.persist_trip_location_batch", persist)

    # Points captured at 23:06, older than the driver's 23:10 position stamp.
    _run(location.update_location_batch(_payload(), current_user={"id": "user_1"}))

    update_one.assert_not_awaited()


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


def test_late_completed_point_hides_the_previous_snapshot_before_refinalizing(monkeypatch: pytest.MonkeyPatch):
    update = AsyncMock()

    async def insert_many(_table, docs, **_kwargs):
        return docs

    monkeypatch.setattr(breadcrumbs.db_supabase, "insert_many_ignore_conflicts", insert_many)
    monkeypatch.setattr(breadcrumbs.db_supabase, "update_one", update)
    completed_ride = _ride(status="completed", ride_completed_at="2026-06-01T23:10:00Z")

    _run(
        breadcrumbs.persist_trip_location_batch(
            "driver_1",
            "ride_1",
            "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
            [_point(1)],
            active_ride=completed_ride,
        )
    )

    payload = update.await_args.args[2]
    assert payload["processing_status"] == "pending"
    assert payload["snapshot_revision"] == 0
    assert payload["snapshot_object_path"] is None
    assert payload["snapshot_url"] is None
    assert payload["finalized_at"] is None


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
