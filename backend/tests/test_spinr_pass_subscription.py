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
            mock_get_rows.side_effect = lambda table, filters, columns=None, limit=None: {
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
            # session_id returned so the app can poll verify-session as a fallback
            assert result["session_id"] == "cs_test_session123"

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
            mock_get_rows.side_effect = lambda table, filters, columns=None, limit=None: {
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
            mock_get_rows.side_effect = lambda table, filters, columns=None, limit=None: {
                "drivers": [mock_driver],
                "service_areas": [{"spinr_pass_enabled": False}],
            }.get(table, [])

            # Execute and expect 403
            with pytest.raises(HTTPException) as exc_info:
                await subscribe_to_plan(request, current_user)

            assert exc_info.value.status_code == 403


class TestWebhookActivation:
    """Test checkout.session.completed webhook activates subscription."""

    def _webhook_event(self, with_subscription="sub_stripe_x"):
        obj = {
            "id": "cs_test_session456",
            "payment_status": "paid",
            "metadata": {
                "subscription_id": "sub-id-123",
                "plan_id": "plan-premium",
                "driver_id": "driver-456",
            },
        }
        if with_subscription:
            obj["subscription"] = with_subscription
        return {"id": "evt_test123", "type": "checkout.session.completed", "data": {"object": obj}}

    async def _run(self, find_return, activate_mock, update_mock, cancel_mock=None):
        from fastapi import Request

        from backend.routes.webhooks import stripe_webhook

        request = AsyncMock(spec=Request)
        request.body = AsyncMock(return_value=b'{"dummy": "event"}')
        request.headers = {"stripe-signature": "sig_test"}

        async def _settings():
            return {"stripe_webhook_secret": "whsec_test123", "stripe_secret_key": "sk_test_123"}

        patches = [
            patch("backend.routes.webhooks.get_app_settings", _settings),
            patch("stripe.Webhook.construct_event", return_value=self._webhook_event()),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.drivers.subscriptions._activate_subscription", activate_mock),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(return_value=find_return)),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
        ]
        if cancel_mock is not None:
            patches.append(patch("backend.routes.drivers.subscriptions._cancel_stripe_subscription", cancel_mock))
        import contextlib

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            return await stripe_webhook(request)

    async def test_webhook_activates_and_links_active_row(self):
        """checkout.session.completed activates a pending row and links the
        Stripe Subscription id once the row is active."""
        activate_mock = AsyncMock()
        update_mock = AsyncMock()
        result = await self._run(
            find_return={"id": "sub-id-123", "status": "active"},
            activate_mock=activate_mock,
            update_mock=update_mock,
        )
        activate_mock.assert_called_once_with("sub-id-123", "plan-premium", None)
        # stripe_subscription_id linked
        linked = [
            c
            for c in update_mock.await_args_list
            if c.args and c.args[2].get("stripe_subscription_id") == "sub_stripe_x"
        ]
        assert linked
        assert result["received"] is True

    async def test_webhook_superseded_row_not_linked_orphan_cancelled(self):
        """If the row was superseded before this stale session paid, don't link
        the id; cancel the orphaned Stripe subscription instead."""
        activate_mock = AsyncMock()
        update_mock = AsyncMock()
        cancel_mock = AsyncMock()
        result = await self._run(
            find_return={"id": "sub-id-123", "status": "superseded"},
            activate_mock=activate_mock,
            update_mock=update_mock,
            cancel_mock=cancel_mock,
        )
        # No stripe_subscription_id linked
        assert not [c for c in update_mock.await_args_list if c.args and c.args[2].get("stripe_subscription_id")]
        cancel_mock.assert_awaited_once_with("sub_stripe_x")
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
        # verify_subscription_session re-reads the row after activation to
        # decide whether to link/return "active" — a flat return_value would
        # make that re-read see the same stale "pending" row and fall through
        # to the "superseded" branch. Model the real before/after activation.
        mock_sub_after_activate = {**mock_sub, "status": "active", "payment_status": "paid"}

        with (
            patch("backend.db_supabase.find_one", AsyncMock(side_effect=[mock_sub, mock_sub_after_activate])),
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[{"id": "driver-1"}])),
            patch("backend.db_supabase.update_one", update_mock),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test_123"}),
            ),
            patch("stripe.checkout.Session.retrieve", return_value=mock_session),
            patch("backend.routes.drivers.subscriptions._activate_subscription", AsyncMock()),
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

        # Stripe Price matches the plan price (49.99 → 4999 cents, cad) and the
        # plan's 30-day cadence (monthly).
        price_obj = {"unit_amount": 4999, "currency": "cad", "recurring": {"interval": "month", "interval_count": 1}}

        with (
            patch("backend.db_supabase.get_rows") as mock_get_rows,
            patch("backend.db_supabase.update_one"),
            patch("backend.db_supabase.insert_one"),
            patch("backend.settings_loader.get_app_settings") as mock_get_settings,
            patch("stripe.Price.retrieve", return_value=price_obj),
            patch("stripe.checkout.Session.create") as mock_session_create,
        ):
            mock_get_rows.side_effect = lambda table, filters, columns=None, limit=None: {
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

    async def test_subscribe_recurring_price_mismatch_rejected(self, mock_driver, mock_plan, mock_settings):
        """A Stripe Price whose amount disagrees with the plan is refused (409)
        rather than silently charging a surprise amount."""
        from fastapi import HTTPException, Request

        from backend.routes.drivers import subscribe_to_plan

        recurring_plan = {**mock_plan, "id": "plan-recurring", "stripe_price_id": "price_wrong"}
        request = AsyncMock(spec=Request)
        request.json = AsyncMock(return_value={"plan_id": "plan-recurring"})

        # Plan is 49.99 (4999) but the Stripe Price says 9999.
        with (
            patch("backend.db_supabase.get_rows") as mock_get_rows,
            patch("backend.settings_loader.get_app_settings", AsyncMock(return_value=mock_settings)),
            patch("stripe.Price.retrieve", return_value={"unit_amount": 9999, "currency": "cad"}),
            patch("stripe.checkout.Session.create") as mock_session_create,
        ):
            mock_get_rows.side_effect = lambda table, filters, columns=None, limit=None: {
                "drivers": [mock_driver],
                "service_areas": [{"spinr_pass_enabled": True}],
                "subscription_plans": [recurring_plan],
                "driver_subscriptions": [],
            }.get(table, [])

            with pytest.raises(HTTPException) as exc:
                await subscribe_to_plan(request, {"id": "user-123"})

        assert exc.value.status_code == 409
        mock_session_create.assert_not_called()


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
            patch("backend.routes.drivers.subscriptions._cancel_stripe_subscription") as cancel_mock,
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
            patch("backend.routes.drivers.subscriptions._cancel_stripe_subscription", AsyncMock()),
        ):
            mock_get_rows.side_effect = self._rows

            result = await cancel_subscription({"id": "user-123"})

        assert result["success"] is True
        update_mock.assert_awaited_once()
        assert "cancelled_at" in update_mock.await_args.args[2]


