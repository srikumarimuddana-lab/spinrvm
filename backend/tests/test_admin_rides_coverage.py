"""Coverage-closing unit tests for backend/routes/admin/rides.py (A1b Track 1 #4).

Prioritization (see docs/change-log/2026-07-29-a1b-admin-rides-coverage.md for the
full rationale): endpoints that mutate ride state or money paths are covered
first and most deeply (cancel, complete, create, send-payable-invoice, payout
retry/bulk-retry/close-period); read-only list/search/export/stat endpoints get
a lighter smoke pass (happy path + one DB-exception path) since a bug there
degrades a dashboard view rather than corrupting production ride/money state.

Follows the existing `client` + `_set_super_admin(app_fixture)` fixture pattern
established in tests/test_admin_business_logic.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_SUPER_ADMIN = {
    "id": "admin-001",
    "role": "super_admin",
    "email": "admin@spinr.app",
    "modules": ["dashboard", "rides", "earnings", "support"],
}

_FINANCE_ADMIN = {
    "id": "admin-fin",
    "role": "finance",
    "email": "finance@spinr.app",
    "modules": ["dashboard", "rides", "earnings"],
}

_NON_FINANCE_ADMIN = {
    "id": "admin-support",
    "role": "support",
    "email": "support@spinr.app",
    "modules": ["dashboard"],
}

_RIDE_SEARCHING = {
    "id": "ride-1",
    "status": "searching",
    "rider_id": "usr-1",
    "driver_id": None,
}

_RIDE_WITH_DRIVER = {
    "id": "ride-2",
    "status": "driver_assigned",
    "rider_id": "usr-1",
    "driver_id": "drv-1",
}

_RIDE_IN_PROGRESS = {
    "id": "ride-3",
    "status": "in_progress",
    "rider_id": "usr-1",
    "driver_id": "drv-1",
}

_DRIVER = {"id": "drv-1", "user_id": "drv-user-1", "name": "Dan Driver"}


@pytest.fixture
def client(test_client):
    return test_client


@pytest.fixture
def app_fixture():
    from backend.server import app

    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def as_super_admin(app_fixture):
    from dependencies import get_admin_user

    app_fixture.dependency_overrides[get_admin_user] = lambda: _SUPER_ADMIN
    yield
    app_fixture.dependency_overrides.clear()


@pytest.fixture
def as_finance_admin(app_fixture):
    from dependencies import get_admin_user

    app_fixture.dependency_overrides[get_admin_user] = lambda: _FINANCE_ADMIN
    yield
    app_fixture.dependency_overrides.clear()


@pytest.fixture
def as_non_finance_admin(app_fixture):
    from dependencies import get_admin_user

    app_fixture.dependency_overrides[get_admin_user] = lambda: _NON_FINANCE_ADMIN
    yield
    app_fixture.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /rides/{id}/cancel — additional branches beyond test_admin_business_logic.py
# ---------------------------------------------------------------------------


class TestAdminCancelRideExtra:
    def test_cancel_already_cancelled_ride_400(self, client, as_super_admin):
        ride = {**_RIDE_SEARCHING, "status": "cancelled"}
        with patch("db_supabase.get_ride", AsyncMock(return_value=ride)):
            resp = client.post("/api/admin/rides/ride-1/cancel", json={"reason": "dup"})
        assert resp.status_code == 400

    def test_cancel_frees_assigned_driver_and_notifies(self, client, as_super_admin):
        """Driver must be freed (set_driver_available) when an assigned ride is cancelled."""
        freed: dict = {}

        async def _set_avail(driver_id, available):
            freed["driver_id"] = driver_id
            freed["available"] = available

        cancelled_ride = {**_RIDE_WITH_DRIVER, "status": "cancelled"}
        with (
            patch("db_supabase.get_ride", AsyncMock(side_effect=[_RIDE_WITH_DRIVER, cancelled_ride])),
            patch("db_supabase.update_ride", AsyncMock(return_value=None)),
            patch("db_supabase.set_driver_available", AsyncMock(side_effect=_set_avail)),
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=_DRIVER)),
            patch("socket_manager.manager.send_personal_message", AsyncMock()),
            patch("socket_manager.manager.broadcast_ride_status", AsyncMock()),
            patch("socket_manager.manager.broadcast_to_admins", AsyncMock()),
            patch("features.send_push_notification", AsyncMock(), create=True),
        ):
            resp = client.post("/api/admin/rides/ride-2/cancel", json={"reason": "no drivers"})
        assert resp.status_code == 200
        assert freed == {"driver_id": "drv-1", "available": True}

    def test_cancel_notifies_driver_and_rider_with_correct_target_app(self, client, as_super_admin):
        """ACTION_ITEMS.md N10 (admin bucket): the driver-facing and rider-facing
        cancellation pushes must each carry the right target_app, not fall
        through to the legacy fcm_token column."""
        cancelled_ride = {**_RIDE_WITH_DRIVER, "status": "cancelled"}
        push_mock = AsyncMock()
        with (
            patch("db_supabase.get_ride", AsyncMock(side_effect=[_RIDE_WITH_DRIVER, cancelled_ride])),
            patch("db_supabase.update_ride", AsyncMock(return_value=None)),
            patch("db_supabase.set_driver_available", AsyncMock()),
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=_DRIVER)),
            patch("socket_manager.manager.send_personal_message", AsyncMock()),
            patch("socket_manager.manager.broadcast_ride_status", AsyncMock()),
            patch("socket_manager.manager.broadcast_to_admins", AsyncMock()),
            patch("routes.admin.rides.send_push_notification", push_mock),
        ):
            resp = client.post("/api/admin/rides/ride-2/cancel", json={"reason": "no drivers"})
        assert resp.status_code == 200
        calls_by_recipient = {c.args[0]: c.kwargs.get("target_app") for c in push_mock.await_args_list}
        assert calls_by_recipient[_DRIVER["user_id"]] == "driver"
        assert calls_by_recipient[_RIDE_WITH_DRIVER["rider_id"]] == "rider"

    def test_cancel_silent_no_op_surfaces_500(self, client, as_super_admin):
        """If the status update doesn't actually persist, this must surface loudly (500), not
        silently report success — a broken admin cancel would otherwise strand riders/drivers
        thinking a ride is over when it is not."""
        with (
            patch("db_supabase.get_ride", AsyncMock(return_value=_RIDE_SEARCHING)),
            patch("db_supabase.update_ride", AsyncMock(return_value=None)),
        ):
            # verify() re-reads and still shows "searching" -> update never took.
            with patch("db_supabase.get_ride", AsyncMock(side_effect=[_RIDE_SEARCHING, _RIDE_SEARCHING])):
                resp = client.post("/api/admin/rides/ride-1/cancel", json={"reason": "x"})
        assert resp.status_code == 500

    def test_cancel_db_write_failure_propagates(self, client, as_super_admin):
        """Both the mig-38 and mig-37 fallback writes fail -> the underlying error must
        propagate (not be silently swallowed) per CLAUDE.md's DB-error rule. The route
        re-raises the raw exception rather than wrapping it in HTTPException, so under
        TestClient (raise_server_exceptions=True) it surfaces as a raised exception here
        rather than a 500 response -- that IS the "surfaced loudly" behaviour we're
        asserting; a real deployment's registered Exception handler turns this into a 500."""
        with (
            patch("db_supabase.get_ride", AsyncMock(return_value=_RIDE_SEARCHING)),
            patch("db_supabase.update_ride", AsyncMock(side_effect=RuntimeError("db down"))),
            pytest.raises(RuntimeError, match="db down"),
        ):
            client.post("/api/admin/rides/ride-1/cancel", json={"reason": "x"})

    def test_cancel_no_reason_defaults(self, client, as_super_admin):
        cancelled_ride = {**_RIDE_SEARCHING, "status": "cancelled"}
        with (
            patch("db_supabase.get_ride", AsyncMock(side_effect=[_RIDE_SEARCHING, cancelled_ride])),
            patch("db_supabase.update_ride", AsyncMock(return_value=None)),
            patch("socket_manager.manager.send_personal_message", AsyncMock()),
            patch("socket_manager.manager.broadcast_ride_status", AsyncMock()),
            patch("socket_manager.manager.broadcast_to_admins", AsyncMock()),
            patch("features.send_push_notification", AsyncMock(), create=True),
        ):
            resp = client.post("/api/admin/rides/ride-1/cancel", json={})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /rides/{id}/complete
