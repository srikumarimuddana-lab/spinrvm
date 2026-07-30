"""Tests for utils/driver_statement_job.py — replay-safe periodic statements."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from utils import driver_statement_job as job

DRIVER = {"id": "d1", "user_id": "u1", "name": "Fallback Name"}
USER = {"id": "u1", "first_name": "Har", "last_name": "S", "email": "drv@example.com"}


def _stmt(active=True):
    return {
        "driver_id": "d1",
        "period_type": "weekly",
        "period_label": "Jul 20 - 26, 2026",
        "trips": 2,
        "earnings": {"total": "74.85"},
        "payouts_total": "60.00",
        "has_activity": active,
    }


def test_due_periods_covers_prior_week_and_month():
    # Thu 2026-07-30: week ending Sun 07-26 is 4 days closed (grace elapsed),
    # June closed a month ago.
    periods = job._due_periods(date(2026, 7, 30))
    assert (job.WEEKLY, date(2026, 7, 20), date(2026, 7, 26)) in periods
    assert (job.MONTHLY, date(2026, 6, 1), date(2026, 6, 30)) in periods


def test_due_periods_withholds_a_period_inside_its_grace_window():
    """A statement must not be cut the moment a period closes: add_tip accepts
    a tip on any completed ride, and a tip landing after the cut would appear
    on NO statement (the ledger blocks a redo; the next period excludes the
    ride). Mon 2026-07-27 is 1 day after the week closed — still in grace."""
    periods = job._due_periods(date(2026, 7, 27))
    assert (job.WEEKLY, date(2026, 7, 20), date(2026, 7, 26)) not in periods
    # ...and it becomes due once the grace has fully elapsed (Wed 07-29).
    assert (job.WEEKLY, date(2026, 7, 20), date(2026, 7, 26)) in job._due_periods(date(2026, 7, 29))


def test_due_periods_catches_up_the_previous_period_after_an_outage():
    """A multi-day outage spanning a boundary must not skip a period forever —
    the ledger claim makes the extra scan free."""
    periods = job._due_periods(date(2026, 7, 30))
    weekly = [p for p in periods if p[0] == job.WEEKLY]
    assert (job.WEEKLY, date(2026, 7, 13), date(2026, 7, 19)) in weekly
    monthly = [p for p in periods if p[0] == job.MONTHLY]
    assert (job.MONTHLY, date(2026, 5, 1), date(2026, 5, 31)) in monthly


def test_due_periods_monthly_grace_on_the_first_of_the_month():
    """On the 1st the month just closed — inside grace, so not yet due."""
    periods = job._due_periods(date(2026, 7, 1))
    assert (job.MONTHLY, date(2026, 6, 1), date(2026, 6, 30)) not in periods
    assert (job.MONTHLY, date(2026, 6, 1), date(2026, 6, 30)) in job._due_periods(date(2026, 7, 3))


def test_statement_id_is_deterministic():
    assert job.statement_id_for("weekly", date(2026, 7, 20), "d1") == "stmt-weekly-2026-07-20-d1"


@pytest.mark.asyncio
async def test_process_driver_sends_email_and_marks_sent():
    with (
        patch.object(job, "db_supabase") as db,
        patch.object(job, "build_statement", AsyncMock(return_value=_stmt())),
        patch.object(job, "generate_driver_statement_pdf", return_value=b"%PDF-fake"),
        patch.object(job, "send_email", AsyncMock(return_value=True)) as send,
    ):
        db.insert_one = AsyncMock()
        db.update_one = AsyncMock()
        db.get_user_by_id = AsyncMock(return_value=USER)
        await job._process_driver(DRIVER, "weekly", date(2026, 7, 20))

    claim = db.insert_one.call_args.args[1]
    assert claim["id"] == "stmt-weekly-2026-07-20-d1"
    assert claim["status"] == "claimed"
    assert claim["period_end"] == "2026-07-26"

    send.assert_awaited_once()
    kwargs = send.call_args.kwargs
    assert kwargs["to"] == "drv@example.com"
    assert "weekly earnings statement" in kwargs["subject"]
    assert kwargs["attachments"][0]["filename"] == "spinr-statement-weekly-2026-07-20.pdf"
    assert kwargs["attachments"][0]["content"].startswith(b"%PDF")

    final = db.update_one.call_args.args[2]
    assert final["status"] == "sent"
    assert final["email_sent_at"]
    assert final["totals"]["payouts_total"] == "60.00"


@pytest.mark.asyncio
async def test_process_driver_claim_conflict_skips_all_work():
    """Unique violation on the claim insert = another replica owns it —
    nothing may be built or sent (the replay-safety contract)."""
    with (
        patch.object(job, "db_supabase") as db,
        patch.object(job, "build_statement", AsyncMock()) as build,
        patch.object(job, "send_email", AsyncMock()) as send,
    ):
        db.insert_one = AsyncMock(side_effect=Exception("duplicate key value violates unique constraint"))
        db.update_one = AsyncMock()
        await job._process_driver(DRIVER, "weekly", date(2026, 7, 20))

    build.assert_not_awaited()
    send.assert_not_awaited()
    db.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_driver_non_unique_insert_error_raises():
    """A real DB failure on the claim must surface (caller logs it), never be
    swallowed as if claimed elsewhere."""
    with patch.object(job, "db_supabase") as db:
        db.insert_one = AsyncMock(side_effect=Exception("connection reset"))
        with pytest.raises(Exception, match="connection reset"):
            await job._process_driver(DRIVER, "weekly", date(2026, 7, 20))


@pytest.mark.asyncio
async def test_process_driver_inactive_skips_email():
    with (
        patch.object(job, "db_supabase") as db,
        patch.object(job, "build_statement", AsyncMock(return_value=_stmt(active=False))),
        patch.object(job, "send_email", AsyncMock()) as send,
    ):
        db.insert_one = AsyncMock()
        db.update_one = AsyncMock()
        db.get_user_by_id = AsyncMock(return_value=USER)
        await job._process_driver(DRIVER, "weekly", date(2026, 7, 20))

    send.assert_not_awaited()
    assert db.update_one.call_args.args[2]["status"] == "skipped_inactive"


@pytest.mark.asyncio
async def test_process_driver_no_email_recorded():
    with (
        patch.object(job, "db_supabase") as db,
        patch.object(job, "build_statement", AsyncMock(return_value=_stmt())),
        patch.object(job, "send_email", AsyncMock()) as send,
    ):
        db.insert_one = AsyncMock()
        db.update_one = AsyncMock()
        db.get_user_by_id = AsyncMock(return_value={**USER, "email": ""})
        await job._process_driver(DRIVER, "weekly", date(2026, 7, 20))

    send.assert_not_awaited()
    assert db.update_one.call_args.args[2]["status"] == "skipped_no_email"


@pytest.mark.asyncio
async def test_process_driver_send_failure_marks_failed():
    with (
        patch.object(job, "db_supabase") as db,
        patch.object(job, "build_statement", AsyncMock(return_value=_stmt())),
        patch.object(job, "generate_driver_statement_pdf", return_value=b"%PDF-fake"),
        patch.object(job, "send_email", AsyncMock(side_effect=RuntimeError("SES down"))),
    ):
        db.insert_one = AsyncMock()
        db.update_one = AsyncMock()
        db.get_user_by_id = AsyncMock(return_value=USER)
        await job._process_driver(DRIVER, "weekly", date(2026, 7, 20))

    final = db.update_one.call_args.args[2]
    assert final["status"] == "failed"
    assert "SES down" in final["failure_reason"]


@pytest.mark.asyncio
async def test_ensure_period_pages_past_the_postgrest_row_cap():
    """PostgREST truncates a query at db-max-rows with no signal, so a single
    big-limit read would silently skip drivers — and the job would consider the
    period fully handled, so nothing would ever surface it."""
    total = job._PAGE_SIZE + 137
    drivers = [{"id": f"drv_{i:05d}", "user_id": f"u{i}"} for i in range(total)]

    async def _get_rows(table, filters=None, **kw):
        if table == "driver_statements":
            return []
        if table == "drivers":
            offset = kw.get("offset") or 0
            limit = kw.get("limit")
            # Model the server-side cap: never return more than one page.
            return drivers[offset : offset + limit]
        return []

    with (
        patch.object(job, "db_supabase") as db,
        patch.object(job, "_process_driver", AsyncMock()) as proc,
    ):
        db.get_rows = AsyncMock(side_effect=_get_rows)
        await job._ensure_period("weekly", date(2026, 7, 20), date(2026, 7, 26))

    assert proc.await_count == total
    processed = {c.args[0]["id"] for c in proc.await_args_list}
    assert len(processed) == total


@pytest.mark.asyncio
async def test_ensure_period_only_processes_unclaimed_drivers():
    drivers = [{"id": "d1", "user_id": "u1"}, {"id": "d2", "user_id": "u2"}]

    async def _get_rows(table, filters=None, **kw):
        if table == "driver_statements":
            return [{"driver_id": "d1"}]  # d1 already has a statement
        if table == "drivers":
            return drivers
        return []

    with (
        patch.object(job, "db_supabase") as db,
        patch.object(job, "_process_driver", AsyncMock()) as proc,
    ):
        db.get_rows = AsyncMock(side_effect=_get_rows)
        await job._ensure_period("weekly", date(2026, 7, 20), date(2026, 7, 26))

    proc.assert_awaited_once()
    assert proc.call_args.args[0]["id"] == "d2"
