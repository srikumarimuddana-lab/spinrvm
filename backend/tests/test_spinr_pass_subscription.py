"""Tests for Spinr Pass subscription flow (Checkout Session payment).

Covers:
- subscribe_to_plan: creates Checkout Session with correct metadata
- checkout.session.completed webhook: activates pending subscription
- verify-session: polls Checkout Session status and activates on payment
- Dev mode: instant activation when Stripe not configured
"""

import uuid as uuid_module
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture
def mock_driver():
    return {
        "id": "driver-123",
        "user_id": "user-123",
        "service_area_id": "area-1",
    }


@pytest.fixture
def mock_plan():
    return {
        "id": "plan-premium",
        "name": "Premium Pass",
        "price": 49.99,
        "duration_days": 30,
        "rides_per_day": -1,
        "description": "Unlimited rides",
        "is_active": True,
        "subscriber_count": 10,
    }


@pytest.fixture
def mock_user():
    return {
        "id": "user-123",
        "email": "driver@example.com",
        "stripe_customer_id": "cus_test123",
    }


@pytest.fixture
def mock_settings():
    return {
        "stripe_secret_key": "sk_test_123abc",
        "stripe_publishable_key": "pk_test_123abc",
        "base_url": "https://api.spinr.ca",
    }


@pytest.fixture
def mock_settings_no_stripe():
    return {
        "base_url": "https://api.spinr.ca",
    }


class TestSubscriptionCheckoutFlow:
    """Test Stripe Checkout Session creation for subscriptions."""

    async def test_subscribe_creates_checkout_session(self, mock_driver, mock_plan, mock_user, mock_settings):
        """subscribe_to_plan creates a Checkout Session with correct metadata."""
        from fastapi import Request

        from backend.routes.drivers import subscribe_to_plan

        request = AsyncMock(spec=Request)
        request.json = AsyncMock(return_value={"plan_id": "plan-premium"})

        current_user = {"id": "user-123"}
        sub_id = str(uuid_module.uuid4())

        with (
            patch("backend.db_supabase.get_rows") as mock_get_rows,
            patch("backend.db_supabase.update_one") as mock_update,
            patch("backend.db_supabase.insert_one") as mock_insert,
            patch("backend.settings_loader.get_app_settings") as mock_get_settings,
            patch("stripe.checkout.Session.create") as mock_session_create,
            patch("uuid.uuid4", return_value=sub_id),
        ):
            # Setup mocks
            mock_get_rows.side_effect = lambda table, filters, limit=None: {
                "drivers": [mock_driver],
                "service_areas": [{"spinr_pass_enabled": True}],
                "subscription_plans": [mock_plan],
                "driver_subscriptions": [],
            }.get(table, [])

            mock_get_settings.return_value = mock_settings
            mock_checkout_session = MagicMock()
            mock_checkout_session.url = "https://checkout.stripe.com/pay/session123"
            mock_checkout_session.id = "cs_test_session123"
            mock_session_create.return_value = mock_checkout_session

            # Execute
            result = await subscribe_to_plan(request, current_user)

            # Verify Checkout Session was created with correct metadata
            mock_session_create.assert_called_once()
            call_kwargs = mock_session_create.call_args[1]
            assert call_kwargs["mode"] == "payment"
            assert call_kwargs["metadata"]["subscription_id"] == str(sub_id)
            assert call_kwargs["metadata"]["driver_id"] == "driver-123"
            assert call_kwargs["metadata"]["plan_id"] == "plan-premium"
            assert call_kwargs["line_items"][0]["price_data"]["unit_amount"] == 4999

            # Verify response includes checkout URL
            assert result["success"] is True
            assert result["checkout_url"] == "https://checkout.stripe.com/pay/session123"
            assert result["subscription_id"] == str(sub_id)

            # Verify subscription row was inserted with pending status
            mock_insert.assert_called_once()
            inserted_sub = mock_insert.call_args[0][1]
            assert inserted_sub["status"] == "pending"
            assert inserted_sub["payment_status"] == "pending"
            assert inserted_sub["stripe_session_id"] == "cs_test_session123"

    async def test_subscribe_dev_mode_no_stripe(self, mock_driver, mock_plan, mock_settings_no_stripe):
        """subscribe_to_plan activates immediately when Stripe not configured (dev mode)."""
        from fastapi import Request

        from backend.routes.drivers import subscribe_to_plan

        request = AsyncMock(spec=Request)
        request.json = AsyncMock(return_value={"plan_id": "plan-premium"})

        current_user = {"id": "user-123"}

        with (
            patch("backend.db_supabase.get_rows") as mock_get_rows,
            patch("backend.db_supabase.update_one"),
            patch("backend.db_supabase.insert_one") as mock_insert,
            patch("backend.settings_loader.get_app_settings") as mock_get_settings,
        ):
            mock_get_rows.side_effect = lambda table, filters, limit=None: {
                "drivers": [mock_driver],
                "service_areas": [{"spinr_pass_enabled": True}],
                "subscription_plans": [mock_plan],
                "driver_subscriptions": [],
            }.get(table, [])

            mock_get_settings.return_value = mock_settings_no_stripe

            # Execute
            result = await subscribe_to_plan(request, current_user)

            # Verify subscription is active immediately (dev mode)
            assert result["success"] is True
            assert result["mode"] == "dev"
            assert result["subscription"]["status"] == "active"
            assert result["subscription"]["payment_status"] == "paid"

            # Verify no checkout URL returned
            assert "checkout_url" not in result

    async def test_subscribe_spinr_pass_disabled(self, mock_driver, mock_plan, mock_settings):
        """subscribe_to_plan rejects subscription when Spinr Pass is disabled for area."""
        from fastapi import HTTPException, Request

        from backend.routes.drivers import subscribe_to_plan

        request = AsyncMock(spec=Request)
        request.json = AsyncMock(return_value={"plan_id": "plan-premium"})

        current_user = {"id": "user-123"}

        with patch("backend.db_supabase.get_rows") as mock_get_rows, patch("backend.settings_loader.get_app_settings"):
            mock_get_rows.side_effect = lambda table, filters, limit=None: {
                "drivers": [mock_driver],
                "service_areas": [{"spinr_pass_enabled": False}],
            }.get(table, [])

            # Execute and expect 403
            with pytest.raises(HTTPException) as exc_info:
                await subscribe_to_plan(request, current_user)

            assert exc_info.value.status_code == 403