# ---------------------------------------------------------------------------


class TestAdminCompleteRide:
    def test_complete_nonexistent_ride_404(self, client, as_super_admin):
        with patch("db_supabase.get_ride", AsyncMock(return_value=None)):
            resp = client.post("/api/admin/rides/no-ride/complete")
        assert resp.status_code == 404

    def test_complete_wrong_state_400(self, client, as_super_admin):
        with patch("db_supabase.get_ride", AsyncMock(return_value=_RIDE_SEARCHING)):
            resp = client.post("/api/admin/rides/ride-1/complete")
        assert resp.status_code == 400

    def test_complete_happy_path_sets_waived_admin(self, client, as_super_admin):
        captured: dict = {}

        async def _update_ride(ride_id, data):
            captured["ride_id"] = ride_id
            captured["data"] = data

        with (
            patch("db_supabase.get_ride", AsyncMock(return_value=_RIDE_IN_PROGRESS)),
            patch("db_supabase.update_ride", AsyncMock(side_effect=_update_ride)),
            patch("routes.admin.rides.log_admin_action", AsyncMock(return_value="audit-1")),
            patch("db_supabase.set_driver_available", AsyncMock()),
            patch("utils.insurance_periods.record_period_transition", AsyncMock()),
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=_DRIVER)),
            patch("socket_manager.manager.send_personal_message", AsyncMock()),
            patch("socket_manager.manager.broadcast_ride_status", AsyncMock()),
            patch("socket_manager.manager.broadcast_to_admins", AsyncMock()),
        ):
            resp = client.post("/api/admin/rides/ride-3/complete")
        assert resp.status_code == 200
        assert captured["data"]["status"] == "completed"
        # Must NOT impersonate 'paid' — no real Stripe charge happened.
        assert captured["data"]["payment_status"] == "waived_admin"

    def test_complete_from_driver_arrived_allowed(self, client, as_super_admin):
        ride = {**_RIDE_IN_PROGRESS, "status": "driver_arrived"}
        with (
            patch("db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("db_supabase.update_ride", AsyncMock(return_value=None)),
            patch("routes.admin.rides.log_admin_action", AsyncMock(return_value="audit-1")),
            patch("db_supabase.set_driver_available", AsyncMock()),
            patch("utils.insurance_periods.record_period_transition", AsyncMock()),
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=_DRIVER)),
            patch("socket_manager.manager.send_personal_message", AsyncMock()),
            patch("socket_manager.manager.broadcast_ride_status", AsyncMock()),
            patch("socket_manager.manager.broadcast_to_admins", AsyncMock()),
        ):
            resp = client.post("/api/admin/rides/ride-3/complete")
        assert resp.status_code == 200

    def test_complete_db_write_failure_returns_500_not_swallowed(self, client, as_super_admin):
        with (
            patch("db_supabase.get_ride", AsyncMock(return_value=_RIDE_IN_PROGRESS)),
            patch("db_supabase.update_ride", AsyncMock(side_effect=RuntimeError("db down"))),
        ):
            resp = client.post("/api/admin/rides/ride-3/complete")
        assert resp.status_code == 500

    def test_complete_never_valid_after_cancelled(self, client, as_super_admin):
        ride = {**_RIDE_IN_PROGRESS, "status": "cancelled"}
        with patch("db_supabase.get_ride", AsyncMock(return_value=ride)):
            resp = client.post("/api/admin/rides/ride-3/complete")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /rides/create
# ---------------------------------------------------------------------------


_CREATE_BODY = {
    "rider_id": "usr-1",
    "pickup_address": "100 Main St",
    "pickup_lat": 50.45,
    "pickup_lng": -104.6,
    "dropoff_address": "200 Elm St",
    "dropoff_lat": 50.46,
    "dropoff_lng": -104.5,
    "total_fare": "12.50",
}


