"""Unit tests for background/REST breadcrumb persistence (trip-distance capture).

Regression guards:
  - full batch persisted (not just the last point)
  - per-point, server-authoritative phase from each point's OWN capture
    timestamp — a batch spanning pickup→trip must bill the navigation points as
    navigation, not inflate trip_in_progress
  - stale points discarded: other-ride ride_id, or captured before this ride
  - batch capped at MAX_BREADCRUMB_BATCH (no unbounded REST insert)
"""

import asyncio
import logging
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.utils.breadcrumbs import (
    MAX_BREADCRUMB_BATCH,
    persist_ride_breadcrumbs,
    persist_trip_location_batch,
)

ACCEPTED = "2026-06-01T23:00:00Z"
ARRIVED = "2026-06-01T23:03:00Z"
STARTED = "2026-06-01T23:05:00Z"


def _pt(lat, lng, ts, **extra):
    return {"lat": lat, "lng": lng, "timestamp": ts, **extra}


def _ride(**over):
    r = {
        "id": "ride_1",
        "status": "in_progress",
        "driver_accepted_at": ACCEPTED,
        "driver_arrived_at": ARRIVED,
        "ride_started_at": STARTED,
    }
    r.update(over)
    return r


def _v2_point(sequence_number, **over):
    point = {
        "sequence_number": sequence_number,
        "captured_at": "2026-06-01T23:06:00Z",
        "lat": 50.42,
        "lng": -104.62,
        "accuracy": 7.5,
    }
    point.update(over)
    return point


def _run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture(autouse=True)
def _disable_active_ride_cache(monkeypatch):
    """Keep persistence tests independent of the in-process Redis fallback."""

    async def _cache_miss(_key):
        return None

    async def _cache_write(_key, _value, *, ttl):
        return True

    monkeypatch.setattr("backend.utils.breadcrumbs.redis_get", _cache_miss)
    monkeypatch.setattr("backend.utils.breadcrumbs.redis_set", _cache_write)


def _patches(ride_rows, capture):
    async def _get_rows(table, query, **kw):
        assert table == "rides"
        return ride_rows

    async def _insert_many(table, docs):
        capture["table"] = table
        capture["docs"] = docs
        return docs

    return (
        patch("backend.utils.breadcrumbs.db_supabase.get_rows", _get_rows),
        patch("backend.utils.breadcrumbs.db_supabase.insert_many", _insert_many),
    )


@pytest.mark.asyncio
async def test_persists_all_points_with_trip_phase():
    cap = {}
    g, i = _patches([_ride()], cap)
    with g, i:
        n = await persist_ride_breadcrumbs(
            "drv_1",
            [
                _pt(50.45, -104.62, "2026-06-01T23:06:17Z", speed=12),
                _pt(50.43, -104.64, "2026-06-01T23:07:17Z"),
                _pt(50.42, -104.65, "2026-06-01T23:08:17Z"),
            ],
        )
    assert n == 3
    assert cap["table"] == "driver_location_history"
    docs = cap["docs"]
    assert len(docs) == 3, "all points persisted, not just the last"
    assert all(d["ride_id"] == "ride_1" for d in docs)
    # ts >= ride_started_at → trip_in_progress, derived server-side
    assert all(d["tracking_phase"] == "trip_in_progress" for d in docs)
    assert all(isinstance(d["timestamp"], datetime) for d in docs)
    assert all(isinstance(d["received_at"], datetime) for d in docs)


@pytest.mark.asyncio
async def test_no_active_ride_persists_nothing():
    insert = AsyncMock()

    async def _get_rows(table, query, **kw):
        return []

    with (
        patch("backend.utils.breadcrumbs.db_supabase.get_rows", _get_rows),
        patch("backend.utils.breadcrumbs.db_supabase.insert_many", insert),
    ):
        n = await persist_ride_breadcrumbs("drv_1", [_pt(50.45, -104.62, "2026-06-01T23:06:17Z")])
    assert n == 0
    insert.assert_not_called()


