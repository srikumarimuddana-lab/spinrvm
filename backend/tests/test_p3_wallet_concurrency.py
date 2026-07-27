"""
P3-4: Concurrent wallet debit tests.

The wallet_pay_for_ride and fare_split_pay_share Postgres RPCs use
SELECT FOR UPDATE to serialize concurrent debits — so double-spend is
impossible at the DB level. These tests verify the Python layer:

  1. The helpers correctly translate "insufficient_funds" DB exceptions
     to ValueError so callers get a typed signal.
  2. asyncio.gather across concurrent helper calls produces the expected
     mix of successes and failures when the mock simulates a real race
     outcome (first call wins, second sees exhausted balance).
  3. The wallet/pay route returns HTTP 400 (not 500 or crash) when the
     RPC signals insufficient funds.

Run:
    pytest backend/tests/test_p3_wallet_concurrency.py -v
"""

from __future__ import annotations

import asyncio
import threading
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

WALLET_ID = "wallet_race_1"
RIDE_ID = "ride_race_1"
PART_ID = "part_race_1"
USER_ID = "user_race_1"

_WALLET = {
    "id": WALLET_ID,
    "user_id": USER_ID,
    "balance": 30.0,
    "is_active": True,
    "updated_at": "2026-01-01T00:00:00",
}
_RIDE = {
    "id": RIDE_ID,
    "rider_id": USER_ID,
    "status": "completed",
    "total_fare": 30.0,
    "grand_total": 30.0,
    "payment_method": "wallet",
    "payment_status": "pending",
}


def _mock_supabase_rpc_once_then_fail(success_data: str, failure_msg: str) -> MagicMock:
    """Return a supabase mock whose rpc().execute() succeeds on the first
    thread-safe call and raises on all subsequent ones."""
    lock = threading.Lock()
    call_count = [0]

    def _execute():
        with lock:
            call_count[0] += 1
            n = call_count[0]
        if n == 1:
            res = MagicMock()
            res.data = success_data
            return res
        raise Exception(failure_msg)

    mock = MagicMock()
    mock.rpc.return_value.execute.side_effect = _execute
    return mock


# ── wallet_pay_for_ride helper ────────────────────────────────────────


class TestWalletPayForRideHelper:
    @pytest.mark.anyio
    async def test_insufficient_funds_raises_value_error(self):
        """RPC exception containing 'insufficient_funds' → ValueError."""
        import repositories.wallet_repo as dbs

        mock = MagicMock()
        mock.rpc.return_value.execute.side_effect = Exception("insufficient_funds: balance 0")

        with patch.object(dbs, "supabase", mock):
            with pytest.raises(ValueError, match="insufficient_funds"):
                await dbs.wallet_pay_for_ride(WALLET_ID, RIDE_ID, Decimal("50.00"))

    @pytest.mark.anyio
    async def test_success_returns_new_balance(self):
        """RPC returning a numeric string → Decimal new balance."""
        import repositories.wallet_repo as dbs

        mock = MagicMock()
        res = MagicMock()
        res.data = "20.00"
        mock.rpc.return_value.execute.return_value = res

        with patch.object(dbs, "supabase", mock):
            balance = await dbs.wallet_pay_for_ride(WALLET_ID, RIDE_ID, Decimal("10.00"))

        assert balance == Decimal("20.00")

    @pytest.mark.anyio
    async def test_wallet_not_found_raises_value_error(self):
        """RPC exception containing 'wallet not found' → ValueError."""
        import repositories.wallet_repo as dbs

        mock = MagicMock()
        mock.rpc.return_value.execute.side_effect = Exception("wallet not found")

        with patch.object(dbs, "supabase", mock):
            with pytest.raises(ValueError, match="wallet_not_found"):
                await dbs.wallet_pay_for_ride(WALLET_ID, RIDE_ID, Decimal("10.00"))

    @pytest.mark.anyio
    async def test_concurrent_first_wins_second_gets_insufficient(self):
        """
        Two concurrent debits on a wallet that can only cover one:
        the mock mirrors what SELECT FOR UPDATE does in Postgres —
        the first succeeds, the second raises 'insufficient_funds'.
        Both tasks must resolve cleanly (no crash, correct exception type).
        """
        import repositories.wallet_repo as dbs

        mock = _mock_supabase_rpc_once_then_fail("0.00", "insufficient_funds")

        with patch.object(dbs, "supabase", mock):
            results = await asyncio.gather(
                dbs.wallet_pay_for_ride(WALLET_ID, RIDE_ID, Decimal("30.00")),
                dbs.wallet_pay_for_ride(WALLET_ID, RIDE_ID, Decimal("30.00")),
                return_exceptions=True,
            )

        successes = [r for r in results if isinstance(r, Decimal)]
        failures = [r for r in results if isinstance(r, ValueError)]
        assert len(successes) == 1, "exactly one debit should succeed"
        assert len(failures) == 1, "exactly one debit should fail"
        assert "insufficient_funds" in str(failures[0])

    @pytest.mark.anyio
    async def test_three_concurrent_one_succeeds_rest_fail(self):
        """Three concurrent debits, only one can win — mirrors a burst retry."""
        import repositories.wallet_repo as dbs

        mock = _mock_supabase_rpc_once_then_fail("0.00", "insufficient_funds")

        with patch.object(dbs, "supabase", mock):
            results = await asyncio.gather(
                dbs.wallet_pay_for_ride(WALLET_ID, RIDE_ID, Decimal("30.00")),
                dbs.wallet_pay_for_ride(WALLET_ID, RIDE_ID, Decimal("30.00")),
                dbs.wallet_pay_for_ride(WALLET_ID, RIDE_ID, Decimal("30.00")),
                return_exceptions=True,
            )

        successes = [r for r in results if isinstance(r, Decimal)]
        failures = [r for r in results if isinstance(r, ValueError)]
        assert len(successes) == 1
        assert len(failures) == 2