class TestActivationAndSuperseding:
    """P2 follow-ups: only pending rows activate, and starting a new checkout
    supersedes older pending sessions so a stale one can't activate late."""

    async def test_activate_skips_non_pending_row(self):
        from backend.routes.drivers import _activate_subscription

        update_mock = AsyncMock()
        with (
            patch(
                "backend.db_supabase.find_one",
                AsyncMock(return_value={"id": "s1", "status": "superseded", "driver_id": "d1"}),
            ),
            patch("backend.db_supabase.update_one", update_mock),
        ):
            await _activate_subscription("s1", "plan-1")

        update_mock.assert_not_awaited()

    async def test_subscribe_supersedes_pending_sessions(self, mock_driver, mock_plan, mock_settings):
        from fastapi import Request

        from backend.routes.drivers import subscribe_to_plan

        request = AsyncMock(spec=Request)
        request.json = AsyncMock(return_value={"plan_id": "plan-premium"})

        def _rows(table, filters, columns=None, limit=None):
            if table == "drivers":
                return [mock_driver]
            if table == "service_areas":
                return [{"spinr_pass_enabled": True}]
            if table == "subscription_plans":
                return [mock_plan]
            if table == "driver_subscriptions":
                # No existing active sub; one stale pending row to supersede.
                if filters.get("status") == "pending":
                    return [{"id": "stale-1", "stripe_session_id": "cs_stale"}]
                return []
            return []

        update_mock = AsyncMock()
        expire_mock = MagicMock()
        with (
            patch("backend.db_supabase.get_rows") as mock_get_rows,
            patch("backend.db_supabase.update_one", update_mock),
            patch("backend.db_supabase.insert_one", AsyncMock()),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value=mock_settings),
            ),
            patch("stripe.checkout.Session.create") as mock_session_create,
            patch("stripe.checkout.Session.expire", expire_mock),
        ):
            mock_get_rows.side_effect = _rows
            session = MagicMock()
            session.url = "https://checkout.stripe.com/pay/x"
            session.id = "cs_new"
            mock_session_create.return_value = session

            result = await subscribe_to_plan(request, {"id": "user-123"})

        superseded = [
            c
            for c in update_mock.await_args_list
            if c.args and c.args[0] == "driver_subscriptions" and c.args[2].get("status") == "superseded"
        ]
        assert superseded, "stale pending row should be superseded"
        assert superseded[0].args[1] == {"id": "stale-1"}
        # the stale Stripe Checkout Session is expired so it can't be paid later
        expire_mock.assert_called_once()
        assert expire_mock.call_args[0][0] == "cs_stale"
        assert result.get("checkout_url")


