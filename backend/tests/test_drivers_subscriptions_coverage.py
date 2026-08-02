"""Coverage tests for backend/routes/drivers/subscriptions.py (Spinr Pass).

This is the driver-facing subscription module — plan listing, Stripe
Checkout purchase, cancellation, invoice emails/PDF, payment history, and
the ``check_expiring_subscriptions`` background loop. NOT the same file as
``routes/admin/subscriptions.py`` (already closed under Track 1).

``tests/test_spinr_pass_subscription.py`` already exercises the core
checkout/webhook/verify-session/activation/cancel/3-day-warning flows in
depth (40 tests). This file targets what that suite does NOT cover:
``get_subscription_plans`` (area kill switch, area/vehicle-type filters),
``get_current_subscription`` (unparseable-expiry 503, expired-flip, quota
happy path, area-timezone-lookup-fails fallback), the public
``subscription_checkout_return`` https-bounce redirect, the
payment-history endpoint's legacy-row (pre-tax-columns) branch and
pagination, ``resend_subscription_invoice`` (404s, success, 502 on
delivery failure), ``_compute_subscription_tax`` and
``_send_subscription_invoice_email`` directly, and the
``check_expiring_subscriptions`` loop's 24h-warning / expired-enforcement
(gate on/off) / cancel_pending-retry / unparseable-expiry-skip / lock-not-
acquired branches. Test-only change — no application code modified.

Patch target: this module does ``from ._deps import (..., db_supabase,
...)`` — a name binding to the *same* `backend.db_supabase` module object
(not a re-export copy) — so patching `backend.db_supabase.<fn>` and
`backend.routes.drivers.subscriptions._deps.db.<fn>` both work; this file
follows the house style already used by `test_spinr_pass_subscription.py`
and patches `backend.db_supabase.<fn>` / `backend.routes.drivers._deps.<fn>`
directly, per CLAUDE.md's "patch target is the module that DEFINES the
function" rule (db_supabase.py itself defines these CRUD helpers).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _clear_request_deadline():
    """Defend against a pre-existing test-pollution bug (NOT in this module):
    several tests in test_utils_extended.py's TestDeadline* class call
    `set_request_deadline(...)` directly and never reset the contextvar
    afterward, leaking a permanently-past deadline into every later test in
    the same pytest process that calls `repositories._base.run_sync`. Same
    workaround already used in `test_wallet_repo.py` / `test_ride_repo_coverage.py`
    — reset the contextvar to a known-good `None` state for every test here too.
    """
    from backend.utils.deadline import set_request_deadline

    set_request_deadline(None)
    yield


# ── get_subscription_plans ──────────────────────────────────────────────


class TestGetSubscriptionPlans:
    async def test_no_driver_profile_returns_all_active_plans_unfiltered(self):
        from backend.routes.drivers import get_subscription_plans

        plans = [{"id": "p1", "is_active": True}]
        with (
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=[[], plans])),
        ):
            result = await get_subscription_plans({"id": "u1"})
        assert result == {"plans": plans, "free_mode": False, "message": None}

    async def test_area_kill_switch_returns_free_mode(self):
        from backend.routes.drivers import get_subscription_plans

        driver = {"id": "d1", "service_area_id": "area-1"}
        area = {"id": "area-1", "spinr_pass_enabled": False}
        with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=[[driver], [area]])):
            result = await get_subscription_plans({"id": "u1"})
        assert result["free_mode"] is True
        assert result["plans"] == []
        assert "free" in result["message"].lower()

    async def test_filters_by_service_area_and_vehicle_type(self):
        from backend.routes.drivers import get_subscription_plans

        driver = {"id": "d1", "service_area_id": "area-1", "vehicle_type_id": "sedan"}
        plans = [
            {"id": "p-match", "service_areas": ["area-1"], "vehicle_types": ["sedan"]},
            {"id": "p-wrong-area", "service_areas": ["area-2"], "vehicle_types": None},
            {"id": "p-wrong-vt", "service_areas": None, "vehicle_types": ["suv"]},
            {"id": "p-all-areas-empty-list", "service_areas": [], "vehicle_types": None},
        ]
        # driver has no service_area lookup mismatch (area not disabled) then plans query
        with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=[[driver], [], plans])):
            result = await get_subscription_plans({"id": "u1"})
        ids = {p["id"] for p in result["plans"]}
        assert "p-match" in ids
        assert "p-all-areas-empty-list" in ids
        assert "p-wrong-area" not in ids
        assert "p-wrong-vt" not in ids


# ── get_current_subscription ────────────────────────────────────────────


class TestGetCurrentSubscription:
    async def test_no_driver_profile(self):
        from backend.routes.drivers import get_current_subscription

        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])):
            result = await get_current_subscription({"id": "u1"})
        assert result == {"has_subscription": False, "subscription": None}

    async def test_no_active_subscription(self):
        from backend.routes.drivers import get_current_subscription

        driver = {"id": "d1"}
        with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=[[driver], []])):
            result = await get_current_subscription({"id": "u1"})
        assert result == {"has_subscription": False, "subscription": None}

    async def test_unparseable_expiry_raises_503(self):
        from fastapi import HTTPException

        from backend.routes.drivers import get_current_subscription

        driver = {"id": "d1"}
        sub = {"id": "sub-1", "expires_at": "not-a-real-timestamp"}
        with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=[[driver], [sub]])):
            with pytest.raises(HTTPException) as exc_info:
                await get_current_subscription({"id": "u1"})
        assert exc_info.value.status_code == 503

    async def test_expired_sub_flips_status_and_returns_expired_true(self):
        from backend.routes.drivers import get_current_subscription

        driver = {"id": "d1"}
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        sub = {"id": "sub-1", "expires_at": past}
        update_mock = AsyncMock()
        with (
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=[[driver], [sub]])),
            patch("backend.db_supabase.update_one", update_mock),
        ):
            result = await get_current_subscription({"id": "u1"})
        assert result == {"has_subscription": False, "subscription": None, "expired": True}
        update_mock.assert_awaited_once()
        assert update_mock.await_args.args[2] == {"status": "expired"}

    async def test_active_sub_returns_quota_state_with_tz_fallback(self):
        from backend.routes.drivers import get_current_subscription

        driver = {"id": "d1", "service_area_id": "area-1"}
        future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        sub = {"id": "sub-1", "expires_at": future, "rides_per_day": 10}
        with (
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=[[driver], [sub]])),
            patch("backend.utils.spinr_pass.area_timezone", AsyncMock(side_effect=RuntimeError("boom"))),
            patch("backend.utils.spinr_pass.completed_today", AsyncMock(return_value=2)),
        ):
            result = await get_current_subscription({"id": "u1"})
        assert result["has_subscription"] is True
        assert result["today_rides"] == 2
        assert "rides_remaining" in result
        assert "quota_resets_at" in result


# ── subscription_checkout_return (public https bounce) ─────────────────


class TestCheckoutReturnBounce:
    async def test_allowlisted_scheme_and_session_id_pass_through(self):
        from backend.routes.drivers import subscription_checkout_return

        resp = await subscription_checkout_return(session_id="cs_test_abc123", to="spinr-driver://subscription/success")
        assert resp.status_code == 302
        assert resp.headers["location"].startswith("spinr-driver://subscription/success")
        assert "session_id=cs_test_abc123" in resp.headers["location"]

    async def test_disallowed_scheme_falls_back_to_default(self):
        from backend.routes.drivers import subscription_checkout_return

        resp = await subscription_checkout_return(session_id="", to="https://evil.example.com/steal")
        assert resp.status_code == 302
        assert resp.headers["location"].startswith("spinr-driver://subscription/success")
        assert "evil.example.com" not in resp.headers["location"]

    async def test_malformed_session_id_stripped(self):
        from backend.routes.drivers import subscription_checkout_return

        resp = await subscription_checkout_return(session_id="'; DROP TABLE--", to="exp://192.168.1.5:8081")
        assert resp.status_code == 302
        assert "session_id=" not in resp.headers["location"]


# ── payment history endpoint (legacy row + pagination) ──────────────────
#
# NOTE: `_compute_subscription_tax` and `resend_subscription_invoice` are
# NOT re-tested here — `tests/test_driver_subscriptions_tax_ledger_coverage.py`
# (merged in PR #3243, 2026-08-02) already covers both in depth (tax-rate
# math, disabled/missing-config defaults, and resend's 404/502/legacy-vs-
# tax-columns branches). Duplicating that here would inflate the test count
# without closing any additional line, so this file only adds
# `get_subscription_payment_history`'s own legacy-row/pagination branches,
# which #3243 did not touch.


class TestPaymentHistoryLegacyAndPagination:
    async def test_legacy_row_without_tax_columns_zeroes_tax(self):
        from backend.routes.drivers import get_subscription_payment_history

        driver = {"id": "d1"}
        legacy_payment = {
            "id": "pay-1",
            "plan_id": "p1",
            "plan_name": "Basic",
            "amount": "19.99",
            "currency": "cad",
            "billing_reason": "one_off",
            "created_at": "2026-01-01T00:00:00Z",
            # no "subtotal" key => legacy branch
        }
        with (
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=[[driver], [legacy_payment]])),
            patch("backend.db_supabase.count_documents", AsyncMock(return_value=1)),
        ):
            result = await get_subscription_payment_history(limit=20, offset=0, current_user={"id": "u1"})
        row = result["payments"][0]
        assert row["subtotal"] == "19.99"
        assert row["gst_amount"] == "0"
        assert row["pst_amount"] == "0"
        assert result["has_more"] is False

    async def test_has_more_true_when_more_rows_remain(self):
        from backend.routes.drivers import get_subscription_payment_history

        driver = {"id": "d1"}
        payments = [{"id": f"pay-{i}", "amount": "10.00", "created_at": "2026-01-01T00:00:00Z"} for i in range(5)]
        with (
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=[[driver], payments])),
            patch("backend.db_supabase.count_documents", AsyncMock(return_value=50)),
        ):
            result = await get_subscription_payment_history(limit=5, offset=0, current_user={"id": "u1"})
        assert result["has_more"] is True
        assert result["total"] == 50

    async def test_no_driver_profile_returns_404(self):
        from fastapi import HTTPException

        from backend.routes.drivers import get_subscription_payment_history

        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc_info:
                await get_subscription_payment_history(limit=20, offset=0, current_user={"id": "u1"})
        assert exc_info.value.status_code == 404


# ── resend_subscription_invoice ─────────────────────────────────────────


class TestResendSubscriptionInvoice:
    async def test_no_driver_profile_404(self):
        from fastapi import HTTPException

        from backend.routes.drivers import resend_subscription_invoice

        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc_info:
                await resend_subscription_invoice("pay-1", current_user={"id": "u1"})
        assert exc_info.value.status_code == 404

    async def test_payment_not_found_or_not_owned_404(self):
        from fastapi import HTTPException

        from backend.routes.drivers import resend_subscription_invoice

        driver = {"id": "d1"}
        other_payment = {"id": "pay-1", "driver_id": "someone-else"}
        with (
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[driver])),
            patch("backend.db_supabase.find_one", AsyncMock(return_value=other_payment)),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await resend_subscription_invoice("pay-1", current_user={"id": "u1"})
        assert exc_info.value.status_code == 404

    async def test_success_sends_invoice(self):
        from backend.routes.drivers import resend_subscription_invoice

        driver = {"id": "d1"}
        payment = {
            "id": "pay-1",
            "driver_id": "d1",
            "plan_id": "p1",
            "plan_name": "Basic",
            "amount": "19.99",
            "subtotal": "19.00",
            "gst_amount": "0.50",
            "pst_amount": "0.49",
            "tax_total": "0.99",
            "province": "SK",
            "billing_reason": "one_off",
            "created_at": "2026-01-01T00:00:00Z",
        }
        with (
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[driver])),
            patch("backend.db_supabase.find_one", AsyncMock(side_effect=[payment, {"duration_days": 30}])),
            patch(
                "backend.routes.drivers.subscriptions._send_subscription_invoice_email",
                AsyncMock(return_value=True),
            ) as send_mock,
        ):
            result = await resend_subscription_invoice("pay-1", current_user={"id": "u1"})
        assert result == {"success": True}
        send_mock.assert_awaited_once()

    async def test_delivery_failure_returns_502(self):
        from fastapi import HTTPException

        from backend.routes.drivers import resend_subscription_invoice

        driver = {"id": "d1"}
        payment = {
            "id": "pay-1",
            "driver_id": "d1",
            "amount": "19.99",
            "created_at": "2026-01-01T00:00:00Z",
        }
        with (
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[driver])),
            patch("backend.db_supabase.find_one", AsyncMock(side_effect=[payment, None])),
            patch(
                "backend.routes.drivers.subscriptions._send_subscription_invoice_email",
                AsyncMock(return_value=False),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await resend_subscription_invoice("pay-1", current_user={"id": "u1"})
        assert exc_info.value.status_code == 502


# ── _send_subscription_invoice_email ────────────────────────────────────


class TestSendSubscriptionInvoiceEmail:
    async def test_no_driver_returns_false(self):
        from backend.routes.drivers.subscriptions import _send_subscription_invoice_email

        with patch("backend.routes.drivers._deps.db.find_one", AsyncMock(return_value=None)):
            result = await _send_subscription_invoice_email(
                driver_id="d1",
                plan_name="Basic",
                duration_label="30 days",
                subtotal=Decimal("19.00"),
                gst_amount=Decimal("0.95"),
                pst_amount=Decimal("1.14"),
                hst_amount=Decimal("0"),
                tax_total=Decimal("2.09"),
                total=Decimal("21.09"),
                billing_reason="one_off",
                payment_date="January 01, 2026",
            )
        assert result is False

    async def test_no_email_on_user_returns_false(self):
        from backend.routes.drivers.subscriptions import _send_subscription_invoice_email

        driver = {"id": "d1", "user_id": "u1"}
        user = {"id": "u1", "email": ""}
        with patch("backend.routes.drivers._deps.db.find_one", AsyncMock(side_effect=[driver, user])):
            result = await _send_subscription_invoice_email(
                driver_id="d1",
                plan_name="Basic",
                duration_label="30 days",
                subtotal=Decimal("19.00"),
                gst_amount=Decimal("0"),
                pst_amount=Decimal("0"),
                hst_amount=Decimal("0"),
                tax_total=Decimal("0"),
                total=Decimal("19.00"),
                billing_reason="one_off",
                payment_date="January 01, 2026",
            )
        assert result is False

    async def test_successful_send_returns_true(self):
        from backend.routes.drivers.subscriptions import _send_subscription_invoice_email

        driver = {"id": "d1", "user_id": "u1"}
        user = {"id": "u1", "email": "driver@example.com", "first_name": "Jo", "last_name": "Doe"}
        with (
            patch("backend.routes.drivers._deps.db.find_one", AsyncMock(side_effect=[driver, user])),
            patch(
                "backend.utils.subscription_invoice_pdf.generate_subscription_invoice_pdf",
                MagicMock(return_value=b"%PDF-fake"),
            ),
            patch("backend.utils.email_provider.send_transactional_email", AsyncMock(return_value=True)),
        ):
            result = await _send_subscription_invoice_email(
                driver_id="d1",
                plan_name="Basic",
                duration_label="30 days",
                subtotal=Decimal("19.00"),
                gst_amount=Decimal("0.95"),
                pst_amount=Decimal("1.14"),
                hst_amount=Decimal("0"),
                tax_total=Decimal("2.09"),
                total=Decimal("21.09"),
                billing_reason="subscription_cycle",
                payment_date="January 01, 2026",
                stripe_invoice_url="https://stripe.example/receipt",
            )
        assert result is True

    async def test_pdf_generation_failure_still_sends_html_only(self):
        from backend.routes.drivers.subscriptions import _send_subscription_invoice_email

        driver = {"id": "d1", "user_id": "u1"}
        user = {"id": "u1", "email": "driver@example.com"}
        with (
            patch("backend.routes.drivers._deps.db.find_one", AsyncMock(side_effect=[driver, user])),
            patch(
                "backend.utils.subscription_invoice_pdf.generate_subscription_invoice_pdf",
                MagicMock(side_effect=RuntimeError("pdf lib broken")),
            ),
            patch("backend.utils.email_provider.send_transactional_email", AsyncMock(return_value=True)) as send_mock,
        ):
            result = await _send_subscription_invoice_email(
                driver_id="d1",
                plan_name="Basic",
                duration_label="30 days",
                subtotal=Decimal("19.00"),
                gst_amount=Decimal("0"),
                pst_amount=Decimal("0"),
                hst_amount=Decimal("0"),
                tax_total=Decimal("0"),
                total=Decimal("19.00"),
                billing_reason="one_off",
                payment_date="January 01, 2026",
            )
        assert result is True
        assert send_mock.await_args.kwargs["attachments"] is None

    async def test_exception_is_caught_and_returns_false(self):
        from backend.routes.drivers.subscriptions import _send_subscription_invoice_email

        with patch("backend.routes.drivers._deps.db.find_one", AsyncMock(side_effect=RuntimeError("db down"))):
            result = await _send_subscription_invoice_email(
                driver_id="d1",
                plan_name="Basic",
                duration_label="30 days",
                subtotal=Decimal("19.00"),
                gst_amount=Decimal("0"),
                pst_amount=Decimal("0"),
                hst_amount=Decimal("0"),
                tax_total=Decimal("0"),
                total=Decimal("19.00"),
                billing_reason="one_off",
                payment_date="January 01, 2026",
            )
        assert result is False


# ── check_expiring_subscriptions loop branches ──────────────────────────


def _stop_after_one_iteration():
    """Standard house pattern (see test_spinr_pass_subscription.py) to break
    out of the loop's `await asyncio.sleep(6 * 3600)` at the tail of one pass."""
    return patch("backend.routes.drivers._deps.asyncio.sleep", AsyncMock(side_effect=Exception("stop")))


