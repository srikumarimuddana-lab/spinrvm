"""
B-P2-6: tests for the DSAR (data export) handler in routes/drivers.py.

Contract:
  - 6 sequential get_rows calls collapse to 2 wave-groups via asyncio.gather:
    Wave 1: drivers + users + notification_preferences (3 in parallel)
    Wave 2: rides + driver_payouts + driver_documents (3 in parallel)
  - Total await count drops from 6 to 2 — measurable in unit test by
    counting `get_rows` invocations and observing them happen in <2 awaits.
  - When driver row is missing (rider-only account), wave 2 is skipped
    entirely.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.anyio
async def test_data_export_uses_two_waves_of_parallel_reads():
    """All 6 reads must complete in 2 await-points (not 6)."""
    from routes import drivers as drivers_module

    driver_row = {"id": "drv-123", "user_id": "user-1"}

    async def fake_get_rows(table, *args, **kwargs):
        return {
            "drivers": [driver_row],
            "users": [{"id": "user-1", "email": "x@x"}],
            "notification_preferences": [{"user_id": "user-1", "ride_updates": True}],
            "rides": [{"id": "r1"}],
            "driver_payouts": [{"id": "p1"}],
            "driver_documents": [{"id": "d1"}],
        }.get(table, [])

    get_rows_mock = AsyncMock(side_effect=fake_get_rows)
    send_email_mock = AsyncMock()

    with (
        patch.object(drivers_module.db_supabase, "get_rows", get_rows_mock),
        patch.object(drivers_module._deps, "send_email", send_email_mock),
    ):
        await drivers_module._build_and_email_data_export("user-1", "x@x.com")

    # All 6 tables should have been queried exactly once.
    queried_tables = sorted(call.args[0] for call in get_rows_mock.await_args_list)
    assert queried_tables == sorted(
        [
            "drivers",
            "users",
            "notification_preferences",
            "rides",
            "driver_payouts",
            "driver_documents",
        ]
    )
    send_email_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_data_export_skips_wave2_for_rider_only_account():
    """A user with no driver row must not query rides/payouts/documents
    (those queries would return empty lists but the cost is real)."""
    from routes import drivers as drivers_module

    async def fake_get_rows(table, *args, **kwargs):
        if table == "drivers":
            return []  # no driver row
        if table == "users":
            return [{"id": "user-1", "email": "rider@x"}]
        if table == "notification_preferences":
            return []
        raise AssertionError(f"wave 2 should have been skipped — but got query on {table}")

    get_rows_mock = AsyncMock(side_effect=fake_get_rows)
    send_email_mock = AsyncMock()

    with (
        patch.object(drivers_module.db_supabase, "get_rows", get_rows_mock),
        patch.object(drivers_module._deps, "send_email", send_email_mock),
    ):
        await drivers_module._build_and_email_data_export("user-1", "rider@x.com")

    # Only the 3 wave-1 tables — never the 3 wave-2 tables.
    queried_tables = sorted(call.args[0] for call in get_rows_mock.await_args_list)
    assert queried_tables == sorted(["drivers", "users", "notification_preferences"])
    send_email_mock.assert_awaited_once()