class TestAdminCreateRide:
    def test_create_ride_no_driver_status_searching(self, client, as_super_admin):
        captured: dict = {}

        async def _insert(table, doc):
            captured["table"] = table
            captured["doc"] = doc

        with (
            patch("db_supabase.insert_one", AsyncMock(side_effect=_insert)),
            patch("routes.admin.rides.log_admin_action", AsyncMock(return_value="audit-1")),
            patch("socket_manager.manager.broadcast_ride_status", AsyncMock()),
        ):
            resp = client.post("/api/admin/rides/create", json=_CREATE_BODY)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "searching"
        assert captured["table"] == "rides"
        # Money value must round-trip as a Decimal-safe string, never a float artifact.
        assert Decimal(str(captured["doc"]["total_fare"])) == Decimal("12.50")

    def test_create_ride_with_driver_status_driver_assigned_and_dispatches(self, client, as_super_admin):
        body = {**_CREATE_BODY, "driver_id": "drv-1"}
        push_mock = AsyncMock()
        with (
            patch("db_supabase.insert_one", AsyncMock(return_value=None)),
            patch("routes.admin.rides.log_admin_action", AsyncMock(return_value="audit-1")),
            patch("db_supabase.set_driver_available", AsyncMock()),
            patch("utils.insurance_periods.record_period_transition", AsyncMock()),
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=_DRIVER)),
            patch(
                "db_supabase.get_user_by_id", AsyncMock(return_value={"id": "usr-1", "first_name": "R", "rating": 5})
            ),
            patch("routes.admin.rides.get_app_settings", AsyncMock(return_value={"ride_offer_timeout_seconds": 15})),
            patch("socket_manager.manager.send_personal_message", AsyncMock()),
            patch("socket_manager.manager.broadcast_ride_status", AsyncMock()),
            patch("routes.admin.rides.send_push_notification", push_mock),
            patch("routes.rides._offer_timeout_handler", AsyncMock(), create=True),
        ):
            resp = client.post("/api/admin/rides/create", json=body)
        assert resp.status_code == 200
        assert resp.json()["status"] == "driver_assigned"
        # ACTION_ITEMS.md N10 (admin bucket): the new-ride-assignment dispatch
        # push must target the driver app, not the legacy fcm_token column.
        push_mock.assert_awaited_once()
        assert push_mock.await_args.args[0] == _DRIVER["user_id"]
        assert push_mock.await_args.kwargs.get("target_app") == "driver"

    def test_create_ride_insert_failure_returns_500(self, client, as_super_admin):
        with patch("db_supabase.insert_one", AsyncMock(side_effect=RuntimeError("db down"))):
            resp = client.post("/api/admin/rides/create", json=_CREATE_BODY)
        assert resp.status_code == 500

    def test_create_ride_missing_required_field_422(self, client, as_super_admin):
        bad = {k: v for k, v in _CREATE_BODY.items() if k != "pickup_lat"}
        resp = client.post("/api/admin/rides/create", json=bad)
        assert resp.status_code == 422

    def test_create_ride_with_promo_records_application(self, client, as_super_admin):
        body = {**_CREATE_BODY, "promo_code": "SAVE5", "subtotal_fare": "20.00"}
        admin_promo_result = {
            "application_id": "app-1",
            "code": "SAVE5",
            "discount_amount": "5.00",
        }
        with (
            patch("routes.promotions.apply_promo_for_admin", AsyncMock(return_value=admin_promo_result), create=True),
            patch("db_supabase.insert_one", AsyncMock(return_value=None)) as mock_insert,
            patch("routes.admin.rides.log_admin_action", AsyncMock(return_value="audit-1")),
            patch("socket_manager.manager.broadcast_ride_status", AsyncMock()),
            # N15/R33: admin_create_ride now also sends a promo-applied push
            # when promo_application_id is set — mock it out here so
            # mock_insert.call_args (the ride insert) isn't shadowed by the
            # unrelated notification-inbox insert send_push_notification
            # performs internally. The push itself is covered separately by
            # test_create_ride_with_promo_notifies_rider below.
            patch("routes.admin.rides.send_push_notification", AsyncMock(return_value=True)),
        ):
            resp = client.post("/api/admin/rides/create", json=body)
        assert resp.status_code == 200
        inserted_doc = mock_insert.call_args[0][1]
        assert inserted_doc["promo_code"] == "SAVE5"
        assert Decimal(str(inserted_doc["discount_amount"])) == Decimal("5.00")

    def test_create_ride_with_promo_notifies_rider(self, client, as_super_admin):
        """N15/R33 (ACTION_ITEMS.md): admin-applied promos previously had zero
        notification call — the rider must be told their promo was redeemed."""
        body = {**_CREATE_BODY, "promo_code": "SAVE5", "subtotal_fare": "20.00"}
        admin_promo_result = {
            "application_id": "app-1",
            "code": "SAVE5",
            "discount_amount": "5.00",
        }
        push = AsyncMock(return_value=True)
        with (
            patch("routes.promotions.apply_promo_for_admin", AsyncMock(return_value=admin_promo_result), create=True),
            patch("db_supabase.insert_one", AsyncMock(return_value=None)),
            patch("routes.admin.rides.log_admin_action", AsyncMock(return_value="audit-1")),
            patch("socket_manager.manager.broadcast_ride_status", AsyncMock()),
            patch("routes.admin.rides.send_push_notification", push),
        ):
            resp = client.post("/api/admin/rides/create", json=body)
        assert resp.status_code == 200

        push.assert_awaited_once()
        args, kwargs = push.await_args
        assert args[0] == "usr-1"
        assert kwargs["data"]["type"] == "promo_applied"
        assert kwargs["data"]["promo_code"] == "SAVE5"
        assert kwargs["data"]["discount_amount"] == "5.00"
        assert kwargs["target_app"] == "rider"

    def test_create_ride_without_promo_does_not_notify(self, client, as_super_admin):
        push = AsyncMock(return_value=True)
        with (
            patch("db_supabase.insert_one", AsyncMock(return_value=None)),
            patch("routes.admin.rides.log_admin_action", AsyncMock(return_value="audit-1")),
            patch("socket_manager.manager.broadcast_ride_status", AsyncMock()),
            patch("routes.admin.rides.send_push_notification", push),
        ):
            resp = client.post("/api/admin/rides/create", json=_CREATE_BODY)
        assert resp.status_code == 200
        push.assert_not_awaited()

    def test_create_ride_promo_push_failure_does_not_break_response(self, client, as_super_admin):
        """The ride + promo redemption already committed above the push call —
        a delivery failure must never surface as a failed ride-creation request."""
        body = {**_CREATE_BODY, "promo_code": "SAVE5", "subtotal_fare": "20.00"}
        admin_promo_result = {
            "application_id": "app-1",
            "code": "SAVE5",
            "discount_amount": "5.00",
        }
        push = AsyncMock(side_effect=RuntimeError("fcm down"))
        with (
            patch("routes.promotions.apply_promo_for_admin", AsyncMock(return_value=admin_promo_result), create=True),
            patch("db_supabase.insert_one", AsyncMock(return_value=None)) as mock_insert,
            patch("routes.admin.rides.log_admin_action", AsyncMock(return_value="audit-1")),
            patch("socket_manager.manager.broadcast_ride_status", AsyncMock()),
            patch("routes.admin.rides.send_push_notification", push),
        ):
            resp = client.post("/api/admin/rides/create", json=body)
        assert resp.status_code == 200
        inserted_doc = mock_insert.call_args[0][1]
        assert inserted_doc["promo_code"] == "SAVE5"


