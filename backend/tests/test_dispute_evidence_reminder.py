"""C23 Action item 2: dispute_evidence_reminder_loop() fires an alert exactly
once per dispute whose evidence_due_by falls within 3 days, via an atomic
claim on evidence_reminder_sent_at (migration 327) -- same idempotency shape
as routes/drivers/subscriptions.py's expiry_warned_3d, so the test structure
mirrors test_subscriptions_coverage.py's TestExpiryWarning3Day suite.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _stop_sleep():
    """asyncio.sleep patched to blow up so the `while True:` loop exits
    after exactly one iteration."""
    return AsyncMock(side_effect=Exception("stop"))


async def _run_once(**extra_patches):
    from backend.utils import dispute_evidence_reminder as der

    patches = {
        "backend.utils.dispute_evidence_reminder.asyncio.sleep": _stop_sleep(),
        "backend.utils.dispute_evidence_reminder.get_rows": AsyncMock(return_value=[]),
        "backend.utils.dispute_evidence_reminder.update_one": AsyncMock(),
    }
    patches.update(extra_patches)

    with contextlib.ExitStack() as stack:
        mocks = {}
        for target, mock_obj in patches.items():
            stack.enter_context(patch(target, mock_obj))
            mocks[target] = mock_obj
        try:
            await der.dispute_evidence_reminder_loop()
        except Exception as e:
            if "stop" not in str(e):
                raise
    return mocks


pytestmark = pytest.mark.anyio


class TestDisputeEvidenceReminderLoop:
    async def test_alerts_on_dispute_due_within_3_days(self):
        now = datetime.now(timezone.utc)
        dispute = {
            "id": "row-1",
            "stripe_dispute_id": "dp_1",
            "ride_id": "ride_1",
            "amount_cents": 2500,
            "reason": "fraudulent",
            "evidence_due_by": (now + timedelta(days=2)).isoformat(),
        }
        update_mock = AsyncMock(return_value={"id": "row-1"})
        escalate_mock = MagicMock()

        await _run_once(
            **{
                "backend.utils.dispute_evidence_reminder.get_rows": AsyncMock(return_value=[dispute]),
                "backend.utils.dispute_evidence_reminder.update_one": update_mock,
                "backend.utils.dispute_evidence_reminder._escalate": escalate_mock,
            }
        )

        update_mock.assert_awaited_once()
        assert update_mock.await_args.args[0] == "stripe_disputes"
        assert update_mock.await_args.args[1] == {"id": "row-1", "evidence_reminder_sent_at": None}
        assert update_mock.await_args.args[2]["$set"]["evidence_reminder_sent_at"] is not None
        escalate_mock.assert_called_once()

    async def test_lost_claim_race_skips_alert(self):
        """Another replica already claimed this dispute -- update_one
        returns None (zero rows matched) and no alert fires."""
        now = datetime.now(timezone.utc)
        dispute = {
            "id": "row-2",
            "stripe_dispute_id": "dp_2",
            "ride_id": "ride_2",
            "evidence_due_by": (now + timedelta(days=1)).isoformat(),
        }
        escalate_mock = MagicMock()

        await _run_once(
            **{
                "backend.utils.dispute_evidence_reminder.get_rows": AsyncMock(return_value=[dispute]),
                "backend.utils.dispute_evidence_reminder.update_one": AsyncMock(return_value=None),
                "backend.utils.dispute_evidence_reminder._escalate": escalate_mock,
            }
        )

        escalate_mock.assert_not_called()

    async def test_due_by_outside_window_is_not_queried_but_defensively_rechecked(self):
        """If the query somehow returns a row whose due_by has drifted
        outside (now, now+3d] between query and claim time, the tick must
        re-check rather than trust the query result blindly."""
        now = datetime.now(timezone.utc)
        stale_dispute = {
            "id": "row-3",
            "stripe_dispute_id": "dp_3",
            "ride_id": "ride_3",
            "evidence_due_by": (now - timedelta(hours=1)).isoformat(),  # already past
        }
        update_mock = AsyncMock(return_value={"id": "row-3"})

        await _run_once(
            **{
                "backend.utils.dispute_evidence_reminder.get_rows": AsyncMock(return_value=[stale_dispute]),
                "backend.utils.dispute_evidence_reminder.update_one": update_mock,
            }
        )

        update_mock.assert_not_awaited()

    async def test_malformed_due_by_skipped_not_raised(self):
        dispute = {
            "id": "row-4",
            "stripe_dispute_id": "dp_4",
            "ride_id": "ride_4",
            "evidence_due_by": "not-a-timestamp",
        }
        update_mock = AsyncMock()

        await _run_once(
            **{
                "backend.utils.dispute_evidence_reminder.get_rows": AsyncMock(return_value=[dispute]),
                "backend.utils.dispute_evidence_reminder.update_one": update_mock,
            }
        )

        update_mock.assert_not_awaited()

    async def test_heartbeat_recorded_even_after_tick_exception(self):
        """CLAUDE.md: 'a loop that silently never starts is worse than one
        that's slow' -- record_heartbeat must fire on every iteration,
        including one where _tick() itself raised, so the watchdog
        (core/lifespan.py's _WATCHDOG_LOOP_NAMES) can page on a genuinely
        stalled loop rather than staying blind to it."""
        heartbeat_mock = MagicMock()

        await _run_once(
            **{
                "backend.utils.dispute_evidence_reminder.get_rows": AsyncMock(side_effect=Exception("db down")),
                "backend.utils.loop_monitor.record_heartbeat": heartbeat_mock,
            }
        )

        heartbeat_mock.assert_called_once_with("dispute_evidence_reminder (6h)")

    async def test_tick_exception_logged_not_raised(self):
        """A failed tick (e.g. DB error) must not crash the loop -- logs and
        waits for the next tick."""
        await _run_once(
            **{
                "backend.utils.dispute_evidence_reminder.get_rows": AsyncMock(side_effect=Exception("db down")),
            }
        )
        # No assertion beyond _run_once not propagating anything but the
        # sleep-stop sentinel -- reaching here means the tick's exception
        # was caught internally.

    async def test_query_filters_out_closed_and_already_reminded_disputes(self):
        """Confirms the query filter shape: $nin on status, None on both
        claim-flag columns -- a regression pin so a future refactor can't
        silently widen this to alert on resolved disputes."""
        get_rows_mock = AsyncMock(return_value=[])

        await _run_once(
            **{
                "backend.utils.dispute_evidence_reminder.get_rows": get_rows_mock,
            }
        )

        get_rows_mock.assert_awaited_once()
        call = get_rows_mock.await_args
        assert call.args[0] == "stripe_disputes"
        filters = call.args[1]
        assert filters["evidence_reminder_sent_at"] is None
        assert filters["evidence_submitted_at"] is None
        assert set(filters["status"]["$nin"]) == {"won", "lost", "warning_closed"}
