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
    """Pins _request_payout_legacy: balance check, bank-account guard, payout persisted.

    POST /payouts is now a 410 stub (weekly auto-payouts); the original logic is
    preserved at _request_payout_legacy behind _STANDARD_CASHOUT_DISABLED for
    rollback. These tests pin that preserved path with the flag patched off.
    """

    @pytest.fixture(autouse=True)
    def _enable_legacy_cashout(self):
        with patch("backend.routes.drivers.payouts._STANDARD_CASHOUT_DISABLED", False):
            yield

    async def _request(
        self,
        amount: float = 50.00,
        payable_balance: float = 100.00,
        has_bank_account: bool = True,
        gst_bn: str | None = "123456789RT0001",
    ):
        from starlette.requests import Request as StarletteRequest

        from backend.routes.drivers import PayoutRequest, _request_payout_legacy

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
            result = await _request_payout_legacy(
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

        from backend.routes.drivers import PayoutRequest, _request_payout_legacy

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
                await _request_payout_legacy(req=req, request=mock_request, current_user={"id": "ghost-driver"})

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
                "backend.routes.drivers._deps.db_supabase.get_rides_for_driver",
                AsyncMock(side_effect=_get_rides_for_driver),
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
                assert query["payout_type"] == {"$in": ["stripe_sync", "legacy_outstanding_correction"]}
                assert query["status"] == "completed"
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

    async def test_t4a_includes_settled_legacy_outstanding_correction(self):
        """A settled (status='completed') legacy_outstanding_correction row
        (2026-08-17 write path) reports the same as a stripe_sync row —
        both are real legacy income actually paid through Stripe."""
        from backend.routes.drivers import get_t4a_summary

        driver = _driver_row()
        rides = [_ride_row(20.00)]

        async def _get_rows(table, query=None, **kwargs):
            if table == "drivers":
                return [driver]
            if table == "payouts":
                return [{"amount": 12.00, "payout_type": "legacy_outstanding_correction", "status": "completed"}]
            return []

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.routes.drivers._deps.db_supabase.get_rides_for_driver", AsyncMock(return_value=rides)),
        ):
            result = await get_t4a_summary(year=2025, current_user={"id": DRIVER_USER_ID})

        assert result["total_earnings"] == "32.00"
        assert result["legacy_synced_earnings"] == "12.00"

    async def test_t4a_excludes_previous_app_imported_rides(self):
        """Rides imported from the old app (utils/legacy_rides) are that app's
        income, and where it was paid through Stripe it is already reported by
        the 'stripe_sync' rows — counting both would report the same legacy
        dollars to the CRA twice and could push a driver over the $500 T4A
        threshold on money Spinr never paid."""
        from backend.routes.drivers import get_t4a_summary

        driver = _driver_row()
        legacy = _ride_row(400.00)
        legacy["legacy_import_metadata"] = {"source": "legacy_mongo_booking_import"}
        rides = [_ride_row(20.00), legacy]

        async def _get_rows(table, query=None, **kwargs):
            if table == "drivers":
                return [driver]
            return []

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.routes.drivers._deps.db_supabase.get_rides_for_driver", AsyncMock(return_value=rides)),
        ):
            result = await get_t4a_summary(year=2025, current_user={"id": DRIVER_USER_ID})

        # Only the $20 Spinr ride counts; the $400 imported ride is not income
        # this payer paid, so it is neither in the total nor in the trip count.
        assert result["total_earnings"] == "20.00"
        assert result["total_trips"] == 1

    async def test_t4a_years_lists_only_years_with_earnings(self):
        """The apps used to synthesize "last three completed years" and offer a
        T4A for each, so a driver with no income for a year was emailed a
        $0.00 slip. Only years with real income are returned."""
        from backend.routes.drivers import get_t4a_years

        driver = _driver_row()
        r2025 = _ride_row(120.00)
        r2025["created_at"] = "2025-04-02T00:00:00+00:00"
        r2023 = _ride_row(60.00)
        r2023["created_at"] = "2023-06-01T00:00:00+00:00"

        async def _get_rows(table, query=None, **kwargs):
            if table == "drivers":
                return [driver]
            if table == "payouts":
                return [{"amount": 300.00, "created_at": "2024-02-02T00:00:00+00:00"}]
            return []

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rides_for_driver",
                AsyncMock(return_value=[r2025, r2023]),
            ),
        ):
            result = await get_t4a_years(current_user={"id": DRIVER_USER_ID})

        # Newest first; 2024 comes from a stripe_sync payout with no rides.
        assert [y["year"] for y in result["years"]] == [2025, 2024, 2023]
        assert result["years"][0]["total_earnings"] == "120.00"
        assert result["years"][0]["total_trips"] == 1
        assert result["years"][1]["total_earnings"] == "300.00"
        assert result["years"][1]["total_trips"] == 0

    async def test_t4a_years_empty_when_no_earnings(self):
        """A driver who has never earned gets an empty list, so the app can
        hide the Tax Documents section instead of offering a $0.00 slip."""
        from backend.routes.drivers import get_t4a_years

        async def _get_rows(table, query=None, **kwargs):
            return [_driver_row()] if table == "drivers" else []

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.routes.drivers._deps.db_supabase.get_rides_for_driver", AsyncMock(return_value=[])),
        ):
            result = await get_t4a_years(current_user={"id": DRIVER_USER_ID})

        assert result["years"] == []

    async def test_t4a_years_excludes_previous_app_imported_rides(self):
        """A migrated driver whose only income was previous-app rides has no
        Spinr tax year — the imported rides must not conjure one."""
        from backend.routes.drivers import get_t4a_years

        legacy = _ride_row(900.00)
        legacy["created_at"] = "2024-03-03T00:00:00+00:00"
        legacy["legacy_import_metadata"] = {"source": "legacy_mongo_booking_import"}

        async def _get_rows(table, query=None, **kwargs):
            return [_driver_row()] if table == "drivers" else []

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.routes.drivers._deps.db_supabase.get_rides_for_driver", AsyncMock(return_value=[legacy])),
        ):
            result = await get_t4a_years(current_user={"id": DRIVER_USER_ID})

        assert result["years"] == []

    async def test_t4a_includes_cancellation_fees_and_bonuses_with_no_trips(self):
        """2026-09-23: a driver whose only income for the year was
        cancellation/no-show fees and quest/referral bonuses — already shown
        on their earnings statement — must see it on the T4A slip too. Before
        this fix the slip read $0.00."""
        from backend.routes.drivers import get_t4a_summary

        driver = _driver_row()
        seen: list = []

        async def _get_rows(table, query=None, **kwargs):
            seen.append((table, query))
            if table == "drivers":
                return [driver]
            if table == "rides" and query.get("status") == "cancelled":
                return [
                    {"id": "c1", "status": "cancelled", "cancellation_fee_driver": 4.00},
                    {"id": "c2", "status": "cancelled", "cancellation_fee_driver": "5.50"},
                ]
            if table == "driver_bonuses":
                return [{"amount": "500.00", "kind": "quest"}, {"amount": 25, "kind": "referral"}]
            return []

        batched = AsyncMock(return_value=[])
        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.routes.drivers._deps.db_supabase.get_rides_for_driver", AsyncMock(return_value=[])),
            patch("backend.routes.drivers._deps.db_supabase.get_rows_batched_in", batched),
        ):
            result = await get_t4a_summary(year=2025, current_user={"id": DRIVER_USER_ID})

        assert result["total_earnings"] == "534.50"
        assert result["net_earnings"] == "534.50"
        assert result["cancellation_fee_earnings"] == "9.50"
        assert result["bonus_earnings"] == "525.00"
        assert result["incentive_earnings"] == "0.00"
        assert result["total_trips"] == 0  # cancelled rides are not trips
        batched.assert_not_called()
        cancel_q = next(q for t, q in seen if t == "rides" and q.get("status") == "cancelled")
        assert cancel_q["driver_id"] == DRIVER_ID
        assert cancel_q["cancelled_at"] == {
            "$gte": "2025-01-01T00:00:00+00:00",
            "$lt": "2026-01-01T00:00:00+00:00",
        }

    async def test_t4a_includes_incentive_claims_for_slip_rides(self):
        """Per-ride incentives live in ride_incentive_claims, not in
        driver_earnings (fare-only) — they are paid income and belong on the
        slip. Looked up only for the non-legacy rides actually on the slip."""
        from backend.routes.drivers import get_t4a_summary

        legacy = {**_ride_row(400.00), "id": "legacy-1", "legacy_import_metadata": {"source": "x"}}
        rides = [_ride_row(20.00), legacy]

        async def _get_rows(table, query=None, **kwargs):
            return [_driver_row()] if table == "drivers" else []

        batched = AsyncMock(return_value=[{"ride_id": "ride-001", "bonus_amount": "5.25"}])
        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.routes.drivers._deps.db_supabase.get_rides_for_driver", AsyncMock(return_value=rides)),
            patch("backend.routes.drivers._deps.db_supabase.get_rows_batched_in", batched),
        ):
            result = await get_t4a_summary(year=2025, current_user={"id": DRIVER_USER_ID})

        assert result["total_earnings"] == "25.25"
        assert result["incentive_earnings"] == "5.25"
        batched.assert_awaited_once_with("ride_incentive_claims", "ride_id", ["ride-001"])

    async def test_t4a_years_offers_year_with_only_fees_and_bonuses(self):
        """The tax-year list must offer a slip for a year whose only income
        was cancellation fees / bonuses, bucketed by cancelled_at / created_at
        — otherwise the app never shows the slip the driver is owed."""
        from backend.routes.drivers import get_t4a_years

        r2025 = {**_ride_row(120.00), "created_at": "2025-04-02T00:00:00+00:00"}

        async def _get_rows(table, query=None, **kwargs):
            if table == "drivers":
                return [_driver_row()]
            if table == "rides" and query.get("status") == "cancelled":
                return [
                    {"cancellation_fee_driver": "4.00", "cancelled_at": "2024-05-01T00:00:00+00:00"},
                    # created_at in 2023 but cancelled in 2024 -> 2024.
                    {
                        "cancellation_fee_driver": "6.00",
                        "created_at": "2023-12-31T23:00:00+00:00",
                        "cancelled_at": "2024-01-01T01:00:00+00:00",
                    },
                ]
            if table == "driver_bonuses":
                return [{"amount": "50.00", "created_at": "2024-07-07T00:00:00+00:00"}]
            return []

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.routes.drivers._deps.db_supabase.get_rides_for_driver", AsyncMock(return_value=[r2025])),
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows_batched_in",
                AsyncMock(return_value=[{"ride_id": "ride-001", "bonus_amount": "3.00"}]),
            ),
        ):
            result = await get_t4a_years(current_user={"id": DRIVER_USER_ID})

        assert [y["year"] for y in result["years"]] == [2025, 2024]
        assert result["years"][0]["total_earnings"] == "123.00"  # ride + its incentive
        assert result["years"][1]["total_earnings"] == "60.00"
        assert result["years"][1]["total_trips"] == 0

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
        stack.enter_context(
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[driver]))
        )
        stack.enter_context(patch("backend.routes.drivers._deps.db_supabase.update_one", update_mock))
        stack.enter_context(
            patch("backend.routes.drivers._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver))
        )
        stack.enter_context(
            patch("backend.routes.drivers._shared._encrypt_driver_pii", AsyncMock(side_effect=lambda d: d))
        )
        stack.enter_context(
            patch("backend.routes.drivers._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d))
        )
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

    @pytest.mark.anyio
    @pytest.mark.parametrize("obligated", [False, True])
    async def test_v2_vehicle_edit_uses_policy_pause_without_raw_offline_write(self, obligated):
        from backend.routes.drivers import UpdateDriverProfileRequest, update_my_driver
        from backend.routes.drivers import profile as profile_route
        from backend.services import driver_availability_service

        driver = self._make_driver(is_online=True, is_available=True, is_verified=True)
        update_mock = AsyncMock(return_value={"id": DRIVER_ID})
        pause = AsyncMock(return_value={"code": "OK", "is_online": True, "accepting_requests": False,
                                       "has_trip": obligated})
        period = AsyncMock()
        with (
            self._patches(driver, update_mock),
            patch.object(profile_route._deps, "has_active_ride_obligation", AsyncMock(return_value=obligated)),
            patch.object(profile_route._deps, "record_period_transition", period),
            patch("backend.utils.vehicle_history.record_vehicle_changes", AsyncMock()),
            patch("backend.utils.driver_status_notifications.notify_driver_status_change", AsyncMock()),
            patch.object(driver_availability_service, "driver_availability_v2_enabled", AsyncMock(return_value=True)),
            patch.object(driver_availability_service, "pause_driver_for_policy", pause),
        ):
            await update_my_driver(
                body=UpdateDriverProfileRequest(vehicle_color="blue"),
                current_user={"id": DRIVER_USER_ID},
            )

        updates = update_mock.await_args.args[2]
        assert updates["status"] == "needs_review"
        assert "is_online" not in updates and "is_available" not in updates
        pause.assert_awaited_once_with(
            DRIVER_USER_ID,
            blocking_statuses={"needs_review"},
            request_id=pause.await_args.kwargs["request_id"],
        )
        period.assert_not_awaited()

    @pytest.mark.anyio
    async def test_v2_profile_policy_race_returns_conflict_before_notice(self):
        from fastapi import HTTPException

        from backend.routes.drivers import UpdateDriverProfileRequest, update_my_driver
        from backend.routes.drivers import profile as profile_route
        from backend.services import driver_availability_service

        driver = self._make_driver(is_online=True, is_available=True, is_verified=True)
        update_mock = AsyncMock(return_value={"id": DRIVER_ID})
        notify = AsyncMock()
        pause = AsyncMock(return_value={"code": "POLICY_STATE_CHANGED"})
        with (
            self._patches(driver, update_mock),
            patch.object(profile_route._deps, "has_active_ride_obligation", AsyncMock(return_value=False)),
            patch("backend.utils.vehicle_history.record_vehicle_changes", AsyncMock()),
            patch("backend.utils.driver_status_notifications.notify_driver_status_change", notify),
            patch.object(driver_availability_service, "driver_availability_v2_enabled", AsyncMock(return_value=True)),
            patch.object(driver_availability_service, "pause_driver_for_policy", pause),
        ):
            with pytest.raises(HTTPException) as error:
                await update_my_driver(
                    body=UpdateDriverProfileRequest(vehicle_color="blue"),
                    current_user={"id": DRIVER_USER_ID},
                )

        assert error.value.status_code == 409
        assert update_mock.await_args.args[2]["status"] == "needs_review"
        assert "is_online" not in update_mock.await_args.args[2]
        notify.assert_not_awaited()
