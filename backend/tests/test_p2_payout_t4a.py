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
from unittest.mock import AsyncMock, patch

import pytest

DRIVER_USER_ID = "driver_user_p2_16"
DRIVER_ID = "driver_row_p2_16"


def _driver_row(stripe_account_id: str | None = None, **extra) -> dict:
    return {
        "id": DRIVER_ID,
        "user_id": DRIVER_USER_ID,
        "stripe_account_id": stripe_account_id,
        "bank_account": None,
        # CRA payout preconditions (enforced before the balance/bank checks):
        # a valid GST/HST BN and SIN on file. Default them so eligibility tests
        # reach the logic they pin; override to exercise the GST/SIN blocks.
        "gst_bn": "123456789RT0001",
        "stripe_id_number_provided": True,
        **extra,
    }


def _bank_account() -> dict:
    return {
        "id": "bank-001",
        "driver_id": DRIVER_ID,
        "bank_name": "Test Bank",
        "account_last4": "1234",
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

    async def _request(
        self,
        amount: float = 50.00,
        payable_balance: float = 100.00,
        has_bank_account: bool = True,
        gst_bn: str | None = "123456789RT0001",
    ):
        from starlette.requests import Request as StarletteRequest

        from backend.routes.drivers import PayoutRequest, request_payout

        req = PayoutRequest(amount=Decimal(str(amount)))
        # GST/HST registration is a hard precondition for payout (CRA rideshare
        # rule); default to a valid BN so the balance/bank logic is reachable.
        driver = {**_driver_row(), "gst_bn": gst_bn}
        inserted = []

        # Build a real Starlette Request so @idempotent_endpoint can read headers.
        mock_request = StarletteRequest(
            {
                "type": "http",
                "method": "POST",
                "path": "/drivers/payouts",
                "query_string": b"",
                "headers": [],
            }
        )

        # get_driver_balance is called internally and makes multiple get_rows calls.
        # Mock it directly to control the returned balance cleanly.
        async def _mock_balance(user):
            return {"payable_balance": str(payable_balance)}

        async def _get_rows(table, query=None, **kwargs):
            if table == "drivers":
                return [driver]
            if table == "bank_accounts":
                return [_bank_account()] if has_bank_account else []
            return []

        # get_app_settings is imported locally inside request_payout, so patch
        # it at the settings_loader module level where it's defined.
        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch(
                "backend.routes.drivers._deps.db_supabase.insert_one",
                AsyncMock(side_effect=lambda t, r: inserted.append(r) or r),
            ),
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(side_effect=_mock_balance)),
            patch("backend.settings_loader.get_app_settings", AsyncMock(return_value={})),  # no Stripe key
        ):
            result = await request_payout(
                req=req,
                request=mock_request,
                current_user={"id": DRIVER_USER_ID},
            )

        return result, inserted

    async def test_payout_persisted_with_pending_status(self):
        result, inserted = await self._request(amount=50.00, payable_balance=100.00)

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
            await self._request(amount=200.00, payable_balance=50.00)

        assert exc_info.value.status_code == 400
        assert "insufficient" in exc_info.value.detail.lower()

    async def test_no_bank_account_raises_400(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await self._request(amount=50.00, payable_balance=100.00, has_bank_account=False)

        assert exc_info.value.status_code == 400
        assert "bank" in exc_info.value.detail.lower()

    async def test_payout_blocked_without_gst_raises_422(self):
        # CRA: rideshare drivers must be GST/HST-registered from their first
        # fare. No BN on file → hard block before any money moves.
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await self._request(gst_bn=None)

        assert exc_info.value.status_code == 422
        assert "gst" in exc_info.value.detail.lower()

    async def test_payout_blocked_with_malformed_gst_raises_422(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await self._request(gst_bn="12345")  # not a 9-digit BN

        assert exc_info.value.status_code == 422

    async def test_driver_not_found_raises_404(self):
        from fastapi import HTTPException
        from starlette.requests import Request as StarletteRequest

        from backend.routes.drivers import PayoutRequest, request_payout

        req = PayoutRequest(amount=Decimal("50.00"))
        mock_request = StarletteRequest(
            {
                "type": "http",
                "method": "POST",
                "path": "/drivers/payouts",
                "query_string": b"",
                "headers": [],
            }
        )

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc_info:
                await request_payout(req=req, request=mock_request, current_user={"id": "ghost-driver"})

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

        async def _get_rows(table, query=None, **kwargs):
            if table == "drivers":
                return [driver]
            return payouts

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)):
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

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
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

        async def _get_rows(table, query=None, **kwargs):
            return [driver]  # for drivers lookup

        async def _get_rides_for_driver(drv, **kwargs):
            return rides

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rides_for_driver", AsyncMock(side_effect=_get_rides_for_driver)
            ),
        ):
            result = await get_t4a_summary(year=2025, current_user={"id": DRIVER_USER_ID})

        assert result["year"] == 2025
        assert result["total_trips"] == 3
        assert float(result["total_earnings"]) == pytest.approx(70.00, abs=0.01)
        assert float(result["net_earnings"]) == pytest.approx(70.00, abs=0.01)

    async def test_t4a_includes_stripe_synced_legacy_payouts(self):
        """Synced legacy payout history (payout_type='stripe_sync', from
        stripe_payout_sync_service) is income the OLD app paid through Stripe;
        it folds into the slip total and is surfaced separately as
        legacy_synced_earnings so the slip can be reconciled."""
        from backend.routes.drivers import get_t4a_summary

        driver = _driver_row()
        rides = [_ride_row(20.00)]

        async def _get_rows(table, query=None, **kwargs):
            if table == "drivers":
                return [driver]
            if table == "payouts":
                assert query["payout_type"] == "stripe_sync"
                assert query["created_at"]["$gte"].startswith("2025-01-01")
                return [{"amount": 500.10, "payout_type": "stripe_sync"}]
            return []

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.routes.drivers._deps.db_supabase.get_rides_for_driver", AsyncMock(return_value=rides)),
        ):
            result = await get_t4a_summary(year=2025, current_user={"id": DRIVER_USER_ID})

        assert result["total_earnings"] == "520.10"
        assert result["net_earnings"] == "520.10"
        assert result["legacy_synced_earnings"] == "500.10"
        assert result["total_trips"] == 1  # synced payouts are not trips

    async def test_driver_not_found_raises_404(self):
        from fastapi import HTTPException

        from backend.routes.drivers import get_t4a_summary

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc_info:
                await get_t4a_summary(year=2025, current_user={"id": "ghost"})

        assert exc_info.value.status_code == 404

    async def test_t4a_includes_gst_fields_when_set(self):
        """gst_registered=True + gst_bn propagate from driver row into summary."""
        from backend.routes.drivers import get_t4a_summary

        driver = {**_driver_row(), "gst_registered": True, "gst_bn": "123456789RT0001"}

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[driver])),
            patch("backend.routes.drivers._deps.db_supabase.get_rides_for_driver", AsyncMock(return_value=[])),
        ):
            result = await get_t4a_summary(year=2025, current_user={"id": DRIVER_USER_ID})

        assert result["gst_registered"] is True
        assert result["gst_bn"] == "123456789RT0001"

    async def test_t4a_gst_fields_default_when_absent(self):
        """Driver rows without GST columns default to False / empty string."""
        from backend.routes.drivers import get_t4a_summary

        driver = _driver_row(gst_bn=None)  # no gst_registered; gst_bn absent

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[driver])),
            patch("backend.routes.drivers._deps.db_supabase.get_rides_for_driver", AsyncMock(return_value=[])),
        ):
            result = await get_t4a_summary(year=2025, current_user={"id": DRIVER_USER_ID})

        assert result["gst_registered"] is False
        assert result["gst_bn"] == ""


