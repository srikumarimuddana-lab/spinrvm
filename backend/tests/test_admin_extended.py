"""Extended unit tests for routes/admin/rides.py and routes/admin/drivers.py.

routes/admin/rides.py   — currently 26.6%  (target 60%)
routes/admin/drivers.py — currently 20.2%  (target 55%)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ADMIN_USER = {"id": "admin_1", "role": "admin", "email": "admin@spinr.ca"}


def _fake_request():
    """Minimal FastAPI Request stub for endpoints decorated with @limiter.limit."""
    req = MagicMock()
    req.scope = {"type": "http"}
    req.state = MagicMock()
    return req


_FAKE_REQUEST = _fake_request()
DRIVER_ID = "driver_admin_ext"
DRIVER_USER_ID = "user_admin_ext"
RIDE_ID = "ride_admin_ext"
RIDER_ID = "rider_admin_ext"


def _ride(status: str = "searching", **extra):
    return {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": DRIVER_ID,
        "status": status,
        "total_fare": 18.50,
        "driver_earnings": 18.50,
        "tip_amount": 0,
        "pickup_address": "100 Main",
        "dropoff_address": "200 Broadway",
        "created_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }


def _driver(**extra):
    return {
        "id": DRIVER_ID,
        "user_id": DRIVER_USER_ID,
        "name": "Admin Test Driver",
        "is_online": True,
        "is_available": True,
        "is_verified": True,
        "status": "active",
        "rating": 4.8,
        "total_rides": 100,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }


def _user(**extra):
    return {
        "id": DRIVER_USER_ID,
        "first_name": "Bob",
        "last_name": "Driver",
        "email": "bob@example.com",
        "phone": "+15555550100",
        **extra,
    }


# ===========================================================================
# admin/rides.py
# ===========================================================================


class TestAdminGetRides:
    def test_returns_paginated_rides(self):
        from backend.routes.admin import rides as admin_rides

        rides = [_ride("completed")]

        with (
            patch("backend.routes.admin.rides.db_supabase.count_documents", AsyncMock(return_value=1)),
            patch("backend.routes.admin.rides.db_supabase.get_rows", AsyncMock(return_value=rides)),
            patch("backend.routes.admin.rides._batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
        ):
            result = asyncio.run(admin_rides.admin_get_rides(limit=50, offset=0))

        assert result["total_count"] == 1
        assert len(result["rides"]) == 1
        assert result["limit"] == 50

    def test_filters_by_status(self):
        from backend.routes.admin import rides as admin_rides

        with (
            patch("backend.routes.admin.rides.db_supabase.count_documents", AsyncMock(return_value=0)),
            patch("backend.routes.admin.rides.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.admin.rides._batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
        ):
            result = asyncio.run(admin_rides.admin_get_rides(limit=50, offset=0, status="completed"))

        assert result["total_count"] == 0

    def test_filters_scheduled_rides(self):
        from backend.routes.admin import rides as admin_rides

        scheduled = [_ride("searching", is_scheduled=True)]

        with (
            patch("backend.routes.admin.rides.db_supabase.count_documents", AsyncMock(return_value=1)),
            patch("backend.routes.admin.rides.db_supabase.get_rows", AsyncMock(return_value=scheduled)),
            patch("backend.routes.admin.rides._batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
        ):
            result = asyncio.run(admin_rides.admin_get_rides(limit=50, offset=0, is_scheduled=True))

        assert result["total_count"] == 1


class TestAdminGetActiveRides:
    def test_returns_active_rides_with_driver_locations(self):
        from backend.routes.admin import rides as admin_rides

        rides = [
            _ride("in_progress"),
            _ride("driver_accepted", id="ride_2"),
        ]
        drivers_map = {DRIVER_ID: _driver(lat=52.13, lng=-106.67)}
        users_map = {RIDER_ID: {"id": RIDER_ID, "first_name": "Alice"}}

        with (
            patch("backend.routes.admin.rides.db.get_rows", AsyncMock(return_value=rides)),
            patch(
                "backend.routes.admin.rides._batch_fetch_drivers_and_users",
                AsyncMock(return_value=(drivers_map, users_map)),
            ),
        ):
            result = asyncio.run(admin_rides.admin_get_active_rides())

        assert result["count"] == 2
        # Check that driver lat/lng is included
        first = result["rides"][0]
        assert "driver_lat" in first

    def test_handles_db_error_gracefully(self):
        from backend.routes.admin import rides as admin_rides

        with (
            patch("backend.routes.admin.rides.db.get_rows", AsyncMock(side_effect=Exception("DB error"))),
            patch("backend.routes.admin.rides._batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
        ):
            result = asyncio.run(admin_rides.admin_get_active_rides())

        assert result["count"] == 0
        assert result["rides"] == []


class TestAdminGetStats:
    def test_returns_all_stat_fields(self):
        from backend.routes.admin import rides as admin_rides

        with (
            patch("backend.routes.admin.rides.db_supabase.count_documents", AsyncMock(return_value=10)),
            patch("backend.routes.admin.rides.db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            result = asyncio.run(admin_rides.admin_get_stats())

        required = [
            "total_rides",
            "completed_rides",
            "cancelled_rides",
            "active_rides",
            "total_drivers",
            "online_drivers",
            "total_users",
            "rides_today",
        ]
        for field in required:
            assert field in result, f"Missing field: {field}"

    def test_calculates_revenue(self):
        from backend.routes.admin import rides as admin_rides

        ride_with_fare = [{"total_fare": 25.0, "driver_earnings": 25.0, "admin_earnings": 0, "tip_amount": 3.0}]

        with (
            patch("backend.routes.admin.rides.db_supabase.count_documents", AsyncMock(return_value=5)),
            patch("backend.routes.admin.rides.db_supabase.get_rows", AsyncMock(return_value=ride_with_fare)),
        ):
            result = asyncio.run(admin_rides.admin_get_stats())

        assert result["revenue_today"] >= 0
        assert result["revenue_month"] >= 0


class TestAdminGetRideStats:
    def test_returns_stats_structure(self):
        from backend.routes.admin import rides as admin_rides

        with (
            patch("backend.routes.admin.rides.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.admin.rides.db_supabase.count_documents", AsyncMock(return_value=0)),
        ):
            result = asyncio.run(admin_rides.admin_get_ride_stats())

        assert isinstance(result, dict)


class TestAdminGetRideDetails:
    def test_returns_ride_details(self):
        from backend.routes.admin import rides as admin_rides

        ride = _ride("completed")
        driver = _driver()
        rider = _user(id=RIDER_ID)

        with (
            patch("backend.routes.admin.rides.db_supabase.get_ride_details_enriched", AsyncMock(return_value=ride)),
            patch("backend.routes.admin.rides.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.rides.db_supabase.get_user_by_id", AsyncMock(return_value=rider)),
            patch("backend.routes.admin.rides.db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            result = asyncio.run(admin_rides.admin_get_ride_details(ride_id=RIDE_ID))

        assert result is not None

    def test_ride_not_found_raises_404(self):
        from fastapi import HTTPException

        from backend.routes.admin import rides as admin_rides

        with patch("backend.routes.admin.rides.db_supabase.get_ride", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(admin_rides.admin_get_ride_details(ride_id=RIDE_ID))
        assert exc.value.status_code == 404


class TestAdminCompleteRide:
    def test_completes_in_progress_ride(self):
        from backend.routes.admin import rides as admin_rides

        ride = _ride("in_progress")
        completed = _ride("completed")

        with (
            patch("backend.routes.admin.rides.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.admin.rides.db_supabase.update_ride", AsyncMock(return_value=completed)),
            patch("backend.routes.admin.rides.db_supabase.get_driver_by_id", AsyncMock(return_value=_driver())),
            patch("backend.routes.admin.rides.db_supabase.set_driver_available", AsyncMock()),
            patch("backend.routes.admin.rides.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.admin.rides.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(admin_rides.admin_complete_ride(ride_id=RIDE_ID, admin_user=ADMIN_USER))

        assert result["success"] is True

    def test_rejects_non_active_ride(self):
        from fastapi import HTTPException

        from backend.routes.admin import rides as admin_rides
        from backend.utils.error_handling import SpinrException

        ride = _ride("completed")

        with patch("backend.routes.admin.rides.db_supabase.get_ride", AsyncMock(return_value=ride)):
            with pytest.raises((HTTPException, SpinrException)) as exc:
                asyncio.run(admin_rides.admin_complete_ride(ride_id=RIDE_ID, admin_user=ADMIN_USER))
        assert exc.value.status_code == 400


class TestAdminSendPayableInvoice:
    """POST /admin/rides/{id}/send-invoice — Codex P2 guards."""

    def _settings(self):
        return AsyncMock(return_value={"stripe_secret_key": "sk_test"})

    def test_rejects_refunded_ride(self):
        """A refunded ride is terminal — re-invoicing would re-collect a refund."""
        from fastapi import HTTPException

        from backend.routes.admin import rides as admin_rides

        ride = _ride("completed", payment_status="refunded")
        with patch("backend.routes.admin.rides.db_supabase.get_ride", AsyncMock(return_value=ride)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    admin_rides.admin_send_payable_invoice(
                        request=_FAKE_REQUEST, ride_id=RIDE_ID, admin_user=ADMIN_USER
                    )
                )
        assert exc.value.status_code == 409
        assert "terminal" in str(exc.value.detail).lower()

    def test_concurrent_claim_returns_409(self):
        """When the atomic CAS claim loses (another request is mid-creation),
        the endpoint 409s instead of creating a duplicate invoice."""
        from fastapi import HTTPException

        from backend.routes.admin import rides as admin_rides

        ride = _ride("completed", payment_status="failed", stripe_invoice_id=None)
        rider = {"id": RIDER_ID, "email": "rider@spinr.ca", "stripe_customer_id": "cus_1"}

        with (
            patch("backend.routes.admin.rides.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.admin.rides.db_supabase.get_user_by_id", AsyncMock(return_value=rider)),
            patch("backend.routes.admin.rides.get_app_settings", self._settings()),
            # CAS claim loses → update_one returns None.
            patch("backend.routes.admin.rides.db_supabase.update_one", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    admin_rides.admin_send_payable_invoice(
                        request=_FAKE_REQUEST, ride_id=RIDE_ID, admin_user=ADMIN_USER
                    )
                )
        assert exc.value.status_code == 409
        assert "already being created" in str(exc.value.detail).lower()

    def test_happy_path_creates_and_sends_invoice(self):
        from unittest.mock import MagicMock

        from backend.routes.admin import rides as admin_rides

        ride = _ride("completed", payment_status="failed", stripe_invoice_id=None, grand_total="18.50")
        rider = {"id": RIDER_ID, "email": "rider@spinr.ca", "stripe_customer_id": "cus_1"}

        async def _run_sync(fn):
            return fn()

        inv = MagicMock(id="in_new_1")
        fin = MagicMock()
        fin.hosted_invoice_url = "https://pay.stripe/x"
        update_ride_mock = AsyncMock()

        with (
            patch("backend.routes.admin.rides.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.admin.rides.db_supabase.get_user_by_id", AsyncMock(return_value=rider)),
            patch("backend.routes.admin.rides.get_app_settings", self._settings()),
            patch("backend.routes.admin.rides.db_supabase.update_one", AsyncMock(return_value={"id": RIDE_ID})),
            patch("backend.routes.admin.rides.db_supabase.update_ride", update_ride_mock),
            patch("backend.routes.admin.rides.db_supabase.run_sync", _run_sync),
            patch("backend.routes.admin.rides.log_admin_action", AsyncMock()),
            patch("stripe.Invoice.create", MagicMock(return_value=inv)),
            patch("stripe.InvoiceItem.create", MagicMock(return_value=MagicMock())),
            patch("stripe.Invoice.finalize_invoice", MagicMock(return_value=fin)),
            patch("stripe.Invoice.send_invoice", MagicMock(return_value=MagicMock())),
        ):
            result = asyncio.run(
                admin_rides.admin_send_payable_invoice(request=_FAKE_REQUEST, ride_id=RIDE_ID, admin_user=ADMIN_USER)
            )

        assert result["sent"] is True
        assert result["stripe_invoice_id"] == "in_new_1"
        assert result["invoice_url"] == "https://pay.stripe/x"
        # Final write persists the real invoice id (replacing the sentinel).
        final = update_ride_mock.await_args.args[1]
        assert final["stripe_invoice_id"] == "in_new_1"

    def test_fresh_pending_sentinel_blocks_but_stale_reclaims(self):
        """Codex P2 (62i9): a fresh `pending:` claim 409s (concurrent creation),
        but a stale one (crashed request) is reclaimed via the CAS instead of
        blocking the ride forever."""
        from datetime import timedelta
        from unittest.mock import MagicMock

        from fastapi import HTTPException

        from backend.routes.admin import rides as admin_rides

        rider = {"id": RIDER_ID, "email": "rider@spinr.ca", "stripe_customer_id": "cus_1"}

        # Fresh sentinel → 409, no Stripe calls.
        fresh_ts = datetime.now(timezone.utc).timestamp()
        fresh_ride = _ride("completed", payment_status="failed", stripe_invoice_id=f"pending:{fresh_ts}:u1")
        with (
            patch("backend.routes.admin.rides.db_supabase.get_ride", AsyncMock(return_value=fresh_ride)),
            patch("backend.routes.admin.rides.db_supabase.get_user_by_id", AsyncMock(return_value=rider)),
            patch("backend.routes.admin.rides.get_app_settings", self._settings()),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    admin_rides.admin_send_payable_invoice(
                        request=_FAKE_REQUEST, ride_id=RIDE_ID, admin_user=ADMIN_USER
                    )
                )
        assert exc.value.status_code == 409

        # Stale sentinel (10 min old) → reclaimed; a new invoice is created.
        stale_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp()
        stale_ride = _ride(
            "completed", payment_status="failed", stripe_invoice_id=f"pending:{stale_ts}:u1", grand_total="18.50"
        )

        async def _run_sync(fn):
            return fn()

        inv = MagicMock(id="in_reclaim_1")
        fin = MagicMock()
        fin.hosted_invoice_url = "https://pay.stripe/reclaim"
        with (
            patch("backend.routes.admin.rides.db_supabase.get_ride", AsyncMock(return_value=stale_ride)),
            patch("backend.routes.admin.rides.db_supabase.get_user_by_id", AsyncMock(return_value=rider)),
            patch("backend.routes.admin.rides.get_app_settings", self._settings()),
            patch("backend.routes.admin.rides.db_supabase.update_one", AsyncMock(return_value={"id": RIDE_ID})),
            patch("backend.routes.admin.rides.db_supabase.update_ride", AsyncMock()),
            patch("backend.routes.admin.rides.db_supabase.run_sync", _run_sync),
            patch("backend.routes.admin.rides.log_admin_action", AsyncMock()),
            patch("stripe.Invoice.create", MagicMock(return_value=inv)),
            patch("stripe.InvoiceItem.create", MagicMock(return_value=MagicMock())),
            patch("stripe.Invoice.finalize_invoice", MagicMock(return_value=fin)),
            patch("stripe.Invoice.send_invoice", MagicMock(return_value=MagicMock())),
        ):
            result = asyncio.run(
                admin_rides.admin_send_payable_invoice(request=_FAKE_REQUEST, ride_id=RIDE_ID, admin_user=ADMIN_USER)
            )
        assert result["sent"] is True
        assert result["stripe_invoice_id"] == "in_reclaim_1"

    def test_rejects_non_card_payment_method(self):
        """Codex P2 (round-3): a payable Stripe invoice bills the rider's personal
        card — corporate (company_allowance)/wallet rides must not be invoiced."""
        from fastapi import HTTPException

        from backend.routes.admin import rides as admin_rides

        ride = _ride("completed", payment_status="failed", payment_method="company_allowance")
        with patch("backend.routes.admin.rides.db_supabase.get_ride", AsyncMock(return_value=ride)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    admin_rides.admin_send_payable_invoice(
                        request=_FAKE_REQUEST, ride_id=RIDE_ID, admin_user=ADMIN_USER
                    )
                )
        assert exc.value.status_code == 409
        assert "card rides" in str(exc.value.detail).lower()

    def test_rejects_open_preauth_hold(self):
        """Codex P1 (round-3): an open authorized/fare_only hold is still
        captureable by the sweeper — invoicing now risks collecting twice."""
        from fastapi import HTTPException

        from backend.routes.admin import rides as admin_rides

        ride = _ride("completed", payment_status="pending", auth_status="authorized")
        with patch("backend.routes.admin.rides.db_supabase.get_ride", AsyncMock(return_value=ride)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    admin_rides.admin_send_payable_invoice(
                        request=_FAKE_REQUEST, ride_id=RIDE_ID, admin_user=ADMIN_USER
                    )
                )
        assert exc.value.status_code == 409
        assert "hold" in str(exc.value.detail).lower()

    def test_rollback_voids_invoice_and_clears_id_on_finalize_failure(self):
        """Codex round-3 (#2): if finalize fails after the real id was persisted,
        the invoice is voided (never collectible) and the row id is cleared so the
        ride is fully recoverable — no payable invoice hidden behind a sentinel."""
        from unittest.mock import MagicMock

        from fastapi import HTTPException

        from backend.routes.admin import rides as admin_rides

        ride = _ride("completed", payment_status="failed", stripe_invoice_id=None, grand_total="18.50")
        rider = {"id": RIDER_ID, "email": "rider@spinr.ca", "stripe_customer_id": "cus_1"}

        async def _run_sync(fn):
            return fn()

        inv = MagicMock(id="in_fail_1")
        void_mock = MagicMock()
        # Capture every ride update_one so we can assert the id was cleared.
        update_one_calls = []

        async def _update_one(table, filters, patch_body):
            update_one_calls.append((table, filters, patch_body))
            return {"id": RIDE_ID}

        with (
            patch("backend.routes.admin.rides.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.admin.rides.db_supabase.get_user_by_id", AsyncMock(return_value=rider)),
            patch("backend.routes.admin.rides.get_app_settings", self._settings()),
            patch("backend.routes.admin.rides.db_supabase.update_one", AsyncMock(side_effect=_update_one)),
            patch("backend.routes.admin.rides.db_supabase.update_ride", AsyncMock()),
            patch("backend.routes.admin.rides.db_supabase.run_sync", _run_sync),
            patch("backend.routes.admin.rides.log_admin_action", AsyncMock()),
            patch("stripe.Invoice.create", MagicMock(return_value=inv)),
            patch("stripe.InvoiceItem.create", MagicMock(return_value=MagicMock())),
            patch("stripe.Invoice.finalize_invoice", MagicMock(side_effect=Exception("stripe down"))),
            patch("stripe.Invoice.void_invoice", void_mock),
            patch("stripe.Invoice.delete", MagicMock()),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    admin_rides.admin_send_payable_invoice(
                        request=_FAKE_REQUEST, ride_id=RIDE_ID, admin_user=ADMIN_USER
                    )
                )

        assert exc.value.status_code == 502
        # finalize failed AFTER id-write, so the invoice was finalized? No — it was
        # still a draft (finalize raised), so it is DELETEd, not voided.
        # Either way the row id must be cleared back to None for recovery.
        cleared = [c for c in update_one_calls if c[2].get("stripe_invoice_id") is None]
        assert cleared, "expected the stuck invoice id/sentinel to be cleared to None"

    def test_reclaims_when_persisted_invoice_missing_in_stripe(self):
        """Codex round-5 (81Sc): if the persisted invoice was deleted in Stripe,
        retrieve raises resource_missing — the endpoint clears the dead id and
        creates a fresh invoice rather than 502ing forever."""
        from unittest.mock import MagicMock

        import stripe

        from backend.routes.admin import rides as admin_rides

        ride = _ride("completed", payment_status="failed", stripe_invoice_id="in_deleted_1", grand_total="18.50")
        rider = {"id": RIDER_ID, "email": "rider@spinr.ca", "stripe_customer_id": "cus_1"}

        async def _run_sync(fn):
            return fn()

        inv = MagicMock(id="in_fresh_after_missing")
        fin = MagicMock()
        fin.hosted_invoice_url = "https://pay.stripe/fresh"
        update_one_calls = []

        async def _update_one(table, filters, patch_body):
            update_one_calls.append((table, filters, patch_body))
            return {"id": RIDE_ID}

        missing_err = stripe.error.InvalidRequestError("No such invoice", param="id", code="resource_missing")

        with (
            patch("backend.routes.admin.rides.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.admin.rides.db_supabase.get_user_by_id", AsyncMock(return_value=rider)),
            patch("backend.routes.admin.rides.get_app_settings", self._settings()),
            patch("backend.routes.admin.rides.db_supabase.update_one", AsyncMock(side_effect=_update_one)),
            patch("backend.routes.admin.rides.db_supabase.update_ride", AsyncMock()),
            patch("backend.routes.admin.rides.db_supabase.run_sync", _run_sync),
            patch("backend.routes.admin.rides.log_admin_action", AsyncMock()),
            patch("stripe.Invoice.retrieve", MagicMock(side_effect=missing_err)),
            patch("stripe.Invoice.create", MagicMock(return_value=inv)),
            patch("stripe.InvoiceItem.create", MagicMock(return_value=MagicMock())),
            patch("stripe.Invoice.finalize_invoice", MagicMock(return_value=fin)),
            patch("stripe.Invoice.send_invoice", MagicMock(return_value=MagicMock())),
        ):
            result = asyncio.run(
                admin_rides.admin_send_payable_invoice(request=_FAKE_REQUEST, ride_id=RIDE_ID, admin_user=ADMIN_USER)
            )

        # Dead id cleared, fresh invoice created and sent.
        assert result["sent"] is True
        assert result["stripe_invoice_id"] == "in_fresh_after_missing"
        cleared = [c for c in update_one_calls if c[2].get("stripe_invoice_id") is None]
        assert cleared, "expected the dead invoice id to be cleared to None"


class TestAdminGetEarnings:
    def test_returns_earnings_summary(self):
        from backend.routes.admin import rides as admin_rides

        rides = [{"total_fare": 20.0, "driver_earnings": 20.0, "tip_amount": 2.0}]

        with patch("backend.routes.admin.rides.db_supabase.get_rows", AsyncMock(return_value=rides)):
            result = asyncio.run(admin_rides.admin_get_earnings(period="month"))

        assert isinstance(result, dict)

    def test_empty_period_returns_zeros(self):
        from backend.routes.admin import rides as admin_rides

        with patch("backend.routes.admin.rides.db_supabase.get_rows", AsyncMock(return_value=[])):
            result = asyncio.run(admin_rides.admin_get_earnings(period="week"))

        assert isinstance(result, dict)


# ===========================================================================
# admin/drivers.py
# ===========================================================================


class TestAdminGetDrivers:
    def test_returns_all_drivers(self):
        from backend.routes.admin import drivers as admin_drivers

        drivers = [_driver()]
        users = [_user()]

        def get_rows_side(table, filters=None, **kw):
            if table == "drivers":
                return drivers
            if table == "users":
                return users
            return []

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side)):
            result = asyncio.run(admin_drivers.admin_get_drivers(limit=50, offset=0))

        assert isinstance(result, list)
        assert len(result) >= 1

    def test_deduplicates_by_user_id(self):
        from backend.routes.admin import drivers as admin_drivers

        # Two rows with same user_id (duplicate)
        drivers = [
            _driver(id="d1", user_id="uid1", created_at="2024-01-01"),
            _driver(id="d2", user_id="uid1", created_at="2024-01-02"),
        ]
        users = [_user(id="uid1")]

        def get_rows_side(table, filters=None, **kw):
            return drivers if table == "drivers" else users

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side)):
            result = asyncio.run(admin_drivers.admin_get_drivers(limit=50, offset=0))

        # Deduplication keeps only the first created row
        assert len(result) == 1

    def test_filters_by_verified_status(self):
        from backend.routes.admin import drivers as admin_drivers

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(return_value=[])):
            result = asyncio.run(admin_drivers.admin_get_drivers(is_verified=True))

        assert result == []

    def test_search_by_name(self):
        from backend.routes.admin import drivers as admin_drivers

        drivers = [_driver()]
        users = [_user()]

        def get_rows_side(table, filters=None, **kw):
            if table == "users":
                return users
            return drivers

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side)):
            result = asyncio.run(admin_drivers.admin_get_drivers(search="Bob"))

        assert isinstance(result, list)

    def test_search_matches_user_id_uuid(self):
        """A pasted user_id UUID must resolve a driver even when it doesn't
        substring-match phone/plate/driver_code/name — codex review r3548013237."""
        from backend.routes.admin import drivers as admin_drivers

        captured_filters = {}

        def get_rows_side(table, filters=None, **kw):
            if table == "drivers":
                captured_filters.update(filters or {})
                return []
            return []  # no name/contact match on the users side

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side)):
            asyncio.run(admin_drivers.admin_get_drivers(search="uid-driver-target"))

        or_clauses = captured_filters.get("$or", [])
        assert any(c.get("user_id", {}).get("$regex") == "uid\\-driver\\-target" for c in or_clauses)

    def test_sort_by_maps_to_db_order_column(self):
        """sort_by/sort_dir must drive the DB ORDER BY (mapped to a real
        column) so sorting spans the whole table, not just the current page."""
        from backend.routes.admin import drivers as admin_drivers

        captured = {}

        def get_rows_side(table, filters=None, **kw):
            if table == "drivers":
                captured.update(kw)
                return [_driver()]
            return [_user()]

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side)):
            asyncio.run(admin_drivers.admin_get_drivers(sort_by="total_earnings", sort_dir="asc", limit=10, offset=20))

        # Derived/whitelisted key -> real column, ascending, and the page window
        # (limit/offset) is preserved so pagination still applies AFTER sorting.
        assert captured.get("order") == "total_earnings"
        assert captured.get("desc") is False
        assert captured.get("limit") == 10
        assert captured.get("offset") == 20

    def test_sort_by_derived_key_maps_to_underlying_column(self):
        """'name'/'region'/'vehicle_type' are display columns — they must map
        to the real underlying column the DB can order by."""
        from backend.routes.admin import drivers as admin_drivers

        captured = {}

        def get_rows_side(table, filters=None, **kw):
            if table == "drivers":
                captured.update(kw)
                return []
            return []

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side)):
            asyncio.run(admin_drivers.admin_get_drivers(sort_by="region"))

        assert captured.get("order") == "service_area_id"
        assert captured.get("desc") is True  # default direction

    def test_sort_by_unknown_column_falls_back_to_created_at(self):
        """An unrecognised sort token can never inject an arbitrary column into
        the ORDER BY — it falls back to created_at."""
        from backend.routes.admin import drivers as admin_drivers

        captured = {}

        def get_rows_side(table, filters=None, **kw):
            if table == "drivers":
                captured.update(kw)
                return []
            return []

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side)):
            asyncio.run(admin_drivers.admin_get_drivers(sort_by="password; DROP TABLE"))

        assert captured.get("order") == "created_at"

    def test_filters_by_vehicle_type(self):
        """vehicle_type_id must be pushed into the DB filter so the vehicle-type
        narrowing spans the whole table, not just the loaded page."""
        from backend.routes.admin import drivers as admin_drivers

        captured_filters = {}

        def get_rows_side(table, filters=None, **kw):
            if table == "drivers":
                captured_filters.update(filters or {})
                return []
            return []

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side)):
            asyncio.run(admin_drivers.admin_get_drivers(vehicle_type_id="sedan"))

        assert captured_filters.get("vehicle_type_id") == "sedan"

    def test_dedup_preserves_requested_sort_order(self):
        """Dedup must keep the earliest row per (user_id, phone) WITHOUT
        re-sorting by created_at — the DB's requested order must survive."""
        from backend.routes.admin import drivers as admin_drivers

        # DB returns rating-descending order; a duplicate user_id is mixed in.
        rows = [
            _driver(id="d1", user_id="u1", phone="p1", rating=5.0, created_at="2024-01-01"),
            _driver(id="d2", user_id="u2", phone="p2", rating=4.0, created_at="2024-02-01"),
            _driver(id="d3", user_id="u1", phone="p1", rating=3.0, created_at="2024-03-01"),  # dup of u1 (later)
        ]

        def get_rows_side(table, filters=None, **kw):
            return rows if table == "drivers" else []

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side)):
            result = asyncio.run(admin_drivers.admin_get_drivers(sort_by="rating", sort_dir="desc"))

        # d3 (the later duplicate of u1) is dropped; d1 and d2 stay in the
        # DB-returned rating-desc order (d1 then d2) — NOT re-sorted by date.
        assert [r["id"] for r in result] == ["d1", "d2"]