class TestActivationPeriodAndVerifySuperseded:
    """P2 follow-ups: activation recomputes the period; verify-session refuses
    to activate/link a superseded row and cancels the orphan Stripe sub."""

    async def test_activate_recomputes_period_for_pending_row(self):
        from backend.routes import drivers as drv

        update_mock = AsyncMock()
        # find_one order: pending sub, plan, driver (none, no push), then
        # _compute_subscription_tax's own drivers lookup (none -> no
        # service_area_id -> tax computation short-circuits to zero/SK
        # without a service_areas lookup). The prior-active lookup is now a
        # get_rows (patched to no prior active).
        find_mock = AsyncMock(
            side_effect=[
                {"id": "s1", "status": "pending", "driver_id": "d1"},
                {"id": "plan-1", "duration_days": 7, "subscriber_count": 0},
                None,
                None,
            ]
        )
        with (
            patch("backend.db_supabase.find_one", find_mock),
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.db_supabase.update_one", update_mock),
        ):
            await drv._activate_subscription("s1", "plan-1")

        # The activation is an atomic claim filtered on status='pending'.
        activates = [
            c
            for c in update_mock.await_args_list
            if c.args and c.args[0] == "driver_subscriptions" and c.args[1] == {"id": "s1", "status": "pending"}
        ]
        assert activates
        payload = activates[0].args[2]["$set"]
        assert payload["status"] == "active"
        assert "started_at" in payload
        assert "expires_at" in payload

    async def test_verify_session_superseded_returns_superseded(self):
        from backend.routes.drivers import verify_subscription_session

        mock_sub = {
            "id": "sub-123",
            "driver_id": "driver-1",
            "plan_id": "p1",
            "status": "pending",
            "payment_status": "pending",
        }
        find_mock = AsyncMock(
            side_effect=[
                mock_sub,
                {"id": "sub-123", "status": "superseded", "driver_id": "driver-1"},
            ]
        )
        session = MagicMock()
        session.payment_status = "paid"
        session.get = lambda k, d=None: "sub_y" if k == "subscription" else d
        cancel_mock = AsyncMock()

        with (
            patch("backend.db_supabase.find_one", find_mock),
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[{"id": "driver-1"}])),
            patch("backend.db_supabase.update_one", AsyncMock()),
            patch("backend.settings_loader.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk"})),
            patch("stripe.checkout.Session.retrieve", return_value=session),
            patch("backend.routes.drivers.subscriptions._activate_subscription", AsyncMock()),
            patch("backend.routes.drivers.subscriptions._cancel_stripe_subscription", cancel_mock),
        ):
            result = await verify_subscription_session("cs_x", {"id": "user-123"})

        assert result["status"] == "superseded"
        cancel_mock.assert_awaited_once_with("sub_y")


class TestActivationAtomicClaim:
    """P2 follow-up: activation is an atomic pending→active claim; the loser of
    a webhook/verify race runs no side effects."""

    async def test_activate_lost_race_skips_side_effects(self):
        from backend.routes import drivers as drv

        # The claim update_one returns None → another caller already flipped it.
        update_mock = AsyncMock(return_value=None)
        find_mock = AsyncMock(
            side_effect=[
                {"id": "s1", "status": "pending", "driver_id": "d1"},
                {"id": "plan-1", "duration_days": 7, "subscriber_count": 0},
                # Driver row fetched before the claim to resolve the expiry
                # timezone (no service_area_id → no service_areas lookup).
                {"id": "d1"},
            ]
        )
        push_mock = AsyncMock()
        with (
            patch("backend.db_supabase.find_one", find_mock),
            patch("backend.db_supabase.update_one", update_mock),
            patch("backend.routes.drivers._deps.send_push_notification", push_mock),
        ):
            await drv._activate_subscription("s1", "plan-1")

        # Only the claim ran — no count increment, no push.
        assert update_mock.await_count == 1
        push_mock.assert_not_awaited()