@pytest.mark.asyncio
async def test_no_active_ride_can_persist_idle_for_live_ws_ping():
    cap = {}

    async def _get_rows(table, query, **kw):
        return []

    async def _insert_many(table, docs):
        cap["table"] = table
        cap["docs"] = docs
        return docs

    with (
        patch("backend.utils.breadcrumbs.db_supabase.get_rows", _get_rows),
        patch("backend.utils.breadcrumbs.db_supabase.insert_many", _insert_many),
    ):
        n = await persist_ride_breadcrumbs(
            "drv_1",
            [_pt(50.45, -104.62, "2026-06-01T23:06:17Z")],
            persist_idle=True,
        )

    assert n == 1
    assert cap["docs"][0]["ride_id"] is None
    assert cap["docs"][0]["tracking_phase"] == "online_idle"


@pytest.mark.asyncio
async def test_phase_attributed_per_point_across_transition():
    """A batch spanning pickup→trip bills navigation points as navigation, not trip."""
    cap = {}
    g, i = _patches([_ride()], cap)
    with g, i:
        n = await persist_ride_breadcrumbs(
            "drv_1",
            [
                _pt(50.40, -104.60, "2026-06-01T23:01:00Z"),  # accepted<ts<arrived → nav
                _pt(50.41, -104.61, "2026-06-01T23:04:00Z"),  # arrived<ts<started → arrived
                _pt(50.42, -104.62, "2026-06-01T23:06:00Z"),  # ts>=started → trip
            ],
        )
    assert n == 3
    assert [d["tracking_phase"] for d in cap["docs"]] == [
        "navigating_to_pickup",
        "arrived_at_pickup",
        "trip_in_progress",
    ]


@pytest.mark.asyncio
async def test_discards_other_ride_and_stale_points():
    cap = {}
    g, i = _patches([_ride()], cap)
    with g, i:
        n = await persist_ride_breadcrumbs(
            "drv_1",
            [
                _pt(50.42, -104.62, "2026-06-01T23:06:00Z"),  # ok → trip
                _pt(50.40, -104.60, "2026-06-01T23:06:30Z", ride_id="ride_OTHER"),  # other ride → drop
                _pt(50.39, -104.59, "2026-06-01T22:50:00Z"),  # before accepted → stale drop
            ],
        )
    assert n == 1
    assert len(cap["docs"]) == 1
    assert cap["docs"][0]["tracking_phase"] == "trip_in_progress"


@pytest.mark.asyncio
async def test_invalid_and_mocked_points_skipped():
    cap = {}
    g, i = _patches([_ride()], cap)
    with g, i:
        n = await persist_ride_breadcrumbs(
            "drv_1",
            [
                _pt(50.42, -104.62, "2026-06-01T23:06:00Z"),
                {"lat": None, "lng": -104.0, "timestamp": "2026-06-01T23:06:30Z"},  # missing lat
                _pt(50.40, -104.66, "2026-06-01T23:07:00Z", mocked=True),  # spoofed
            ],
        )
    assert n == 1
    assert len(cap["docs"]) == 1


@pytest.mark.asyncio
async def test_batch_capped_to_max():
    cap = {}
    g, i = _patches([_ride()], cap)
    pts = [_pt(50.40 + k * 1e-5, -104.60, "2026-06-01T23:06:00Z") for k in range(MAX_BREADCRUMB_BATCH + 200)]
    with g, i:
        n = await persist_ride_breadcrumbs("drv_1", pts)
    assert n == MAX_BREADCRUMB_BATCH, "REST batch must be bounded"
    assert len(cap["docs"]) == MAX_BREADCRUMB_BATCH


@pytest.mark.asyncio
async def test_non_list_and_non_dict_points_are_ignored():
    insert = AsyncMock()

    async def _get_rows(table, query, **kw):
        return [_ride()]

    with (
        patch("backend.utils.breadcrumbs.db_supabase.get_rows", _get_rows),
        patch("backend.utils.breadcrumbs.db_supabase.insert_many", insert),
    ):
        assert await persist_ride_breadcrumbs("drv_1", "not-a-list") == 0
        assert await persist_ride_breadcrumbs("drv_1", ["not-a-dict"]) == 0

    insert.assert_not_called()