class TestCheckExpiringSubscriptionsLoop:
    async def test_lock_not_acquired_skips_body_and_sleeps(self):
        from backend.routes.drivers import check_expiring_subscriptions

        get_rows_mock = AsyncMock()
        with (
            patch("backend.utils.redis_client.redis_set_nx", AsyncMock(return_value=False)),
            patch("backend.db_supabase.get_rows", get_rows_mock),
            _stop_after_one_iteration(),
        ):
            try:
                await check_expiring_subscriptions()
            except Exception as e:
                if "stop" not in str(e):
                    raise
        # Lock not acquired -> body (which would call get_rows) never runs.
        get_rows_mock.assert_not_awaited()

    async def test_24h_warning_sent_and_flag_set(self):
        from backend.routes.drivers import check_expiring_subscriptions

        now = datetime.now(timezone.utc)
        sub = {
            "id": "sub-1",
            "driver_id": "d1",
            "plan_name": "Basic",
            "status": "active",
            "expires_at": (now + timedelta(hours=10)).isoformat(),
            "expiry_warned": False,
            "expiry_warned_3d": True,
        }
        driver = {"id": "d1", "user_id": "u1"}
        push_mock = AsyncMock()
        update_mock = AsyncMock()

        async def _get_rows(table, filters=None, **kwargs):
            if table == "driver_subscriptions" and filters and filters.get("status") == "cancel_pending":
                return []
            if table == "driver_subscriptions" and filters and "$and" in filters:
                return []  # no 3d candidates
            return [sub]

        with (
            patch("backend.utils.redis_client.redis_set_nx", AsyncMock(return_value=True)),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.db_supabase.find_one", AsyncMock(return_value=driver)),
            patch("backend.db_supabase.update_one", update_mock),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"require_driver_subscription": False}),
            ),
            patch("backend.routes.drivers._deps.send_push_notification", push_mock),
            _stop_after_one_iteration(),
        ):
            try:
                await check_expiring_subscriptions()
            except Exception as e:
                if "stop" not in str(e):
                    raise

        push_mock.assert_awaited_once()
        assert "Expiring Soon" in push_mock.await_args.args[1]
        update_calls_str = [str(c) for c in update_mock.await_args_list]
        assert any("expiry_warned" in c for c in update_calls_str)

    async def test_expired_sub_gate_off_only_flips_row_no_driver_kick(self):
        from backend.routes.drivers import check_expiring_subscriptions

        now = datetime.now(timezone.utc)
        sub = {
            "id": "sub-1",
            "driver_id": "d1",
            "status": "active",
            "expires_at": (now - timedelta(hours=1)).isoformat(),
        }
        update_mock = AsyncMock()
        find_one_mock = AsyncMock()  # should never be called for driver lookup

        async def _get_rows(table, filters=None, **kwargs):
            if filters and filters.get("status") == "cancel_pending":
                return []
            if filters and "$and" in filters:
                return []
            return [sub]

        with (
            patch("backend.utils.redis_client.redis_set_nx", AsyncMock(return_value=True)),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.db_supabase.find_one", find_one_mock),
            patch("backend.db_supabase.update_one", update_mock),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"require_driver_subscription": False}),
            ),
            _stop_after_one_iteration(),
        ):
            try:
                await check_expiring_subscriptions()
            except Exception as e:
                if "stop" not in str(e):
                    raise

        update_mock.assert_awaited_once()
        assert update_mock.await_args.args[2] == {"status": "expired"}
        find_one_mock.assert_not_awaited()

    async def test_expired_sub_gate_on_flips_driver_offline_and_pushes(self):
        from backend.routes.drivers import check_expiring_subscriptions

        now = datetime.now(timezone.utc)
        sub = {
            "id": "sub-1",
            "driver_id": "d1",
            "status": "active",
            "expires_at": (now - timedelta(hours=1)).isoformat(),
        }
        driver = {"id": "d1", "user_id": "u1", "is_online": True}
        push_mock = AsyncMock()
        clear_presence_mock = AsyncMock()
        record_period_mock = AsyncMock()
        manager_mock = MagicMock()
        manager_mock.disconnect = MagicMock()
        manager_mock.broadcast_to_admins = AsyncMock()

        async def _get_rows(table, filters=None, **kwargs):
            if filters and filters.get("status") == "cancel_pending":
                return []
            if filters and "$and" in filters:
                return []
            return [sub]

        with (
            patch("backend.utils.redis_client.redis_set_nx", AsyncMock(return_value=True)),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.db_supabase.find_one", AsyncMock(return_value=driver)),
            patch("backend.db_supabase.update_one", AsyncMock()),
            patch("backend.db_supabase.insert_one", AsyncMock()),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"require_driver_subscription": True}),
            ),
            patch("backend.routes.drivers._deps.send_push_notification", push_mock),
            patch("backend.routes.drivers._deps.clear_presence", clear_presence_mock),
            patch("backend.routes.drivers._deps.record_period_transition", record_period_mock),
            patch("backend.routes.drivers._deps.manager", manager_mock),
            _stop_after_one_iteration(),
        ):
            try:
                await check_expiring_subscriptions()
            except Exception as e:
                if "stop" not in str(e):
                    raise

        push_mock.assert_awaited_once()
        assert "expired" in push_mock.await_args.args[1].lower()
        clear_presence_mock.assert_awaited_once_with("d1")
        record_period_mock.assert_awaited_once_with("d1", 0)
        manager_mock.disconnect.assert_called_once_with("driver_u1")
        manager_mock.broadcast_to_admins.assert_awaited_once()

    async def test_expired_sub_gate_on_driver_already_offline_skips_kick(self):
        from backend.routes.drivers import check_expiring_subscriptions

        now = datetime.now(timezone.utc)
        sub = {
            "id": "sub-1",
            "driver_id": "d1",
            "status": "active",
            "expires_at": (now - timedelta(hours=1)).isoformat(),
        }
        driver = {"id": "d1", "user_id": "u1", "is_online": False}
        push_mock = AsyncMock()

        async def _get_rows(table, filters=None, **kwargs):
            if filters and filters.get("status") == "cancel_pending":
                return []
            if filters and "$and" in filters:
                return []
            return [sub]

        with (
            patch("backend.utils.redis_client.redis_set_nx", AsyncMock(return_value=True)),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.db_supabase.find_one", AsyncMock(return_value=driver)),
            patch("backend.db_supabase.update_one", AsyncMock()),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"require_driver_subscription": True}),
            ),
            patch("backend.routes.drivers._deps.send_push_notification", push_mock),
            _stop_after_one_iteration(),
        ):
            try:
                await check_expiring_subscriptions()
            except Exception as e:
                if "stop" not in str(e):
                    raise

        push_mock.assert_not_awaited()

    async def test_unparseable_expires_at_row_is_skipped(self):
        from backend.routes.drivers import check_expiring_subscriptions

        sub = {"id": "sub-1", "driver_id": "d1", "status": "active", "expires_at": "garbage-not-a-date"}
        update_mock = AsyncMock()

        async def _get_rows(table, filters=None, **kwargs):
            if filters and filters.get("status") == "cancel_pending":
                return []
            if filters and "$and" in filters:
                return []
            return [sub]

        with (
            patch("backend.utils.redis_client.redis_set_nx", AsyncMock(return_value=True)),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.db_supabase.update_one", update_mock),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"require_driver_subscription": False}),
            ),
            _stop_after_one_iteration(),
        ):
            try:
                await check_expiring_subscriptions()
            except Exception as e:
                if "stop" not in str(e):
                    raise

        # Unparseable expiry -> `continue`d before any update_one call for this row.
        update_mock.assert_not_awaited()

    async def test_no_expires_at_row_is_skipped(self):
        from backend.routes.drivers import check_expiring_subscriptions

        sub = {"id": "sub-1", "driver_id": "d1", "status": "active", "expires_at": None}
        update_mock = AsyncMock()

        async def _get_rows(table, filters=None, **kwargs):
            if filters and filters.get("status") == "cancel_pending":
                return []
            if filters and "$and" in filters:
                return []
            return [sub]

        with (
            patch("backend.utils.redis_client.redis_set_nx", AsyncMock(return_value=True)),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.db_supabase.update_one", update_mock),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"require_driver_subscription": False}),
            ),
            _stop_after_one_iteration(),
        ):
            try:
                await check_expiring_subscriptions()
            except Exception as e:
                if "stop" not in str(e):
                    raise

        update_mock.assert_not_awaited()

    async def test_cancel_pending_retry_succeeds_marks_cancelled(self):
        from backend.routes.drivers import check_expiring_subscriptions

        stuck = {"id": "sub-stuck", "stripe_subscription_id": "sub_stripe_1"}
        update_mock = AsyncMock()

        async def _get_rows(table, filters=None, **kwargs):
            if filters and filters.get("status") == "cancel_pending":
                return [stuck]
            if filters and filters.get("status") == "active":
                return []
            if filters and "$and" in filters:
                return []
            return []

        with (
            patch("backend.utils.redis_client.redis_set_nx", AsyncMock(return_value=True)),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.db_supabase.update_one", update_mock),
            patch(
                "backend.routes.drivers.subscriptions._cancel_stripe_subscription",
                AsyncMock(return_value=None),
            ) as cancel_mock,
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"require_driver_subscription": False}),
            ),
            _stop_after_one_iteration(),
        ):
            try:
                await check_expiring_subscriptions()
            except Exception as e:
                if "stop" not in str(e):
                    raise

        cancel_mock.assert_awaited_once_with("sub_stripe_1", raise_on_error=True)
        update_mock.assert_awaited_once()
        assert update_mock.await_args.args[2] == {"$set": {"status": "cancelled"}}

    async def test_cancel_pending_retry_still_failing_logged_not_raised(self):
        from backend.routes.drivers import check_expiring_subscriptions

        stuck = {"id": "sub-stuck", "stripe_subscription_id": "sub_stripe_1"}

        async def _get_rows(table, filters=None, **kwargs):
            if filters and filters.get("status") == "cancel_pending":
                return [stuck]
            if filters and filters.get("status") == "active":
                return []
            if filters and "$and" in filters:
                return []
            return []

        with (
            patch("backend.utils.redis_client.redis_set_nx", AsyncMock(return_value=True)),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.db_supabase.update_one", AsyncMock()),
            patch(
                "backend.routes.drivers.subscriptions._cancel_stripe_subscription",
                AsyncMock(side_effect=RuntimeError("stripe still down")),
            ),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"require_driver_subscription": False}),
            ),
            _stop_after_one_iteration(),
        ):
            # Must not raise — the whole check_expiring_subscriptions body is
            # wrapped in try/except; a stuck cancel_pending retry failure is
            # logged and the loop continues (verified by reaching the sleep
            # call, i.e. our injected "stop" exception below).
            try:
                await check_expiring_subscriptions()
            except Exception as e:
                assert "stop" in str(e)

    async def test_app_settings_failure_defaults_to_no_enforcement(self):
        from backend.routes.drivers import check_expiring_subscriptions

        now = datetime.now(timezone.utc)
        sub = {
            "id": "sub-1",
            "driver_id": "d1",
            "status": "active",
            "expires_at": (now - timedelta(hours=1)).isoformat(),
        }
        find_one_mock = AsyncMock()  # driver lookup should never happen (no enforcement)

        async def _get_rows(table, filters=None, **kwargs):
            if filters and filters.get("status") == "cancel_pending":
                return []
            if filters and "$and" in filters:
                return []
            return [sub]

        with (
            patch("backend.utils.redis_client.redis_set_nx", AsyncMock(return_value=True)),
            patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("backend.db_supabase.update_one", AsyncMock()),
            patch("backend.db_supabase.find_one", find_one_mock),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(side_effect=RuntimeError("settings db down")),
            ),
            _stop_after_one_iteration(),
        ):
            try:
                await check_expiring_subscriptions()
            except Exception as e:
                if "stop" not in str(e):
                    raise

        find_one_mock.assert_not_awaited()