class TestSubscriptionPaymentsLedger:
    """subscription_payments ledger (migration 151) records realized revenue."""

    async def test_records_payment_when_amount_positive(self):
        from backend.routes import drivers as drv

        insert_mock = AsyncMock()
        with patch("backend.db_supabase.insert_one", insert_mock):
            await drv._record_subscription_payment(
                driver_id="d1",
                subscription_id="s1",
                plan_id="p1",
                plan_name="Pro",
                amount=49.99,
                billing_reason="one_off",
            )
        insert_mock.assert_awaited_once()
        table, row = insert_mock.await_args.args[0], insert_mock.await_args.args[1]
        assert table == "subscription_payments"
        assert row["amount"] == "49.99"
        assert row["billing_reason"] == "one_off"
        assert row["driver_id"] == "d1"

    async def test_skips_zero_amount(self):
        from backend.routes import drivers as drv

        insert_mock = AsyncMock()
        with patch("backend.db_supabase.insert_one", insert_mock):
            await drv._record_subscription_payment(
                driver_id="d1",
                subscription_id="s1",
                plan_id="p1",
                plan_name="Free",
                amount=0,
                billing_reason="dev",
            )
        insert_mock.assert_not_awaited()

    async def test_activate_records_oneoff_payment(self):
        from backend.routes import drivers as drv

        record_mock = AsyncMock()
        find_mock = AsyncMock(
            side_effect=[
                {"id": "s1", "status": "pending", "driver_id": "d1", "plan_name": "Pro", "stripe_session_id": "cs1"},
                {"id": "p1", "duration_days": 30, "price": 49.99, "subscriber_count": 0},
                None,
                # _compute_subscription_tax's own drivers lookup: no
                # service_area_id -> short-circuits to zero tax/SK.
                None,
            ]
        )
        with (
            patch("backend.db_supabase.find_one", find_mock),
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.db_supabase.update_one", AsyncMock()),
            patch("backend.routes.drivers.subscriptions._record_subscription_payment", record_mock),
        ):
            await drv._activate_subscription("s1", "p1")
        record_mock.assert_awaited_once()
        assert record_mock.await_args.kwargs["billing_reason"] == "one_off"

    async def test_activate_skips_ledger_for_recurring(self):
        from backend.routes import drivers as drv

        record_mock = AsyncMock()
        find_mock = AsyncMock(
            side_effect=[
                {"id": "s1", "status": "pending", "driver_id": "d1"},
                {"id": "p1", "duration_days": 30, "price": 49.99, "stripe_price_id": "price_x", "subscriber_count": 0},
                None,
            ]
        )
        with (
            patch("backend.db_supabase.find_one", find_mock),
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.db_supabase.update_one", AsyncMock()),
            patch("backend.routes.drivers.subscriptions._record_subscription_payment", record_mock),
        ):
            await drv._activate_subscription("s1", "p1")
        # Recurring is recorded by invoice.paid, not here.
        record_mock.assert_not_awaited()