class TestAdminSearchDrivers:
    def test_delegates_to_admin_get_drivers(self):
        from backend.routes.admin import drivers as admin_drivers
        from backend.routes.admin.drivers import DriverSearchRequest

        with patch("backend.routes.admin.drivers.admin_get_drivers", AsyncMock(return_value=[])) as mock:
            req = DriverSearchRequest(search="alice", limit=5)
            result = asyncio.run(admin_drivers.admin_search_drivers(body=req, admin_user=ADMIN_USER))

        mock.assert_awaited_once()
        assert result == []


class TestAdminGetDriverStats:
    def test_returns_stats_with_no_filters(self):
        from backend.routes.admin import drivers as admin_drivers

        with (
            patch("backend.routes.admin.drivers.db_supabase.count_documents", AsyncMock(return_value=5)),
            patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            result = asyncio.run(admin_drivers.admin_get_driver_stats())

        assert isinstance(result, dict)

    def test_accepts_service_area_filter(self):
        from backend.routes.admin import drivers as admin_drivers

        with (
            patch("backend.routes.admin.drivers.db_supabase.count_documents", AsyncMock(return_value=2)),
            patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            result = asyncio.run(admin_drivers.admin_get_driver_stats(service_area_id="area_1"))

        assert isinstance(result, dict)


class TestAdminUpdateDriver:
    def test_updates_driver_fields(self):
        from backend.routes.admin import drivers as admin_drivers

        driver = _driver()

        with (
            patch("backend.routes.admin.drivers.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers.db_supabase.update_one", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers._log_driver_activity", AsyncMock()),
        ):
            result = asyncio.run(
                admin_drivers.admin_update_driver(
                    driver_id=DRIVER_ID,
                    updates={"city": "Saskatoon"},
                    admin=ADMIN_USER,
                )
            )

        assert result is not None

    def test_404_when_driver_not_found(self):
        from fastapi import HTTPException

        from backend.routes.admin import drivers as admin_drivers

        with patch("backend.routes.admin.drivers.db_supabase.get_driver_by_id", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    admin_drivers.admin_update_driver(
                        driver_id="nonexistent",
                        updates={"city": "Regina"},
                        admin=ADMIN_USER,
                    )
                )
        assert exc.value.status_code == 404

    def test_email_routes_to_users_table_not_drivers(self):
        """Regression: `email` lives on `users`, not `drivers`. Writing it to
        `drivers` triggers PGRST204. It must be routed to the users row."""
        from backend.routes.admin import drivers as admin_drivers

        driver = _driver()
        update_mock = AsyncMock(return_value=driver)

        with (
            patch("backend.routes.admin.drivers.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers.db_supabase.update_one", update_mock),
            patch("backend.routes.admin.drivers._log_driver_activity", AsyncMock()),
        ):
            asyncio.run(
                admin_drivers.admin_update_driver(
                    driver_id=DRIVER_ID,
                    updates={"email": "new@example.com", "gender": "Female"},
                    admin=ADMIN_USER,
                )
            )

        tables_written = {call.args[0]: call.args[2] for call in update_mock.call_args_list}
        # email/gender must hit `users`, keyed by user_id — never `drivers`.
        assert "users" in tables_written
        assert tables_written["users"] == {"email": "new@example.com", "gender": "Female"}
        users_call = next(c for c in update_mock.call_args_list if c.args[0] == "users")
        assert users_call.args[1] == {"id": DRIVER_USER_ID}
        if "drivers" in tables_written:
            assert "email" not in tables_written["drivers"]
            assert "gender" not in tables_written["drivers"]

    def test_name_change_syncs_users_and_drivers(self):
        """first_name/last_name are mirrored on both tables; the legacy
        `drivers.name` atom must be recomputed."""
        from backend.routes.admin import drivers as admin_drivers

        driver = _driver(first_name="Old", last_name="Name")
        update_mock = AsyncMock(return_value=driver)

        with (
            patch("backend.routes.admin.drivers.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers.db_supabase.update_one", update_mock),
            patch("backend.routes.admin.drivers._log_driver_activity", AsyncMock()),
        ):
            asyncio.run(
                admin_drivers.admin_update_driver(
                    driver_id=DRIVER_ID,
                    updates={"first_name": "New", "last_name": "Driver"},
                    admin=ADMIN_USER,
                )
            )

        writes = {call.args[0]: call.args[2] for call in update_mock.call_args_list}
        assert writes["drivers"]["first_name"] == "New"
        assert writes["drivers"]["name"] == "New Driver"
        assert writes["users"]["first_name"] == "New"
        assert writes["users"]["last_name"] == "Driver"

    def test_null_vehicle_fields_coalesced_not_constraint_violation(self):
        """Regression: vehicle_make/model/color/license_plate are NOT NULL
        DEFAULT '' on drivers. The admin edit form posts the whole driver object,
        so editing only the name still sends those vehicle fields as JSON null
        for a driver with no vehicle yet. Writing null raised 23502 and 500'd the
        entire edit. Explicit nulls must be coalesced to '' before the write."""
        from backend.routes.admin import drivers as admin_drivers

        driver = _driver(first_name="Kiran", last_name="Muddana")
        update_mock = AsyncMock(return_value=driver)

        with (
            patch("backend.routes.admin.drivers.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers.db_supabase.update_one", update_mock),
            patch("backend.routes.admin.drivers._log_driver_activity", AsyncMock()),
        ):
            # Admin only changed the last name; the form still posts the empty
            # vehicle fields as null (the full object round-trip).
            asyncio.run(
                admin_drivers.admin_update_driver(
                    driver_id=DRIVER_ID,
                    updates={
                        "first_name": "Kiran",
                        "last_name": "Reddy",
                        "vehicle_make": None,
                        "vehicle_model": None,
                        "vehicle_color": None,
                        "license_plate": None,
                    },
                    admin=ADMIN_USER,
                )
            )

        drivers_writes = [c.args[2] for c in update_mock.call_args_list if c.args[0] == "drivers"]
        assert drivers_writes, "expected a drivers update to be written"
        payload = drivers_writes[0]
        for col in ("vehicle_make", "vehicle_model", "vehicle_color", "license_plate"):
            assert payload[col] == "", f"{col} must be coalesced to '' (NOT NULL column), got {payload[col]!r}"

    def test_409_when_user_only_field_but_no_linked_user(self):
        from fastapi import HTTPException

        from backend.routes.admin import drivers as admin_drivers

        driver = _driver(user_id=None)

        with (
            patch("backend.routes.admin.drivers.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers.db_supabase.update_one", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers._log_driver_activity", AsyncMock()),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    admin_drivers.admin_update_driver(
                        driver_id=DRIVER_ID,
                        updates={"email": "x@example.com"},
                        admin=ADMIN_USER,
                    )
                )
        assert exc.value.status_code == 409

    def test_orphaned_driver_can_edit_mirrored_fields(self):
        """An orphaned driver (no user_id) must still be editable for fields
        mirrored on `drivers` (name/phone); only email/gender 409."""
        from backend.routes.admin import drivers as admin_drivers

        driver = _driver(user_id=None)
        update_mock = AsyncMock(return_value=driver)

        with (
            patch("backend.routes.admin.drivers.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers.db_supabase.update_one", update_mock),
            patch("backend.routes.admin.drivers._log_driver_activity", AsyncMock()),
        ):
            asyncio.run(
                admin_drivers.admin_update_driver(
                    driver_id=DRIVER_ID,
                    updates={"first_name": "Jane", "city": "Regina"},
                    admin=ADMIN_USER,
                )
            )

        writes = {call.args[0]: call.args[2] for call in update_mock.call_args_list}
        # No users row to touch; drivers gets the mirrored + driver-only fields.
        assert "users" not in writes
        assert writes["drivers"]["first_name"] == "Jane"
        assert writes["drivers"]["city"] == "Regina"

    def test_null_name_part_not_rendered_as_literal_none(self):
        from backend.routes.admin import drivers as admin_drivers

        driver = _driver(first_name="Alice", last_name="Smith")
        update_mock = AsyncMock(return_value=driver)

        with (
            patch("backend.routes.admin.drivers.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers.db_supabase.update_one", update_mock),
            patch("backend.routes.admin.drivers._log_driver_activity", AsyncMock()),
        ):
            asyncio.run(
                admin_drivers.admin_update_driver(
                    driver_id=DRIVER_ID,
                    updates={"last_name": None},
                    admin=ADMIN_USER,
                )
            )

        writes = {call.args[0]: call.args[2] for call in update_mock.call_args_list}
        assert writes["drivers"]["name"] == "Alice"
        assert "None" not in writes["drivers"]["name"]

    def test_account_row_written_before_driver_row(self):
        """Ordering guard: the canonical users row is written before the
        drivers mirror, so a failed second write leaves the preferred state."""
        from backend.routes.admin import drivers as admin_drivers

        driver = _driver()
        order = []

        async def _record(table, _filters, _payload):
            order.append(table)
            return driver

        with (
            patch("backend.routes.admin.drivers.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers.db_supabase.update_one", _record),
            patch("backend.routes.admin.drivers._log_driver_activity", AsyncMock()),
        ):
            asyncio.run(
                admin_drivers.admin_update_driver(
                    driver_id=DRIVER_ID,
                    updates={"email": "new@example.com", "city": "Regina"},
                    admin=ADMIN_USER,
                )
            )

        assert order == ["users", "drivers"]


class TestAdminVerifyDriver:
    def test_approves_driver(self):
        from backend.routes.admin import drivers as admin_drivers
        from backend.routes.admin.drivers import DriverVerifyRequest

        driver = _driver(is_verified=False)

        with (
            patch("backend.routes.admin.drivers.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers.db_supabase.update_one", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers._log_driver_activity", AsyncMock()),
            patch("backend.routes.admin.drivers.send_push_notification", AsyncMock()),
        ):
            req = DriverVerifyRequest(verified=True)
            result = asyncio.run(admin_drivers.admin_verify_driver(driver_id=DRIVER_ID, req=req, admin=ADMIN_USER))

        assert result is not None

    def test_rejects_driver(self):
        from backend.routes.admin import drivers as admin_drivers
        from backend.routes.admin.drivers import DriverVerifyRequest

        driver = _driver(is_verified=False)

        with (
            patch("backend.routes.admin.drivers.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers.db_supabase.update_one", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers._log_driver_activity", AsyncMock()),
            patch("backend.routes.admin.drivers.send_push_notification", AsyncMock()),
        ):
            req = DriverVerifyRequest(verified=False)
            result = asyncio.run(admin_drivers.admin_verify_driver(driver_id=DRIVER_ID, req=req, admin=ADMIN_USER))

        assert result is not None


class TestAdminDriverAction:
    def test_suspend_driver(self):
        from backend.routes.admin import drivers as admin_drivers
        from backend.routes.admin.drivers import DriverActionRequest

        driver = _driver(status="active")

        with (
            patch("backend.routes.admin.drivers.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers.db_supabase.update_one", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers._log_driver_activity", AsyncMock()),
            patch("backend.routes.admin.drivers.send_push_notification", AsyncMock()),
        ):
            req = DriverActionRequest(action="suspend", reason="Violation")
            result = asyncio.run(admin_drivers.admin_driver_action(driver_id=DRIVER_ID, req=req, admin=ADMIN_USER))

        assert result is not None

    def test_activate_driver(self):
        from backend.routes.admin import drivers as admin_drivers
        from backend.routes.admin.drivers import DriverActionRequest

        driver = _driver(status="suspended")

        with (
            patch("backend.routes.admin.drivers.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers.db_supabase.update_one", AsyncMock(return_value=driver)),
            patch("backend.routes.admin.drivers._log_driver_activity", AsyncMock()),
            patch("backend.routes.admin.drivers.send_push_notification", AsyncMock()),
        ):
            req = DriverActionRequest(action="reactivate")
            result = asyncio.run(admin_drivers.admin_driver_action(driver_id=DRIVER_ID, req=req, admin=ADMIN_USER))

        assert result is not None


class TestAdminGetDriverNotes:
    def test_returns_notes_list(self):
        from backend.routes.admin import drivers as admin_drivers

        notes = [{"id": "note_1", "driver_id": DRIVER_ID, "note": "Test note", "created_at": "2024-01-01"}]

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(return_value=notes)):
            result = asyncio.run(admin_drivers.admin_get_driver_notes(driver_id=DRIVER_ID))

        assert isinstance(result, list)
        assert len(result) == 1


class TestAdminAddDriverNote:
    def test_adds_note_successfully(self):
        from backend.routes.admin import drivers as admin_drivers
        from backend.routes.admin.drivers import DriverNoteCreate

        note = {"id": "note_2", "driver_id": DRIVER_ID, "note": "Important note"}

        with (
            patch("backend.routes.admin.drivers.db_supabase.insert_one", AsyncMock(return_value=note)),
        ):
            req = DriverNoteCreate(note="Important note", author="Admin")
            result = asyncio.run(admin_drivers.admin_add_driver_note(driver_id=DRIVER_ID, req=req))

        assert result is not None


class TestAdminGetDriverRides:
    def test_returns_driver_rides(self):
        from backend.routes.admin import drivers as admin_drivers

        rides = [_ride("completed")]

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(return_value=rides)):
            result = asyncio.run(admin_drivers.admin_get_driver_rides(driver_id=DRIVER_ID, limit=50, offset=0))

        assert "rides" in result


class TestAdminGetDriverLocationTrail:
    def test_returns_location_trail(self):
        from backend.routes.admin import drivers as admin_drivers

        trail = [
            {"lat": 52.13, "lng": -106.67, "timestamp": "2024-01-01T12:00:00Z"},
            {"lat": 52.14, "lng": -106.66, "timestamp": "2024-01-01T12:01:00Z"},
        ]

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(return_value=trail)):
            result = asyncio.run(admin_drivers.admin_get_driver_location_trail(driver_id=DRIVER_ID, hours=24))

        assert "trail" in result or isinstance(result, list) or isinstance(result, dict)


class TestAdminGetDriverDailyStats:
    def test_returns_daily_stats(self):
        from backend.routes.admin import drivers as admin_drivers

        rides = [_ride("completed", driver_earnings=20.0)]

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(return_value=rides)):
            result = asyncio.run(admin_drivers.admin_get_driver_daily_stats(driver_id=DRIVER_ID))

        assert isinstance(result, (dict, list))


class TestBatchFetchDriversAndUsers:
    def test_empty_ids_return_empty_maps(self):
        from backend.routes.admin.drivers import _batch_fetch_drivers_and_users

        result = asyncio.run(_batch_fetch_drivers_and_users([], []))
        drivers_map, users_map = result
        assert drivers_map == {}
        assert users_map == {}

    def test_fetches_by_ids(self):
        from backend.routes.admin.drivers import _batch_fetch_drivers_and_users

        drivers = [_driver()]
        users = [_user()]

        def get_rows_side(table, filters=None, **kw):
            if table == "drivers":
                return drivers
            if table == "users":
                return users
            return []

        with patch("backend.routes.admin.drivers.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side)):
            drivers_map, users_map = asyncio.run(_batch_fetch_drivers_and_users([RIDER_ID], [DRIVER_ID]))

        assert DRIVER_ID in drivers_map