# ---------------------------------------------------------------------------
# POST /rides/{id}/send-invoice (money-adjacent Stripe invoice flow) —
# these tests exercise every guard clause without reaching a live Stripe call.
# ---------------------------------------------------------------------------


class TestAdminSendPayableInvoiceGuards:
    _COMPLETED_UNPAID = {
        "id": "ride-9",
        "status": "completed",
        "payment_status": "pending",
        "payment_method": "card",
        "rider_id": "usr-1",
        "total_fare": "15.00",
        "grand_total": "15.00",
        "tip_amount": "0",
    }

    def test_send_invoice_nonexistent_ride_404(self, client, as_super_admin):
        with patch("db_supabase.get_ride", AsyncMock(return_value=None)):
            resp = client.post("/api/admin/rides/no-ride/send-invoice")
        assert resp.status_code == 404

    def test_send_invoice_ride_not_completed_409(self, client, as_super_admin):
        ride = {**self._COMPLETED_UNPAID, "status": "in_progress"}
        with patch("db_supabase.get_ride", AsyncMock(return_value=ride)):
            resp = client.post("/api/admin/rides/ride-9/send-invoice")
        assert resp.status_code == 409

    @pytest.mark.parametrize("terminal_status", ["paid", "waived_admin", "refunded", "partially_refunded"])
    def test_send_invoice_terminal_payment_status_409(self, client, as_super_admin, terminal_status):
        ride = {**self._COMPLETED_UNPAID, "payment_status": terminal_status}
        with patch("db_supabase.get_ride", AsyncMock(return_value=ride)):
            resp = client.post("/api/admin/rides/ride-9/send-invoice")
        assert resp.status_code == 409

    def test_send_invoice_processing_returns_409(self, client, as_super_admin):
        ride = {**self._COMPLETED_UNPAID, "payment_status": "processing"}
        with patch("db_supabase.get_ride", AsyncMock(return_value=ride)):
            resp = client.post("/api/admin/rides/ride-9/send-invoice")
        assert resp.status_code == 409

    def test_send_invoice_non_card_ride_409(self, client, as_super_admin):
        ride = {**self._COMPLETED_UNPAID, "payment_method": "wallet"}
        with patch("db_supabase.get_ride", AsyncMock(return_value=ride)):
            resp = client.post("/api/admin/rides/ride-9/send-invoice")
        assert resp.status_code == 409

    @pytest.mark.parametrize("auth_status", ["authorized", "fare_only"])
    def test_send_invoice_open_preauth_hold_409(self, client, as_super_admin, auth_status):
        ride = {**self._COMPLETED_UNPAID, "auth_status": auth_status}
        with patch("db_supabase.get_ride", AsyncMock(return_value=ride)):
            resp = client.post("/api/admin/rides/ride-9/send-invoice")
        assert resp.status_code == 409

    def test_send_invoice_no_rider_email_422(self, client, as_super_admin):
        with (
            patch("db_supabase.get_ride", AsyncMock(return_value=self._COMPLETED_UNPAID)),
            patch("db_supabase.get_user_by_id", AsyncMock(return_value={"id": "usr-1", "email": ""})),
        ):
            resp = client.post("/api/admin/rides/ride-9/send-invoice")
        assert resp.status_code == 422

    def test_send_invoice_stripe_not_configured_503(self, client, as_super_admin):
        with (
            patch("db_supabase.get_ride", AsyncMock(return_value=self._COMPLETED_UNPAID)),
            patch("db_supabase.get_user_by_id", AsyncMock(return_value={"id": "usr-1", "email": "r@example.com"})),
            patch("routes.admin.rides.get_app_settings", AsyncMock(return_value={})),
        ):
            resp = client.post("/api/admin/rides/ride-9/send-invoice")
        assert resp.status_code == 503

    def test_send_invoice_zero_amount_409(self, client, as_super_admin):
        ride = {**self._COMPLETED_UNPAID, "grand_total": "0", "total_fare": "0", "tip_amount": "0"}
        with (
            patch("db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("db_supabase.get_user_by_id", AsyncMock(return_value={"id": "usr-1", "email": "r@example.com"})),
            patch("routes.admin.rides.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test_x"})),
        ):
            resp = client.post("/api/admin/rides/ride-9/send-invoice")
        assert resp.status_code == 409

    def test_send_invoice_concurrent_pending_claim_409(self, client, as_super_admin):
        """A fresh (non-stale) pending: sentinel blocks a second concurrent request."""
        fresh_sentinel = f"pending:{datetime.now(timezone.utc).timestamp()}:abc"
        ride = {**self._COMPLETED_UNPAID, "stripe_invoice_id": fresh_sentinel, "stripe_customer_id": None}
        with (
            patch("db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch(
                "db_supabase.get_user_by_id",
                AsyncMock(return_value={"id": "usr-1", "email": "r@example.com", "stripe_customer_id": "cus_1"}),
            ),
            patch("routes.admin.rides.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test_x"})),
        ):
            resp = client.post("/api/admin/rides/ride-9/send-invoice")
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Payouts — retry / bulk-retry / close-period (money-adjacent, role-gated)
# ---------------------------------------------------------------------------