class TestSubscribeConcurrencyConflict:
    """The partial unique index (migration 153) surfaces as a clean 409;
    a genuine insert failure surfaces as 503."""

    def _rows_factory(self, mock_driver, mock_plan, pending_after_conflict):
        # pending_after_conflict simulates whether the post-failure pending
        # re-query finds a row (concurrent winner exists → 409) or not (→ 503).
        def _rows(table, filters, columns=None, limit=None):
            if table == "driver_subscriptions":
                if filters.get("status") == "pending":
                    return [{"id": "other-pending"}] if pending_after_conflict else []
                return []
            return {
                "drivers": [mock_driver],
                "service_areas": [{"spinr_pass_enabled": True}],
                "subscription_plans": [mock_plan],
            }.get(table, [])

        return _rows

    async def test_subscribe_insert_conflict_returns_409(self, mock_driver, mock_plan, mock_settings):
        from fastapi import HTTPException, Request

        from backend.routes.drivers import subscribe_to_plan

        request = AsyncMock(spec=Request)
        request.json = AsyncMock(return_value={"plan_id": "plan-premium"})

        expire_mock = MagicMock()
        with (
            patch("backend.db_supabase.get_rows") as mock_get_rows,
            patch("backend.db_supabase.update_one", AsyncMock()),
            patch("backend.db_supabase.insert_one", AsyncMock(side_effect=Exception("unique violation"))),
            patch("backend.settings_loader.get_app_settings", AsyncMock(return_value=mock_settings)),
            patch("stripe.checkout.Session.create") as mock_session_create,
            patch("stripe.checkout.Session.expire", expire_mock),
        ):
            mock_get_rows.side_effect = self._rows_factory(mock_driver, mock_plan, pending_after_conflict=True)
            session = MagicMock()
            session.url = "https://checkout.stripe.com/pay/x"
            session.id = "cs_new"
            mock_session_create.return_value = session

            with pytest.raises(HTTPException) as exc:
                await subscribe_to_plan(request, {"id": "user-123"})

        assert exc.value.status_code == 409
        # the race-loser's Stripe session is expired so it can't be paid
        expire_mock.assert_called_once()
        assert expire_mock.call_args[0][0] == "cs_new"

    async def test_subscribe_insert_failure_without_conflict_returns_503(self, mock_driver, mock_plan, mock_settings):
        from fastapi import HTTPException, Request

        from backend.routes.drivers import subscribe_to_plan

        request = AsyncMock(spec=Request)
        request.json = AsyncMock(return_value={"plan_id": "plan-premium"})

        with (
            patch("backend.db_supabase.get_rows") as mock_get_rows,
            patch("backend.db_supabase.update_one", AsyncMock()),
            patch("backend.db_supabase.insert_one", AsyncMock(side_effect=Exception("network down"))),
            patch("backend.settings_loader.get_app_settings", AsyncMock(return_value=mock_settings)),
            patch("stripe.checkout.Session.create") as mock_session_create,
            patch("stripe.checkout.Session.expire", MagicMock()),
        ):
            # No pending row exists after the failure → not a concurrency conflict.
            mock_get_rows.side_effect = self._rows_factory(mock_driver, mock_plan, pending_after_conflict=False)
            session = MagicMock()
            session.url = "https://checkout.stripe.com/pay/x"
            session.id = "cs_new"
            mock_session_create.return_value = session

            with pytest.raises(HTTPException) as exc:
                await subscribe_to_plan(request, {"id": "user-123"})

        assert exc.value.status_code == 503


class TestRecurringIntervalAndModeLedger:
    """Round-6 P2s: validate Price interval; one-off ledger keys on actual mode."""

    async def test_subscribe_recurring_interval_mismatch_rejected(self, mock_driver, mock_plan, mock_settings):
        from fastapi import HTTPException, Request

        from backend.routes.drivers import subscribe_to_plan

        # 30-day plan, but the Stripe Price bills weekly → reject even though the
        # amount/currency match.
        recurring_plan = {**mock_plan, "id": "plan-recurring", "duration_days": 30, "stripe_price_id": "price_wk"}
        request = AsyncMock(spec=Request)
        request.json = AsyncMock(return_value={"plan_id": "plan-recurring"})

        with (
            patch("backend.db_supabase.get_rows") as mock_get_rows,
            patch("backend.settings_loader.get_app_settings", AsyncMock(return_value=mock_settings)),
            patch(
                "stripe.Price.retrieve",
                return_value={
                    "unit_amount": 4999,
                    "currency": "cad",
                    "recurring": {"interval": "week", "interval_count": 1},
                },
            ),
            patch("stripe.checkout.Session.create") as mock_session_create,
        ):
            mock_get_rows.side_effect = lambda table, filters, columns=None, limit=None: {
                "drivers": [mock_driver],
                "service_areas": [{"spinr_pass_enabled": True}],
                "subscription_plans": [recurring_plan],
                "driver_subscriptions": [],
            }.get(table, [])

            with pytest.raises(HTTPException) as exc:
                await subscribe_to_plan(request, {"id": "user-123"})

        assert exc.value.status_code == 409
        mock_session_create.assert_not_called()

    async def test_activate_records_oneoff_when_mode_payment_despite_price_id(self):
        from backend.routes import drivers as drv

        # Plan HAS a price_id now, but the session actually created was one-off
        # (mode="payment") — the ledger write must follow the real mode.
        record_mock = AsyncMock()
        find_mock = AsyncMock(
            side_effect=[
                {"id": "s1", "status": "pending", "driver_id": "d1", "plan_name": "Pro", "stripe_session_id": "cs1"},
                {"id": "p1", "duration_days": 30, "price": 49.99, "stripe_price_id": "price_x", "subscriber_count": 0},
                None,
                # _compute_subscription_tax's own drivers lookup: no
                # service_area_id -> short-circuits to zero tax/SK.
                None,
            ]
        )
        with (
            patch("backend.db_supabase.find_one", find_mock),
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.db_supabase.update_one", AsyncMock()),
            patch("backend.routes.drivers.subscriptions._record_subscription_payment", record_mock),
        ):
            await drv._activate_subscription("s1", "p1", "payment")
        record_mock.assert_awaited_once()
        assert record_mock.await_args.kwargs["billing_reason"] == "one_off"

    async def test_activate_skips_ledger_when_mode_subscription(self):
        from backend.routes import drivers as drv

        record_mock = AsyncMock()
        find_mock = AsyncMock(
            side_effect=[
                {"id": "s1", "status": "pending", "driver_id": "d1"},
                {"id": "p1", "duration_days": 30, "price": 49.99, "subscriber_count": 0},
                None,
            ]
        )
        with (
            patch("backend.db_supabase.find_one", find_mock),
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.db_supabase.update_one", AsyncMock()),
            patch("backend.routes.drivers.subscriptions._record_subscription_payment", record_mock),
        ):
            await drv._activate_subscription("s1", "p1", "subscription")
        record_mock.assert_not_awaited()


