"""Tests for utils/t4a_annual_job.py — P4-7 CRA T4A annual issuance loop.

Covers:
- Eligible driver selection (earnings >= $500 threshold)
- Ineligible drivers are excluded (earnings < $500)
- Push notification sent per eligible driver
- Audit log written once per batch
- Redis leader lock prevents duplicate runs
- Loop skips when not last day of February
- Loop skips when hour < 08:00 UTC
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_driver(driver_id: str, user_id: str) -> dict:
    return {"id": driver_id, "user_id": user_id}


def _make_ride(driver_id: str, earnings: str) -> dict:
    return {
        "driver_id": driver_id,
        "status": "completed",
        "driver_earnings": earnings,
        "ride_completed_at": "2025-06-15T10:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# _driver_annual_earnings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_driver_annual_earnings_sums_correctly():
    rides = [_make_ride("d1", "200.00"), _make_ride("d1", "350.50")]
    with patch("utils.t4a_annual_job.db_supabase") as mock_db:
        mock_db.get_rows = AsyncMock(return_value=rides)
        from utils.t4a_annual_job import _driver_annual_earnings

        total = await _driver_annual_earnings("d1", 2025)

    assert total == Decimal("550.50")


@pytest.mark.asyncio
async def test_driver_annual_earnings_empty_rides():
    with patch("utils.t4a_annual_job.db_supabase") as mock_db:
        mock_db.get_rows = AsyncMock(return_value=[])
        from utils.t4a_annual_job import _driver_annual_earnings

        total = await _driver_annual_earnings("d1", 2025)

    assert total == Decimal("0")


@pytest.mark.asyncio
async def test_driver_annual_earnings_handles_none_earnings():
    rides = [{"driver_id": "d1", "status": "completed", "driver_earnings": None}]
    with patch("utils.t4a_annual_job.db_supabase") as mock_db:
        mock_db.get_rows = AsyncMock(return_value=rides)
        from utils.t4a_annual_job import _driver_annual_earnings

        total = await _driver_annual_earnings("d1", 2025)

    assert total == Decimal("0")


@pytest.mark.asyncio
async def test_driver_annual_earnings_includes_stripe_synced_payouts():
    """Legacy-era income synced from Stripe transfer history
    (payout_type='stripe_sync') counts toward the $500 threshold and the
    notified amount — the old app's rides were never imported, so the synced
    payouts are the only record of that income."""
    rides = [_make_ride("d1", "300.00")]
    synced = [
        {"driver_id": "d1", "payout_type": "stripe_sync", "amount": 250.25},
        {"driver_id": "d1", "payout_type": "stripe_sync", "amount": "100.00"},
    ]

    async def _get_rows(table, filters=None, **kw):
        if table == "rides":
            return rides
        if table == "payouts":
            assert filters["payout_type"] == {"$in": ["stripe_sync", "legacy_outstanding_correction"]}
            assert filters["status"] == "completed"
            assert filters["created_at"]["$gte"].startswith("2025-01-01")
            assert filters["created_at"]["$lt"].startswith("2026-01-01")
            return synced
        return []

    with patch("utils.t4a_annual_job.db_supabase") as mock_db:
        mock_db.get_rows = AsyncMock(side_effect=_get_rows)
        from utils.t4a_annual_job import _driver_annual_earnings

        total = await _driver_annual_earnings("d1", 2025)

    assert total == Decimal("650.25")


@pytest.mark.asyncio
async def test_driver_annual_earnings_includes_settled_correction_payouts():
    """A settled legacy_outstanding_correction (2026-08-17 write path)
    counts toward the $500 threshold exactly like stripe_sync — both are
    real legacy income actually paid through Stripe."""
    rides = [_make_ride("d1", "300.00")]
    synced = [{"driver_id": "d1", "payout_type": "legacy_outstanding_correction", "amount": 12.50}]

    async def _get_rows(table, filters=None, **kw):
        if table == "rides":
            return rides
        if table == "payouts":
            return synced
        return []

    with patch("utils.t4a_annual_job.db_supabase") as mock_db:
        mock_db.get_rows = AsyncMock(side_effect=_get_rows)
        from utils.t4a_annual_job import _driver_annual_earnings

        total = await _driver_annual_earnings("d1", 2025)

    assert total == Decimal("312.50")


# ---------------------------------------------------------------------------
# _run_issuance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_issuance_notifies_eligible_skips_ineligible():
    """Driver A ($600) notified; driver B ($300) skipped; audit log written."""
    drivers = [
        _make_driver("dA", "uA"),
        _make_driver("dB", "uB"),
    ]

    async def fake_earnings(driver_id: str, year: int) -> Decimal:
        return Decimal("600.00") if driver_id == "dA" else Decimal("300.00")

    push_mock = AsyncMock()
    audit_mock = AsyncMock()

    with (
        patch("utils.t4a_annual_job.db_supabase") as mock_db,
        patch("utils.t4a_annual_job._driver_annual_earnings", side_effect=fake_earnings),
        patch("utils.t4a_annual_job.send_push_notification", push_mock),
        patch("utils.t4a_annual_job.log_admin_action", audit_mock),
    ):
        mock_db.get_rows = AsyncMock(return_value=drivers)

        from utils.t4a_annual_job import _run_issuance

        await _run_issuance(2025)

    # Only driver A gets a push
    assert push_mock.call_count == 1
    push_args = push_mock.call_args
    assert push_args[0][0] == "uA"
    assert "2025" in push_args[1]["body"] or "2025" in str(push_args)

    # Audit log written once
    audit_mock.assert_called_once()
    audit_kwargs = audit_mock.call_args[1]
    assert audit_kwargs["action"] == "t4a_annual_issuance"
    assert audit_kwargs["details"]["eligible_count"] == 1
    assert audit_kwargs["details"]["notified_count"] == 1
    assert audit_kwargs["details"]["total_drivers"] == 2


@pytest.mark.asyncio
async def test_run_issuance_no_eligible_drivers():
    """No drivers above threshold — no pushes, audit still written."""
    drivers = [_make_driver("dA", "uA")]

    push_mock = AsyncMock()
    audit_mock = AsyncMock()

    with (
        patch("utils.t4a_annual_job.db_supabase") as mock_db,
        patch("utils.t4a_annual_job._driver_annual_earnings", AsyncMock(return_value=Decimal("100.00"))),
        patch("utils.t4a_annual_job.send_push_notification", push_mock),
        patch("utils.t4a_annual_job.log_admin_action", audit_mock),
    ):
        mock_db.get_rows = AsyncMock(return_value=drivers)

        from utils.t4a_annual_job import _run_issuance

        await _run_issuance(2025)

    push_mock.assert_not_called()
    audit_mock.assert_called_once()
    assert audit_mock.call_args[1]["details"]["eligible_count"] == 0


@pytest.mark.asyncio
async def test_run_issuance_skips_driver_with_no_user_id():
    """Drivers missing user_id are skipped silently."""
    drivers = [{"id": "dA", "user_id": None}]

    push_mock = AsyncMock()

    with (
        patch("utils.t4a_annual_job.db_supabase") as mock_db,
        patch("utils.t4a_annual_job._driver_annual_earnings", AsyncMock(return_value=Decimal("999.00"))),
        patch("utils.t4a_annual_job.send_push_notification", push_mock),
        patch("utils.t4a_annual_job.log_admin_action", AsyncMock()),
    ):
        mock_db.get_rows = AsyncMock(return_value=drivers)

        from utils.t4a_annual_job import _run_issuance

        await _run_issuance(2025)

    push_mock.assert_not_called()


@pytest.mark.asyncio
async def test_run_issuance_continues_after_push_failure():
    """Push failure for one driver doesn't abort the rest."""
    drivers = [_make_driver("dA", "uA"), _make_driver("dB", "uB")]

    call_count = 0

    async def flaky_push(user_id, **kwargs):
        nonlocal call_count
        call_count += 1
        if user_id == "uA":
            raise RuntimeError("FCM down")

    with (
        patch("utils.t4a_annual_job.db_supabase") as mock_db,
        patch("utils.t4a_annual_job._driver_annual_earnings", AsyncMock(return_value=Decimal("600.00"))),
        patch("utils.t4a_annual_job.send_push_notification", side_effect=flaky_push),
        patch("utils.t4a_annual_job.log_admin_action", AsyncMock()),
    ):
        mock_db.get_rows = AsyncMock(return_value=drivers)

        from utils.t4a_annual_job import _run_issuance

        await _run_issuance(2025)  # must not raise

    assert call_count == 2  # attempted both