@pytest.mark.asyncio
async def test_active_ride_can_be_reused_without_lookup():
    cap = {}
    get_rows = AsyncMock(return_value=[_ride(id="wrong_ride")])

    async def _insert_many(table, docs):
        cap["docs"] = docs
        return docs

    with (
        patch("backend.utils.breadcrumbs.db_supabase.get_rows", get_rows),
        patch("backend.utils.breadcrumbs.db_supabase.insert_many", _insert_many),
    ):
        n = await persist_ride_breadcrumbs(
            "drv_1",
            [_pt(50.45, -104.62, "2026-06-01T23:06:17Z")],
            active_ride=_ride(id="already_loaded"),
        )

    assert n == 1
    get_rows.assert_not_called()
    assert cap["docs"][0]["ride_id"] == "already_loaded"


@pytest.mark.asyncio
async def test_future_capture_time_is_clamped_to_received_at():
    cap = {}
    g, i = _patches([_ride()], cap)
    with g, i:
        n = await persist_ride_breadcrumbs(
            "drv_1",
            [_pt(50.45, -104.62, "2099-01-01T00:00:00Z")],
        )

    assert n == 1
    assert cap["docs"][0]["timestamp"] == cap["docs"][0]["received_at"]


@pytest.mark.asyncio
async def test_invalid_coordinate_ranges_and_no_fix_are_skipped():
    cap = {}
    g, i = _patches([_ride()], cap)
    with g, i:
        n = await persist_ride_breadcrumbs(
            "drv_1",
            [
                _pt(0, 0, "2026-06-01T23:06:00Z"),
                _pt(91, -104.62, "2026-06-01T23:06:10Z"),
                _pt(50.45, -104.62, "2026-06-01T23:06:20Z"),
            ],
        )

    assert n == 1
    assert cap["docs"][0]["lat"] == 50.45


def test_v2_replay_keeps_acknowledgement_stable_and_reports_insert_count():
    calls = []

    async def _insert_many_ignore_conflicts(table, docs, on_conflict):
        calls.append((table, docs, on_conflict))
        return docs if len(calls) == 1 else []

    with patch(
        "backend.utils.breadcrumbs.db_supabase.insert_many_ignore_conflicts",
        _insert_many_ignore_conflicts,
    ):
        first = _run(
            persist_trip_location_batch(
                "drv_1",
                "ride_1",
                "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
                [_v2_point(1), _v2_point(2)],
                active_ride=_ride(),
            )
        )
        replay = _run(
            persist_trip_location_batch(
                "drv_1",
                "ride_1",
                "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
                [_v2_point(1), _v2_point(2)],
                active_ride=_ride(),
            )
        )

    assert first.inserted_count == 2
    assert replay.inserted_count == 0
    assert first.ack.to_dict() == replay.ack.to_dict()
    assert first.ack.acked_through == 2
    assert calls[0][0] == "driver_location_history"
    assert calls[0][2] == "ride_id,driver_id,recording_session_id,sequence_number"


def test_v2_preserves_capture_time_and_derives_phase_from_server_ride():
    captured = {}

    async def _insert_many_ignore_conflicts(table, docs, on_conflict):
        captured["docs"] = docs
        return docs

    with patch(
        "backend.utils.breadcrumbs.db_supabase.insert_many_ignore_conflicts",
        _insert_many_ignore_conflicts,
    ):
        result = _run(
            persist_trip_location_batch(
                "drv_1",
                "ride_1",
                "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
                [_v2_point(1, captured_at="2099-01-01T00:00:00Z", tracking_phase="trip_in_progress")],
                active_ride=_ride(),
            )
        )

    row = captured["docs"][0]
    assert result.ack.accepted_count == 1
    assert row["captured_at"].isoformat() == "2099-01-01T00:00:00+00:00"
    assert row["received_at"] < row["captured_at"]
    assert row["tracking_phase"] == "trip_in_progress"


def test_legacy_writer_delegates_identified_v2_batches():
    captured = {}

    async def _persist_v2(driver_id, ride_id, session_id, points, *, active_ride):
        captured.update(
            driver_id=driver_id,
            ride_id=ride_id,
            session_id=session_id,
            points=points,
            active_ride=active_ride,
        )
        return SimpleNamespace(inserted_count=1)

    with patch("backend.utils.breadcrumbs.persist_trip_location_batch", _persist_v2):
        inserted = _run(
            persist_ride_breadcrumbs(
                "drv_1",
                [
                    _v2_point(
                        1,
                        recording_session_id="6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
                    )
                ],
                active_ride=_ride(),
            )
        )

    assert inserted == 1
    assert captured["driver_id"] == "drv_1"
    assert captured["ride_id"] == "ride_1"
    assert captured["session_id"] == "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f"