# ── New tests for the four gap-closure features ──────────────────────────────


class TestVehicleTypeEnforcement:
    """Go-online gate rejects drivers whose vehicle type is not covered by their plan."""

    async def test_go_online_blocked_when_vehicle_type_not_in_plan(self):
        from backend.routes.drivers import update_driver_status

        driver = {"id": "d1", "user_id": "u1", "vehicle_type_id": "vt-sedan", "status": "active"}
        plan = {"id": "plan-1", "vehicle_types": ["vt-suv", "vt-van"]}
        active_sub = {
            "id": "sub-1",
            "driver_id": "d1",
            "plan_id": "plan-1",
            "status": "active",
            "expires_at": "2099-01-01T00:00:00+00:00",
        }

        def fake_get_rows(table, filters, **kw):
            if table == "drivers":
                return [driver]
            if table == "driver_subscriptions":
                return [active_sub]
            return []

        async def fake_find_one(table, filters, **kw):
            if table == "drivers":
                return driver
            if table == "subscription_plans":
                return plan
            return None

        with (
            patch("backend.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.db_supabase.get_rows", side_effect=fake_get_rows),
            patch("backend.db_supabase.find_one", side_effect=fake_find_one),
            patch("backend.db_supabase.update_one", AsyncMock()),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"require_driver_subscription": True}),
            ),
        ):
            with pytest.raises(Exception) as exc_info:
                await update_driver_status(
                    driver_id="d1",
                    is_online=True,
                    lat=None,
                    lng=None,
                    current_user={"id": "u1"},
                )
            assert "vehicle" in str(exc_info.value).lower() or "402" in str(exc_info.value)

    async def test_go_online_allowed_when_plan_vehicle_types_null(self):
        """Null vehicle_types on plan = all types allowed (no 402 raised from vt gate)."""
        from backend.routes.drivers import update_driver_status

        driver = {
            "id": "d1",
            "user_id": "u1",
            "vehicle_type_id": "vt-sedan",
            "status": "active",
            "is_online": False,
            "is_available": False,
            "lat": 52.1,
            "lng": -106.6,
        }
        plan = {"id": "plan-1", "vehicle_types": None}
        active_sub = {
            "id": "sub-1",
            "driver_id": "d1",
            "plan_id": "plan-1",
            "status": "active",
            "expires_at": "2099-01-01T00:00:00+00:00",
        }

        def fake_get_rows(table, filters, **kw):
            if table == "drivers":
                return [driver]
            if table == "driver_subscriptions":
                return [active_sub]
            if table == "driver_document_status":
                return []
            return []

        async def fake_find_one(table, filters, **kw):
            if table == "drivers":
                return driver
            if table == "subscription_plans":
                return plan
            return None

        with (
            patch("backend.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.db_supabase.get_rows", side_effect=fake_get_rows),
            patch("backend.db_supabase.find_one", side_effect=fake_find_one),
            patch("backend.db_supabase.update_one", AsyncMock()),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"require_driver_subscription": True}),
            ),
            patch("backend.routes.drivers._deps.clear_presence", AsyncMock()),
            patch("backend.routes.drivers._deps.mark_present", AsyncMock()),
            patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
            patch("backend.routes.drivers._deps.manager", MagicMock()),
        ):
            try:
                await update_driver_status(
                    driver_id="d1",
                    is_online=True,
                    lat=None,
                    lng=None,
                    current_user={"id": "u1"},
                )
            except Exception as e:
                assert "vehicle" not in str(e).lower(), f"Unexpected vehicle type block: {e}"


