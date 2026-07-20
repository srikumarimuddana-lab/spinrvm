"""Regression guards for the late-tail delivery semantics of
``persist_trip_location_batch``.

The driver app force-flushes its durable outbox at ride completion, but a
dead network can still deliver the final stretch of GPS points *after*
``ride_completed_at`` is stamped. The contract these tests lock in:

  - the ``after_ride_window`` rejection is based on each point's own
    ``captured_at``, NOT on delivery time — a tail captured during the trip
    but uploaded after completion must be accepted;
  - points genuinely captured after completion are rejected point-by-point
    while the rest of the batch is still accepted and acknowledged;
  - a tail batch that inserts nothing new must not re-queue route
    finalization (idempotent replay stays cheap).

If any of these regress, completed rides permanently lose their route tail
("route ends before the dropoff") because the client deletes acked points.
"""

import asyncio
from unittest.mock import AsyncMock, patch

from backend.utils.breadcrumbs import persist_trip_location_batch

ACCEPTED = "2026-07-19T09:40:00Z"
STARTED = "2026-07-19T09:46:00Z"
COMPLETED = "2026-07-19T10:04:00Z"
SESSION = "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f"


def _completed_ride():
    return {
        "id": "ride_1",
        "status": "completed",
        "driver_accepted_at": ACCEPTED,
        "driver_arrived_at": "2026-07-19T09:44:00Z",
        "ride_started_at": STARTED,
        "ride_completed_at": COMPLETED,
    }


def _point(sequence_number, captured_at, **over):
    point = {
        "sequence_number": sequence_number,
        "captured_at": captured_at,
        "lat": 50.42,
        "lng": -104.62,
        "accuracy": 7.5,
    }
    point.update(over)
    return point


def _run(coroutine):
    return asyncio.run(coroutine)


def test_tail_captured_before_completion_is_accepted_when_delivered_after():
    """Delivery time must never disqualify a point captured inside the ride."""
    captured = {}
    update = AsyncMock()

    async def _insert(table, docs, *, on_conflict):
        captured["docs"] = docs
        return docs

    with (
        patch("backend.utils.breadcrumbs.db_supabase.insert_many_ignore_conflicts", _insert),
        patch("backend.utils.breadcrumbs.db_supabase.update_one", update),
    ):
        result = _run(
            persist_trip_location_batch(
                "drv_1",
                "ride_1",
                SESSION,
                [
                    # The stranded final stretch: captured in the last two
                    # minutes of the trip, delivered after completion.
                    _point(7, "2026-07-19T10:02:10Z"),
                    _point(8, "2026-07-19T10:03:05Z"),
                    _point(9, "2026-07-19T10:03:58Z"),
                ],
                active_ride=_completed_ride(),
            )
        )

    assert result.ack.accepted_count == 3
    assert result.ack.acked_through == 9
    assert result.ack.to_dict()["rejected"] == []
    assert [row["sequence_number"] for row in captured["docs"]] == [7, 8, 9]
    assert all(row["tracking_phase"] == "trip_in_progress" for row in captured["docs"])
    # New evidence on a completed ride re-queues route finalization.
    update.assert_awaited_once()
    args, _ = update.await_args
    assert args[:2] == ("ride_routes", {"ride_id": "ride_1"})
    assert args[2]["processing_status"] == "pending"


def test_points_captured_after_completion_are_rejected_individually():
    """after_ride_window is a per-point captured_at bound, not a batch bound."""
    captured = {}
    update = AsyncMock()

    async def _insert(table, docs, *, on_conflict):
        captured["docs"] = docs
        return docs

    with (
        patch("backend.utils.breadcrumbs.db_supabase.insert_many_ignore_conflicts", _insert),
        patch("backend.utils.breadcrumbs.db_supabase.update_one", update),
    ):
        result = _run(
            persist_trip_location_batch(
                "drv_1",
                "ride_1",
                SESSION,
                [
                    _point(9, "2026-07-19T10:03:58Z"),  # inside the window
                    _point(10, "2026-07-19T10:05:30Z"),  # after completion
                ],
                active_ride=_completed_ride(),
            )
        )

    assert result.ack.accepted_count == 1
    # The rejected point is permanently unprocessable — the ack must still
    # advance past it so the client can delete it instead of retrying forever.
    assert result.ack.acked_through == 10
    assert result.ack.to_dict()["rejected"] == [{"sequence_number": 10, "reason": "after_ride_window"}]
    assert [row["sequence_number"] for row in captured["docs"]] == [9]


def test_fully_late_batch_rejects_everything_and_does_not_requeue():
    update = AsyncMock()
    insert = AsyncMock(return_value=[])

    with (
        patch("backend.utils.breadcrumbs.db_supabase.insert_many_ignore_conflicts", insert),
        patch("backend.utils.breadcrumbs.db_supabase.update_one", update),
    ):
        result = _run(
            persist_trip_location_batch(
                "drv_1",
                "ride_1",
                SESSION,
                [
                    _point(10, "2026-07-19T10:05:30Z"),
                    _point(11, "2026-07-19T10:06:00Z"),
                ],
                active_ride=_completed_ride(),
            )
        )

    assert result.ack.accepted_count == 0
    assert result.inserted_count == 0
    assert {r["reason"] for r in result.ack.to_dict()["rejected"]} == {"after_ride_window"}
    # Nothing inserted → no rows → no finalization churn.
    insert.assert_not_awaited()
    update.assert_not_awaited()


def test_completion_fix_at_exact_completion_timestamp_is_accepted():
    """The completion fix is captured at (not after) ride_completed_at —
    the window bound must be inclusive or every completion fix would be lost."""
    captured = {}
    update = AsyncMock()

    async def _insert(table, docs, *, on_conflict):
        captured["docs"] = docs
        return docs

    with (
        patch("backend.utils.breadcrumbs.db_supabase.insert_many_ignore_conflicts", _insert),
        patch("backend.utils.breadcrumbs.db_supabase.update_one", update),
    ):
        result = _run(
            persist_trip_location_batch(
                "drv_1",
                "ride_1",
                SESSION,
                [_point(12, COMPLETED, is_completion_fix=True, source="completion")],
                active_ride=_completed_ride(),
            )
        )

    assert result.ack.accepted_count == 1
    assert captured["docs"][0]["is_completion_fix"] is True