class TestWebhookActivation:
    """Test checkout.session.completed webhook activates subscription."""

    async def test_webhook_activates_subscription(self):
        """checkout.session.completed webhook activates pending subscription."""
        from fastapi import Request

        from backend.routes.webhooks import stripe_webhook

        webhook_event = {
            "id": "evt_test123",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_session456",
                    "payment_status": "paid",
                    "metadata": {
                        "subscription_id": "sub-id-123",
                        "plan_id": "plan-premium",
                        "driver_id": "driver-456",
                    },
                }
            },
        }

        request = AsyncMock(spec=Request)
        request.body = AsyncMock(return_value=b'{"dummy": "event"}')
        request.headers = {"stripe-signature": "sig_test"}

        with (
            patch("backend.settings_loader.get_app_settings") as mock_get_settings,
            patch("stripe.Webhook.construct_event") as mock_construct,
            patch("backend.db_supabase.claim_stripe_event") as mock_claim,
            patch("backend.db_supabase.mark_stripe_event_processed") as mock_mark,
            patch("backend.routes.drivers._activate_subscription") as mock_activate,
        ):
            mock_get_settings.return_value = {
                "stripe_webhook_secret": "whsec_test123",
                "stripe_secret_key": "sk_test_123",
            }
            mock_construct.return_value = webhook_event
            mock_claim.return_value = True  # First time seeing this event

            # Execute
            result = await stripe_webhook(request)

            # Verify subscription was activated
            mock_activate.assert_called_once_with("sub-id-123", "plan-premium")
            mock_mark.assert_called_once_with("evt_test123")
            assert result["received"] is True