# ─────────────────────────────────────────────────────────────────────────────
# PUT /drivers/me — gst_registered + gst_bn field write
# ─────────────────────────────────────────────────────────────────────────────


class TestUpdateDriverGstFields:
    """Pins that gst_registered and gst_bn reach the DB via PUT /drivers/me.

    L-P1-4: UpdateDriverProfileRequest previously used `gst_number` (wrong
    column name) and was missing `gst_registered`.  The DB columns are
    `gst_registered` (bool) and `gst_bn` (text), added in migration 58.
    """

    def _make_driver(self, **extra) -> dict:
        return {"id": DRIVER_ID, "user_id": DRIVER_USER_ID, "status": "active", **extra}

    def _patches(self, driver: dict, update_mock: AsyncMock):
        """Return an ExitStack that covers all DB calls in update_my_driver."""
        import contextlib

        stack = contextlib.ExitStack()
        stack.enter_context(patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[driver])))
        stack.enter_context(patch("backend.routes.drivers._deps.db_supabase.update_one", update_mock))
        stack.enter_context(
            patch("backend.routes.drivers._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver))
        )
        stack.enter_context(patch("backend.routes.drivers._shared._encrypt_driver_pii", AsyncMock(side_effect=lambda d: d)))
        stack.enter_context(patch("backend.routes.drivers._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)))
        return stack

    @pytest.mark.anyio
    async def test_gst_registered_reaches_db(self):
        """Setting gst_registered=True via PUT /drivers/me writes to drivers table."""
        from backend.routes.drivers import UpdateDriverProfileRequest, update_my_driver

        driver = self._make_driver()
        update_mock = AsyncMock(return_value=None)

        with self._patches(driver, update_mock):
            await update_my_driver(
                body=UpdateDriverProfileRequest(gst_registered=True),
                current_user={"id": DRIVER_USER_ID},
            )

        update_mock.assert_called_once()
        _, _filter, updates = update_mock.call_args.args
        assert updates.get("gst_registered") is True
        assert "gst_number" not in updates  # old wrong field must not appear

    @pytest.mark.anyio
    async def test_gst_bn_reaches_db(self):
        """Setting gst_bn via PUT /drivers/me writes the correct column name."""
        from backend.routes.drivers import UpdateDriverProfileRequest, update_my_driver

        driver = self._make_driver()
        update_mock = AsyncMock(return_value=None)

        with self._patches(driver, update_mock):
            await update_my_driver(
                body=UpdateDriverProfileRequest(gst_registered=True, gst_bn="123456789RT0001"),
                current_user={"id": DRIVER_USER_ID},
            )

        _, _filter, updates = update_mock.call_args.args
        assert updates.get("gst_bn") == "123456789RT0001"
        assert "gst_number" not in updates

    @pytest.mark.anyio
    async def test_omitted_gst_fields_not_written(self):
        """Omitting GST fields from PUT body leaves driver row unchanged."""
        from backend.routes.drivers import UpdateDriverProfileRequest, update_my_driver

        driver = self._make_driver()
        update_mock = AsyncMock(return_value=None)

        with self._patches(driver, update_mock):
            await update_my_driver(
                body=UpdateDriverProfileRequest(preferred_language="fr"),
                current_user={"id": DRIVER_USER_ID},
            )

        _, _filter, updates = update_mock.call_args.args
        assert "gst_registered" not in updates
        assert "gst_bn" not in updates