# ---------------------------------------------------------------------------
# _maybe_run_tick — date/time guards and Redis lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maybe_run_tick_skips_when_not_last_day_of_february():
    """Does nothing on a day that is not last-day-of-February."""
    from datetime import datetime, timezone

    not_feb_28 = datetime(2025, 3, 1, 10, 0, tzinfo=timezone.utc)

    with (
        patch("utils.t4a_annual_job.datetime") as mock_dt,
        patch("utils.t4a_annual_job._run_issuance", AsyncMock()) as mock_issue,
    ):
        mock_dt.now.return_value = not_feb_28

        from utils.t4a_annual_job import _maybe_run_tick

        await _maybe_run_tick()

    mock_issue.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_run_tick_skips_before_run_hour():
    """Does nothing when it is last-day-of-Feb but hour < 08:00 UTC."""
    from datetime import datetime, timezone

    early = datetime(2025, 2, 28, 7, 59, tzinfo=timezone.utc)

    with (
        patch("utils.t4a_annual_job.datetime") as mock_dt,
        patch("utils.t4a_annual_job._run_issuance", AsyncMock()) as mock_issue,
    ):
        mock_dt.now.return_value = early

        from utils.t4a_annual_job import _maybe_run_tick

        await _maybe_run_tick()

    mock_issue.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_run_tick_redis_lock_prevents_duplicate():
    """Second call with same lock key is a no-op."""
    from datetime import datetime, timezone

    feb28 = datetime(2025, 2, 28, 9, 0, tzinfo=timezone.utc)

    with (
        patch("utils.t4a_annual_job.datetime") as mock_dt,
        patch("utils.t4a_annual_job.redis_set_nx", AsyncMock(return_value=False)),
        patch("utils.t4a_annual_job._run_issuance", AsyncMock()) as mock_issue,
    ):
        mock_dt.now.return_value = feb28

        from utils.t4a_annual_job import _maybe_run_tick

        await _maybe_run_tick()

    mock_issue.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_run_tick_runs_when_lock_acquired():
    """Runs issuance exactly once when Redis lock is acquired on Feb 28."""
    from datetime import datetime, timezone

    feb28 = datetime(2025, 2, 28, 9, 0, tzinfo=timezone.utc)

    with (
        patch("utils.t4a_annual_job.datetime") as mock_dt,
        patch("utils.t4a_annual_job.redis_set_nx", AsyncMock(return_value=True)),
        patch("utils.t4a_annual_job._run_issuance", AsyncMock()) as mock_issue,
    ):
        mock_dt.now.return_value = feb28

        from utils.t4a_annual_job import _maybe_run_tick

        await _maybe_run_tick()

    mock_issue.assert_called_once_with(2024)  # prior year


@pytest.mark.asyncio
async def test_maybe_run_tick_leap_year_feb29():
    """In a leap year the job fires on Feb 29 (last day of Feb)."""
    from datetime import datetime, timezone

    feb29_leap = datetime(2028, 2, 29, 9, 0, tzinfo=timezone.utc)

    with (
        patch("utils.t4a_annual_job.datetime") as mock_dt,
        patch("utils.t4a_annual_job.redis_set_nx", AsyncMock(return_value=True)),
        patch("utils.t4a_annual_job._run_issuance", AsyncMock()) as mock_issue,
    ):
        mock_dt.now.return_value = feb29_leap

        from utils.t4a_annual_job import _maybe_run_tick

        await _maybe_run_tick()

    mock_issue.assert_called_once_with(2027)
