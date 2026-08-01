"""Coverage tests for backend/routes/admin/subscriptions.py.

Spinr Pass driver-subscription plan management is money-adjacent (indirectly
touches Stripe checkout/billing), so plan CRUD and the payment-history/stats
endpoints (which surface money figures) are prioritized. This is a TEST-ONLY
change — no application code in routes/admin/subscriptions.py is modified.

House style follows the sibling `test_admin_*_coverage.py` files in this
directory: an `admin_client` fixture overriding `get_admin_user`, and
`patch.object(<module>.db_supabase, "<fn>", AsyncMock(...))` to stub the
generic CRUD helpers at the point they're imported into the module under
test (per CLAUDE.md's patch-target guidance for db_supabase re-exports).

Money-value fixtures use Decimal-based amounts (str(Decimal(...))) rather
than raw floats, per CLAUDE.md's Decimal-only convention, even though these
are just JSON-serializable fixture dicts crossing the API boundary.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from backend.routes.admin import subscriptions as subs_mod

_ADMIN = {
    "id": "admin-1",
    "role": "super_admin",
    "email": "admin@spinr.app",
    "modules": ["earnings"],
}


@pytest.fixture
def admin_client(test_client):
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: dict(_ADMIN)
    yield test_client
    app.dependency_overrides.pop(get_admin_user, None)


def _dbp(**overrides):
    """Patch db_supabase functions on the module under test."""
    return patch.multiple(
        subs_mod.db_supabase, **{k: AsyncMock(**v) if isinstance(v, dict) else v for k, v in overrides.items()}
    )


# ── list_subscription_plans ────────────────────────────────────────────


class TestListSubscriptionPlans:
    @pytest.mark.asyncio
    async def test_returns_rows(self, admin_client):
        rows = [{"id": "p1", "name": "Basic", "price": float(Decimal("19.99"))}]
        with patch.object(subs_mod.db_supabase, "get_rows", AsyncMock(return_value=rows)):
            resp = admin_client.get("/api/admin/subscription-plans")
        assert resp.status_code == 200
        assert resp.json() == rows


# ── create_subscription_plan ───────────────────────────────────────────


class TestCreateSubscriptionPlan:
    def test_creates_plan_and_logs(self, admin_client):
        payload = {
            "name": "Pro",
            "price": float(Decimal("49.99")),
            "duration_days": 30,
            "rides_per_day": -1,
            "description": "Pro tier",
            "features": ["Priority support"],
            "is_active": True,
        }
        with (
            patch.object(subs_mod.db_supabase, "insert_one", AsyncMock(return_value=None)),
            patch.object(subs_mod, "log_admin_action", AsyncMock(return_value=None)) as mock_log,
        ):
            resp = admin_client.post("/api/admin/subscription-plans", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Pro"
        assert body["price"] == payload["price"]
        assert "id" in body and "created_at" in body
        mock_log.assert_awaited_once()

    def test_defaults_optional_fields(self, admin_client):
        payload = {"name": "Basic", "price": float(Decimal("9.99"))}
        with (
            patch.object(subs_mod.db_supabase, "insert_one", AsyncMock(return_value=None)),
            patch.object(subs_mod, "log_admin_action", AsyncMock(return_value=None)),
        ):
            resp = admin_client.post("/api/admin/subscription-plans", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["description"] == ""
        assert body["features"] == []
        assert body["duration_days"] == 30
        assert body["rides_per_day"] == -1


# ── update_subscription_plan ───────────────────────────────────────────


class TestUpdateSubscriptionPlan:
    def test_updates_provided_fields_only(self, admin_client):
        with (
            patch.object(subs_mod.db_supabase, "update_one", AsyncMock(return_value=None)) as mock_update,
            patch.object(subs_mod, "log_admin_action", AsyncMock(return_value=None)) as mock_log,
        ):
            resp = admin_client.put("/api/admin/subscription-plans/p1", json={"price": float(Decimal("59.99"))})
        assert resp.status_code == 200
        assert resp.json() == {"success": True}
        mock_update.assert_awaited_once()
        call_args = mock_update.call_args
        assert call_args.args[0] == "subscription_plans"
        assert call_args.args[1] == {"id": "p1"}
        assert call_args.args[2]["price"] == float(Decimal("59.99"))
        assert "updated_at" in call_args.args[2]
        mock_log.assert_awaited_once()

    def test_no_fields_provided_skips_db_write(self, admin_client):
        """Every field is None -> updates dict is empty -> no DB call, no audit log."""
        with (
            patch.object(subs_mod.db_supabase, "update_one", AsyncMock(return_value=None)) as mock_update,
            patch.object(subs_mod, "log_admin_action", AsyncMock(return_value=None)) as mock_log,
        ):
            resp = admin_client.put("/api/admin/subscription-plans/p1", json={})
        assert resp.status_code == 200
        assert resp.json() == {"success": True}
        mock_update.assert_not_awaited()
        mock_log.assert_not_awaited()


# ── delete_subscription_plan ───────────────────────────────────────────


class TestDeleteSubscriptionPlan:
    def test_deletes_and_logs(self, admin_client):
        with (
            patch.object(subs_mod.db_supabase, "delete_many", AsyncMock(return_value=None)) as mock_delete,
            patch.object(subs_mod, "log_admin_action", AsyncMock(return_value=None)) as mock_log,
        ):
            resp = admin_client.delete("/api/admin/subscription-plans/p1")
        assert resp.status_code == 200
        assert resp.json() == {"success": True}
        mock_delete.assert_awaited_once_with("subscription_plans", {"id": "p1"})
        mock_log.assert_awaited_once()


# ── list_driver_subscriptions ──────────────────────────────────────────


class TestListDriverSubscriptions:
    def test_returns_all_when_no_status_filter(self, admin_client):
        rows = [{"id": "s1", "status": "active"}, {"id": "s2", "status": "expired"}]
        with patch.object(subs_mod.db_supabase, "get_rows", AsyncMock(return_value=rows)):
            resp = admin_client.get("/api/admin/driver-subscriptions")
        assert resp.status_code == 200
        assert resp.json() == rows

    def test_filters_by_status(self, admin_client):
        rows = [{"id": "s1", "status": "active"}, {"id": "s2", "status": "expired"}]
        with patch.object(subs_mod.db_supabase, "get_rows", AsyncMock(return_value=rows)):
            resp = admin_client.get("/api/admin/driver-subscriptions", params={"status": "active"})
        assert resp.status_code == 200
        assert resp.json() == [{"id": "s1", "status": "active"}]


# ── admin_get_subscription_stats ───────────────────────────────────────


class TestSubscriptionStats:
    @pytest.mark.asyncio
    async def test_default_range_happy_path(self, admin_client):
        sub_id_ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        subs = [
            {
                "driver_id": "d1",
                "status": "active",
                "payment_status": "paid",
                "plan_id": "p1",
                "price": float(Decimal("19.99")),
                "created_at": sub_id_ts,
            },
            {
                "driver_id": "d2",
                "status": "expired",
                "payment_status": "paid",
                "plan_id": "p1",
                "price": float(Decimal("19.99")),
                "created_at": sub_id_ts,
            },
            {
                "driver_id": "d3",
                "status": "cancelled",
                "payment_status": "paid",
                "plan_id": "p2",
                "price": float(Decimal("9.99")),
                "created_at": sub_id_ts,
            },
            # pending/superseded checkout row with no realized subscriber
            {
                "driver_id": "d4",
                "status": "pending",
                "payment_status": "pending",
                "plan_id": "p2",
                "price": float(Decimal("9.99")),
                "created_at": sub_id_ts,
            },
        ]
        payments = [
            {
                "id": "pay1",
                "driver_id": "d1",
                "plan_id": "p1",
                "plan_name": "Basic",
                "amount": float(Decimal("19.99")),
                "billing_reason": "subscription_create",
                "created_at": sub_id_ts,
            }
        ]
        plans = [{"id": "p1", "name": "Basic"}, {"id": "p2", "name": "Pro"}]
        service_areas = [
            {"id": "a1", "name": "Regina"},
            {"id": "a2", "name": "Sub-area", "parent_service_area_id": "a1"},
        ]

        async def _get_rows(table, *args, **kwargs):
            if table == "driver_subscriptions":
                return subs
            if table == "subscription_plans":
                return plans
            if table == "subscription_payments":
                return payments
            if table == "service_areas":
                return service_areas
            return []

        with (
            patch.object(subs_mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)),
            patch.object(subs_mod, "_batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
        ):
            resp = admin_client.get("/api/admin/subscription-stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stats"]["total_subscribers"] == 3  # pending row excluded
        assert body["stats"]["active"] == 1
        assert body["stats"]["expired"] == 1
        assert body["stats"]["cancelled"] == 1
        assert body["stats"]["total_revenue"] == pytest.approx(19.99)
        assert len(body["plan_breakdown"]) == 2
        assert body["service_areas"] == [{"id": "a1", "name": "Regina"}]  # sub-area excluded

    @pytest.mark.asyncio
    async def test_explicit_date_range_and_service_area_filter(self, admin_client):
        in_range_ts = "2026-06-15T12:00:00"
        subs = [
            {
                "driver_id": "d1",
                "status": "active",
                "payment_status": "paid",
                "plan_id": "p1",
                "price": float(Decimal("19.99")),
                "created_at": in_range_ts,
            },
        ]
        payments = [
            {
                "id": "pay1",
                "driver_id": "d1",
                "plan_id": "p1",
                "plan_name": "Basic",
                "amount": float(Decimal("19.99")),
                "billing_reason": "subscription_create",
                "created_at": in_range_ts,
            }
        ]

        async def _get_rows(table, *args, **kwargs):
            if table == "driver_subscriptions":
                return subs
            if table == "subscription_plans":
                return [{"id": "p1", "name": "Basic"}]
            if table == "subscription_payments":
                return payments
            if table == "service_areas":
                return []
            return []

        with (
            patch.object(subs_mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)),
            patch.object(
                subs_mod,
                "_batch_fetch_drivers_and_users",
                AsyncMock(
                    return_value=({"d1": {"user_id": "u1", "service_area_id": "area-1"}}, {"u1": {"name": "Alex"}})
                ),
            ),
        ):
            resp = admin_client.get(
                "/api/admin/subscription-stats",
                params={"start_date": "2026-06-01", "end_date": "2026-06-30Z", "service_area_ids": "area-1,area-2"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["stats"]["total_subscribers"] == 1
        assert body["transactions"][0]["driver_id"] == "d1"

    @pytest.mark.asyncio
    async def test_service_area_filter_excludes_non_matching_driver(self, admin_client):
        subs = [
            {
                "driver_id": "d1",
                "status": "active",
                "payment_status": "paid",
                "plan_id": "p1",
                "price": float(Decimal("19.99")),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ]

        async def _get_rows(table, *args, **kwargs):
            if table == "driver_subscriptions":
                return subs
            if table == "subscription_plans":
                return []
            if table == "subscription_payments":
                return []
            if table == "service_areas":
                return []
            return []

        with (
            patch.object(subs_mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)),
            patch.object(
                subs_mod,
                "_batch_fetch_drivers_and_users",
                AsyncMock(
                    return_value=({"d1": {"user_id": "u1", "service_area_id": "area-999"}}, {"u1": {"name": "Alex"}})
                ),
            ),
        ):
            resp = admin_client.get("/api/admin/subscription-stats", params={"service_area_ids": "area-1"})
        assert resp.status_code == 200
        assert resp.json()["stats"]["total_subscribers"] == 0


# ── admin_list_subscription_payments ───────────────────────────────────


class TestListSubscriptionPayments:
    def test_no_date_range_uses_count_documents_pagination(self, admin_client):
        rows = [
            {
                "id": "pay1",
                "driver_id": "d1",
                "plan_id": "p1",
                "plan_name": "Basic",
                "amount": float(Decimal("19.99")),
                "subtotal": float(Decimal("18.00")),
                "gst_amount": float(Decimal("0.90")),
                "pst_amount": float(Decimal("1.09")),
                "hst_amount": 0.0,
                "province": "SK",
                "currency": "cad",
                "billing_reason": "subscription_create",
                "stripe_invoice_id": "in_123",
                "created_at": "2026-06-15T12:00:00",
            }
        ]
        with (
            patch.object(subs_mod.db_supabase, "get_rows", AsyncMock(return_value=rows)),
            patch.object(subs_mod.db_supabase, "count_documents", AsyncMock(return_value=1)),
            patch.object(subs_mod, "_batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
        ):
            resp = admin_client.get("/api/admin/subscription/payments")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["has_more"] is False
        assert body["payments"][0]["subtotal"] == 18.0
        assert body["payments"][0]["currency"] == "CAD"

    def test_legacy_row_without_tax_breakdown_zeroes_tax(self, admin_client):
        rows = [
            {
                "id": "pay2",
                "driver_id": "d2",
                "plan_id": "p1",
                "plan_name": "Basic",
                "amount": float(Decimal("19.99")),
                # no "subtotal" key -> legacy pre-migration-186 row
                "province": None,
                "currency": None,
                "billing_reason": "subscription_create",
                "created_at": "2026-06-15T12:00:00",
            }
        ]
        with (
            patch.object(subs_mod.db_supabase, "get_rows", AsyncMock(return_value=rows)),
            patch.object(subs_mod.db_supabase, "count_documents", AsyncMock(return_value=1)),
            patch.object(subs_mod, "_batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
        ):
            resp = admin_client.get("/api/admin/subscription/payments")
        assert resp.status_code == 200
        row = resp.json()["payments"][0]
        assert row["subtotal"] == row["amount"]
        assert row["gst_amount"] == 0.0
        assert row["pst_amount"] == 0.0
        assert row["hst_amount"] == 0.0
        assert row["province"] == "SK"
        assert row["currency"] == "CAD"

    def test_date_range_filters_and_recomputes_total(self, admin_client):
        in_range = {
            "id": "pay-in",
            "driver_id": "d1",
            "plan_id": "p1",
            "plan_name": "Basic",
            "amount": float(Decimal("19.99")),
            "billing_reason": "subscription_create",
            "created_at": "2026-06-15T12:00:00",
        }
        out_of_range = {
            "id": "pay-out",
            "driver_id": "d1",
            "plan_id": "p1",
            "plan_name": "Basic",
            "amount": float(Decimal("19.99")),
            "billing_reason": "subscription_create",
            "created_at": "2026-01-01T12:00:00",
        }
        no_date = {
            "id": "pay-nodate",
            "driver_id": "d1",
            "plan_id": "p1",
            "plan_name": "Basic",
            "amount": float(Decimal("19.99")),
            "billing_reason": "subscription_create",
            "created_at": None,
        }
        with (
            patch.object(subs_mod.db_supabase, "get_rows", AsyncMock(return_value=[in_range, out_of_range, no_date])),
            patch.object(subs_mod, "_batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
        ):
            resp = admin_client.get(
                "/api/admin/subscription/payments",
                params={"start_date": "2026-06-01", "end_date": "2026-06-30"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["payments"][0]["id"] == "pay-in"

    def test_all_filters_forwarded(self, admin_client):
        with (
            patch.object(subs_mod.db_supabase, "get_rows", AsyncMock(return_value=[])) as mock_get_rows,
            patch.object(subs_mod.db_supabase, "count_documents", AsyncMock(return_value=0)),
            patch.object(subs_mod, "_batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
        ):
            resp = admin_client.get(
                "/api/admin/subscription/payments",
                params={"driver_id": "d1", "plan_id": "p1", "billing_reason": "subscription_create"},
            )
        assert resp.status_code == 200
        call_kwargs = mock_get_rows.call_args
        filters_used = call_kwargs.args[1]
        assert filters_used == {"driver_id": "d1", "plan_id": "p1", "billing_reason": "subscription_create"}


# ── update_subscription_tax_config ─────────────────────────────────────


class TestUpdateSubscriptionTaxConfig:
    def test_area_not_found_returns_404(self, admin_client):
        with patch.object(subs_mod.db_supabase, "find_one", AsyncMock(return_value=None)):
            resp = admin_client.put(
                "/api/admin/service-areas/area-missing/subscription-tax",
                json={"enabled": True, "province": "SK", "gst_rate": 5.0, "pst_rate": 6.0, "hst_rate": 0.0},
            )
        assert resp.status_code == 404

    def test_success_updates_and_logs(self, admin_client):
        area = {"id": "area-1", "name": "Regina"}
        with (
            patch.object(subs_mod.db_supabase, "find_one", AsyncMock(return_value=area)),
            patch.object(subs_mod.db_supabase, "update_one", AsyncMock(return_value=None)) as mock_update,
            patch.object(subs_mod, "log_admin_action", AsyncMock(return_value=None)) as mock_log,
        ):
            resp = admin_client.put(
                "/api/admin/service-areas/area-1/subscription-tax",
                json={"enabled": True, "province": "SK", "gst_rate": 5.0, "pst_rate": 6.0, "hst_rate": 0.0},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["subscription_tax_config"]["province"] == "SK"
        mock_update.assert_awaited_once()
        mock_log.assert_awaited_once()


# ── offer_analytics ─────────────────────────────────────────────────────


class TestOfferAnalytics:
    @pytest.mark.asyncio
    async def test_no_offers_in_window_returns_empty_shape(self, admin_client):
        with patch.object(subs_mod.db_supabase, "get_rows", AsyncMock(return_value=[])):
            resp = admin_client.get("/api/admin/offer-analytics")
        assert resp.status_code == 200
        body = resp.json()
        assert body["areas"] == []
        assert body["totals"]["total_offers"] == 0

    @pytest.mark.asyncio
    async def test_invalid_date_strings_fall_back_to_defaults(self, admin_client):
        with patch.object(subs_mod.db_supabase, "get_rows", AsyncMock(return_value=[])):
            resp = admin_client.get(
                "/api/admin/offer-analytics", params={"start_date": "not-a-date", "end_date": "also-bad"}
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_date_only_end_date_extends_to_end_of_day(self, admin_client):
        offers = [
            {
                "ride_id": "r1",
                "status": "accepted",
                "offered_at": "2026-06-15T10:00:00+00:00",
                "responded_at": "2026-06-15T10:00:10+00:00",
            }
        ]
        rides = [{"id": "r1", "service_area_id": "area-1"}]
        areas = [{"id": "area-1", "name": "Regina"}]

        async def _get_rows(table, *args, **kwargs):
            if table == "ride_offers":
                return offers
            if table == "rides":
                return rides
            if table == "service_areas":
                return areas
            return []

        with patch.object(subs_mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)):
            resp = admin_client.get(
                "/api/admin/offer-analytics", params={"start_date": "2026-06-01", "end_date": "2026-06-15"}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["totals"]["total_offers"] == 1
        assert body["totals"]["accepted"] == 1
        assert body["totals"]["acceptance_rate"] == 1.0
        assert body["areas"][0]["service_area_name"] == "Regina"

    @pytest.mark.asyncio
    async def test_single_service_area_filter_narrows_totals(self, admin_client):
        offers = [
            {"ride_id": "r1", "status": "accepted", "offered_at": "2026-06-15T10:00:00+00:00"},
            {"ride_id": "r2", "status": "declined", "offered_at": "2026-06-15T11:00:00+00:00"},
        ]
        rides = [{"id": "r1", "service_area_id": "area-1"}, {"id": "r2", "service_area_id": "area-2"}]
        areas = [{"id": "area-1", "name": "Regina"}, {"id": "area-2", "name": "Saskatoon"}]

        async def _get_rows(table, *args, **kwargs):
            if table == "ride_offers":
                return offers
            if table == "rides":
                return rides
            if table == "service_areas":
                return areas
            return []

        with patch.object(subs_mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)):
            resp = admin_client.get("/api/admin/offer-analytics", params={"service_area_id": "area-1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["totals"]["total_offers"] == 1
        assert len(body["areas"]) == 1
        assert body["areas"][0]["service_area_id"] == "area-1"

    @pytest.mark.asyncio
    async def test_pagination_hard_cap_sets_truncated_flag(self, admin_client):
        """First page returns a full 5,000-row page repeatedly until the 200k
        hard cap trips, so the loop must stop and mark the result truncated
        rather than paging forever."""
        full_page = [
            {"ride_id": f"r{i}", "status": "accepted", "offered_at": "2026-06-15T10:00:00+00:00"} for i in range(5000)
        ]

        call_count = {"n": 0}

        async def _get_rows(table, *args, **kwargs):
            if table == "ride_offers":
                call_count["n"] += 1
                return full_page
            if table == "rides":
                return []
            if table == "service_areas":
                return []
            return []

        with patch.object(subs_mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)):
            resp = admin_client.get("/api/admin/offer-analytics")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("truncated") is True
        assert "warning" in body
        # 200_000 / 5_000 = 40 pages before the cap trips
        assert call_count["n"] == 40


class TestParseTs:
    def test_invalid_timestamp_falls_back_to_min(self):
        result = subs_mod._parse_ts("not-a-timestamp")
        assert result == datetime.min.replace(tzinfo=timezone.utc)

    def test_naive_timestamp_gets_utc_attached(self):
        result = subs_mod._parse_ts("2026-06-15T10:00:00")
        assert result.tzinfo is not None


# ── admin_download_subscription_invoice ────────────────────────────────


class TestDownloadSubscriptionInvoice:
    def test_payment_not_found_returns_404(self, admin_client):
        with patch.object(subs_mod.db_supabase, "find_one", AsyncMock(return_value=None)):
            resp = admin_client.get("/api/admin/subscription/payments/missing/invoice.pdf")
        assert resp.status_code == 404

    def test_pdf_build_failure_returns_404(self, admin_client):
        payment = {"id": "pay1", "driver_id": "d1", "amount": float(Decimal("19.99"))}
        with (
            patch.object(subs_mod.db_supabase, "find_one", AsyncMock(return_value=payment)),
            patch("backend.utils.subscription_invoice.build_subscription_invoice_pdf", AsyncMock(return_value=None)),
        ):
            resp = admin_client.get("/api/admin/subscription/payments/pay1/invoice.pdf")
        assert resp.status_code == 404

    def test_success_returns_pdf_and_logs(self, admin_client):
        payment = {"id": "pay1", "driver_id": "d1", "amount": float(Decimal("19.99"))}
        with (
            patch.object(subs_mod.db_supabase, "find_one", AsyncMock(return_value=payment)),
            patch(
                "backend.utils.subscription_invoice.build_subscription_invoice_pdf",
                AsyncMock(return_value=(b"%PDF-1.4 fake", "invoice-pay1.pdf")),
            ),
            patch.object(subs_mod, "log_admin_action", AsyncMock(return_value=None)) as mock_log,
        ):
            resp = admin_client.get("/api/admin/subscription/payments/pay1/invoice.pdf")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert "invoice-pay1.pdf" in resp.headers["content-disposition"]
        mock_log.assert_awaited_once()


# ── admin_resend_subscription_invoice ──────────────────────────────────


class TestResendSubscriptionInvoice:
    def test_payment_not_found_returns_404(self, admin_client):
        with patch.object(subs_mod.db_supabase, "find_one", AsyncMock(return_value=None)):
            resp = admin_client.post("/api/admin/subscription/payments/missing/resend-invoice")
        assert resp.status_code == 404

    def test_kwargs_build_failure_returns_404(self, admin_client):
        payment = {"id": "pay1", "driver_id": "d1"}
        with (
            patch.object(subs_mod.db_supabase, "find_one", AsyncMock(return_value=payment)),
            patch("backend.utils.subscription_invoice.build_invoice_email_kwargs", AsyncMock(return_value=None)),
        ):
            resp = admin_client.post("/api/admin/subscription/payments/pay1/resend-invoice")
        assert resp.status_code == 404

    def test_cooldown_active_returns_429(self, admin_client):
        payment = {"id": "pay1", "driver_id": "d1"}
        with (
            patch.object(subs_mod.db_supabase, "find_one", AsyncMock(return_value=payment)),
            patch(
                "backend.utils.subscription_invoice.build_invoice_email_kwargs",
                AsyncMock(return_value={"to": "d@x.com"}),
            ),
            patch("backend.utils.redis_client.redis_set_nx", AsyncMock(return_value=False)),
        ):
            resp = admin_client.post("/api/admin/subscription/payments/pay1/resend-invoice")
        assert resp.status_code == 429

    def test_send_failure_clears_cooldown_and_returns_502(self, admin_client):
        payment = {"id": "pay1", "driver_id": "d1"}
        with (
            patch.object(subs_mod.db_supabase, "find_one", AsyncMock(return_value=payment)),
            patch(
                "backend.utils.subscription_invoice.build_invoice_email_kwargs",
                AsyncMock(return_value={"to": "d@x.com"}),
            ),
            patch("backend.utils.redis_client.redis_set_nx", AsyncMock(return_value=True)),
            patch("backend.utils.redis_client.redis_delete", AsyncMock(return_value=None)) as mock_del,
            patch(
                "backend.routes.drivers.subscriptions._send_subscription_invoice_email", AsyncMock(return_value=False)
            ),
        ):
            resp = admin_client.post("/api/admin/subscription/payments/pay1/resend-invoice")
        assert resp.status_code == 502
        mock_del.assert_awaited_once()

    def test_success_sends_and_logs(self, admin_client):
        payment = {"id": "pay1", "driver_id": "d1"}
        with (
            patch.object(subs_mod.db_supabase, "find_one", AsyncMock(return_value=payment)),
            patch(
                "backend.utils.subscription_invoice.build_invoice_email_kwargs",
                AsyncMock(return_value={"to": "d@x.com"}),
            ),
            patch("backend.utils.redis_client.redis_set_nx", AsyncMock(return_value=True)),
            patch(
                "backend.routes.drivers.subscriptions._send_subscription_invoice_email", AsyncMock(return_value=True)
            ),
            patch.object(subs_mod, "log_admin_action", AsyncMock(return_value=None)) as mock_log,
        ):
            resp = admin_client.post("/api/admin/subscription/payments/pay1/resend-invoice")
        assert resp.status_code == 200
        assert resp.json() == {"success": True}
        mock_log.assert_awaited_once()
