"""Tests for the weekly auto-payout service (utils/auto_payout.py).

Covers:
- Balance computation (_compute_payable_balance)
- Batch idempotency (week_key unique guard)
- Per-driver Stripe Transfer with idempotency key
- Reserve-then-transfer pattern
- Minimum payout threshold ($10)
- Instant payout service-area kill switch gate
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

DRIVER_ID = "driver_auto_001"
WEEK_KEY = "2026-W33"


def _ride(**extra):
    return {
        "id": f"ride_{extra.get('driver_earnings', '10')}",
        "driver_id": DRIVER_ID,
        "status": "completed",
        "driver_earnings": "25.00",
        "base_fare": "20.00",
        "distance_fare": "3.00",
        "time_fare": "2.00",
        "tip_amount": "5.00",
        "tax_amount": "3.25",
        "cancellation_fee_driver": None,
        "fare_breakdown_snapshot": None,
        "legacy_import_metadata": {},
        **extra,
    }


def _driver(**extra):
    return {
        "id": DRIVER_ID,
        "user_id": "user_auto_001",
        "stripe_account_id": "acct_TEST_AUTO",
        **extra,
    }


# ── Balance computation ────────────────────────────────────────────────


class TestComputePayableBalance:
    @pytest.mark.anyio
    async def test_basic_balance(self):
        from backend.utils.auto_payout import _compute_payable_balance

        rides = [_ride(driver_earnings="50.00", tax_amount="5.00")]
        payouts = []
        bonuses = []

        async def mock_get_rows(table, filters, **kw):
            if table == "rides" and filters.get("status") == "completed":
                return rides
            if table == "rides" and filters.get("status") == "cancelled":
                return []
            if table == "payouts":
                return payouts
            if table == "driver_bonuses":
                return bonuses
            return []

        mock_sb = MagicMock()
        mock_sb.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(data=[])

        with (
            patch("backend.utils.auto_payout.db_supabase.get_rows", side_effect=mock_get_rows),
            patch("backend.utils.auto_payout.db_supabase.supabase", mock_sb),
        ):
            balance = await _compute_payable_balance(DRIVER_ID)

        assert balance == Decimal("55.00")

    @pytest.mark.anyio
    async def test_deducts_completed_payouts(self):
        from backend.utils.auto_payout import _compute_payable_balance

        rides = [_ride(driver_earnings="100.00", tax_amount="0.00")]
        payouts = [{"amount": "30.00", "status": "completed", "payout_type": "auto"}]

        async def mock_get_rows(table, filters, **kw):
            if table == "rides" and filters.get("status") == "completed":
                return rides
            if table == "rides" and filters.get("status") == "cancelled":
                return []
            if table == "payouts":
                return payouts
            if table == "driver_bonuses":
                return []
            return []

        mock_sb = MagicMock()
        mock_sb.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(data=[])

        with (
            patch("backend.utils.auto_payout.db_supabase.get_rows", side_effect=mock_get_rows),
            patch("backend.utils.auto_payout.db_supabase.supabase", mock_sb),
        ):
            balance = await _compute_payable_balance(DRIVER_ID)

        assert balance == Decimal("70.00")

    @pytest.mark.anyio
    async def test_excludes_reversed_and_failed_payouts(self):
        from backend.utils.auto_payout import _compute_payable_balance

        rides = [_ride(driver_earnings="100.00", tax_amount="0.00")]
        payouts = [
            {"amount": "30.00", "status": "reversed", "payout_type": "auto"},
            {"amount": "20.00", "status": "failed", "payout_type": "auto"},
        ]

        async def mock_get_rows(table, filters, **kw):
            if table == "rides" and filters.get("status") == "completed":
                return rides
            if table == "rides" and filters.get("status") == "cancelled":
                return []
            if table == "payouts":
                return payouts
            if table == "driver_bonuses":
                return []
            return []

        mock_sb = MagicMock()
        mock_sb.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(data=[])

        with (
            patch("backend.utils.auto_payout.db_supabase.get_rows", side_effect=mock_get_rows),
            patch("backend.utils.auto_payout.db_supabase.supabase", mock_sb),
        ):
            balance = await _compute_payable_balance(DRIVER_ID)

        assert balance == Decimal("100.00")

    @pytest.mark.anyio
    async def test_excludes_stripe_sync_payouts(self):
        from backend.utils.auto_payout import _compute_payable_balance

        rides = [_ride(driver_earnings="100.00", tax_amount="0.00")]
        payouts = [
            {"amount": "50.00", "status": "completed", "payout_type": "stripe_sync"},
        ]

        async def mock_get_rows(table, filters, **kw):
            if table == "rides" and filters.get("status") == "completed":
                return rides
            if table == "rides" and filters.get("status") == "cancelled":
                return []
            if table == "payouts":
                return payouts
            if table == "driver_bonuses":
                return []
            return []

        mock_sb = MagicMock()
        mock_sb.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(data=[])

        with (
            patch("backend.utils.auto_payout.db_supabase.get_rows", side_effect=mock_get_rows),
            patch("backend.utils.auto_payout.db_supabase.supabase", mock_sb),
        ):
            balance = await _compute_payable_balance(DRIVER_ID)

        assert balance == Decimal("100.00")


# ── Batch run ──────────────────────────────────────────────────────────


class TestRunWeeklyAutoPayout:
    @pytest.mark.anyio
    async def test_skips_already_completed_batch(self):
        from backend.utils.auto_payout import run_weekly_auto_payout

        async def mock_get_rows(table, filters, **kw):
            if table == "auto_payout_batches":
                return [{"week_key": WEEK_KEY, "status": "completed"}]
            return []

        with (
            patch("backend.utils.auto_payout.current_week_key", return_value=WEEK_KEY),
            patch("backend.utils.auto_payout.db_supabase.get_rows", side_effect=mock_get_rows),
        ):
            result = await run_weekly_auto_payout()

        assert result["status"] == "already_completed"

    @pytest.mark.anyio
    async def test_skips_when_stripe_not_configured(self):
        from backend.utils.auto_payout import run_weekly_auto_payout

        async def mock_get_rows(table, filters, **kw):
            if table == "auto_payout_batches":
                return []
            return []

        mock_settings = AsyncMock(return_value={"stripe_secret_key": ""})

        with (
            patch("backend.utils.auto_payout.current_week_key", return_value=WEEK_KEY),
            patch("backend.utils.auto_payout.db_supabase.get_rows", side_effect=mock_get_rows),
            patch("backend.utils.auto_payout.db_supabase.insert_one", new_callable=AsyncMock),
            patch("backend.settings_loader.get_app_settings", mock_settings),
        ):
            result = await run_weekly_auto_payout()

        assert result["status"] == "stripe_not_configured"

    @pytest.mark.anyio
    async def test_pays_eligible_driver(self):
        from backend.utils.auto_payout import run_weekly_auto_payout

        driver = _driver()
        rides = [_ride(driver_earnings="50.00", tax_amount="5.00")]
        insert_calls = []
        update_calls = []

        async def mock_get_rows(table, filters, **kw):
            if table == "auto_payout_batches":
                return []
            if table == "drivers":
                return [driver]
            if table == "rides" and filters.get("status") == "completed":
                return rides
            if table == "rides" and filters.get("status") == "cancelled":
                return []
            if table == "payouts":
                return []
            if table == "driver_bonuses":
                return []
            return []

        async def mock_insert_one(table, doc):
            insert_calls.append((table, doc))

        async def mock_update_one(table, filters, updates):
            update_calls.append((table, filters, updates))

        mock_sb = MagicMock()
        mock_sb.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(data=[])

        mock_transfer = MagicMock()
        mock_transfer.id = "tr_TEST_123"

        mock_settings = AsyncMock(return_value={"stripe_secret_key": "sk_test_xxx"})

        with (
            patch("backend.utils.auto_payout.current_week_key", return_value=WEEK_KEY),
            patch("backend.utils.auto_payout.db_supabase.get_rows", side_effect=mock_get_rows),
            patch("backend.utils.auto_payout.db_supabase.insert_one", side_effect=mock_insert_one),
            patch("backend.utils.auto_payout.db_supabase.update_one", side_effect=mock_update_one),
            patch("backend.utils.auto_payout.db_supabase.supabase", mock_sb),
            patch("stripe.Transfer.create", return_value=mock_transfer),
            patch("backend.settings_loader.get_app_settings", mock_settings),
        ):
            result = await run_weekly_auto_payout()

        assert result["status"] == "completed"
        assert result["drivers_paid"] == 1
        assert result["drivers_eligible"] == 1

        payout_inserts = [c for c in insert_calls if c[0] == "payouts"]
        assert len(payout_inserts) == 1
        assert payout_inserts[0][1]["status"] == "reserved"
        assert payout_inserts[0][1]["payout_type"] == "auto"

        payout_updates = [c for c in update_calls if c[0] == "payouts"]
        assert any(u[2].get("status") == "completed" for u in payout_updates)

    @pytest.mark.anyio
    async def test_skips_driver_below_minimum(self):
        from backend.utils.auto_payout import run_weekly_auto_payout

        driver = _driver()
        rides = [_ride(driver_earnings="5.00", tax_amount="0.00")]

        async def mock_get_rows(table, filters, **kw):
            if table == "auto_payout_batches":
                return []
            if table == "drivers":
                return [driver]
            if table == "rides" and filters.get("status") == "completed":
                return rides
            if table == "rides" and filters.get("status") == "cancelled":
                return []
            if table == "payouts":
                return []
            if table == "driver_bonuses":
                return []
            return []

        async def mock_insert_one(table, doc):
            pass

        async def mock_update_one(table, filters, updates):
            pass

        mock_sb = MagicMock()
        mock_sb.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(data=[])

        mock_settings = AsyncMock(return_value={"stripe_secret_key": "sk_test_xxx"})

        with (
            patch("backend.utils.auto_payout.current_week_key", return_value=WEEK_KEY),
            patch("backend.utils.auto_payout.db_supabase.get_rows", side_effect=mock_get_rows),
            patch("backend.utils.auto_payout.db_supabase.insert_one", side_effect=mock_insert_one),
            patch("backend.utils.auto_payout.db_supabase.update_one", side_effect=mock_update_one),
            patch("backend.utils.auto_payout.db_supabase.supabase", mock_sb),
            patch("backend.settings_loader.get_app_settings", mock_settings),
        ):
            result = await run_weekly_auto_payout()

        assert result["drivers_paid"] == 0
        assert result["drivers_eligible"] == 0

    @pytest.mark.anyio
    async def test_skips_driver_without_stripe_account(self):
        from backend.utils.auto_payout import run_weekly_auto_payout

        driver = _driver(stripe_account_id=None)

        async def mock_get_rows(table, filters, **kw):
            if table == "auto_payout_batches":
                return []
            if table == "drivers":
                return [driver]
            return []

        async def mock_insert_one(table, doc):
            pass

        async def mock_update_one(table, filters, updates):
            pass

        mock_settings = AsyncMock(return_value={"stripe_secret_key": "sk_test_xxx"})

        with (
            patch("backend.utils.auto_payout.current_week_key", return_value=WEEK_KEY),
            patch("backend.utils.auto_payout.db_supabase.get_rows", side_effect=mock_get_rows),
            patch("backend.utils.auto_payout.db_supabase.insert_one", side_effect=mock_insert_one),
            patch("backend.utils.auto_payout.db_supabase.update_one", side_effect=mock_update_one),
            patch("backend.settings_loader.get_app_settings", mock_settings),
        ):
            result = await run_weekly_auto_payout()

        assert result["drivers_paid"] == 0


# ── Instant payout kill switch ─────────────────────────────────────────


class TestInstantPayoutKillSwitch:
    @pytest.mark.anyio
    async def test_blocks_when_disabled_for_service_area(self):
        from backend.routes.drivers.payouts import _require_instant_payout_enabled

        driver = {"service_area_id": "sa_001"}

        async def mock_get_rows(table, filters, **kw):
            if table == "service_areas":
                return [{"id": "sa_001", "instant_payout_enabled": False}]
            return []

        with patch("backend.routes.drivers.payouts.db_supabase.get_rows", side_effect=mock_get_rows):
            with pytest.raises(HTTPException) as exc_info:
                await _require_instant_payout_enabled(driver)
            assert exc_info.value.status_code == 403

    @pytest.mark.anyio
    async def test_allows_when_enabled_for_service_area(self):
        from backend.routes.drivers.payouts import _require_instant_payout_enabled

        driver = {"service_area_id": "sa_001"}

        async def mock_get_rows(table, filters, **kw):
            if table == "service_areas":
                return [{"id": "sa_001", "instant_payout_enabled": True}]
            return []

        with patch("backend.routes.drivers.payouts.db_supabase.get_rows", side_effect=mock_get_rows):
            await _require_instant_payout_enabled(driver)

    @pytest.mark.anyio
    async def test_allows_when_no_service_area(self):
        from backend.routes.drivers.payouts import _require_instant_payout_enabled

        driver = {"service_area_id": None}
        await _require_instant_payout_enabled(driver)

    @pytest.mark.anyio
    async def test_allows_when_service_area_missing_from_db(self):
        from backend.routes.drivers.payouts import _require_instant_payout_enabled

        driver = {"service_area_id": "sa_nonexistent"}

        async def mock_get_rows(table, filters, **kw):
            return []

        with patch("backend.routes.drivers.payouts.db_supabase.get_rows", side_effect=mock_get_rows):
            await _require_instant_payout_enabled(driver)


# ── Standard cashout 410 ──────────────────────────────────────────────


class TestStandardCashoutDisabled:
    @pytest.mark.anyio
    async def test_request_payout_returns_410(self):
        from backend.routes.drivers.payouts import request_payout

        with pytest.raises(HTTPException) as exc_info:
            await request_payout(current_user={"id": "user_001"})
        assert exc_info.value.status_code == 410