class TestPaymentHistoryEndpoint:
    """GET /drivers/subscription/payments scopes to the authenticated driver."""

    async def test_returns_own_payments(self):
        from backend.routes.drivers import get_subscription_payment_history

        driver = {"id": "d1", "user_id": "u1"}
        payments = [
            {
                "id": "pay-1",
                "driver_id": "d1",
                "plan_id": "plan-1",
                "plan_name": "Premium Pass",
                "amount": "49.99",
                "currency": "CAD",
                "billing_reason": "one_off",
                "stripe_invoice_id": None,
                "created_at": "2025-06-01T10:00:00+00:00",
            }
        ]

        with (
            patch(
                "backend.db_supabase.get_rows",
                AsyncMock(
                    side_effect=[
                        [driver],
                        payments,
                    ]
                ),
            ),
            patch("backend.db_supabase.count_documents", AsyncMock(return_value=1)),
        ):
            result = await get_subscription_payment_history(limit=20, offset=0, current_user={"id": "u1"})

        assert result["total"] == 1
        assert result["payments"][0]["plan_name"] == "Premium Pass"
        assert result["payments"][0]["amount"] == "49.99"

    async def test_returns_404_when_no_driver_profile(self):
        from fastapi import HTTPException

        from backend.routes.drivers import get_subscription_payment_history

        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await get_subscription_payment_history(limit=20, offset=0, current_user={"id": "u-nobody"})
            assert exc.value.status_code == 404

    async def test_amount_is_decimal_string_two_dp(self):
        """Amount must always be serialised to exactly 2 decimal places."""
        from backend.routes.drivers import get_subscription_payment_history

        driver = {"id": "d1", "user_id": "u1"}
        payments = [
            {
                "id": "pay-2",
                "driver_id": "d1",
                "plan_id": "plan-1",
                "plan_name": "Standard Pass",
                "amount": "29.9",
                "currency": "CAD",
                "billing_reason": "one_off",
                "stripe_invoice_id": None,
                "created_at": "2025-06-15T08:00:00+00:00",
            }
        ]

        with (
            patch(
                "backend.db_supabase.get_rows",
                AsyncMock(
                    side_effect=[
                        [driver],
                        payments,
                    ]
                ),
            ),
            patch("backend.db_supabase.count_documents", AsyncMock(return_value=1)),
        ):
            result = await get_subscription_payment_history(limit=20, offset=0, current_user={"id": "u1"})

        assert result["payments"][0]["amount"] == "29.90"


class TestOfferAnalyticsEndpoint:
    """GET /admin/offer-analytics groups by service area correctly."""

    async def test_groups_by_service_area(self):
        from backend.routes.admin.subscriptions import get_offer_analytics

        offers = [
            {
                "id": "o1",
                "ride_id": "r1",
                "status": "accepted",
                "offered_at": "2025-06-01T10:00:00+00:00",
                "responded_at": "2025-06-01T10:00:05+00:00",
            },
            {
                "id": "o2",
                "ride_id": "r1",
                "status": "declined",
                "offered_at": "2025-06-01T10:00:00+00:00",
                "responded_at": "2025-06-01T10:00:08+00:00",
            },
            {
                "id": "o3",
                "ride_id": "r2",
                "status": "accepted",
                "offered_at": "2025-06-02T10:00:00+00:00",
                "responded_at": "2025-06-02T10:00:03+00:00",
            },
        ]
        rides = [
            {"id": "r1", "service_area_id": "area-a"},
            {"id": "r2", "service_area_id": "area-b"},
        ]
        areas = [
            {"id": "area-a", "name": "Downtown"},
            {"id": "area-b", "name": "Airport"},
        ]

        def fake_get_rows(table, filters, **kw):
            if table == "ride_offers":
                return offers
            if table == "rides":
                return rides
            if table == "service_areas":
                return areas
            return []

        with patch("backend.db_supabase.get_rows", side_effect=fake_get_rows):
            result = await get_offer_analytics(
                start_date="2025-06-01",
                end_date="2025-06-30",
                service_area_id=None,
                _admin={"id": "admin-1", "role": "admin"},
            )

        assert result["totals"]["total_offers"] == 3
        assert result["totals"]["accepted"] == 2
        assert result["totals"]["acceptance_rate"] == pytest.approx(2 / 3, abs=0.001)
        area_ids = {a["service_area_id"] for a in result["areas"]}
        assert "area-a" in area_ids
        assert "area-b" in area_ids

    async def test_filters_to_single_area(self):
        from backend.routes.admin.subscriptions import get_offer_analytics

        offers = [
            {
                "id": "o1",
                "ride_id": "r1",
                "status": "accepted",
                "offered_at": "2025-06-01T10:00:00+00:00",
                "responded_at": "2025-06-01T10:00:05+00:00",
            },
            {
                "id": "o2",
                "ride_id": "r2",
                "status": "expired",
                "offered_at": "2025-06-01T10:00:00+00:00",
                "responded_at": None,
            },
        ]
        rides = [
            {"id": "r1", "service_area_id": "area-a"},
            {"id": "r2", "service_area_id": "area-b"},
        ]

        def fake_get_rows(table, filters, **kw):
            if table == "ride_offers":
                return offers
            if table == "rides":
                return rides
            if table == "service_areas":
                return [{"id": "area-a", "name": "Downtown"}, {"id": "area-b", "name": "Airport"}]
            return []

        with patch("backend.db_supabase.get_rows", side_effect=fake_get_rows):
            result = await get_offer_analytics(
                start_date="2025-06-01",
                end_date="2025-06-30",
                service_area_id="area-a",
                _admin={"id": "admin-1", "role": "admin"},
            )

        assert len(result["areas"]) == 1
        assert result["areas"][0]["service_area_id"] == "area-a"
        assert result["areas"][0]["total_offers"] == 1
        assert result["areas"][0]["accepted"] == 1


