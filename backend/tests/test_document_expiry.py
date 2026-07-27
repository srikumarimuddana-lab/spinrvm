"""Regression tests for utils/document_expiry.py (P0-5).

Covers:
- Expired driver is suspended (is_online=False, status='suspended')
- Suspended driver is not included in find_nearby_drivers results
- Documents already past their expiry date are processed (loop-bound fix)
- Driver with no expired documents is not suspended
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module-level imports — all external calls are patched in each test.
# ---------------------------------------------------------------------------


@pytest.fixture
def now():
    return datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)


def _make_driver(
    driver_id: str = "d1",
    user_id: str = "u1",
    license_expiry: str | None = None,
    status: str = "active",
) -> dict:
    return {
        "id": driver_id,
        "user_id": user_id,
        "license_expiry_date": license_expiry,
        "insurance_expiry_date": None,
        "vehicle_inspection_expiry_date": None,
        "background_check_expiry_date": None,
        "work_eligibility_expiry_date": None,
        "status": status,
        "doc_expiry_warned_at": None,
    }


# ---------------------------------------------------------------------------
# P0-5 sub-fix 1 + 3: expired docs → driver is suspended
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_expired_driver_is_suspended(now):
    """Driver whose licence expired yesterday must be suspended and disconnected."""
    expired_ts = (now - timedelta(days=1)).isoformat()
    driver = _make_driver(license_expiry=expired_ts)

    mock_db = MagicMock()
    mock_db.get_rows = AsyncMock(
        side_effect=[
            [driver],  # all_drivers fetch
            [],  # driver_documents fetch (no extra docs)
        ]
    )
    # Truthy return: the suspension update_one is a replay-safety claim
    # (F6) -- the caller only proceeds to clear_presence/disconnect/notify
    # when the update actually claimed a row (falsy means already
    # suspended by a prior tick or sibling replica).
    mock_db.update_one = AsyncMock(return_value=driver)

    mock_manager = MagicMock()
    mock_send_push = AsyncMock()
    mock_clear_presence = AsyncMock()

    with (
        patch("utils.document_expiry.db", mock_db),
        patch("utils.document_expiry.manager", mock_manager),
        patch("utils.document_expiry.send_push_notification", mock_send_push),
        patch("utils.document_expiry.clear_presence", mock_clear_presence),
    ):
        from utils.document_expiry import check_expiring_documents

        await check_expiring_documents()

    # Must call update_one with a flat dict (no $set wrapper) — P0-5 sub-fix 1.
    # The filter also carries a status != 'suspended' replay-safety guard
    # (F6) so a re-tick or sibling replica can't re-suspend/re-notify an
    # already-suspended driver.
    mock_db.update_one.assert_awaited_once_with(
        "drivers",
        {"id": "d1", "status": {"$ne": "suspended"}},
        {"is_online": False, "is_available": False, "status": "suspended"},
    )
    # Presence must be cleared from Redis
    mock_clear_presence.assert_awaited_once_with("d1")
    # WS connection must be dropped
    mock_manager.disconnect.assert_called_once_with("driver_u1")
    # Suspension notification sent
    mock_send_push.assert_awaited_once()
    args = mock_send_push.call_args[0]
    assert args[0] == "u1"
    assert "suspended" in args[1].lower()


# ---------------------------------------------------------------------------
# P0-5 sub-fix 3: past-expiry loop bound
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_doc_expired_long_ago_is_still_processed(now):
    """A licence that expired 30 days ago must still trigger suspension.

    The original bug skipped documents already past their expiry date.
    """
    stale_expired_ts = (now - timedelta(days=30)).isoformat()
    driver = _make_driver(license_expiry=stale_expired_ts)

    mock_db = MagicMock()
    mock_db.get_rows = AsyncMock(side_effect=[[driver], []])
    mock_db.update_one = AsyncMock(return_value=None)

    with (
        patch("utils.document_expiry.db", mock_db),
        patch("utils.document_expiry.manager", MagicMock()),
        patch("utils.document_expiry.send_push_notification", AsyncMock()),
        patch("utils.document_expiry.clear_presence", AsyncMock()),
    ):
        from utils.document_expiry import check_expiring_documents

        await check_expiring_documents()

    mock_db.update_one.assert_awaited_once()


# ---------------------------------------------------------------------------
# Non-expired driver is never suspended
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_valid_driver_not_suspended():
    """Driver with a licence valid for 60 days must receive no suspension call.

    check_expiring_documents() compares against the real wall-clock
    datetime.now(), not a fixture -- computing "future" from the frozen
    `now` fixture (2026-04-28) meant this "60 days out" date could actually
    be in the past by the time the suite runs, making the driver look
    expired for real. Anchor to the real clock instead.
    """
    future_ts = (datetime.now(timezone.utc) + timedelta(days=60)).isoformat()
    driver = _make_driver(license_expiry=future_ts)

    mock_db = MagicMock()
    mock_db.get_rows = AsyncMock(side_effect=[[driver], []])
    mock_db.update_one = AsyncMock(return_value=None)

    with (
        patch("utils.document_expiry.db", mock_db),
        patch("utils.document_expiry.manager", MagicMock()),
        patch("utils.document_expiry.send_push_notification", AsyncMock()),
        patch("utils.document_expiry.clear_presence", AsyncMock()),
    ):
        from utils.document_expiry import check_expiring_documents

        await check_expiring_documents()

    mock_db.update_one.assert_not_awaited()


# ---------------------------------------------------------------------------
# P0-5 sub-fix 2: find_nearby_drivers SQL-level filter (unit test of the
# Python wrapper — the Postgres function itself is tested via migration 55)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_expired_driver_not_dispatched():
    """find_nearby_drivers must not return a suspended driver.

    The Postgres function (migration 55) enforces AND status != 'suspended'.
    This test verifies the Python wrapper returns only what the RPC returns —
    i.e., it does not re-introduce suspended drivers on the Python side.
    """
    active_driver = {"id": "d2", "name": "Alice", "status": "active"}

    mock_supabase = MagicMock()
    mock_rpc_result = MagicMock()
    mock_rpc_result.data = [active_driver]
    mock_supabase.rpc.return_value.execute.return_value = mock_rpc_result

    # find_nearby_drivers is implemented in repositories/driver_repo.py,
    # which imports its own `supabase` from repositories._base at module
    # level -- patching db_supabase.supabase doesn't reach it.
    with patch("repositories.driver_repo.supabase", mock_supabase):
        import db_supabase

        result = await db_supabase.find_nearby_drivers(52.1, -106.6, 5000)

    assert len(result) == 1
    assert result[0]["id"] == "d2"
    # Confirm the RPC was called with the right function name — the SQL-level
    # filter lives inside the 'find_nearby_drivers' Postgres function.
    mock_supabase.rpc.assert_called_once_with(
        "find_nearby_drivers",
        {"lat": 52.1, "lng": -106.6, "radius_meters": 5000},
    )