class TestVerifySession:
    """Test verify-session endpoint polls, authorizes, and activates on payment."""

    async def test_verify_session_already_active(self):
        """verify-session returns early if subscription already active."""
        from backend.routes.drivers import verify_subscription_session

        current_user = {"id": "user-123"}

        with (
            patch("backend.db_supabase.find_one") as mock_find,
            patch(
                "backend.db_supabase.get_rows",
                AsyncMock(return_value=[{"id": "driver-1"}]),
            ),
        ):
            mock_find.return_value = {
                "id": "sub-123",
                "driver_id": "driver-1",
                "status": "active",
                "payment_status": "paid",
            }

            result = await verify_subscription_session("cs_test_123", current_user)

            assert result["status"] == "active"

    async def test_verify_session_rejects_non_owner(self):
        """A driver cannot verify a session belonging to a different driver."""
        from fastapi import HTTPException

        from backend.routes.drivers import verify_subscription_session

        current_user = {"id": "user-123"}

        with (
            patch(
                "backend.db_supabase.find_one",
                AsyncMock(return_value={"id": "sub-123", "driver_id": "driver-OTHER"}),
            ),
            patch(
                "backend.db_supabase.get_rows",
                AsyncMock(return_value=[{"id": "driver-1"}]),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await verify_subscription_session("cs_test_123", current_user)

        # 404 (not 403) so the session id is not an existence oracle.
        assert exc.value.status_code == 404

    async def test_verify_session_persists_subscription_id(self):
        """On paid, verify-session copies session.subscription onto the row so
        renewal/cancellation webhooks can match it (B4 fallback)."""
        from backend.routes.drivers import verify_subscription_session

        current_user = {"id": "user-123"}
        mock_sub = {
            "id": "sub-123",
            "driver_id": "driver-1",
            "plan_id": "plan-premium",
            "status": "pending",
            "payment_status": "pending",
        }

        mock_session = MagicMock()
        mock_session.payment_status = "paid"
        mock_session.get = lambda k, d=None: "sub_stripe_99" if k == "subscription" else d

        update_mock = AsyncMock()

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=mock_sub)),
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[{"id": "driver-1"}])),
            patch("backend.db_supabase.update_one", update_mock),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test_123"}),
            ),
            patch("stripe.checkout.Session.retrieve", return_value=mock_session),
            patch("backend.routes.drivers._activate_subscription", AsyncMock()),
        ):
            result = await verify_subscription_session("cs_test_123", current_user)

        assert result["status"] == "active"
        # stripe_subscription_id persisted onto the row.
        persisted = [c for c in update_mock.await_args_list if c.args and c.args[0] == "driver_subscriptions"]
        assert any(c.args[2].get("stripe_subscription_id") == "sub_stripe_99" for c in persisted)

    async def test_verify_session_pending_payment(self):
        """verify-session returns pending if Stripe session not yet paid."""
        from backend.routes.drivers import verify_subscription_session

        current_user = {"id": "user-123"}

        mock_sub = {
            "id": "sub-123",
            "driver_id": "driver-1",
            "status": "pending",
            "payment_status": "pending",
        }

        mock_session = MagicMock()
        mock_session.payment_status = "unpaid"

        with (
            patch("backend.db_supabase.find_one", AsyncMock(return_value=mock_sub)),
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[{"id": "driver-1"}])),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test_123"}),
            ),
            patch("stripe.checkout.Session.retrieve", return_value=mock_session),
        ):
            result = await verify_subscription_session("cs_test_123", current_user)

            assert result["status"] == "pending"


