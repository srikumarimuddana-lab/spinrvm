"""A background REST upload must move the assigned rider's live marker."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks

from routes.drivers import location
from utils.breadcrumbs import LocationBatchAck, LocationBatchPersistResult


@pytest.fixture
def delivery(monkeypatch):
    monkeypatch.setattr(
        "settings_loader.get_app_settings", AsyncMock(return_value={"background_location_fanout_enabled": True})
    )
    ride = {"id": "ride-1", "driver_id": "driver-1", "rider_id": "rider-1", "status": "in_progress"}

    async def rows(table, filters, **kwargs):
        if table == "drivers":
            return [{"id": "driver-1", "is_online": True}]
        if table == "rides":
            return [ride.copy()]
        return []

    monkeypatch.setattr(location.db_supabase, "get_rows", rows)
    monkeypatch.setattr(location, "_write_marker_if_due", AsyncMock())
    monkeypatch.setattr(location, "_newer_than_last_written_marker", AsyncMock(return_value=True))
    monkeypatch.setattr(location._deps, "mark_present", AsyncMock())
    trusted = AsyncMock(return_value=(True, "ok"))
    monkeypatch.setattr("utils.location_integrity.check_location_integrity", trusted)
    send = AsyncMock()
    monkeypatch.setattr(location._deps.manager, "send_personal_message", send)
    persist = AsyncMock(
        return_value=LocationBatchPersistResult(
            ack=LocationBatchAck(
                recording_session_id="6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
                acked_through=2,
                accepted_count=2,
                rejected=(),
            ),
            inserted_count=2,
        )
    )
    monkeypatch.setattr("utils.breadcrumbs.persist_trip_location_batch", persist)
    return ride, send, trusted


@pytest.mark.parametrize("naive", [False, True])
def test_background_batch_delivers_newest_capture_to_rider_after_persistence(delivery, naive):
    _, send, _ = delivery
    now = datetime.now(timezone.utc)
    # Enqueue order need not equal capture order across native producers.
    points = [
        {
            "sequence_number": seq,
            "captured_at": ts.isoformat(),
            "lat": lat,
            "lng": -104.6,
            "accuracy": 8,
            "speed": 12,
            "heading": 90,
        }
        for seq, ts, lat in [(1, now, 50.45), (2, now - timedelta(seconds=5), 50.44)]
    ]
    tasks = BackgroundTasks()
    if naive:
        points[0]["captured_at"] = now.replace(tzinfo=None).isoformat()
    asyncio.run(
        location.update_location_batch(
            {
                "ride_id": "ride-1",
                "recording_session_id": "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
                "points": points,
            },
            background_tasks=tasks,
            current_user={"id": "user-1"},
        )
    )
    send.assert_not_awaited()
    asyncio.run(tasks())
    send.assert_awaited_once_with(
        {
            "type": "driver_location_update",
            "driver_id": "driver-1",
            "ride_id": "ride-1",
            "lat": 50.45,
            "lng": -104.6,
            "heading": 90.0,
            "speed": 12.0,
            "accuracy": 8.0,
            "captured_at": now.isoformat(),
        },
        "rider_rider-1",
        durable=False,
    )


@pytest.mark.parametrize(
    "status,allowed",
    [
        ("driver_assigned", False),
        ("driver_accepted", True),
        ("driver_arrived", True),
        ("in_progress", True),
        ("completed", False),
        ("cancelled", False),
    ],
)
def test_delivery_rechecks_current_ride_visibility(delivery, status, allowed):
    ride, send, _ = delivery
    ride["status"] = status
    asyncio.run(
        location._apply_v2_live_marker_update(
            "driver-1", "ride-1", 50.45, -104.6, 90, 12, 8, False, True, datetime.now(timezone.utc)
        )
    )
    assert send.await_count == int(allowed)


@pytest.mark.parametrize("age", [61, -10])
def test_history_or_future_capture_does_not_masquerade_as_live(delivery, age):
    _, send, _ = delivery
    asyncio.run(
        location._apply_v2_live_marker_update(
            "driver-1",
            "ride-1",
            50.45,
            -104.6,
            90,
            12,
            8,
            False,
            True,
            datetime.now(timezone.utc) - timedelta(seconds=age),
        )
    )
    send.assert_not_awaited()
    location._write_marker_if_due.assert_not_awaited()
    location._deps.mark_present.assert_not_awaited()
    delivery[2].assert_not_awaited()


def test_reassigned_ride_does_not_receive_previous_driver_position(delivery):
    ride, send, _ = delivery
    ride["driver_id"] = "different-driver"
    asyncio.run(
        location._apply_v2_live_marker_update(
            "driver-1", "ride-1", 50.45, -104.6, 90, 12, 8, False, True, datetime.now(timezone.utc)
        )
    )
    send.assert_not_awaited()


def test_untrusted_capture_does_not_reach_rider(delivery):
    _, send, trusted = delivery
    trusted.return_value = (False, "mocked")
    asyncio.run(
        location._apply_v2_live_marker_update(
            "driver-1", "ride-1", 50.45, -104.6, 90, 12, 8, True, True, datetime.now(timezone.utc)
        )
    )
    send.assert_not_awaited()


def test_fanout_can_be_disabled_without_disabling_marker_write(delivery, monkeypatch):
    _, send, _ = delivery
    monkeypatch.setattr("settings_loader.get_app_settings", AsyncMock(return_value={}))
    asyncio.run(
        location._apply_v2_live_marker_update(
            "driver-1", "ride-1", 50.45, -104.6, 90, 12, 8, False, True, datetime.now(timezone.utc)
        )
    )
    location._write_marker_if_due.assert_awaited_once()
    send.assert_not_awaited()


def test_database_rejected_marker_is_not_delivered(delivery):
    _, send, _ = delivery
    location._write_marker_if_due.return_value = False
    asyncio.run(
        location._apply_v2_live_marker_update(
            "driver-1", "ride-1", 50.45, -104.6, 90, 12, 8, False, True, datetime.now(timezone.utc)
        )
    )
    send.assert_not_awaited()


def test_legacy_stale_batch_retains_history_without_poisoning_live_integrity(delivery, monkeypatch):
    _, _, trusted = delivery
    persist = AsyncMock(return_value=1)
    monkeypatch.setattr("utils.breadcrumbs.persist_ride_breadcrumbs", persist)
    point = {
        "latitude": 50.45,
        "longitude": -104.6,
        "timestamp": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
    }
    asyncio.run(
        location.update_location_batch([point], background_tasks=BackgroundTasks(), current_user={"id": "user-1"})
    )
    persist.assert_awaited_once()
    trusted.assert_not_awaited()
    location._write_marker_if_due.assert_not_awaited()