# ── wallet/pay route ──────────────────────────────────────────────────


class TestWalletPayRouteRaceHandling:
    """The route must surface the ValueError as HTTP 400, not 500."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        import dependencies
        from backend.server import app

        app.dependency_overrides[dependencies.get_current_user] = lambda: {
            "id": USER_ID,
            "phone": "+11234567890",
            "role": "rider",
            "is_driver": False,
        }
        with TestClient(app) as c:
            yield c
        app.dependency_overrides.clear()

    def test_insufficient_funds_returns_400(self, client):
        """When the RPC raises ValueError('insufficient_funds'), route → 400."""
        from unittest.mock import AsyncMock

        mock_wallet_pay = AsyncMock(side_effect=ValueError("insufficient_funds"))
        mock_get_wallet = AsyncMock(return_value=_WALLET)
        mock_find_one = AsyncMock(return_value=_RIDE)

        with (
            patch("routes.wallet.wallet_pay_for_ride", mock_wallet_pay),
            patch("routes.wallet.get_or_create_wallet", mock_get_wallet),
            patch("routes.wallet.db.find_one", mock_find_one),
        ):
            resp = client.post("/api/v1/wallet/pay", json={"ride_id": RIDE_ID, "amount": 30.0})

        assert resp.status_code == 400
        assert "insufficient" in resp.json()["detail"].lower()

    def test_successful_pay_returns_200_with_balance(self, client):
        """Happy path: RPC returns new balance, route returns 200."""
        from unittest.mock import AsyncMock

        mock_wallet_pay = AsyncMock(return_value=Decimal("0.00"))
        mock_get_wallet = AsyncMock(return_value=_WALLET)
        mock_find_one = AsyncMock(return_value=_RIDE)
        mock_record_tx = AsyncMock(return_value={"id": "txn_1"})
        mock_update = AsyncMock(return_value=None)

        with (
            patch("routes.wallet.wallet_pay_for_ride", mock_wallet_pay),
            patch("routes.wallet.get_or_create_wallet", mock_get_wallet),
            patch("routes.wallet.db.find_one", mock_find_one),
            patch("routes.wallet._record_transaction", mock_record_tx),
            patch("routes.wallet.db.update_one", mock_update),
        ):
            resp = client.post("/api/v1/wallet/pay", json={"ride_id": RIDE_ID, "amount": 30.0})

        assert resp.status_code == 200
        data = resp.json()
        assert data["balance"] == "0.00"

    def test_non_insufficient_funds_value_error_returns_503(self, client):
        """An unexpected ValueError from the RPC escalates to 503."""
        from unittest.mock import AsyncMock

        mock_wallet_pay = AsyncMock(side_effect=ValueError("wallet_not_found"))
        mock_get_wallet = AsyncMock(return_value=_WALLET)
        mock_find_one = AsyncMock(return_value=_RIDE)

        with (
            patch("routes.wallet.wallet_pay_for_ride", mock_wallet_pay),
            patch("routes.wallet.get_or_create_wallet", mock_get_wallet),
            patch("routes.wallet.db.find_one", mock_find_one),
        ):
            resp = client.post("/api/v1/wallet/pay", json={"ride_id": RIDE_ID, "amount": 30.0})

        assert resp.status_code == 503


# ── fare_split_pay_share helper ───────────────────────────────────────


class TestFareSplitPayShareHelper:
    @pytest.mark.anyio
    async def test_insufficient_funds_raises_value_error(self):
        """RPC exception containing 'insufficient_funds' → ValueError."""
        import repositories.wallet_repo as dbs

        mock = MagicMock()
        mock.rpc.return_value.execute.side_effect = Exception("insufficient_funds: low balance")

        with patch.object(dbs, "supabase", mock):
            with pytest.raises(ValueError, match="insufficient_funds"):
                await dbs.fare_split_pay_share(WALLET_ID, PART_ID, Decimal("50.00"))

    @pytest.mark.anyio
    async def test_success_returns_new_balance(self):
        """RPC returning a numeric value → Decimal new balance."""
        import repositories.wallet_repo as dbs

        mock = MagicMock()
        res = MagicMock()
        res.data = "5.50"
        mock.rpc.return_value.execute.return_value = res

        with patch.object(dbs, "supabase", mock):
            balance = await dbs.fare_split_pay_share(WALLET_ID, PART_ID, Decimal("15.00"))

        assert balance == Decimal("5.50")

    @pytest.mark.anyio
    async def test_concurrent_race_one_wins_one_fails(self):
        """Two concurrent fare-split payments from the same wallet — only one succeeds."""
        import repositories.wallet_repo as dbs

        mock = _mock_supabase_rpc_once_then_fail("0.00", "insufficient_funds")

        with patch.object(dbs, "supabase", mock):
            results = await asyncio.gather(
                dbs.fare_split_pay_share(WALLET_ID, PART_ID, Decimal("30.00")),
                dbs.fare_split_pay_share(WALLET_ID, PART_ID, Decimal("30.00")),
                return_exceptions=True,
            )

        successes = [r for r in results if isinstance(r, Decimal)]
        failures = [r for r in results if isinstance(r, ValueError)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert "insufficient_funds" in str(failures[0])
