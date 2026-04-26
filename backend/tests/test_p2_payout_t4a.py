"""
P2-16: Payout / T4A driver flows (D8, D13)

Implemented endpoints:
  POST /drivers/payouts  — request payout (balance check, bank-account check)
  GET  /drivers/payouts  — payout history scoped to driver
  GET  /drivers/t4a/{year} — annual earnings summary

These tests pin:
  - request_payout: persists payout row; insufficient funds → 400; no bank account → 400
  - request_payout: driver not found → 404
  - request_payout: no Stripe key → status="pending" (safe fallback)
  - get_payout_history: driver not found → 404; returns payouts scoped to driver
  - get_t4a_summary: sums driver_earnings across rides; driver not found → 404

Run:
    pytest backend/tests/test_p2_payout_t4a.py -v
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

DRIVER_USER_ID = "driver_user_p2_16"
DRIVER_ID = "driver_row_p2_16"


def _driver_row(stripe_account_id: str | None = None) -> dict:
    return {
        "id": DRIVER_ID,
        "user_id": DRIVER_USER_ID,
        "stripe_account_id": stripe_account_id,
        "bank_account": None,
    }


def _bank_account() -> dict:
    return {
        "id": "bank-001",
        "driver_id": DRIVER_ID,
        "bank_name": "Test Bank",
        "account_number_last4": "1234",
    }


def _payout_row(amount: float = 50.00, status: str = "pending") -> dict:
    return {
        "id": "payout-001",
        "driver_id": DRIVER_ID,
        "amount": amount,
        "status": status,
        "bank_name": "Test Bank",
        "account_last4": "1234",
        "created_at": "2025-01-01T00:00:00",
    }


def _ride_row(earnings: float = 20.00) -> dict:
    return {
        "id": "ride-001",
        "driver_id": DRIVER_ID,
        "status": "completed",
        "driver_earnings": earnings,
        "tip_amount": 0,
    }


class _SimpleCursor:
    """Minimal cursor stub for code paths that don't await get_rows."""

    def __init__(self, items):
        self._items = items

    def sort(self, *a, **k):
        return self

    def skip(self, n):
        return self

    def limit(self, n):
        return self

    async def to_list(self, length=None):
        return self._items