class TestRecurringSubscription:
    """B3/B5: plans with a stripe_price_id use mode=subscription Checkout, and
    cancelling stops Stripe billing (not just the DB row)."""

    async def test_subscribe_recurring_uses_subscription_mode(self, mock_driver, mock_settings):
        from fastapi import Request

        from backend.routes.drivers import subscribe_to_plan

        recurring_plan = {
            "id": "plan-recurring",
            "name": "Pro Pass",
            "price": 49.99,
            "duration_days": 30,
            "rides_per_day": -1,
            "description": "Auto-renew",
            "is_active": True,
            "subscriber_count": 0,
            "stripe_price_id": "price_abc123",
        }

        request = AsyncMock(spec=Request)
        request.json = AsyncMock(return_value={"plan_id": "plan-recurring"})
        current_user = {"id": "user-123"}

        with (
            patch("backend.db_supabase.get_rows") as mock_get_rows,
            patch("backend.db_supabase.update_one"),
            patch("backend.db_supabase.insert_one"),
            patch("backend.settings_loader.get_app_settings") as mock_get_settings,
            patch("stripe.checkout.Session.create") as mock_session_create,
        ):
            mock_get_rows.side_effect = lambda table, filters, limit=None: {
                "drivers": [mock_driver],
                "service_areas": [{"spinr_pass_enabled": True}],
                "subscription_plans": [recurring_plan],
                "driver_subscriptions": [],
            }.get(table, [])
            mock_get_settings.return_value = mock_settings

            session = MagicMock()
            session.url = "https://checkout.stripe.com/pay/recurring"
            session.id = "cs_recurring_1"
            mock_session_create.return_value = session

            result = await subscribe_to_plan(request, current_user)

        call_kwargs = mock_session_create.call_args[1]
        assert call_kwargs["mode"] == "subscription"
        assert call_kwargs["line_items"][0]["price"] == "price_abc123"
        # ids mirrored onto the Stripe Subscription for renewal-event matching
        assert call_kwargs["subscription_data"]["metadata"]["plan_id"] == "plan-recurring"
        assert result["checkout_url"] == "https://checkout.stripe.com/pay/recurring"


class TestCancelStripeSubscription:
    """_cancel_stripe_subscription stops recurring billing; no-ops otherwise."""

    async def test_cancels_when_configured(self):
        from backend.routes.drivers import _cancel_stripe_subscription

        with (
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
            ),
            patch("stripe.Subscription.delete") as del_mock,
        ):
            await _cancel_stripe_subscription("sub_123")

        del_mock.assert_called_once()
        assert del_mock.call_args[0][0] == "sub_123"

    async def test_noop_without_subscription_id(self):
        from backend.routes.drivers import _cancel_stripe_subscription

        with patch("stripe.Subscription.delete") as del_mock:
            await _cancel_stripe_subscription(None)

        del_mock.assert_not_called()

    async def test_noop_without_stripe_secret(self):
        from backend.routes.drivers import _cancel_stripe_subscription

        with (
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={}),
            ),
            patch("stripe.Subscription.delete") as del_mock,
        ):
            await _cancel_stripe_subscription("sub_123")

        del_mock.assert_not_called()

    async def test_stripe_failure_does_not_raise(self):
        from backend.routes.drivers import _cancel_stripe_subscription

        with (
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
            ),
            patch("stripe.Subscription.delete", side_effect=Exception("stripe down")),
        ):
            # Best-effort: must swallow so the DB-side cancel still proceeds.
            await _cancel_stripe_subscription("sub_123")


class TestCancelSubscriptionEndpoint:
    """/subscription/cancel must not mark the row cancelled if the Stripe
    cancellation fails (comment 7) — otherwise the app shows 'cancelled'
    while Stripe keeps billing."""

    @staticmethod
    def _rows(table, filters, limit=None):
        return {
            "drivers": [{"id": "driver-1"}],
            "driver_subscriptions": [{"id": "sub-1", "stripe_subscription_id": "sub_stripe_1"}],
        }.get(table, [])

    async def test_cancel_502_when_stripe_fails_row_not_cancelled(self):
        from fastapi import HTTPException

        from backend.routes.drivers import cancel_subscription

        with (
            patch("backend.db_supabase.get_rows") as mock_get_rows,
            patch("backend.db_supabase.update_one") as update_mock,
            patch("backend.routes.drivers._cancel_stripe_subscription") as cancel_mock,
        ):
            mock_get_rows.side_effect = self._rows
            cancel_mock.side_effect = Exception("stripe down")

            with pytest.raises(HTTPException) as exc:
                await cancel_subscription({"id": "user-123"})

        assert exc.value.status_code == 502
        update_mock.assert_not_awaited()

    async def test_cancel_succeeds_marks_row_cancelled(self):
        from backend.routes.drivers import cancel_subscription

        with (
            patch("backend.db_supabase.get_rows") as mock_get_rows,
            patch("backend.db_supabase.update_one") as update_mock,
            patch("backend.routes.drivers._cancel_stripe_subscription", AsyncMock()),
        ):
            mock_get_rows.side_effect = self._rows

            result = await cancel_subscription({"id": "user-123"})

        assert result["success"] is True
        update_mock.assert_awaited_once()
        assert "cancelled_at" in update_mock.await_args.args[2]
