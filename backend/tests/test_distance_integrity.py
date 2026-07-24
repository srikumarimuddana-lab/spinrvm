"""Tests for the ride distance/route integrity-signal writer."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from utils.distance_integrity import record_integrity_event


def _run(coro):
    return asyncio.run(coro)


def test_valid_event_is_written():
    with patch("utils.distance_integrity.db_supabase.insert_one", AsyncMock(return_value={"id": "e1"})) as insert:
        ok = _run(record_integrity_event("ride_1", "booked_distance_suspect", {"booked_km": 0.7}))
    assert ok is True
    args, _ = insert.await_args
    assert args[0] == "ride_distance_integrity_events"
    assert args[1]["ride_id"] == "ride_1"
    assert args[1]["kind"] == "booked_distance_suspect"
    assert args[1]["details"] == {"booked_km": 0.7}


def test_unknown_kind_is_refused_without_db_call():
    with patch("utils.distance_integrity.db_supabase.insert_one", AsyncMock()) as insert:
        ok = _run(record_integrity_event("ride_1", "not_a_real_kind"))
    assert ok is False
    insert.assert_not_awaited()


def test_write_failure_is_swallowed():
    with patch(
        "utils.distance_integrity.db_supabase.insert_one",
        AsyncMock(side_effect=RuntimeError("db down")),
    ):
        ok = _run(record_integrity_event("ride_1", "trip_window_compressed", {"ratio": 0.3}))
    assert ok is False  # never raises — detection signal must not break the caller


def test_none_details_defaults_to_empty():
    with patch("utils.distance_integrity.db_supabase.insert_one", AsyncMock(return_value={"id": "e1"})) as insert:
        _run(record_integrity_event("ride_1", "coverage_fallback"))
    assert insert.await_args[0][1]["details"] == {}