def test_v2_acknowledges_past_permanently_rejected_coordinate():
    captured = {}

    async def _insert_many_ignore_conflicts(table, docs, on_conflict):
        captured["docs"] = docs
        return docs

    with patch(
        "backend.utils.breadcrumbs.db_supabase.insert_many_ignore_conflicts",
        _insert_many_ignore_conflicts,
    ):
        result = _run(
            persist_trip_location_batch(
                "drv_1",
                "ride_1",
                "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
                [_v2_point(1), _v2_point(2, lat=91), _v2_point(3)],
                active_ride=_ride(),
            )
        )

    assert result.ack.acked_through == 3
    assert result.ack.accepted_count == 2
    assert result.ack.to_dict()["rejected"] == [{"sequence_number": 2, "reason": "invalid_coordinate"}]
    assert [row["sequence_number"] for row in captured["docs"]] == [1, 3]


def test_v2_invalid_coordinate_never_enters_logs(caplog: pytest.LogCaptureFixture):
    async def _insert_many_ignore_conflicts(table, docs, on_conflict):
        raise AssertionError("invalid points must not reach the database")

    with (
        caplog.at_level(logging.INFO),
        patch(
            "backend.utils.breadcrumbs.db_supabase.insert_many_ignore_conflicts",
            _insert_many_ignore_conflicts,
        ),
    ):
        result = _run(
            persist_trip_location_batch(
                "drv_1",
                "ride_1",
                "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
                [_v2_point(1, lat=91.123456, lng=-104.987654)],
                active_ride=_ride(),
            )
        )

    assert result.ack.to_dict()["rejected"] == [{"sequence_number": 1, "reason": "invalid_coordinate"}]
    assert "91.123456" not in caplog.text
    assert "-104.987654" not in caplog.text


def test_new_late_completed_points_debounce_route_re_finalization():
    update = AsyncMock()

    async def _insert_many_ignore_conflicts(_table, docs, *, on_conflict):
        assert on_conflict == "ride_id,driver_id,recording_session_id,sequence_number"
        return docs

    completed_ride = _ride(status="completed", ride_completed_at="2026-06-01T23:10:00Z")
    with (
        patch(
            "backend.utils.breadcrumbs.db_supabase.insert_many_ignore_conflicts",
            _insert_many_ignore_conflicts,
        ),
        patch("backend.utils.breadcrumbs.db_supabase.update_one", update),
    ):
        result = _run(
            persist_trip_location_batch(
                "drv_1",
                "ride_1",
                "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
                [_v2_point(4)],
                active_ride=completed_ride,
            )
        )

    assert result.inserted_count == 1
    args, kwargs = update.await_args
    assert args[:2] == ("ride_routes", {"ride_id": "ride_1"})
    assert args[2]["processing_status"] == "pending"
    assert args[2]["processing_claimed_at"] is None
    assert "route_revision" not in args[2]
    assert kwargs["upsert"] is False


def test_duplicate_late_completed_replay_does_not_requeue_route():
    update = AsyncMock()

    async def _insert_many_ignore_conflicts(_table, _docs, *, on_conflict):
        assert on_conflict == "ride_id,driver_id,recording_session_id,sequence_number"
        return []

    completed_ride = _ride(status="completed", ride_completed_at="2026-06-01T23:10:00Z")
    with (
        patch(
            "backend.utils.breadcrumbs.db_supabase.insert_many_ignore_conflicts",
            _insert_many_ignore_conflicts,
        ),
        patch("backend.utils.breadcrumbs.db_supabase.update_one", update),
    ):
        result = _run(
            persist_trip_location_batch(
                "drv_1",
                "ride_1",
                "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f",
                [_v2_point(4)],
                active_ride=completed_ride,
            )
        )

    assert result.inserted_count == 0
    update.assert_not_awaited()
