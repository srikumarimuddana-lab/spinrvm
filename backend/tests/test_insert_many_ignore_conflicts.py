"""Tests for conflict-safe Supabase bulk inserts."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from backend.repositories import _base

pytestmark = pytest.mark.unit


def _run(coroutine):
    return asyncio.run(coroutine)


def test_insert_many_ignore_conflicts_serializes_and_upserts_once(monkeypatch: pytest.MonkeyPatch) -> None:
    table = MagicMock()
    response = MagicMock(data=[{"id": "stored"}])
    table.upsert.return_value.execute.return_value = response
    client = MagicMock()
    client.table.return_value = table

    async def run_immediately(func, retry_policy="read"):
        return func()

    monkeypatch.setattr(_base, "supabase", client)
    monkeypatch.setattr(_base, "run_sync", run_immediately)

    rows = _run(
        _base.insert_many_ignore_conflicts(
            "driver_location_history",
            [{"captured_at": datetime(2026, 7, 17, tzinfo=timezone.utc)}],
            "ride_id,driver_id,recording_session_id,sequence_number",
        )
    )

    assert rows == [{"id": "stored"}]
    client.table.assert_called_once_with("driver_location_history")
    table.upsert.assert_called_once_with(
        [{"captured_at": "2026-07-17T00:00:00+00:00"}],
        on_conflict="ride_id,driver_id,recording_session_id,sequence_number",
        ignore_duplicates=True,
    )


def test_insert_many_ignore_conflicts_rejects_invalid_rows() -> None:
    with pytest.raises(TypeError, match="requires dict rows"):
        _run(
            _base.insert_many_ignore_conflicts(
                "driver_location_history",
                [{"sequence_number": 1}, "invalid"],  # type: ignore[list-item]
                "ride_id,driver_id,recording_session_id,sequence_number",
            )
        )