class TestAdminPayoutRetry:
    def test_retry_requires_finance_role_403(self, client, as_non_finance_admin):
        resp = client.post("/api/admin/payouts/payout-1/retry")
        assert resp.status_code == 403

    def test_retry_nonexistent_payout_404(self, client, as_finance_admin):
        with patch("db_supabase.find_one", AsyncMock(return_value=None)):
            resp = client.post("/api/admin/payouts/payout-1/retry")
        assert resp.status_code == 404

    def test_retry_wrong_status_409(self, client, as_finance_admin):
        with patch("db_supabase.find_one", AsyncMock(return_value={"id": "payout-1", "status": "completed"})):
            resp = client.post("/api/admin/payouts/payout-1/retry")
        assert resp.status_code == 409

    def test_retry_happy_path_sets_pending(self, client, as_finance_admin):
        captured: dict = {}

        async def _update(table, filt, fields):
            captured.update({"table": table, "filt": filt, "fields": fields})

        with (
            patch("db_supabase.find_one", AsyncMock(return_value={"id": "payout-1", "status": "failed"})),
            patch("db_supabase.update_one", AsyncMock(side_effect=_update)),
            patch("routes.admin.rides.log_admin_action", AsyncMock(return_value="audit-1")) as audit_mock,
        ):
            resp = client.post("/api/admin/payouts/payout-1/retry")
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"
        assert captured["fields"]["status"] == "pending"
        audit_mock.assert_awaited_once()
        assert audit_mock.call_args[0][1] == "payout_retry_requested"

    def test_retry_allows_super_admin_role(self, client, as_super_admin):
        with (
            patch("db_supabase.find_one", AsyncMock(return_value={"id": "payout-1", "status": "cancelled"})),
            patch("db_supabase.update_one", AsyncMock(return_value=None)),
        ):
            resp = client.post("/api/admin/payouts/payout-1/retry")
        assert resp.status_code == 200


class TestAdminBulkRetryPayouts:
    def test_bulk_retry_requires_finance_role_403(self, client, as_non_finance_admin):
        resp = client.post("/api/admin/payouts/bulk-retry", json={"payout_ids": ["p1"]})
        assert resp.status_code == 403

    def test_bulk_retry_both_modes_rejected_400(self, client, as_finance_admin):
        resp = client.post(
            "/api/admin/payouts/bulk-retry",
            json={"payout_ids": ["p1"], "since": "2026-01-01T00:00:00+00:00"},
        )
        assert resp.status_code == 400

    def test_bulk_retry_explicit_ids_mixed_outcomes(self, client, as_finance_admin):
        async def _find_one(table, filt):
            pid = filt.get("id")
            if pid == "p-missing":
                return None
            if pid == "p-wrong-status":
                return {"id": pid, "status": "completed"}
            return {"id": pid, "status": "failed"}

        with (
            patch("db_supabase.find_one", AsyncMock(side_effect=_find_one)),
            patch("db_supabase.update_one", AsyncMock(return_value=None)),
        ):
            resp = client.post(
                "/api/admin/payouts/bulk-retry",
                json={"payout_ids": ["p-ok", "p-missing", "p-wrong-status"]},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["retried"] == 1
        assert body["skipped"] == 1
        assert body["failed_to_initiate"] == 1

    def test_bulk_retry_since_mode(self, client, as_finance_admin):
        rows = [{"id": "p1", "status": "failed", "driver_id": "d1"}]
        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=rows)),
            patch("db_supabase.update_one", AsyncMock(return_value=None)),
            patch("routes.admin.rides.log_admin_action", AsyncMock(return_value="audit-2")) as audit_mock,
        ):
            resp = client.post(
                "/api/admin/payouts/bulk-retry",
                json={"since": "2026-01-01T00:00:00+00:00"},
            )
        assert resp.status_code == 200
        assert resp.json()["retried"] == 1
        audit_mock.assert_awaited_once()
        assert audit_mock.call_args[0][1] == "payout_bulk_retry_requested"

    def test_bulk_retry_update_failure_counted_not_swallowed(self, client, as_finance_admin):
        async def _find_one(table, filt):
            return {"id": filt.get("id"), "status": "failed"}

        with (
            patch("db_supabase.find_one", AsyncMock(side_effect=_find_one)),
            patch("db_supabase.update_one", AsyncMock(side_effect=RuntimeError("db down"))),
        ):
            resp = client.post("/api/admin/payouts/bulk-retry", json={"payout_ids": ["p1"]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["failed_to_initiate"] == 1
        assert body["retried"] == 0


class TestAdminClosePayoutPeriod:
    def test_close_period_requires_finance_role_403(self, client, as_non_finance_admin):
        resp = client.post("/api/admin/payouts/close-period", json={"year": 2026, "month": 5})
        assert resp.status_code == 403

    def test_close_period_happy_path(self, client, as_finance_admin):
        rows = [{"id": "payout-1", "amount": "100.00"}, {"id": "payout-2", "amount": "50.50"}]
        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=rows)),
            patch("routes.admin.rides.log_admin_action", AsyncMock(return_value="audit-99")),
        ):
            resp = client.post("/api/admin/payouts/close-period", json={"year": 2026, "month": 5})
        assert resp.status_code == 200
        body = resp.json()
        assert body["period"] == "2026-05"
        assert body["payout_count"] == 2
        assert body["total_amount"] == 150.50
        assert body["audit_log_id"] == "audit-99"

    def test_close_period_invalid_month_422(self, client, as_finance_admin):
        resp = client.post("/api/admin/payouts/close-period", json={"year": 2026, "month": 13})
        assert resp.status_code == 422

    def test_close_period_no_payouts_zero_amount(self, client, as_finance_admin):
        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("routes.admin.rides.log_admin_action", AsyncMock(return_value="audit-100")),
        ):
            resp = client.post("/api/admin/payouts/close-period", json={"year": 2026, "month": 6})
        assert resp.status_code == 200
        assert resp.json()["total_amount"] == 0


