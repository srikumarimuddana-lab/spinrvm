"""Unit tests for the driver deactivation-appeal service (migration 320).

Mirrors the mocking style used in tests/test_driver_crc_consent.py — direct
patches on db_supabase functions, no real DB. See
services/driver_appeals.py for why the account-reactivation side effect is
deliberately NOT tested here (it lives in routes/admin/driver_appeals.py,
which reuses routes.admin.drivers.admin_driver_action).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    from services.driver_appeals import (
        DuplicatePendingAppealError,
        create_appeal,
        get_appeal_stats,
        list_appeals,
        mark_resolved,
    )
except ImportError:
    from backend.services.driver_appeals import (  # type: ignore[no-redef]
        DuplicatePendingAppealError,
        create_appeal,
        get_appeal_stats,
        list_appeals,
        mark_resolved,
    )


@pytest.mark.anyio
async def test_create_appeal_rejects_invalid_appeal_type():
    with pytest.raises(ValueError, match="invalid appeal_type"):
        await create_appeal("driver-1", appeal_type="bogus", driver_message="please review")


@pytest.mark.anyio
async def test_create_appeal_rejects_empty_message():
    with patch("services.driver_appeals.db_supabase.find_one", AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="driver_message is required"):
            await create_appeal("driver-1", appeal_type="suspension", driver_message="   ")


@pytest.mark.anyio
async def test_create_appeal_blocks_a_second_pending_appeal():
    """One pending appeal at a time — matches the policy's review-then-
    respond process rather than letting a driver flood the queue."""
    existing_pending = {"id": "appeal-1", "driver_id": "driver-1", "status": "pending"}
    with patch("services.driver_appeals.db_supabase.find_one", AsyncMock(return_value=existing_pending)):
        with pytest.raises(DuplicatePendingAppealError):
            await create_appeal("driver-1", appeal_type="suspension", driver_message="please review")


@pytest.mark.anyio
async def test_create_appeal_converts_db_race_into_duplicate_error():
    """migration 320's partial unique index (driver_appeals_one_pending_per_driver)
    is the actual guarantee — the pre-check above is just a friendlier error
    path. Simulate two concurrent submissions both passing the pre-check by
    having db_supabase.insert_one raise DuplicateRecordError (the unique
    index violation), and confirm it surfaces as the same
    DuplicatePendingAppealError the pre-check produces, not a raw DB error."""
    from db_supabase import DuplicateRecordError

    with (
        patch("services.driver_appeals.db_supabase.find_one", AsyncMock(return_value=None)),
        patch(
            "services.driver_appeals.db_supabase.insert_one",
            AsyncMock(side_effect=DuplicateRecordError(details={"original": "unique violation"})),
        ),
    ):
        with pytest.raises(DuplicatePendingAppealError):
            await create_appeal("driver-1", appeal_type="suspension", driver_message="please review")


@pytest.mark.anyio
async def test_create_appeal_writes_expected_row():
    insert_mock = AsyncMock(return_value={"id": "new-appeal"})
    with (
        patch("services.driver_appeals.db_supabase.find_one", AsyncMock(return_value=None)),
        patch("services.driver_appeals.db_supabase.insert_one", insert_mock),
    ):
        out = await create_appeal(
            "driver-1",
            appeal_type="ban",
            driver_message="I was banned unfairly",
            original_reason="Accident on trip #123",
        )
    assert out == {"id": "new-appeal"}
    insert_args = insert_mock.await_args.args
    assert insert_args[0] == "driver_appeals"
    row = insert_args[1]
    assert row["driver_id"] == "driver-1"
    assert row["appeal_type"] == "ban"
    assert row["driver_message"] == "I was banned unfairly"
    assert row["original_reason"] == "Accident on trip #123"
    assert row["status"] == "pending"


@pytest.mark.anyio
async def test_list_appeals_rejects_invalid_status_filter():
    with pytest.raises(ValueError, match="invalid status"):
        await list_appeals(status="bogus")


@pytest.mark.anyio
async def test_list_appeals_filters_by_status():
    get_rows_mock = AsyncMock(return_value=[{"id": "a1", "status": "pending"}])
    with patch("services.driver_appeals.db_supabase.get_rows", get_rows_mock):
        out = await list_appeals(status="pending")
    assert out == [{"id": "a1", "status": "pending"}]
    call_args = get_rows_mock.await_args
    assert call_args.args[0] == "driver_appeals"
    assert call_args.args[1] == {"status": "pending"}


@pytest.mark.anyio
async def test_get_appeal_stats_counts_each_status():
    count_mock = AsyncMock(side_effect=[3, 5, 2])  # pending, approved, denied
    with patch("services.driver_appeals.db_supabase.count_documents", count_mock):
        out = await get_appeal_stats()
    assert out == {"pending": 3, "approved": 5, "denied": 2}


@pytest.mark.anyio
async def test_mark_resolved_rejects_invalid_status():
    with pytest.raises(ValueError, match="invalid resolution status"):
        await mark_resolved("appeal-1", status="pending", admin_note=None, resolved_by="admin-1")


@pytest.mark.anyio
async def test_mark_resolved_writes_expected_fields():
    update_mock = AsyncMock(return_value=None)
    with patch("services.driver_appeals.db_supabase.update_one", update_mock):
        await mark_resolved("appeal-1", status="approved", admin_note="Looks legitimate", resolved_by="admin-1")

    update_args = update_mock.await_args.args
    assert update_args[0] == "driver_appeals"
    assert update_args[1] == {"id": "appeal-1"}
    fields = update_args[2]
    assert fields["status"] == "approved"
    assert fields["admin_note"] == "Looks legitimate"
    assert fields["resolved_by"] == "admin-1"
    assert "resolved_at" in fields
    assert "updated_at" in fields