# ─────────────────────────────────────────────────────────────────────────────
# POST /drivers/payouts
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
class TestRequestPayout:
    """Pins request_payout: balance check, bank-account guard, payout persisted.

    Code under test: backend/routes/drivers.py::request_payout (~line 1353).
    """

    async def _request(self, amount: float = 50.00, available_balance: float = 100.00, has_bank_account: bool = True):
        from backend.routes.drivers import PayoutRequest, request_payout

        req = PayoutRequest(amount=Decimal(str(amount)))
        driver = _driver_row()
        inserted = []

        # get_driver_balance is called internally and makes multiple get_rows calls.
        # Mock it directly to control the returned balance cleanly.
        async def _mock_balance(user):
            return {"available_balance": available_balance}

        async def _get_rows(table, query=None, **kwargs):
            if table == "drivers":
                return [driver]
            if table == "bank_accounts":
                return [_bank_account()] if has_bank_account else []
            return []

        with (
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch(
                "backend.routes.drivers.db_supabase.insert_one",
                AsyncMock(side_effect=lambda t, r: inserted.append(r) or r),
            ),
            patch("backend.routes.drivers.get_driver_balance", AsyncMock(side_effect=_mock_balance)),
            patch("backend.routes.drivers.get_app_settings", AsyncMock(return_value={})),  # no Stripe key
        ):
            result = await request_payout(
                req=req,
                current_user={"id": DRIVER_USER_ID},
            )

        return result, inserted

    async def test_payout_persisted_with_pending_status(self):
        result, inserted = await self._request(amount=50.00, available_balance=100.00)

        assert result["success"] is True
        assert inserted, "Payout row was not persisted"
        row = inserted[0]
        assert row["driver_id"] == DRIVER_ID
        assert float(row["amount"]) == 50.00
        # No Stripe key → pending
        assert row["status"] == "pending"

    async def test_insufficient_funds_raises_400(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await self._request(amount=200.00, available_balance=50.00)

        assert exc_info.value.status_code == 400
        assert "insufficient" in exc_info.value.detail.lower()

    async def test_no_bank_account_raises_400(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await self._request(amount=50.00, available_balance=100.00, has_bank_account=False)

        assert exc_info.value.status_code == 400
        assert "bank" in exc_info.value.detail.lower()

    async def test_driver_not_found_raises_404(self):
        from fastapi import HTTPException

        from backend.routes.drivers import PayoutRequest, request_payout

        req = PayoutRequest(amount=Decimal("50.00"))

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc_info:
                await request_payout(req=req, current_user={"id": "ghost-driver"})

        assert exc_info.value.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# GET /drivers/payouts
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
class TestGetPayoutHistory:
    """Pins get_payout_history: driver-scoped; not-found guard.

    Code under test: backend/routes/drivers.py::get_payout_history (~line 1411).
    """

    async def test_payout_history_returns_driver_payouts(self):
        from backend.routes.drivers import get_payout_history

        driver = _driver_row()
        payouts = [_payout_row(50.00), _payout_row(30.00, "completed")]
        cursor = _SimpleCursor(payouts)

        call_count = [0]

        def _get_rows(table, query=None, **kwargs):
            call_count[0] += 1
            if table == "drivers":
                return [driver]
            # Return cursor for payouts (not awaited in the endpoint)
            return cursor

        with patch("backend.routes.drivers.db_supabase.get_rows", MagicMock(side_effect=_get_rows)):
            # get_rows for drivers is awaited, but for payouts it's not;
            # wrap driver call in AsyncMock and payouts in sync mock
            async def _async_get_rows(table, query=None, **kwargs):
                if table == "drivers":
                    return [driver]
                return cursor

            with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=_async_get_rows)):
                result = await get_payout_history(
                    limit=20,
                    offset=0,
                    current_user={"id": DRIVER_USER_ID},
                )

        assert result["success"] is True
        assert len(result["payouts"]) == 2

    async def test_driver_not_found_raises_404(self):
        from fastapi import HTTPException

        from backend.routes.drivers import get_payout_history

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc_info:
                await get_payout_history(
                    limit=20,
                    offset=0,
                    current_user={"id": "ghost"},
                )

        assert exc_info.value.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# GET /drivers/t4a/{year}
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
class TestGetT4ASummary:
    """Pins get_t4a_summary: sums driver_earnings across completed rides.

    Code under test: backend/routes/drivers.py::get_t4a_summary (~line 1429).
    """

    async def test_t4a_sums_driver_earnings(self):
        from backend.routes.drivers import get_t4a_summary

        driver = _driver_row()
        rides = [_ride_row(20.00), _ride_row(35.00), _ride_row(15.00)]
        cursor = _SimpleCursor(rides)

        async def _get_rows(table, query=None, **kwargs):
            return [driver]  # for drivers lookup

        def _get_rides_for_driver(drv, **kwargs):
            return cursor

        with (
            patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch(
                "backend.routes.drivers.db_supabase.get_rides_for_driver", MagicMock(side_effect=_get_rides_for_driver)
            ),
        ):
            result = await get_t4a_summary(year=2025, current_user={"id": DRIVER_USER_ID})

        assert result["year"] == 2025
        assert result["total_trips"] == 3
        assert float(result["total_earnings"]) == pytest.approx(70.00, abs=0.01)
        assert float(result["net_earnings"]) == pytest.approx(70.00, abs=0.01)

    async def test_driver_not_found_raises_404(self):
        from fastapi import HTTPException

        from backend.routes.drivers import get_t4a_summary

        with patch("backend.routes.drivers.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc_info:
                await get_t4a_summary(year=2025, current_user={"id": "ghost"})

        assert exc_info.value.status_code == 404