class TestAdminRegenerateImportedSnapshots:
    """Ranked-blocker #18 (baseline #12): this endpoint previously wrote no
    audit_logs row for a bulk write (route_snapshot_url) across up to 500
    imported rides."""

    def test_regenerate_requires_super_admin_403(self, client, as_finance_admin):
        resp = client.post("/api/admin/rides/regenerate-imported-snapshots", json={})
        assert resp.status_code == 403

    def test_regenerate_no_rides_skips_audit(self, client, as_super_admin):
        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("routes.admin.rides.log_admin_action", AsyncMock(return_value="audit-1")) as audit_mock,
        ):
            resp = client.post("/api/admin/rides/regenerate-imported-snapshots", json={})
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
        # No rides to process -> the endpoint returns before reaching the
        # audit call; nothing happened, so nothing to log.
        audit_mock.assert_not_awaited()

    def test_regenerate_happy_path_audits_totals(self, client, as_super_admin):
        rides = [
            {
                "id": "ride-1",
                "pickup_lat": 50.4,
                "pickup_lng": -104.6,
                "dropoff_lat": 50.5,
                "dropoff_lng": -104.7,
                "planned_route_polyline": None,
            }
        ]
        fake_storage = MagicMock()
        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=rides)),
            patch("db_supabase.update_one", AsyncMock(return_value=None)),
            patch("utils.route_snapshot.render_ride_snapshot_google", AsyncMock(return_value=None)),
            patch("utils.route_snapshot.render_ride_snapshot", MagicMock(return_value=b"png-bytes")),
            patch("supabase_client.supabase", MagicMock(storage=fake_storage)),
            patch("routes.admin.rides.log_admin_action", AsyncMock(return_value="audit-2")) as audit_mock,
            patch("asyncio.sleep", AsyncMock()),
        ):
            resp = client.post("/api/admin/rides/regenerate-imported-snapshots", json={"limit": 10})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        audit_mock.assert_awaited_once()
        assert audit_mock.call_args[0][1] == "ride_snapshots_regenerated"
        assert audit_mock.call_args[0][4]["total"] == 1


# ---------------------------------------------------------------------------
# Read-only endpoints — lighter smoke coverage (happy path + one error path)
# ---------------------------------------------------------------------------