class TestExpiryWarning3Day:
    """3-day warning sets expiry_warned_3d flag; 24h warning still fires later."""

    async def test_3d_warning_push_sent_and_flag_set(self):
        from datetime import datetime, timedelta, timezone

        from backend.routes import drivers as drv

        now = datetime.now(timezone.utc)
        sub = {
            "id": "sub-1",
            "driver_id": "d1",
            "plan_name": "Premium Pass",
            "status": "active",
            "expires_at": (now + timedelta(days=2)).isoformat(),
            "expiry_warned": False,
            "expiry_warned_3d": False,
        }
        driver = {"id": "d1", "user_id": "u1"}

        push_mock = AsyncMock()
        update_mock = AsyncMock()

        with (
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[sub])),
            patch("backend.db_supabase.find_one", AsyncMock(return_value=driver)),
            patch("backend.db_supabase.update_one", update_mock),
            patch("backend.routes.drivers._deps.send_push_notification", push_mock),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"require_driver_subscription": False}),
            ),
            patch("backend.routes.drivers._deps.asyncio.sleep", AsyncMock(side_effect=Exception("stop"))),
        ):
            try:
                await drv.check_expiring_subscriptions()
            except Exception as e:
                if "stop" not in str(e):
                    raise

        assert push_mock.await_count >= 1
        all_titles = [str(c.args[1]) for c in push_mock.await_args_list]
        assert any("3" in t or "Days" in t or "days" in t.lower() for t in all_titles)

        update_calls_str = [str(c) for c in update_mock.await_args_list]
        assert any("expiry_warned_3d" in c for c in update_calls_str)

    async def test_3d_warning_skipped_when_flag_already_set(self):
        """expiry_warned_3d=True means no second 3-day push."""
        from datetime import datetime, timedelta, timezone

        from backend.routes import drivers as drv

        now = datetime.now(timezone.utc)
        sub = {
            "id": "sub-1",
            "driver_id": "d1",
            "plan_name": "Premium Pass",
            "status": "active",
            "expires_at": (now + timedelta(days=2)).isoformat(),
            "expiry_warned": False,
            "expiry_warned_3d": True,  # already warned
        }
        driver = {"id": "d1", "user_id": "u1"}

        push_mock = AsyncMock()

        with (
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[sub])),
            patch("backend.db_supabase.find_one", AsyncMock(return_value=driver)),
            patch("backend.db_supabase.update_one", AsyncMock()),
            patch("backend.routes.drivers._deps.send_push_notification", push_mock),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"require_driver_subscription": False}),
            ),
            patch("backend.routes.drivers._deps.asyncio.sleep", AsyncMock(side_effect=Exception("stop"))),
        ):
            try:
                await drv.check_expiring_subscriptions()
            except Exception as e:
                if "stop" not in str(e):
                    raise

        # No 3d warning push should have fired
        three_day_pushes = [c for c in push_mock.await_args_list if "3" in str(c.args[1]) or "Days" in str(c.args[1])]
        assert len(three_day_pushes) == 0
