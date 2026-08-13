"""
B-P2-6 / N1: tests for the DSAR (data export) handler in routes/drivers.py.

Contract:
  - Sequential get_rows calls collapse into 3 wave-groups via asyncio.gather:
    Wave 1: drivers + users + notification_preferences (3 in parallel)
    Wave 2: rides (as driver) + driver_payouts + driver_documents (3 in
      parallel) — gated on having a `drivers` row (skipped for a rider-only
      account).
    Wave 3: rides (as rider, filtered by rider_id) + saved_addresses (2 in
      parallel) — NOT gated on driver_id; every account can have ridden as
      a passenger. Added for N1 (ACTION_ITEMS.md): a rider-only account's
      export previously contained only account + notification_preferences,
      which is not a real answer to a PIPEDA access request from someone
      whose relationship with Spinr is as a rider.
  - `rides` is queried twice when both waves run (once per filter — driver_id
    for wave 2, rider_id for wave 3) since a single account can be both.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.anyio
async def test_data_export_uses_three_waves_of_parallel_reads():
    """All reads must complete in 3 await-points, not one per table."""
    from routes import drivers as drivers_module

    driver_row = {"id": "drv-123", "user_id": "user-1"}

    async def fake_get_rows(table, filters=None, **kwargs):
        if table == "rides":
            if filters and filters.get("driver_id"):
                return [{"id": "r1"}]
            if filters and filters.get("rider_id"):
                return [{"id": "r2"}]
            return []
        return {
            "drivers": [driver_row],
            "users": [{"id": "user-1", "email": "x@x"}],
            "notification_preferences": [{"user_id": "user-1", "ride_updates": True}],
            "driver_payouts": [{"id": "p1"}],
            "driver_documents": [{"id": "d1"}],
            "saved_addresses": [{"id": "a1"}],
        }.get(table, [])

    get_rows_mock = AsyncMock(side_effect=fake_get_rows)
    send_email_mock = AsyncMock()

    with (
        patch.object(drivers_module.db_supabase, "get_rows", get_rows_mock),
        patch.object(drivers_module._deps, "send_email", send_email_mock),
        patch.object(drivers_module.tax_exports, "_upload_export_zip", AsyncMock(return_value="https://x/y.zip")),
    ):
        await drivers_module._build_and_email_data_export("user-1", "x@x.com")

    # "rides" appears twice (driver-filtered + rider-filtered); every other
    # DSAR-payload table appears at least once. Not an exact-equality check —
    # a successful link-email render (mocked _upload_export_zip above) reads
    # its own unrelated branding config via get_rows, which isn't part of
    # this contract.
    queried_tables = [call.args[0] for call in get_rows_mock.await_args_list]
    assert queried_tables.count("rides") == 2
    for table in (
        "drivers",
        "users",
        "notification_preferences",
        "driver_payouts",
        "driver_documents",
        "saved_addresses",
    ):
        assert table in queried_tables, f"expected a query on {table}"
    send_email_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_data_export_skips_wave2_but_not_wave3_for_rider_only_account():
    """A user with no driver row must not query rides-as-driver/payouts/
    documents (wave 2), but MUST still query rides-as-rider/saved_addresses
    (wave 3) — that's the whole point of N1's fix."""
    from routes import drivers as drivers_module

    async def fake_get_rows(table, filters=None, **kwargs):
        if table == "drivers":
            return []  # no driver row
        if table == "users":
            return [{"id": "user-1", "email": "rider@x"}]
        if table == "notification_preferences":
            return []
        if table == "rides":
            if filters and filters.get("driver_id"):
                raise AssertionError("wave 2 should have been skipped for a rider-only account")
            return [{"id": "r2"}]  # rider_id-filtered — wave 3, always runs
        if table == "saved_addresses":
            return [{"id": "a1"}]
        raise AssertionError(f"unexpected query on {table}")

    get_rows_mock = AsyncMock(side_effect=fake_get_rows)
    send_email_mock = AsyncMock()

    with (
        patch.object(drivers_module.db_supabase, "get_rows", get_rows_mock),
        patch.object(drivers_module._deps, "send_email", send_email_mock),
        patch.object(drivers_module.tax_exports, "_upload_export_zip", AsyncMock(return_value="https://x/y.zip")),
    ):
        await drivers_module._build_and_email_data_export("user-1", "rider@x.com")

    queried_tables = sorted(call.args[0] for call in get_rows_mock.await_args_list)
    assert queried_tables == sorted(["drivers", "users", "notification_preferences", "rides", "saved_addresses"])
    send_email_mock.assert_awaited_once()
