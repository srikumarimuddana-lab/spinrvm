"""Replay-safety tests for push_retry (F5): atomic per-row lease claim.

Two replicas run the loop concurrently. Before this fix both could SELECT the
same `sent_at IS NULL` row and both deliver it (duplicate ride-offer / SOS
pushes). `_process_row` now leases each row via a compare-and-swap on `attempts`
before sending; only the winning replica delivers.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_ROW = {
    "id": "row-1",
    "user_id": "user-1",
    "title": "Ride offer",
    "body": "New ride",
    "data": {},
    "attempts": 0,
    "target_app": "rider",
    "users": {"fcm_token_rider": "ExponentPushToken[abc]"},
}


def test_process_row_skips_send_when_claim_lost():
    """A row already claimed by another replica must NOT be delivered again."""
    from backend.utils import push_retry as pr

    with (
        patch("backend.utils.push_retry._claim_row", AsyncMock(return_value=False)),
        patch("backend.utils.push_retry._is_expo_token", MagicMock(return_value=True)),
        patch("backend.utils.push_retry._send_expo_push", AsyncMock(return_value=True)) as send,
        patch("backend.utils.push_retry.run_sync", AsyncMock(return_value=None)) as rs,
    ):
        asyncio.run(pr._process_row(dict(_ROW)))

    send.assert_not_awaited()  # claim lost -> no duplicate delivery
    rs.assert_not_awaited()  # and no sent_at write


def test_process_row_delivers_and_marks_sent_when_claim_won():
    from backend.utils import push_retry as pr

    with (
        patch("backend.utils.push_retry._claim_row", AsyncMock(return_value=True)),
        patch("backend.utils.push_retry._is_expo_token", MagicMock(return_value=True)),
        patch("backend.utils.push_retry._send_expo_push", AsyncMock(return_value=True)) as send,
        patch("backend.utils.push_retry.run_sync", AsyncMock(return_value=None)) as rs,
    ):
        asyncio.run(pr._process_row(dict(_ROW)))

    send.assert_awaited_once()  # winner delivers
    rs.assert_awaited()  # sent_at marked


def _supabase_with_claim_result(rows):
    sb = MagicMock()
    chain = sb.table.return_value.update.return_value.eq.return_value.eq.return_value.is_.return_value
    chain.execute.return_value = MagicMock(data=rows)
    return sb


def test_claim_row_true_when_row_returned_false_when_zero_rows():
    from backend.utils import push_retry as pr

    async def _passthrough(fn):
        return fn()

    # Non-empty data -> this worker won the compare-and-swap.
    with (
        patch("backend.utils.push_retry.supabase", _supabase_with_claim_result([{"id": "row-1"}])),
        patch("backend.utils.push_retry.run_sync", AsyncMock(side_effect=_passthrough)),
    ):
        assert asyncio.run(pr._claim_row("row-1", 0, "2026-01-01T00:00:00+00:00")) is True

    # Zero rows -> another replica already advanced attempts; we skip.
    with (
        patch("backend.utils.push_retry.supabase", _supabase_with_claim_result([])),
        patch("backend.utils.push_retry.run_sync", AsyncMock(side_effect=_passthrough)),
    ):
        assert asyncio.run(pr._claim_row("row-1", 0, "2026-01-01T00:00:00+00:00")) is False