class TestAdminRidesReadEndpointsSmoke:
    def test_get_rides_happy_path(self, client, as_super_admin):
        with (
            patch("db_supabase.count_documents", AsyncMock(return_value=1)),
            patch("db_supabase.get_rows", AsyncMock(return_value=[_RIDE_SEARCHING])),
            patch("routes.admin.drivers._batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
        ):
            resp = client.get("/api/admin/rides")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_count"] == 1
        assert len(body["rides"]) == 1

    def test_get_rides_with_search_filters(self, client, as_super_admin):
        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("db_supabase.count_documents", AsyncMock(return_value=0)),
        ):
            resp = client.get("/api/admin/rides", params={"search": "smith", "status": "completed"})
        assert resp.status_code == 200

    def test_get_active_rides_db_failure_degrades_to_empty(self, client, as_super_admin):
        """active-rides swallows the DB error and returns an empty list (documented
        best-effort live-map behaviour) — verify it degrades gracefully rather than 500ing
        the live monitoring page."""
        with (
            patch("db_supabase.get_rows", AsyncMock(side_effect=RuntimeError("db down"))),
            patch("routes.admin.drivers._batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
        ):
            resp = client.get("/api/admin/rides/active")
        assert resp.status_code == 200
        assert resp.json()["rides"] == []

    def test_get_unpaid_rides_happy_path(self, client, as_super_admin):
        unpaid = {**_RIDE_SEARCHING, "status": "completed", "payment_status": "failed"}
        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=[unpaid])),
            patch("routes.admin.drivers._batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
        ):
            resp = client.get("/api/admin/rides/unpaid")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_get_ride_details_not_found_404(self, client, as_super_admin):
        with patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=None)):
            resp = client.get("/api/admin/rides/no-ride/details")
        assert resp.status_code == 404

    def test_get_ride_details_happy_path_non_card(self, client, as_super_admin):
        ride = {"id": "ride-1", "payment_method": "wallet"}
        with patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=ride)):
            resp = client.get("/api/admin/rides/ride-1/details")
        assert resp.status_code == 200
        assert resp.json()["card_last4"] is None

    def test_export_rides_defaults_last_24h(self, client, as_super_admin):
        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            resp = client.get("/api/admin/rides/export")
        assert resp.status_code == 200
        assert resp.json()["total_count"] == 0

    def test_ride_stats_happy_path(self, client, as_super_admin):
        with (
            patch("db_supabase.get_ride_count_by_date_range", AsyncMock(return_value=5)),
            patch(
                "db_supabase.rpc",
                AsyncMock(return_value=[{"sum_total_fare": "10.00", "sum_tip": "1.00", "completed_count": 2}]),
            ),
        ):
            resp = client.get("/api/admin/rides/stats")
        assert resp.status_code == 200
        assert resp.json()["today_count"] == 5

    def test_ride_trend_happy_path(self, client, as_super_admin):
        with patch("db_supabase.rpc", AsyncMock(return_value=[])):
            resp = client.get("/api/admin/rides/trend", params={"days": 7})
        assert resp.status_code == 200
        assert len(resp.json()["daily_chart"]) == 7

    def test_ride_financials_happy_path(self, client, as_super_admin):
        with (
            patch("db_supabase.get_ride_count_by_date_range", AsyncMock(return_value=3)),
            patch("db_supabase.rpc", AsyncMock(return_value=[{}])),
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            resp = client.get("/api/admin/rides/financials", params={"period": "today"})
        assert resp.status_code == 200
        assert resp.json()["period"] == "today"

    def test_ride_financials_invalid_period_422(self, client, as_super_admin):
        resp = client.get("/api/admin/rides/financials", params={"period": "century"})
        assert resp.status_code == 422

    def test_get_payouts_list_happy_path(self, client, as_super_admin):
        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("routes.admin.drivers._batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
        ):
            resp = client.get("/api/admin/payouts")
        assert resp.status_code == 200

    def test_get_single_payout_not_found_404(self, client, as_super_admin):
        with patch("db_supabase.find_one", AsyncMock(return_value=None)):
            resp = client.get("/api/admin/payouts/no-such")
        assert resp.status_code == 404

    def test_get_ride_location_trail_happy_path(self, client, as_super_admin):
        with patch("db_supabase.get_ride_location_trail", AsyncMock(return_value=[{"lat": 1, "lng": 2}])):
            resp = client.get("/api/admin/rides/ride-1/location-trail")
        assert resp.status_code == 200
        assert resp.json() == [{"lat": 1, "lng": 2}]

    def test_get_live_ride_not_found_404(self, client, as_super_admin):
        with patch("db_supabase.get_live_ride_data", AsyncMock(return_value=None)):
            resp = client.get("/api/admin/rides/no-ride/live")
        assert resp.status_code == 404

    def test_get_live_ride_happy_path(self, client, as_super_admin):
        with patch("db_supabase.get_live_ride_data", AsyncMock(return_value={"id": "ride-1", "driver_lat": 1})):
            resp = client.get("/api/admin/rides/ride-1/live")
        assert resp.status_code == 200

    def test_get_ride_invoice_not_found_404(self, client, as_super_admin):
        with patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=None)):
            resp = client.get("/api/admin/rides/no-ride/invoice")
        assert resp.status_code == 404

    def test_get_ride_invoice_happy_path_no_lock(self, client, as_super_admin):
        ride = {
            "id": "ride-1",
            "status": "completed",
            "total_fare": "12.00",
            "grand_total": "12.00",
        }
        with patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=ride)):
            resp = client.get("/api/admin/rides/ride-1/invoice")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ride_id"] == "ride-1"
        assert body["fare_locked"] is False
        assert body["grand_total"] == "12.00"

    def test_get_ride_invoice_fare_locked_uses_snapshot(self, client, as_super_admin):
        ride = {
            "id": "ride-1",
            "status": "completed",
            "fare_breakdown_snapshot": {"lines": [{"label": "Base", "amount": "5.00"}], "grand_total": "5.00"},
        }
        with (
            patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=ride)),
            patch("routes.admin.rides.get_app_settings", AsyncMock(return_value={"fare_lock_enabled": True})),
        ):
            resp = client.get("/api/admin/rides/ride-1/invoice")
        assert resp.status_code == 200
        body = resp.json()
        assert body["fare_locked"] is True
        assert body["grand_total"] == "5.00"

    def test_send_receipt_ride_not_found_404(self, client, as_super_admin):
        with patch("db_supabase.get_ride", AsyncMock(return_value=None)):
            resp = client.post("/api/admin/rides/no-ride/send-receipt")
        assert resp.status_code == 404

    def test_send_receipt_no_rider_email_no_override_422(self, client, as_super_admin):
        ride = {"id": "ride-1", "rider_id": "usr-1", "tip_amount": "0"}
        with (
            patch("db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("db_supabase.get_user_by_id", AsyncMock(return_value={"id": "usr-1", "email": ""})),
        ):
            resp = client.post("/api/admin/rides/ride-1/send-receipt")
        assert resp.status_code == 422

    def test_send_receipt_provider_failure_502(self, client, as_super_admin):
        ride = {"id": "ride-1", "rider_id": "usr-1", "tip_amount": "0"}
        with (
            patch("db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("db_supabase.get_user_by_id", AsyncMock(return_value={"id": "usr-1", "email": "r@example.com"})),
            patch("services.payment_service.send_ride_receipt", AsyncMock(return_value=False), create=True),
            patch("routes.admin.rides.log_admin_action", AsyncMock(return_value="audit-1")),
        ):
            resp = client.post("/api/admin/rides/ride-1/send-receipt")
        assert resp.status_code == 502

    def test_send_receipt_happy_path_with_override_email(self, client, as_super_admin):
        ride = {"id": "ride-1", "rider_id": "usr-1", "tip_amount": "1.00"}
        with (
            patch("db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("db_supabase.get_user_by_id", AsyncMock(return_value={"id": "usr-1", "email": ""})),
            patch("services.payment_service.send_ride_receipt", AsyncMock(return_value=True), create=True),
            patch("routes.admin.rides.log_admin_action", AsyncMock(return_value="audit-1")),
        ):
            resp = client.post("/api/admin/rides/ride-1/send-receipt", json={"email": "override@example.com"})
        assert resp.status_code == 200
        assert resp.json() == {"sent": True, "ride_id": "ride-1"}

    def test_get_heatmap_data_happy_path(self, client, as_super_admin):
        rows = [
            {
                "pickup_lat": 50.4,
                "pickup_lng": -104.6,
                "dropoff_lat": 50.5,
                "dropoff_lng": -104.5,
                "corporate_account_id": "corp-1",
            },
            {
                "pickup_lat": None,
                "pickup_lng": None,
                "dropoff_lat": None,
                "dropoff_lng": None,
                "corporate_account_id": None,
            },
        ]
        with patch("db_supabase.get_rows", AsyncMock(return_value=rows)):
            resp = client.get(
                "/api/admin/rides/heatmap-data",
                params={"filter": "corporate", "start_date": "2026-01-01", "end_date": "2026-01-31"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["stats"]["total_rides"] == 2
        assert body["stats"]["corporate_rides"] == 1
        assert len(body["pickup_points"]) == 1

    def test_get_earnings_happy_path(self, client, as_super_admin):
        rollup = [
            {
                "sum_total_fare": "100.00",
                "completed_count": 4,
                "sum_driver_earnings": "80.00",
                "sum_admin_earnings": "20.00",
            }
        ]
        with patch("db_supabase.rpc", AsyncMock(return_value=rollup)):
            resp = client.get("/api/admin/earnings", params={"period": "week"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_revenue"] == 100.0
        assert body["total_rides"] == 4

    def test_get_earnings_rides_happy_path(self, client, as_super_admin):
        rides = [
            {
                "id": "ride-1",
                "ride_code": "R1",
                "status": "completed",
                "total_fare": "10.00",
                "driver_earnings": "8.00",
                "admin_earnings": "2.00",
                "tip_amount": "1.00",
                "tax_amount": "0.50",
                "discount_amount": "0",
                "surge_multiplier": "1",
                "driver_id": "drv-1",
                "rider_id": "usr-1",
            }
        ]
        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=rides)),
            patch("routes.admin.drivers._batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
        ):
            resp = client.get("/api/admin/earnings/rides")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["rides"][0]["ride_id"] == "ride-1"

    def test_get_earnings_overview_happy_path(self, client, as_super_admin):
        with (
            patch("db_supabase.rpc", AsyncMock(return_value=[])),
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            resp = client.get("/api/admin/earnings/overview", params={"period": "30d"})
        assert resp.status_code == 200
        assert resp.json()["period"]["key"] == "30d"

    def test_get_earnings_overview_invalid_period_422(self, client, as_super_admin):
        resp = client.get("/api/admin/earnings/overview", params={"period": "decade"})
        assert resp.status_code == 422

    def test_get_earnings_overview_daily_series_uses_round_half_up(self, client, as_super_admin):
        """N15 regression: daily_series gbv/net_revenue must use the codebase's
        ROUND_HALF_UP convention (`to_decimal`), not Python's bare `round()`
        (which defaults to banker's rounding, ROUND_HALF_EVEN, for Decimal
        input). "10.125" is a deliberate .5-boundary value where the two
        conventions diverge: round(Decimal("10.125"), 2) == 10.12 (HALF_EVEN),
        while ROUND_HALF_UP quantize gives 10.13 — this pins the correct one.
        """
        today_key = datetime.now(timezone.utc).date().isoformat()

        async def _rpc(name, params=None, **kw):
            if name == "admin_earnings_daily_series":
                return [{"day": today_key, "gbv": "10.125", "trips": 1, "net_revenue": "10.125"}]
            return []

        with (
            patch("db_supabase.rpc", AsyncMock(side_effect=_rpc)),
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            resp = client.get("/api/admin/earnings/overview", params={"period": "7d"})
        assert resp.status_code == 200
        daily = {d["date"]: d for d in resp.json()["daily_series"]}
        assert daily[today_key]["gbv"] == 10.13
        assert daily[today_key]["net_revenue"] == 10.13

    def test_export_rides_happy_path(self, client, as_super_admin):
        rides = [
            {
                "id": "ride-1",
                "pickup_address": "A",
                "dropoff_address": "B",
                "total_fare": "10.00",
                "status": "completed",
                "created_at": "2026-01-01T00:00:00Z",
                "rider_id": "usr-1",
                "driver_id": "drv-1",
            }
        ]
        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=rides)),
            patch("routes.admin.drivers._batch_fetch_drivers_and_users", AsyncMock(return_value=({}, {}))),
            patch("db_supabase.insert_one", AsyncMock(return_value=None)) as mock_insert,
        ):
            resp = client.get("/api/admin/export/rides")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        # Audit log entry (F-41) must be written for every export.
        assert mock_insert.call_args[0][0] == "audit_logs"

    def test_export_drivers_happy_path_empty(self, client, as_super_admin):
        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("db_supabase.insert_one", AsyncMock(return_value=None)),
        ):
            resp = client.get("/api/admin/export/drivers")
        assert resp.status_code == 200
        assert resp.json() == {"drivers": [], "count": 0}

    def test_get_payouts_overview_happy_path_empty(self, client, as_super_admin):
        with (
            patch("db_supabase.rpc", AsyncMock(return_value=[])),
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            resp = client.get("/api/admin/payouts/overview", params={"period": "7d"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["period"]["key"] == "7d"
        assert body["metrics"]["payouts_count"]["current"] == 0

    def test_get_payouts_overview_service_area_no_drivers_returns_empty_shell(self, client, as_super_admin):
        with patch("db_supabase.get_rows", AsyncMock(return_value=[])):
            resp = client.get("/api/admin/payouts/overview", params={"service_area_id": "area-1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["metrics"]["outstanding_payable"]["current"] == 0.0
        assert body["daily_series"] == []

    def test_get_admin_stats_happy_path(self, client, as_super_admin):
        with (
            patch("db_supabase.count_documents", AsyncMock(return_value=1)),
            patch("db_supabase.rpc", AsyncMock(return_value=[{"sum_total_fare": "5.00"}])),
        ):
            resp = client.get("/api/admin/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_rides"] == 1

    def test_fare_estimate_happy_path(self, client, as_super_admin):
        with patch("routes.rides.fare_estimate", AsyncMock(return_value={"total": "10.00"}), create=True):
            resp = client.get(
                "/api/admin/rides/fare-estimate",
                params={
                    "pickup_lat": 50.4,
                    "pickup_lng": -104.6,
                    "dropoff_lat": 50.5,
                    "dropoff_lng": -104.5,
                    "distance_km": 5,
                    "duration_minutes": 10,
                    "vehicle_type_id": "vt-1",
                },
            )
        assert resp.status_code == 200

    def test_promo_preview_happy_path(self, client, as_super_admin):
        validation = {
            "code": "SAVE5",
            "discount_type": "fixed",
            "discount_amount": "5.00",
            "promo_id": "promo-1",
        }
        with patch("routes.promotions._validate_promo_for_user", AsyncMock(return_value=validation), create=True):
            resp = client.post(
                "/api/admin/promo/preview",
                json={"rider_id": "usr-1", "code": "SAVE5", "ride_fare": "20.00"},
            )
        assert resp.status_code == 200
        assert resp.json()["code"] == "SAVE5"

    def test_places_autocomplete_not_configured_503(self, client, as_super_admin):
        with patch("routes.admin.rides.get_app_settings", AsyncMock(return_value={})):
            resp = client.get("/api/admin/places/autocomplete", params={"input": "walm"})
        assert resp.status_code == 503

    def test_places_details_not_configured_503(self, client, as_super_admin):
        with patch("routes.admin.rides.get_app_settings", AsyncMock(return_value={})):
            resp = client.get("/api/admin/places/details", params={"place_id": "pid"})
        assert resp.status_code == 503

    def test_get_payout_stats_reaches_stats_handler_not_shadowed_by_payout_id(self, client, as_super_admin):
        """Regression test for issue #3068: `GET /payouts/stats` was being
        captured by `GET /payouts/{payout_id}` (payout_id="stats") because the
        latter was registered first, 404ing instead of ever reaching
        admin_get_payout_stats. Fixed by moving admin_get_payout_stats's route
        registration above admin_get_payout's, matching the code comment's
        stated invariant. This test now asserts the stats route is actually
        reached (200, correct response shape) rather than shadowed."""
        with patch("routes.admin.rides.db.rpc", AsyncMock(return_value=[])):
            resp = client.get("/api/admin/payouts/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {
            "total_paid",
            "total_pending",
            "total_failed",
            "payout_count",
            "pending_count",
        }

    def test_get_payout_stats_uses_round_half_up(self, client, as_super_admin):
        """Regression test for ACTION_ITEMS G8/A40's N15 fast-follow: the local
        `_d()` helper in admin_get_payout_stats used bare `round()` on a Decimal,
        which defaults to ROUND_HALF_EVEN and can disagree with the codebase's
        ROUND_HALF_UP convention on an exact .5-boundary value. Mirrors
        test_dispute_stats_total_refunded_uses_round_half_up's ".125" case from
        docs/change-log/2026-08-19-admin-decimal-round-convention-fix.md."""
        row = [
            {"total_paid": "10.125", "total_pending": "0", "total_failed": "0", "payout_count": 1, "pending_count": 0}
        ]
        with patch("routes.admin.rides.db.rpc", AsyncMock(return_value=row)):
            resp = client.get("/api/admin/payouts/stats")
        assert resp.status_code == 200
        # ROUND_HALF_UP: 10.125 -> 10.13 (banker's rounding would give 10.12).
        assert resp.json()["total_paid"] == 10.13
