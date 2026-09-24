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
from fastapi import BackgroundTasks, HTTPException

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


def _install_driver_and_ride(monkeypatch: pytest.MonkeyPatch, ride: dict, *, online: bool = False) -> AsyncMock:
    async def get_rows(table, filters, **kwargs):
        if table == "drivers":
            return [{"id": "driver_1", "user_id": "user_1", "is_online": online}]
        if table == "rides":
            return [ride]
        if table == "users":
            return [{"current_session_id": "session-1"}]
        raise AssertionError(f"unexpected table: {table}")

    update_one = AsyncMock()
    monkeypatch.setattr(location.db_supabase, "get_rows", get_rows)
    monkeypatch.setattr(location, "_availability_v2_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(location.db_supabase, "update_one", update_one)
    monkeypatch.setattr(location.db_supabase, "update_driver_location", update_one)
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
    """The marker write is deferred via BackgroundTasks (< 150 ms SLA path —
    the ack never depends on its outcome), so "persists before updating"
    now means: the durable persist has already completed by the time the
    marker-update task is *scheduled*, and running that scheduled task is
    what actually performs the marker write."""
    events = []
    _install_driver_and_ride(monkeypatch, _ride())

    async def persist(driver_id, ride_id, session_id, points, *, active_ride, driver_last_known=None):
        events.append("persist")
        assert driver_id == "driver_1"
        assert ride_id == "ride_1"
        assert active_ride["id"] == "ride_1"
        # The caller passes the already-fetched driver row's position (no
        # extra DB read) so the batch's first point is checked against it.
        assert driver_last_known == {"lat": None, "lng": None, "location_captured_at": None}
        return _result()

    async def update(*args, **kwargs):
        events.append("marker")

    monkeypatch.setattr(location, "_write_marker_if_due", AsyncMock(side_effect=update))
    monkeypatch.setattr("utils.breadcrumbs.persist_trip_location_batch", persist)
    # This test is about persist-then-marker sequencing, not the ordering
    # guard (covered separately) -- avoid cross-test Redis-fallback pollution
    # from other tests reusing the same driver_id/captured_at.
    monkeypatch.setattr(location, "_newer_than_last_written_marker", AsyncMock(return_value=True))

    bg = BackgroundTasks()
    response = _run(
        location.update_location_batch(
            _payload(
                [_point(1, datetime.now(timezone.utc).isoformat()), _point(2, datetime.now(timezone.utc).isoformat())]
            ),
            background_tasks=bg,
            current_user={"id": "user_1"},
        )
    )

    assert response == _result().ack.to_dict()
    assert events == ["persist"]
    assert len(bg.tasks) == 1
    assert bg.tasks[0].func.__name__ == "_apply_v2_live_marker_update"

    _run(bg())
    assert events == ["persist", "marker"]


def test_v2_trip_batch_without_epoch_keeps_history_but_never_writes_marker(monkeypatch: pytest.MonkeyPatch):
    """Trip history remains acknowledged while idle marker authority is missing."""
    _install_driver_and_ride(monkeypatch, _ride(), online=True)
    monkeypatch.setattr(location, "_availability_v2_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(location, "_guard_revoked_session", AsyncMock())
    persist = AsyncMock(return_value=_result())
    renew = AsyncMock()
    marker = AsyncMock()
    monkeypatch.setattr("utils.breadcrumbs.persist_trip_location_batch", persist)
    monkeypatch.setattr(location, "renew_scoped_presence", renew)
    monkeypatch.setattr(location, "_write_marker_if_due", marker)
    monkeypatch.setattr("utils.location_integrity.check_location_integrity", AsyncMock(return_value=(True, None)))

    bg = BackgroundTasks()
    response = _run(
        location.update_location_batch(
            _payload([_point(1, datetime.now(timezone.utc).isoformat())]),
            background_tasks=bg,
            current_user={"id": "user_1"},
            token_session_id="session-1",
        )
    )
    assert response == _result().ack.to_dict()
    persist.assert_awaited_once()
    assert len(bg.tasks) == 1
    _run(bg())
    renew.assert_not_awaited()
    marker.assert_not_awaited()


def test_v2_trip_batch_rejects_superseded_session_before_history_persist(monkeypatch: pytest.MonkeyPatch):
    persist = AsyncMock(return_value=_result())
    _install_driver_and_ride(monkeypatch, _ride(), online=True)
    monkeypatch.setattr(location, "_availability_v2_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(location, "_guard_revoked_session", AsyncMock())

    async def get_rows(table, filters, **kwargs):
        if table == "drivers":
            return [{"id": "driver_1", "user_id": "user_1", "is_online": True}]
        if table == "rides":
            return [_ride()]
        if table == "users":
            return [{"current_session_id": "replacement-session"}]
        return []

    monkeypatch.setattr(location.db_supabase, "get_rows", get_rows)
    monkeypatch.setattr("utils.breadcrumbs.persist_trip_location_batch", persist)
    with pytest.raises(HTTPException) as exc:
        _run(
            location.update_location_batch(
                _payload(),
                background_tasks=BackgroundTasks(),
                current_user={"id": "user_1"},
                token_session_id="session-1",
            )
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "SESSION_SUPERSEDED"
    persist.assert_not_awaited()


def test_v2_trip_batch_stale_epoch_keeps_history_for_current_session(monkeypatch: pytest.MonkeyPatch):
    _install_driver_and_ride(monkeypatch, _ride(), online=True)
    monkeypatch.setattr(location, "_availability_v2_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(location, "_guard_revoked_session", AsyncMock())
    persist = AsyncMock(return_value=_result())
    renew = AsyncMock(return_value={"status": "stale_epoch", "code": "ONLINE_EPOCH_STALE"})
    marker = AsyncMock()
    monkeypatch.setattr("utils.breadcrumbs.persist_trip_location_batch", persist)
    monkeypatch.setattr(location, "renew_scoped_presence", renew)
    monkeypatch.setattr(location, "_write_marker_if_due", marker)
    monkeypatch.setattr("utils.location_integrity.check_location_integrity", AsyncMock(return_value=(True, None)))
    payload = _payload([_point(1, datetime.now(timezone.utc).isoformat())])
    payload["online_epoch"] = "4"

    bg = BackgroundTasks()
    response = _run(
        location.update_location_batch(
            payload,
            background_tasks=bg,
            current_user={"id": "user_1"},
            token_session_id="session-1",
        )
    )
    assert response == _result().ack.to_dict()
    persist.assert_awaited_once()
    _run(bg())
    renew.assert_awaited_once()
    marker.assert_not_awaited()


def test_v2_batch_rejects_a_ride_assigned_to_another_driver_as_not_found(monkeypatch: pytest.MonkeyPatch):
    """Ownership moved from the DB filter ({id, driver_id}) to Python so the
    driver and ride reads can run concurrently. The contract must not
    change: a ride that is not this driver's is 404, never a 409 hint that
    it exists in some state."""
    _install_driver_and_ride(monkeypatch, _ride(driver_id="driver_2"))
    persist = AsyncMock()
    monkeypatch.setattr("utils.breadcrumbs.persist_trip_location_batch", persist)

    with pytest.raises(HTTPException) as excinfo:
        _run(
            location.update_location_batch(
                _payload(), background_tasks=BackgroundTasks(), current_user={"id": "user_1"}
            )
        )

    assert excinfo.value.status_code == 404
    persist.assert_not_called()


def test_v2_batch_reads_driver_and_ride_concurrently(monkeypatch: pytest.MonkeyPatch):
    """Both reads must be in flight before either resolves — one Supabase
    round-trip instead of two on the < 150 ms location-write SLA path."""
    import asyncio as _asyncio

    started: list[str] = []
    release = _asyncio.Event()

    async def get_rows(table, filters, **kwargs):
        if table not in ("drivers", "rides"):
            return []  # e.g. the revoked-session guard's app_settings read
        started.append(table)
        # The first of the two reads blocks until the other has ALSO started;
        # a sequential implementation deadlocks here and the test times out.
        if len(started) < 2:
            await _asyncio.wait_for(release.wait(), timeout=2)
        else:
            release.set()
        if table == "drivers":
            return [{"id": "driver_1", "user_id": "user_1", "is_online": False}]
        return [_ride()]

    monkeypatch.setattr(location.db_supabase, "get_rows", get_rows)
    monkeypatch.setattr(location.db_supabase, "update_one", AsyncMock())

    async def persist(driver_id, ride_id, session_id, points, *, active_ride, driver_last_known=None):
        return _result()

    monkeypatch.setattr("utils.breadcrumbs.persist_trip_location_batch", persist)

    response = _run(
        location.update_location_batch(_payload(), background_tasks=BackgroundTasks(), current_user={"id": "user_1"})
    )

    assert response == _result().ack.to_dict()
    assert sorted(started) == ["drivers", "rides"]


def test_v2_batch_skips_live_marker_update_when_integrity_check_rejects(monkeypatch: pytest.MonkeyPatch):
    """A38/A40 finding #7: v2 must run the same spoofing/teleport guard as v1
    before trusting a point for the driver's live `lat`/`lng` marker."""
    events = []
    update_one = _install_driver_and_ride(monkeypatch, _ride())

    async def persist(driver_id, ride_id, session_id, points, *, active_ride, driver_last_known=None):
        events.append("persist")
        return _result()

    async def update(*args, **kwargs):
        events.append("marker")

    async def untrusted(*args, **kwargs):
        return False, "mock_location"

    update_one.side_effect = update
    monkeypatch.setattr("utils.breadcrumbs.persist_trip_location_batch", persist)
    monkeypatch.setattr("utils.location_integrity.check_location_integrity", untrusted)

    bg = BackgroundTasks()
    response = _run(location.update_location_batch(_payload(), background_tasks=bg, current_user={"id": "user_1"}))

    assert response == _result().ack.to_dict()
    assert events == ["persist"]

    _run(bg())
    # Breadcrumbs/ack still land (regulatory GPS trace + settlement anomaly
    # filter own that job); only the real-time marker write is skipped, even
    # after the deferred task runs.
    assert events == ["persist"]


def test_deferred_marker_write_skips_a_stale_out_of_order_point(monkeypatch: pytest.MonkeyPatch):
    """Deferring the marker write via BackgroundTasks removed the ordering
    that used to come for free from a sequential client's request/response
    cycle. If an older batch's deferred task happens to run after a newer
    batch's, it must not overwrite the fresher coordinates."""
    from datetime import datetime, timezone

    from routes.drivers import location as loc

    cache: dict[str, str] = {}

    async def fake_redis_get(key):
        return cache.get(key)

    async def fake_redis_set(key, value, ttl=None):
        cache[key] = value

    monkeypatch.setattr("utils.redis_client.redis_get", fake_redis_get)
    monkeypatch.setattr("utils.redis_client.redis_set", fake_redis_set)

    older = datetime(2026, 6, 1, 23, 6, 0, tzinfo=timezone.utc)
    newer = datetime(2026, 6, 1, 23, 6, 30, tzinfo=timezone.utc)

    # Newer batch's task runs first (as if it raced ahead of the older one).
    assert _run(loc._newer_than_last_written_marker("driver_1", newer)) is True
    # Older batch's task runs after -- must be recognized as stale.
    assert _run(loc._newer_than_last_written_marker("driver_1", older)) is False
    # A genuinely newer point after that is still accepted.
    even_newer = datetime(2026, 6, 1, 23, 7, 0, tzinfo=timezone.utc)
    assert _run(loc._newer_than_last_written_marker("driver_1", even_newer)) is True


@pytest.mark.parametrize("payload", [_payload([_point(1), _point(3)]), _payload([_point(i) for i in range(501)])])
def test_v2_batch_rejects_non_contiguous_or_oversized_input(payload):
    with pytest.raises(HTTPException) as exc_info:
        _run(location.update_location_batch(payload, background_tasks=BackgroundTasks(), current_user={"id": "user_1"}))

    assert exc_info.value.status_code == 422


def test_v2_batch_returns_503_when_durable_persistence_fails(monkeypatch: pytest.MonkeyPatch):
    _install_driver_and_ride(monkeypatch, _ride())

    async def fail_persist(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("utils.breadcrumbs.persist_trip_location_batch", fail_persist)

    with pytest.raises(HTTPException) as exc_info:
        _run(
            location.update_location_batch(
                _payload(), background_tasks=BackgroundTasks(), current_user={"id": "user_1"}
            )
        )

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

    response = _run(
        location.update_location_batch(_payload(), background_tasks=BackgroundTasks(), current_user={"id": "user_1"})
    )

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
            background_tasks=BackgroundTasks(),
            current_user={"id": "user_1"},
        )
    )

    assert response == {"success": True}
    persisted.assert_awaited_once()


def test_location_request_contract_uses_a_single_ride_session_and_ordered_points():
    request = LocationBatchRequest.model_validate(_payload())

    assert request.ride_id == "ride_1"
    assert request.points[0].sequence_number == 1
