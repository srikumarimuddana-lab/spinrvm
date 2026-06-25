"""Regression tests for C3: the rider cancellation-fee wallet debit must be
atomic + idempotent (single RPC), not a read-compute-write across two calls.

Background
----------
cancel_ride_rider charged the wallet cancellation fee like this:
    read balance -> compute new_balance in Python -> update_one(wallets, ...)
                 -> insert_one(wallet_transactions, ...)
No row lock, no conditional filter on the prior balance, and two independent
PostgREST calls. A cancel racing a top-up (or a double-tapped cancel) could
lose a write and mischarge the rider; and because 'cancellation_fee' was never
on the wallet_transactions type allowlist, the ledger insert 23514'd *after*
the balance had already been written — a debited wallet with no ledger row.

Fix: migration 188 adds wallet_debit_cancellation_fee (one transaction: lock,
clamp-debit, ledger insert) + the 'cancellation_fee' type. cancel_ride_rider
now routes the fee through that RPC.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio

RIDER_ID = "rider_c3"
RIDE_ID = "ride_c3_001"

_MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "188_wallet_cancellation_fee_atomic_debit.sql"


# --------------------------------------------------------------------------- #
# Handler: the wallet fee goes through the atomic RPC, not a raw debit
# --------------------------------------------------------------------------- #


async def _run_wallet_cancel(rpc_return):
    from backend.routes import rides as rides_mod

    ride = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": None,  # no driver -> isolates the rider wallet debit path
        "status": "driver_arrived",
        "payment_method": "wallet",
        "service_area_id": None,
    }
    cancelled = {**ride, "status": "cancelled"}
    wallet = {"id": "wallet_c3", "balance": 50.0, "is_active": True}

    rpc_mock = AsyncMock(return_value=rpc_return)
    update_one_mock = AsyncMock(return_value={"id": RIDE_ID})  # claim succeeds
    insert_one_mock = AsyncMock()

    with (
        patch("backend.routes.rides._require_ride_in_state_rider", AsyncMock(return_value=ride)),
        patch("backend.routes.rides.calculate_cancellation_fee", MagicMock(return_value=(Decimal("0.50"), Decimal("0")))),
        patch("backend.routes.rides.get_app_settings", AsyncMock(return_value={})),
        patch("backend.routes.rides.db_supabase.update_one", update_one_mock),
        patch("backend.routes.rides.db_supabase.insert_one", insert_one_mock),
        patch("backend.routes.rides.db_supabase.find_one", AsyncMock(return_value=wallet)),
        patch("backend.routes.rides.db_supabase.rpc", rpc_mock),
        patch("backend.routes.rides.db_supabase.update_ride", AsyncMock()),
        patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=cancelled)),
        patch("backend.routes.rides.db_supabase.run_sync", AsyncMock(return_value=MagicMock(data=[]))),
        patch("backend.routes.rides.pay_driver_cancellation_fee", AsyncMock()),
        patch("backend.routes.rides.log_user_action", AsyncMock()),
        patch("backend.routes.rides.manager.send_personal_message", AsyncMock()),
        patch("backend.routes.rides.manager.broadcast_ride_status", AsyncMock()),
        patch("backend.routes.rides.manager.broadcast_to_admins", AsyncMock()),
    ):
        fn = getattr(rides_mod.cancel_ride_rider, "__wrapped__", rides_mod.cancel_ride_rider)
        result = await fn(request=None, ride_id=RIDE_ID, current_user={"id": RIDER_ID})

    return result, rpc_mock, update_one_mock, insert_one_mock


async def test_wallet_cancel_fee_routes_through_atomic_rpc():
    result, rpc_mock, update_one_mock, insert_one_mock = await _run_wallet_cancel(
        [{"new_balance": "49.50", "actual_charge": "0.50"}]
    )

    assert result["success"] is True

    # The fee went through the atomic, idempotent RPC...
    rpc_mock.assert_awaited_once()
    name, params = rpc_mock.await_args[0][0], rpc_mock.await_args[0][1]
    assert name == "wallet_debit_cancellation_fee"
    assert params["p_wallet_id"] == "wallet_c3"
    assert params["p_user_id"] == RIDER_ID
    assert params["p_ride_id"] == RIDE_ID
    # Amount passed as an exact decimal string (NUMERIC-safe), == admin+driver fee.
    assert params["p_amount"] == "0.50"

    # ...and NOT through the legacy non-atomic read-compute-write.
    insert_one_mock.assert_not_awaited()  # no direct wallet_transactions ledger insert
    for call in update_one_mock.await_args_list:
        assert call.args[0] != "wallets", "fee must not touch wallets via raw update_one"


async def test_wallet_cancel_survives_empty_rpc_result():
    """If the RPC layer returns None/[] (e.g. supabase unavailable in a unit
    context), the handler must not crash dereferencing the result."""
    result, rpc_mock, _, _ = await _run_wallet_cancel(None)
    assert result["success"] is True
    rpc_mock.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Migration 188 contract — the atomicity/idempotency guarantees can't be
# silently weakened (these run without a live DB).
# --------------------------------------------------------------------------- #


def _migration_sql() -> str:
    assert _MIGRATION.exists(), f"migration missing: {_MIGRATION}"
    return _MIGRATION.read_text()


def test_migration_allowlists_cancellation_fee_type():
    sql = _migration_sql()
    assert "wallet_transactions_type_check" in sql
    assert "'cancellation_fee'" in sql


def test_migration_function_is_atomic_idempotent_and_clamped():
    sql = _migration_sql().lower()
    assert "create or replace function wallet_debit_cancellation_fee" in sql
    assert "for update" in sql, "wallet row must be locked"
    assert "security definer" in sql
    assert "set search_path" in sql
    # idempotency guard keyed on the ledger row for this ride
    assert "exists" in sql and "reference_id = p_ride_id" in sql
    # clamp to available balance — never negative
    assert "least(" in sql
    # PostgREST roles must not call it directly
    assert "revoke execute on function wallet_debit_cancellation_fee" in sql


def test_migration_number_is_unique():
    mig_dir = _MIGRATION.parent
    prefixes = [f.name.split("_", 1)[0] for f in mig_dir.glob("188_*.sql")]
    assert prefixes.count("188") == 1, f"duplicate 188 migration prefix: {prefixes}"
    assert os.path.basename(str(_MIGRATION)).startswith("188_")
