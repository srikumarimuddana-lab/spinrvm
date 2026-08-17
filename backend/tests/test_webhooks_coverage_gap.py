"""Closes the REMAINING coverage gap on routes/webhooks.py (A1c Sub-tier C
batch 11 — the largest, most Stripe-critical file in the whole Sub-tier C
list at 748 stmts / 75.40% baseline).

routes/webhooks.py already has extensive coverage across several files —
test_webhooks_main.py (2019 lines), test_corporate_webhook.py,
test_webhook_stripe_v15.py, test_orphan_refund.py, test_ses_webhook.py,
test_twilio_inbound.py. This file is deliberately scoped to branches none of
those exercise, confirmed by grep before writing a single test here:

  - charge.dispute.created / charge.dispute.closed — the entire dispute
    lifecycle handler (~120 lines) had ZERO test coverage anywhere in the
    repo before this file.
  - account.updated — the webhook DISPATCH branch that calls
    apply_account_update was untested (the underlying stripe_kyc_sync
    service function itself is tested elsewhere, e.g.
    test_stripe_mapping_import_service.py, but never via the webhook route).
  - customer.subscription.updated's past_due branch and its no-matching-row
    skip (only the canceled/active branches were tested).
  - The "matched allowlist but fell through dispatch" defensive branch (a
    handler-logic-gap guard for a bug class, not a real event type).
  - _extract_invoice_payment_intent's third fallback path (a *successful*
    stripe.Invoice.retrieve(expand=['payments']) call) — only the failure
    variant of this fallback was tested.
  - invoice.paid's plan-duration_days fallback when the invoice carries no
    parseable billing period.
  - _invoice_period_end_iso / _invoice_period_start_iso's own exception
    paths, tested directly rather than only incidentally via invoice.paid.
  - _handle_ride_invoice_paid's two best-effort swallow branches (the WS
    payment_completed push failing, and the GST receipt email failing) —
    every existing test mocks both to succeed.
  - charge.refunded's full dispatch through the ROUTE (ride found: mark
    refunded + ledger + push; ride not found: orphan path) —
    test_orphan_refund.py's TestChargeRefundedOrphanIntegration calls
    `_record_orphan_refund` directly, never exercising the surrounding
    `elif event_type == "charge.refunded":` block itself.
  - checkout.session.completed's stripe_subscription_id-linking success
    branch and its stale/superseded-row orphan-Stripe-subscription-cancel
    branch (the one existing test swallows all exceptions and never mocks
    the driver_subscriptions re-read, so it doesn't reliably reach either).
  - customer.subscription.deleted's legacy customer-id fallback lookup
    (only the primary stripe_subscription_id match was tested) and its
    push-notification-failure swallow.
  - invoice.payment_failed's no-matching-row branch and its
    push-notification-failure swallow.
  - customer.subscription.updated's "active" status positive reactivation
    (only the guarded-reject case was tested).
  - payment_intent.succeeded's GST receipt-send failure swallow, and
    payment_intent.payment_failed's ride-update-returns-None (500) and
    driver-lookup-exception-swallow branches.
  - SES helpers: `_confirm_sns_subscription`'s untrusted-URL / non-2xx /
    request-exception branches, `_suppress_marketing_email`'s blank-target
    early return and user-lookup-exception swallow, `_handle_ses_notification`'s
    malformed-JSON-Message branch, and the route's UnsubscribeConfirmation/
    unknown msg_type passthrough.
  - `_resolve_user_id_by_phone`'s exception swallow, and the Twilio inbound
    route's SIGNED path (auth_token set) — both the signature-invalid 403
    reject and the signature-valid accept — which every existing
    test_twilio_inbound.py test skips via the dev auth_token-unset bypass.

Test-only: no application code in routes/webhooks.py is modified here.
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _ensure_real_twilio_imported():
    """Force a fresh, real `twilio` import before relying on it.

    Defensive against a pre-existing test-suite hygiene issue (same class as
    A8): under the full suite, `sys.modules["twilio"]` can end up replaced
    by a non-package stand-in left behind by another test's imperfectly-
    scoped patch elsewhere in the suite, so `from twilio.request_validator
    import RequestValidator` fails with `ModuleNotFoundError: 'twilio' is
    not a package` (or, worse, silently degrades the source's own signature
    validation into a no-op). Dropping the (possibly-polluted) cache entries
    and re-importing guarantees both the test and the source code under test
    resolve against the genuine package. Root cause not chased further here
    (test-only scope) — see this PR's Change Impact Log.
    """
    sys.modules.pop("twilio", None)
    sys.modules.pop("twilio.request_validator", None)
    import twilio.request_validator  # noqa: F401


def _make_stripe_event(event_type: str, data_object: dict, event_id: str = "evt_gap_1") -> dict:
    return {"id": event_id, "type": event_type, "data": {"object": data_object}}


def _event_obj(raw: dict) -> MagicMock:
    obj = MagicMock()
    obj.get = lambda k, d=None: raw.get(k, d)
    obj.to_dict_recursive = lambda: raw
    return obj


def _req() -> MagicMock:
    req = MagicMock()
    req.body = AsyncMock(return_value=b"payload")
    req.headers = {"stripe-signature": "sig"}
    return req


def _settings_fn(**extra):
    async def f():
        return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk", **extra}

    return f


# ---------------------------------------------------------------------------
# charge.dispute.created
# ---------------------------------------------------------------------------


class TestChargeDisputeCreated:
    def test_dispute_with_matching_ride_updates_status_and_broadcasts(self):
        from backend.routes import webhooks as wh

        data_obj = {
            "id": "dp_1",
            "payment_intent": "pi_disputed",
            "amount": 4500,
            "reason": "fraudulent",
            "status": "warning_needs_response",
        }
        raw = _make_stripe_event("charge.dispute.created", data_obj, event_id="evt_dispute_1")
        insert_mock = AsyncMock()
        update_one_mock = AsyncMock()
        broadcast_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.get_rows",
                AsyncMock(return_value=[{"id": "ride_disputed", "payment_intent_id": "pi_disputed"}]),
            ),
            patch("backend.routes.webhooks.db_supabase.insert_one", insert_mock),
            patch("backend.routes.webhooks.db_supabase.update_one", update_one_mock),
            patch("backend.socket_manager.manager.broadcast_to_admins", broadcast_mock),
            patch("socket_manager.manager.broadcast_to_admins", broadcast_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        # stripe_disputes row inserted with the ride linked.
        table, row = insert_mock.await_args.args[:2]
        assert table == "stripe_disputes"
        assert row["ride_id"] == "ride_disputed"
        assert row["stripe_dispute_id"] == "dp_1"
        assert row["reason"] == "fraudulent"
        # Ride flipped to disputed.
        upd_table, upd_filter, upd_fields = update_one_mock.await_args.args[:3]
        assert upd_table == "rides"
        assert upd_filter == {"id": "ride_disputed"}
        assert upd_fields["payment_status"] == "disputed"
        broadcast_mock.assert_awaited_once()
        broadcast_call = broadcast_mock.await_args.args[0]
        assert broadcast_call["type"] == "charge_dispute_created"
        assert broadcast_call["ride_id"] == "ride_disputed"

    def test_dispute_without_matching_ride_still_records(self):
        """No payment_intent → ride match: dispute is still persisted with
        ride_id=None (nothing to reconcile against yet, not a failure)."""
        from backend.routes import webhooks as wh

        data_obj = {
            "id": "dp_2",
            "payment_intent": "",
            "amount": 1000,
            "reason": "duplicate",
            "status": "needs_response",
        }
        raw = _make_stripe_event("charge.dispute.created", data_obj, event_id="evt_dispute_2")
        insert_mock = AsyncMock()
        get_rows_mock = AsyncMock(return_value=[])

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.get_rows", get_rows_mock),
            patch("backend.routes.webhooks.db_supabase.insert_one", insert_mock),
            patch("backend.socket_manager.manager.broadcast_to_admins", AsyncMock()),
            patch("socket_manager.manager.broadcast_to_admins", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        get_rows_mock.assert_not_awaited()  # blank PI: never even queries for a ride
        row = insert_mock.await_args.args[1]
        assert row["ride_id"] is None
        assert row["payment_intent_id"] == ""

    def test_ws_broadcast_failure_is_swallowed(self):
        """A dashboard WS outage must not fail the webhook — Stripe already
        needs the dispute row persisted; the admin push is best-effort."""
        from backend.routes import webhooks as wh

        data_obj = {
            "id": "dp_3",
            "payment_intent": "pi_ws_fail",
            "amount": 2000,
            "reason": "fraudulent",
            "status": "won",
        }
        raw = _make_stripe_event("charge.dispute.created", data_obj, event_id="evt_dispute_3")

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.webhooks.db_supabase.insert_one", AsyncMock()),
            patch(
                "backend.socket_manager.manager.broadcast_to_admins",
                AsyncMock(side_effect=Exception("ws down")),
            ),
            patch(
                "socket_manager.manager.broadcast_to_admins",
                AsyncMock(side_effect=Exception("ws down")),
            ),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True


# ---------------------------------------------------------------------------
# charge.dispute.closed
# ---------------------------------------------------------------------------


class TestChargeDisputeClosed:
    def test_won_dispute_restores_paid_status(self):
        from backend.routes import webhooks as wh

        data_obj = {"id": "dp_won", "payment_intent": "pi_won", "status": "won"}
        raw = _make_stripe_event("charge.dispute.closed", data_obj, event_id="evt_close_1")
        update_one_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.find_one",
                AsyncMock(return_value={"id": "dispute_row_1", "ride_id": "ride_won"}),
            ),
            patch(
                "backend.routes.webhooks.db_supabase.get_rows",
                AsyncMock(return_value=[{"id": "ride_won", "rider_id": "rider_won"}]),
            ),
            patch("backend.routes.webhooks.db_supabase.update_one", update_one_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        # Two update_one calls: the dispute row's status, then the ride's payment_status.
        calls = update_one_mock.await_args_list
        dispute_call = next(c for c in calls if c.args[0] == "stripe_disputes")
        assert dispute_call.args[2]["status"] == "won"
        ride_call = next(c for c in calls if c.args[0] == "rides")
        assert ride_call.args[1] == {"id": "ride_won"}
        assert ride_call.args[2]["payment_status"] == "paid"

    def test_lost_dispute_marks_dispute_lost(self):
        from backend.routes import webhooks as wh

        data_obj = {"id": "dp_lost", "payment_intent": "pi_lost", "status": "lost"}
        raw = _make_stripe_event("charge.dispute.closed", data_obj, event_id="evt_close_2")
        update_one_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.find_one",
                AsyncMock(return_value={"id": "dispute_row_2", "ride_id": "ride_lost"}),
            ),
            patch(
                "backend.routes.webhooks.db_supabase.get_rows",
                AsyncMock(return_value=[{"id": "ride_lost", "rider_id": "rider_lost"}]),
            ),
            patch("backend.routes.webhooks.db_supabase.update_one", update_one_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        ride_call = next(c for c in update_one_mock.await_args_list if c.args[0] == "rides")
        assert ride_call.args[2]["payment_status"] == "dispute_lost"

    def test_no_existing_dispute_row_recovers_ride_via_payment_intent(self):
        """The stripe_disputes row from .created may be missing (e.g. lost in a
        migration gap, or this .closed arrived first) — the ride is still
        located and updated via a direct payment_intent_id lookup."""
        from backend.routes import webhooks as wh

        data_obj = {"payment_intent": "pi_recover", "status": "won"}
        raw = _make_stripe_event("charge.dispute.closed", data_obj, event_id="evt_close_3")
        update_one_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(return_value=None)),
            patch(
                "backend.routes.webhooks.db_supabase.get_rows",
                AsyncMock(return_value=[{"id": "ride_recovered"}]),
            ),
            patch("backend.routes.webhooks.db_supabase.update_one", update_one_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        ride_call = next(c for c in update_one_mock.await_args_list if c.args[0] == "rides")
        assert ride_call.args[1] == {"id": "ride_recovered"}

    def test_no_ride_id_resolvable_skips_ride_update(self):
        from backend.routes import webhooks as wh

        data_obj = {"payment_intent": "pi_orphan", "status": "won"}
        raw = _make_stripe_event("charge.dispute.closed", data_obj, event_id="evt_close_4")
        update_one_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(return_value=None)),
            patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.webhooks.db_supabase.update_one", update_one_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        # No ride row anywhere to update → only ever touches stripe_disputes,
        # never "rides".
        assert all(c.args[0] != "rides" for c in update_one_mock.await_args_list)


# ---------------------------------------------------------------------------
# account.updated — Stripe Connect KYC mirror dispatch
# ---------------------------------------------------------------------------


class TestAccountUpdatedDispatch:
    def test_dispatches_to_apply_account_update(self):
        from backend.routes import webhooks as wh

        data_obj = {"id": "acct_1", "individual": {"id_number_provided": True}}
        raw = _make_stripe_event("account.updated", data_obj, event_id="evt_acct_1")
        apply_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.services.stripe_kyc_sync.apply_account_update", apply_mock),
            patch("services.stripe_kyc_sync.apply_account_update", apply_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        apply_mock.assert_awaited_once()
        called_data, kwargs = apply_mock.await_args.args, apply_mock.await_args.kwargs
        assert called_data[0]["id"] == "acct_1"
        assert kwargs["event_id"] == "evt_acct_1"


# ---------------------------------------------------------------------------
# customer.subscription.updated — past_due branch + no-matching-row skip
# ---------------------------------------------------------------------------


class TestSubscriptionUpdatedPastDue:
    def test_past_due_status_flips_payment_status(self):
        from backend.routes import webhooks as wh

        data_obj = {"id": "sub_past_due_1", "status": "past_due"}
        raw = _make_stripe_event("customer.subscription.updated", data_obj, event_id="evt_sub_pd_1")
        update_one_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.find_one",
                AsyncMock(return_value={"id": "row_1", "status": "active"}),
            ),
            patch("backend.routes.webhooks.db_supabase.update_one", update_one_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        update_one_mock.assert_awaited_once_with(
            "driver_subscriptions", {"id": "row_1"}, {"payment_status": "past_due"}
        )

    def test_no_matching_row_is_a_noop_ack(self):
        from backend.routes import webhooks as wh

        data_obj = {"id": "sub_unknown", "status": "active"}
        raw = _make_stripe_event("customer.subscription.updated", data_obj, event_id="evt_sub_unknown")
        update_one_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(return_value=None)),
            patch("backend.routes.webhooks.db_supabase.update_one", update_one_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        update_one_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# "matched allowlist but fell through dispatch" defensive branch
# ---------------------------------------------------------------------------


class TestHandlerLogicGapGuard:
    def test_allowlisted_but_unhandled_type_logs_error_and_acks(self):
        """Defensive guard for a future bug class: an event type added to
        _STRIPE_HANDLED_EVENTS without a matching elif branch must log at
        ERROR (not silently vanish into the generic unknown-event warning)
        while still acking 200 so Stripe doesn't retry forever."""
        from backend.routes import webhooks as wh

        data_obj = {"id": "obj_1"}
        raw = _make_stripe_event("payout.updated", data_obj, event_id="evt_gap_branch")

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()) as mark_mock,
            # Force the allowlist to include an event type with no elif branch.
            patch(
                "backend.routes.webhooks._STRIPE_HANDLED_EVENTS",
                frozenset(wh._STRIPE_HANDLED_EVENTS | {"payout.updated"}),
            ),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        # Falls into the "unhandled" ack path (return before mark_stripe_event_processed).
        assert result["received"] is True
        assert result.get("unhandled") is True
        mark_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# _extract_invoice_payment_intent — successful Invoice.retrieve fallback
# ---------------------------------------------------------------------------


class TestExtractInvoicePaymentIntentRetrieveFallback:
    def test_successful_retrieve_resolves_pi_from_basil_shape(self):
        from backend.routes.webhooks import _extract_invoice_payment_intent

        invoice = {"id": "in_needs_retrieve"}  # no payment_intent, no payments shape
        refreshed = MagicMock()
        refreshed.to_dict_recursive = lambda: {
            "id": "in_needs_retrieve",
            "payments": {"data": [{"payment": {"payment_intent": "pi_from_retrieve"}}]},
        }

        with patch("stripe.Invoice.retrieve", MagicMock(return_value=refreshed)) as retrieve_mock:
            pi = _extract_invoice_payment_intent(invoice, stripe_secret="sk_test")

        assert pi == "pi_from_retrieve"
        retrieve_mock.assert_called_once_with("in_needs_retrieve", expand=["payments"], api_key="sk_test")

    def test_no_invoice_id_or_secret_returns_none_without_calling_stripe(self):
        from backend.routes.webhooks import _extract_invoice_payment_intent

        with patch("stripe.Invoice.retrieve", MagicMock()) as retrieve_mock:
            assert _extract_invoice_payment_intent({}, stripe_secret="") is None
        retrieve_mock.assert_not_called()

    def test_legacy_top_level_payment_intent_dict_shape(self):
        """payment_intent can be an expanded dict ({'id': ...}) rather than a
        bare string id — both must resolve to the same id."""
        from backend.routes.webhooks import _extract_invoice_payment_intent

        invoice = {"payment_intent": {"id": "pi_expanded"}}
        assert _extract_invoice_payment_intent(invoice) == "pi_expanded"


# ---------------------------------------------------------------------------
# _invoice_period_end_iso / _invoice_period_start_iso — direct unit tests
# ---------------------------------------------------------------------------


class TestInvoicePeriodHelpers:
    def test_end_iso_extracts_from_first_line(self):
        from backend.routes.webhooks import _invoice_period_end_iso

        invoice = {"lines": {"data": [{"period": {"end": 1735689600}}]}}
        out = _invoice_period_end_iso(invoice)
        assert out is not None and out.startswith("2025-01-01")

    def test_end_iso_missing_lines_returns_none(self):
        from backend.routes.webhooks import _invoice_period_end_iso

        assert _invoice_period_end_iso({}) is None
        assert _invoice_period_end_iso({"lines": {"data": []}}) is None

    def test_end_iso_malformed_structure_returns_none(self):
        """A non-dict lines/period shape must not raise — caller falls back
        to plan duration."""
        from backend.routes.webhooks import _invoice_period_end_iso

        assert _invoice_period_end_iso({"lines": "not-a-dict"}) is None

    def test_start_iso_extracts_from_first_line(self):
        from backend.routes.webhooks import _invoice_period_start_iso

        invoice = {"lines": {"data": [{"period": {"start": 1735689600}}]}}
        out = _invoice_period_start_iso(invoice)
        assert out is not None and out.startswith("2025-01-01")

    def test_start_iso_malformed_structure_returns_none(self):
        from backend.routes.webhooks import _invoice_period_start_iso

        assert _invoice_period_start_iso({"lines": "not-a-dict"}) is None
        assert _invoice_period_start_iso({}) is None


# ---------------------------------------------------------------------------
# invoice.paid (subscription cycle) — duration_days fallback
# ---------------------------------------------------------------------------


class TestInvoicePaidDurationFallback:
    def test_no_period_end_falls_back_to_plan_duration_days(self):
        """When the invoice carries no parseable billing period,
        expires_at must be computed from the plan's duration_days rather
        than defaulting silently to Stripe's own (possibly wrong) cadence."""
        from backend.routes import webhooks as wh

        data_obj = {
            "id": "in_no_period",
            "subscription": "sub_fallback",
            # No "lines" key at all → _invoice_period_end_iso returns None.
            "amount_paid": 0,
        }
        raw = _make_stripe_event("invoice.paid", data_obj, event_id="evt_inv_fallback")
        update_one_mock = AsyncMock()

        async def _find_one(table, filt):
            if table == "driver_subscriptions":
                return {"id": "row_fb", "driver_id": "drv_fb", "plan_id": "plan_fb", "status": "active"}
            if table == "subscription_plans":
                return {"id": "plan_fb", "duration_days": 7}
            return None

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(side_effect=_find_one)),
            patch("backend.routes.webhooks.db_supabase.update_one", update_one_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        sub_update = next(c for c in update_one_mock.await_args_list if c.args[0] == "driver_subscriptions")
        # amount_paid == 0 → no fire-and-forget invoice email task, no ledger
        # write attempted (guarded by `if _inv_amount and Decimal(...) > 0`),
        # so the only update_one call is the renewal itself.
        assert sub_update.args[2]["status"] == "active"
        assert "expires_at" in sub_update.args[2]


# ---------------------------------------------------------------------------
# _handle_ride_invoice_paid — best-effort WS + receipt swallow branches
# ---------------------------------------------------------------------------


class TestRideInvoicePaidBestEffortSwallow:
    def _event(self, data_object, event_id):
        raw = _make_stripe_event("invoice.paid", data_object, event_id=event_id)
        return _event_obj(raw)

    def test_ws_push_failure_does_not_abort_settlement(self):
        from backend.routes import webhooks as wh

        data_obj = {
            "id": "in_ws_fail",
            "metadata": {"ride_id": "ride_ws_fail"},
            "amount_paid": 500,
            "payment_intent": "pi_ws",
        }
        update_ride_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(
                __import__("stripe").Webhook,
                "construct_event",
                return_value=self._event(data_obj, "evt_ws_fail"),
            ),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.get_ride",
                AsyncMock(
                    return_value={"id": "ride_ws_fail", "rider_id": "u1", "payment_status": "failed", "tip_amount": "0"}
                ),
            ),
            patch("backend.routes.webhooks.db_supabase.update_ride", update_ride_mock),
            patch("backend.services.payment_service.record_payment_event", AsyncMock()),
            patch("services.payment_service.record_payment_event", AsyncMock()),
            patch("backend.services.payment_service.send_ride_receipt", AsyncMock(return_value=True)),
            patch("services.payment_service.send_ride_receipt", AsyncMock(return_value=True)),
            patch(
                "backend.socket_manager.manager.send_personal_message",
                AsyncMock(side_effect=Exception("ws send failed")),
            ),
            patch(
                "socket_manager.manager.send_personal_message",
                AsyncMock(side_effect=Exception("ws send failed")),
            ),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        update_ride_mock.assert_awaited_once()  # settlement itself still happened

    def test_receipt_email_failure_does_not_abort_settlement(self):
        from backend.routes import webhooks as wh

        data_obj = {
            "id": "in_rcpt_fail",
            "metadata": {"ride_id": "ride_rcpt_fail"},
            "amount_paid": 500,
            "payment_intent": "pi_rcpt",
        }
        update_ride_mock = AsyncMock()
        record_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(
                __import__("stripe").Webhook,
                "construct_event",
                return_value=self._event(data_obj, "evt_rcpt_fail"),
            ),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.get_ride",
                AsyncMock(
                    return_value={
                        "id": "ride_rcpt_fail",
                        "rider_id": "u1",
                        "payment_status": "failed",
                        "tip_amount": "0",
                    }
                ),
            ),
            patch("backend.routes.webhooks.db_supabase.update_ride", update_ride_mock),
            patch("backend.services.payment_service.record_payment_event", record_mock),
            patch("services.payment_service.record_payment_event", record_mock),
            patch(
                "backend.services.payment_service.send_ride_receipt",
                AsyncMock(side_effect=Exception("email provider down")),
            ),
            patch(
                "services.payment_service.send_ride_receipt",
                AsyncMock(side_effect=Exception("email provider down")),
            ),
            patch("backend.socket_manager.manager.send_personal_message", AsyncMock()),
            patch("socket_manager.manager.send_personal_message", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        record_mock.assert_awaited_once()  # ledger write still happened before the email attempt


# ---------------------------------------------------------------------------
# _event_to_plain_dict — last-ditch JSON round-trip fallback (Codex-class edge)
# ---------------------------------------------------------------------------


class TestEventToPlainDictJsonFallback:
    def test_object_with_no_get_and_no_to_dict_recursive_falls_back_to_str_json(self):
        """An object exposing neither `.get` nor `_to_dict_recursive` /
        `to_dict_recursive` must still normalize via str(event) → json.loads,
        as long as __str__ happens to produce valid JSON (a StripeObject's
        __str__ does)."""
        from backend.routes.webhooks import _event_to_plain_dict

        class _Weird:
            def __str__(self):
                return '{"id": "evt_weird", "type": "t"}'

        out = _event_to_plain_dict(_Weird())
        assert out == {"id": "evt_weird", "type": "t"}


# ---------------------------------------------------------------------------
# charge.refunded — full dispatch through the route (not just
# _record_orphan_refund in isolation, which test_orphan_refund.py covers)
# ---------------------------------------------------------------------------


class TestChargeRefundedFullDispatch:
    def test_ride_found_marks_refunded_records_ledger_and_pushes(self):
        from backend.routes import webhooks as wh

        data_obj = {"payment_intent": "pi_refund_1", "amount_refunded": 1500, "currency": "cad"}
        raw = _make_stripe_event("charge.refunded", data_obj, event_id="evt_refund_1")
        update_one_mock = AsyncMock()
        record_refund_mock = AsyncMock()
        push_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.get_rows",
                AsyncMock(return_value=[{"id": "ride_refunded", "rider_id": "rider_1"}]),
            ),
            patch("backend.routes.webhooks.db_supabase.update_one", update_one_mock),
            patch("backend.services.payment_service.record_refund_event", record_refund_mock),
            patch("services.payment_service.record_refund_event", record_refund_mock),
            patch("backend.routes.webhooks.send_push_notification", push_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        upd_table, upd_filter, upd_fields = update_one_mock.await_args.args[:3]
        assert upd_table == "rides"
        assert upd_filter == {"id": "ride_refunded"}
        assert upd_fields["payment_status"] == "refunded"
        assert upd_fields["refund_amount"] == "15.00"
        record_refund_mock.assert_awaited_once()
        assert record_refund_mock.await_args.kwargs["refund_cents"] == 1500
        push_mock.assert_awaited_once()
        assert push_mock.await_args.args[0] == "rider_1"

    def test_ride_found_push_failure_is_swallowed_at_debug(self):
        from backend.routes import webhooks as wh

        data_obj = {"payment_intent": "pi_refund_push_fail", "amount_refunded": 500, "currency": "cad"}
        raw = _make_stripe_event("charge.refunded", data_obj, event_id="evt_refund_push_fail")

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.get_rows",
                AsyncMock(return_value=[{"id": "ride_pf", "rider_id": "rider_pf"}]),
            ),
            patch("backend.routes.webhooks.db_supabase.update_one", AsyncMock()),
            patch("backend.services.payment_service.record_refund_event", AsyncMock()),
            patch("services.payment_service.record_refund_event", AsyncMock()),
            patch(
                "backend.routes.webhooks.send_push_notification",
                AsyncMock(side_effect=Exception("push down")),
            ),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True

    def test_no_ride_found_routes_to_orphan_via_full_dispatch(self):
        """End-to-end through the route (not the direct-call unit tests in
        test_orphan_refund.py) — confirms the dispatch actually reaches
        _record_orphan_refund when no ride matches the payment_intent."""
        from backend.routes import webhooks as wh

        data_obj = {"payment_intent": "pi_no_ride", "amount_refunded": 999, "currency": "cad"}
        raw = _make_stripe_event("charge.refunded", data_obj, event_id="evt_refund_orphan")
        insert_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.webhooks.db_supabase.insert_one", insert_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        row = insert_mock.await_args.args[1]
        assert row["reason"] == "no_ride_for_pi"


# ---------------------------------------------------------------------------
# checkout.session.completed — success-linking + stale/superseded-cancel
# ---------------------------------------------------------------------------


class TestCheckoutSessionCompletedBranches:
    def test_active_row_links_stripe_subscription_id(self):
        from backend.routes import webhooks as wh

        data_obj = {
            "id": "cs_link",
            "payment_status": "paid",
            "mode": "subscription",
            "subscription": "sub_new_123",
            "metadata": {"subscription_id": "row_link", "plan_id": "plan_1", "driver_id": "drv_1"},
        }
        raw = _make_stripe_event("checkout.session.completed", data_obj, event_id="evt_checkout_link")
        activate_mock = AsyncMock()
        update_one_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.drivers.subscriptions._activate_subscription", activate_mock),
            patch("routes.drivers.subscriptions._activate_subscription", activate_mock),
            patch(
                "backend.routes.webhooks.db_supabase.find_one",
                AsyncMock(return_value={"id": "row_link", "status": "active"}),
            ),
            patch("backend.routes.webhooks.db_supabase.update_one", update_one_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        activate_mock.assert_awaited_once_with("row_link", "plan_1", "subscription")
        update_one_mock.assert_awaited_once_with(
            "driver_subscriptions", {"id": "row_link"}, {"stripe_subscription_id": "sub_new_123"}
        )

    def test_superseded_row_cancels_orphan_stripe_subscription(self):
        from backend.routes import webhooks as wh

        data_obj = {
            "id": "cs_stale",
            "payment_status": "paid",
            "subscription": "sub_orphan_456",
            "metadata": {"subscription_id": "row_stale", "plan_id": "plan_1", "driver_id": "drv_2"},
        }
        raw = _make_stripe_event("checkout.session.completed", data_obj, event_id="evt_checkout_stale")
        activate_mock = AsyncMock()
        cancel_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.drivers.subscriptions._activate_subscription", activate_mock),
            patch("routes.drivers.subscriptions._activate_subscription", activate_mock),
            # Row is now 'superseded' — not active — so this session's
            # checkout was for a plan the driver already replaced.
            patch(
                "backend.routes.webhooks.db_supabase.find_one",
                AsyncMock(return_value={"id": "row_stale", "status": "superseded"}),
            ),
            patch("backend.routes.drivers.subscriptions._cancel_stripe_subscription", cancel_mock),
            patch("routes.drivers.subscriptions._cancel_stripe_subscription", cancel_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        cancel_mock.assert_awaited_once_with("sub_orphan_456")


# ---------------------------------------------------------------------------
# customer.subscription.deleted — legacy customer-id fallback + push swallow
# ---------------------------------------------------------------------------


class TestSubscriptionDeletedCustomerFallback:
    def test_falls_back_to_customer_id_when_no_stripe_sub_match(self):
        from backend.routes import webhooks as wh

        data_obj = {"id": "sub_gone", "customer": "cus_legacy_1"}
        raw = _make_stripe_event("customer.subscription.deleted", data_obj, event_id="evt_del_fallback")
        update_one_mock = AsyncMock()
        push_mock = AsyncMock()

        async def _find_one(table, filt):
            if table == "driver_subscriptions" and "stripe_subscription_id" in filt:
                return None  # no primary match
            if table == "users":
                return {"id": "user_legacy"}
            if table == "drivers" and filt.get("user_id") == "user_legacy":
                return {"id": "driver_legacy", "user_id": "user_legacy"}
            if table == "driver_subscriptions" and filt.get("driver_id") == "driver_legacy":
                return {"id": "row_legacy", "driver_id": "driver_legacy", "status": "active"}
            if table == "drivers" and filt.get("id") == "driver_legacy":
                return {"id": "driver_legacy", "user_id": "user_legacy"}
            return None

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(side_effect=_find_one)),
            patch("backend.routes.webhooks.db_supabase.update_one", update_one_mock),
            patch("backend.routes.webhooks.send_push_notification", push_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        cancel_call = next(c for c in update_one_mock.await_args_list if c.args[0] == "driver_subscriptions")
        assert cancel_call.args[1] == {"id": "row_legacy"}
        assert cancel_call.args[2]["status"] == "cancelled"
        push_mock.assert_awaited_once()

    def test_no_match_anywhere_is_a_noop_ack(self):
        from backend.routes import webhooks as wh

        data_obj = {"id": "sub_totally_unknown", "customer": "cus_unknown"}
        raw = _make_stripe_event("customer.subscription.deleted", data_obj, event_id="evt_del_unknown")
        update_one_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(return_value=None)),
            patch("backend.routes.webhooks.db_supabase.update_one", update_one_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        update_one_mock.assert_not_awaited()

    def test_push_notification_failure_is_swallowed(self):
        from backend.routes import webhooks as wh

        data_obj = {"id": "sub_del_push_fail"}
        raw = _make_stripe_event("customer.subscription.deleted", data_obj, event_id="evt_del_push_fail")

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.find_one",
                AsyncMock(
                    return_value={"id": "row_pf", "driver_id": "drv_pf", "status": "active", "user_id": "user_pf"}
                ),
            ),
            patch("backend.routes.webhooks.db_supabase.update_one", AsyncMock()),
            patch(
                "backend.routes.webhooks.send_push_notification",
                AsyncMock(side_effect=Exception("push down")),
            ),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True


# ---------------------------------------------------------------------------
# invoice.payment_failed — no-row branch + push swallow
# ---------------------------------------------------------------------------


class TestInvoicePaymentFailedBranches:
    def test_no_matching_row_is_a_noop_ack(self):
        from backend.routes import webhooks as wh

        data_obj = {"subscription": "sub_unknown_fail"}
        raw = _make_stripe_event("invoice.payment_failed", data_obj, event_id="evt_ipf_norow")
        update_one_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(return_value=None)),
            patch("backend.routes.webhooks.db_supabase.update_one", update_one_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        update_one_mock.assert_not_awaited()

    def test_push_notification_failure_is_swallowed(self):
        from backend.routes import webhooks as wh

        data_obj = {"subscription": "sub_pf_2"}
        raw = _make_stripe_event("invoice.payment_failed", data_obj, event_id="evt_ipf_push_fail")

        async def _find_one(table, filt):
            if table == "driver_subscriptions":
                return {"id": "row_ipf", "driver_id": "drv_ipf"}
            if table == "drivers":
                return {"id": "drv_ipf", "user_id": "user_ipf"}
            return None

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(side_effect=_find_one)),
            patch("backend.routes.webhooks.db_supabase.update_one", AsyncMock()),
            patch(
                "backend.routes.webhooks.send_push_notification",
                AsyncMock(side_effect=Exception("push down")),
            ),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True


# ---------------------------------------------------------------------------
# customer.subscription.updated — "active" positive reactivation
# ---------------------------------------------------------------------------


class TestSubscriptionUpdatedActiveReactivates:
    def test_active_status_on_non_cancelled_row_reactivates(self):
        from backend.routes import webhooks as wh

        data_obj = {"id": "sub_reactivate", "status": "active"}
        raw = _make_stripe_event("customer.subscription.updated", data_obj, event_id="evt_sub_active")
        update_one_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.find_one",
                AsyncMock(return_value={"id": "row_react", "status": "past_due", "cancelled_at": None}),
            ),
            patch("backend.routes.webhooks.db_supabase.update_one", update_one_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        update_one_mock.assert_awaited_once_with(
            "driver_subscriptions", {"id": "row_react"}, {"status": "active", "payment_status": "paid"}
        )


# ---------------------------------------------------------------------------
# payment_intent.succeeded — GST receipt failure swallow
# payment_intent.payment_failed — ride-update-None (500) + driver lookup swallow
# ---------------------------------------------------------------------------


class TestPaymentIntentSucceededReceiptFailure:
    def test_receipt_send_failure_does_not_abort_the_webhook(self):
        from backend.routes import webhooks as wh

        data_obj = {
            "id": "pi_rcpt_swallow",
            "metadata": {"ride_id": "ride_rcpt_swallow", "user_id": "user_rs"},
            "amount_received": 3000,
        }
        raw = _make_stripe_event("payment_intent.succeeded", data_obj, event_id="evt_pi_rcpt_swallow")

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.get_ride",
                AsyncMock(
                    return_value={
                        "id": "ride_rcpt_swallow",
                        "grand_total": 30.00,
                        "rider_id": "rider_rs",
                        "payment_status": "failed",
                    }
                ),
            ),
            patch(
                "backend.routes.webhooks.db_supabase.update_ride",
                AsyncMock(return_value={"id": "ride_rcpt_swallow"}),
            ),
            patch("backend.services.payment_service.record_payment_event", AsyncMock()),
            patch("services.payment_service.record_payment_event", AsyncMock()),
            patch(
                "backend.services.payment_service.send_ride_receipt",
                AsyncMock(side_effect=Exception("email down")),
            ),
            patch(
                "services.payment_service.send_ride_receipt",
                AsyncMock(side_effect=Exception("email down")),
            ),
            patch("backend.routes.webhooks.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True


class TestPaymentIntentPaymentFailedBranches:
    def test_ride_update_returns_none_raises_500(self):
        from fastapi import HTTPException

        from backend.routes import webhooks as wh

        data_obj = {
            "id": "pi_fail_notfound",
            "metadata": {"ride_id": "ride_fail_gone", "user_id": "user_ff"},
            "last_payment_error": {"message": "Card declined"},
        }
        raw = _make_stripe_event("payment_intent.payment_failed", data_obj, event_id="evt_pf_notfound")

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.db_supabase.update_ride", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(wh.stripe_webhook(request=_req()))

        assert exc.value.status_code == 500

    def test_driver_lookup_exception_is_swallowed(self):
        """A DB blip while looking up the driver to notify must not fail the
        whole webhook — the rider-facing failure notification already went
        out; the driver nudge is best-effort."""
        from backend.routes import webhooks as wh

        data_obj = {
            "id": "pi_fail_driver_lookup",
            "metadata": {"ride_id": "ride_dl", "user_id": "user_dl"},
            "last_payment_error": {"message": "Card declined"},
        }
        raw = _make_stripe_event("payment_intent.payment_failed", data_obj, event_id="evt_pf_driver_lookup")

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.update_ride",
                AsyncMock(return_value={"id": "ride_dl"}),
            ),
            patch(
                "backend.routes.webhooks.db_supabase.get_ride",
                AsyncMock(side_effect=Exception("db blip")),
            ),
            patch("backend.routes.webhooks.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True


# ---------------------------------------------------------------------------
# SES helpers — _confirm_sns_subscription / _suppress_marketing_email /
# _handle_ses_notification malformed-JSON / route UnsubscribeConfirmation
# ---------------------------------------------------------------------------


class TestConfirmSnsSubscriptionBranches:
    @pytest.mark.anyio
    async def test_untrusted_url_refuses_without_http_call(self):
        from backend.routes.webhooks import _confirm_sns_subscription

        with patch("httpx.AsyncClient") as client_cls:
            await _confirm_sns_subscription({"SubscribeURL": "https://evil.example.com/confirm"})
        client_cls.assert_not_called()

    @pytest.mark.anyio
    async def test_non_2xx_confirm_response_logged_not_raised(self):
        from backend.routes.webhooks import _confirm_sns_subscription

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=MagicMock(status_code=500))

        with (
            patch("backend.utils.sns_verify.is_trusted_sns_url", return_value=True),
            patch("utils.sns_verify.is_trusted_sns_url", return_value=True),
            patch("httpx.AsyncClient", return_value=client),
        ):
            await _confirm_sns_subscription({"SubscribeURL": "https://sns.ca-central-1.amazonaws.com/confirm"})
        # Must not raise — logged and returned.

    @pytest.mark.anyio
    async def test_request_exception_is_swallowed(self):
        from backend.routes.webhooks import _confirm_sns_subscription

        with (
            patch("backend.utils.sns_verify.is_trusted_sns_url", return_value=True),
            patch("utils.sns_verify.is_trusted_sns_url", return_value=True),
            patch("httpx.AsyncClient", side_effect=Exception("network down")),
        ):
            await _confirm_sns_subscription({"SubscribeURL": "https://sns.ca-central-1.amazonaws.com/confirm"})
        # Must not raise.


class TestSuppressMarketingEmailBranches:
    @pytest.mark.anyio
    async def test_blank_normalized_target_returns_early(self):
        from backend.routes.webhooks import _suppress_marketing_email

        find_mock = AsyncMock()
        with (
            patch("backend.services.marketing_consent.normalize_target", return_value=""),
            patch("services.marketing_consent.normalize_target", return_value=""),
            patch("backend.routes.webhooks.db_supabase.find_one", find_mock),
        ):
            await _suppress_marketing_email("not-an-email", reason="bounce", detail=None, message_id="m1")
        find_mock.assert_not_awaited()

    @pytest.mark.anyio
    async def test_user_lookup_exception_does_not_block_suppression(self):
        from backend.routes.webhooks import _suppress_marketing_email

        add_suppression_mock = AsyncMock()
        with (
            patch("backend.services.marketing_consent.normalize_target", return_value="x@example.com"),
            patch("services.marketing_consent.normalize_target", return_value="x@example.com"),
            patch(
                "backend.routes.webhooks.db_supabase.find_one",
                AsyncMock(side_effect=Exception("db down")),
            ),
            patch("backend.services.marketing_consent.add_marketing_suppression", add_suppression_mock),
            patch("services.marketing_consent.add_marketing_suppression", add_suppression_mock),
        ):
            await _suppress_marketing_email("x@example.com", reason="bounce", detail=None, message_id="m2")
        # Attribution failed, but suppression still applied (user_id=None).
        add_suppression_mock.assert_awaited_once()
        assert add_suppression_mock.await_args.kwargs["user_id"] is None


class TestSesRouteMiscBranches:
    """Uses the conftest `test_client` fixture (not an ad-hoc TestClient(app))
    — matching test_ses_webhook.py's own pattern is required for coverage to
    attribute hits correctly: TestClient's ASGI bridge runs the app in a
    portal thread, and constructing a bare TestClient(app) outside a `with`
    block was observed to execute real, assertion-passing requests that
    nonetheless left routes/webhooks.py showing 0% on the exercised lines —
    while the fixture's `with TestClient(app) as client: yield client` form
    (which starts the portal via the context manager before any request)
    attributes coverage normally, same as every other route test in this repo."""

    def test_malformed_message_json_is_ignored_not_500(self, test_client):
        with (
            patch("backend.utils.sns_verify.verify_sns_signature", return_value=True),
            patch("utils.sns_verify.verify_sns_signature", return_value=True),
        ):
            r = test_client.post(
                "/api/v1/webhooks/ses",
                json={"Type": "Notification", "Message": "{not valid json"},
            )
        assert r.status_code == 200
        assert r.json().get("ignored") == "bad_message"

    def test_unsubscribe_confirmation_acknowledged_without_action(self, test_client):
        with (
            patch("backend.utils.sns_verify.verify_sns_signature", return_value=True),
            patch("utils.sns_verify.verify_sns_signature", return_value=True),
        ):
            r = test_client.post(
                "/api/v1/webhooks/ses",
                json={"Type": "UnsubscribeConfirmation"},
            )
        assert r.status_code == 200
        assert r.json() == {"received": True, "ignored": "UnsubscribeConfirmation"}


# ---------------------------------------------------------------------------
# Twilio inbound — signed-signature-verification path (existing tests only
# cover the dev auth_token-unset bypass)
# ---------------------------------------------------------------------------


class TestTwilioInboundSignedPath:
    """See TestSesRouteMiscBranches' docstring: uses the conftest
    `test_client` fixture rather than an ad-hoc TestClient(app) so coverage
    attributes hits correctly."""

    def test_invalid_signature_rejected_403(self, test_client):
        _ensure_real_twilio_imported()
        with patch(
            "backend.routes.webhooks.get_app_settings",
            AsyncMock(return_value={"twilio_auth_token": "secret_token"}),
        ):
            r = test_client.post(
                "/api/v1/webhooks/twilio-inbound",
                data={"Body": "STOP", "From": "+13065551234"},
                headers={"X-Twilio-Signature": "totally-wrong"},
            )
        assert r.status_code == 403

    def test_valid_signature_accepted_and_processes_stop(self, test_client):
        _ensure_real_twilio_imported()
        from twilio.request_validator import RequestValidator

        auth_token = "secret_token_2"
        # Build the exact URL the handler reconstructs (PUBLIC_API_BASE_URL + path).
        from backend.core.config import settings as app_settings

        base = (app_settings.PUBLIC_API_BASE_URL or "").rstrip("/")
        url = f"{base}/api/v1/webhooks/twilio-inbound"
        params = {"Body": "STOP", "From": "+13065559999"}
        sig = RequestValidator(auth_token).compute_signature(url, params)

        with (
            patch(
                "backend.routes.webhooks.get_app_settings",
                AsyncMock(return_value={"twilio_auth_token": auth_token}),
            ),
            patch("backend.db_supabase.find_one", AsyncMock(return_value=None)),
            patch("db_supabase.find_one", AsyncMock(return_value=None)),
            patch("backend.services.marketing_consent.add_marketing_suppression", AsyncMock()),
            patch("services.marketing_consent.add_marketing_suppression", AsyncMock()),
        ):
            r = test_client.post(
                "/api/v1/webhooks/twilio-inbound",
                data=params,
                headers={"X-Twilio-Signature": sig},
            )
        assert r.status_code == 200
        assert "<Response>" in r.text


# ---------------------------------------------------------------------------
# Small remaining branches: amount_due fallback, generic signature-verify
# exception, wallet_topup push swallow, charge.refunded no-PI-at-all orphan
# path via full dispatch, subscription.deleted different-stripe-sub-linked
# warning, invoice.paid-without-subscription-id skip, renewal/payout push
# swallows, _suppress_address blank-normalized-email early return, and
# _resolve_user_id_by_phone's exception swallow.
# ---------------------------------------------------------------------------


class TestHandleRideInvoicePaidAmountDueFallback:
    def test_amount_paid_none_falls_back_to_amount_due(self):
        from backend.routes import webhooks as wh

        data_obj = {
            "id": "in_amt_due",
            "metadata": {"ride_id": "ride_amt_due"},
            "amount_due": 777,
            "payment_intent": "pi_amt_due",
            # amount_paid deliberately absent (None via .get default)
        }
        raw = _make_stripe_event("invoice.paid", data_obj, event_id="evt_amt_due")
        record_mock = AsyncMock()
        update_ride_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.get_ride",
                AsyncMock(
                    return_value={"id": "ride_amt_due", "rider_id": "u1", "payment_status": "failed", "tip_amount": "0"}
                ),
            ),
            patch("backend.routes.webhooks.db_supabase.update_ride", update_ride_mock),
            patch("backend.services.payment_service.record_payment_event", record_mock),
            patch("services.payment_service.record_payment_event", record_mock),
            patch("backend.services.payment_service.send_ride_receipt", AsyncMock(return_value=True)),
            patch("services.payment_service.send_ride_receipt", AsyncMock(return_value=True)),
            patch("backend.socket_manager.manager.send_personal_message", AsyncMock()),
            patch("socket_manager.manager.send_personal_message", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        assert record_mock.await_args.kwargs["amount_cents"] == 777


class TestSignatureVerificationGenericException:
    def test_non_stripe_exception_during_construct_event_treated_as_verify_failure(self):
        """A non-ValueError, non-SignatureVerificationError exception (e.g. a
        transient bug in the stripe SDK itself) must still be treated as a
        failed-verification attempt for THIS secret and continue trying the
        next candidate secret, rather than propagating raw."""
        from backend.routes import webhooks as wh

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(
                __import__("stripe").Webhook,
                "construct_event",
                side_effect=RuntimeError("unexpected SDK error"),
            ),
        ):
            with pytest.raises(Exception) as exc:
                asyncio.run(wh.stripe_webhook(request=_req()))
        assert exc.value.status_code == 400


class TestWalletTopupPushFailureSwallowed:
    def test_push_failure_does_not_abort_wallet_topup(self):
        from backend.routes import webhooks as wh

        data_obj = {
            "id": "pi_wallet_push_fail",
            "metadata": {"scope": "wallet_topup", "wallet_id": "w1", "user_id": "u1", "amount_cad": "5.00"},
        }
        raw = _make_stripe_event("payment_intent.succeeded", data_obj, event_id="evt_wallet_push_fail")

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.db_supabase.wallet_apply_credit",
                AsyncMock(return_value={"balance_after": "10.00", "deduped": False}),
            ),
            patch(
                "db_supabase.wallet_apply_credit",
                AsyncMock(return_value={"balance_after": "10.00", "deduped": False}),
            ),
            patch(
                "backend.routes.webhooks.send_push_notification",
                AsyncMock(side_effect=Exception("push down")),
            ),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        assert result["scope"] == "wallet_topup"


class TestChargeRefundedNoPaymentIntentAtAllFullDispatch:
    def test_blank_payment_intent_routes_to_no_payment_intent_orphan(self):
        from backend.routes import webhooks as wh

        # No "payment_intent" key at all → charge.get("payment_intent") is None.
        data_obj = {"amount_refunded": 250, "currency": "cad"}
        raw = _make_stripe_event("charge.refunded", data_obj, event_id="evt_refund_no_pi")
        insert_mock = AsyncMock()
        get_rows_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.get_rows", get_rows_mock),
            patch("backend.routes.webhooks.db_supabase.insert_one", insert_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        get_rows_mock.assert_not_awaited()  # never even looks for a ride without a PI
        row = insert_mock.await_args.args[1]
        assert row["reason"] == "no_payment_intent"
        assert row["payment_intent_id"] is None


class TestSubscriptionDeletedDifferentStripeSubWarning:
    def test_active_row_linked_to_different_stripe_sub_is_not_cancelled(self):
        """A customer-id-matched active row that's ALREADY linked to a
        DIFFERENT stripe_subscription_id is a newer pass (e.g. after a plan
        switch) — this older sub's deletion must not touch it."""
        from backend.routes import webhooks as wh

        data_obj = {"id": "sub_old_deleted", "customer": "cus_multi"}
        raw = _make_stripe_event("customer.subscription.deleted", data_obj, event_id="evt_del_diff_sub")
        update_one_mock = AsyncMock()

        async def _find_one(table, filt):
            if table == "driver_subscriptions" and "stripe_subscription_id" in filt:
                return None
            if table == "users":
                return {"id": "user_multi"}
            if table == "drivers" and filt.get("user_id") == "user_multi":
                return {"id": "driver_multi"}
            if table == "driver_subscriptions" and filt.get("driver_id") == "driver_multi":
                # Active row exists but is linked to a DIFFERENT (newer) sub.
                return {"id": "row_newer", "stripe_subscription_id": "sub_newer_current"}
            return None

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(side_effect=_find_one)),
            patch("backend.routes.webhooks.db_supabase.update_one", update_one_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        update_one_mock.assert_not_awaited()  # newer pass left untouched


class TestInvoicePaidRenewalPushFailureSwallowed:
    def test_push_failure_on_renewal_cycle_does_not_abort(self):
        """Mirrors test_webhooks_main.py's
        test_invoice_paid_renews_and_notifies_on_cycle mocking shape exactly,
        but makes the renewal push raise — the renewal itself (DB write)
        must still have succeeded and the webhook must still ack 200."""
        from datetime import datetime, timedelta, timezone

        from backend.routes import webhooks as wh

        period_end = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
        data_obj = {
            "id": "in_push_fail",
            "subscription": "sub_push_fail",
            "billing_reason": "subscription_cycle",
            "lines": {"data": [{"period": {"end": period_end}}]},
        }
        raw = _make_stripe_event("invoice.paid", data_obj, event_id="evt_renewal_push_fail")
        update_mock = AsyncMock()
        find_mock = AsyncMock(
            side_effect=[
                {"id": "row1", "driver_id": "d1", "plan_id": "p1"},
                {"id": "p1", "duration_days": 30, "price": 49.99},
                None,
                {"id": "d1", "user_id": "u1"},
            ]
        )

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", find_mock),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
            patch(
                "backend.routes.webhooks.send_push_notification",
                AsyncMock(side_effect=Exception("push down")),
            ),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True
        update_mock.assert_awaited_once()  # renewal write still happened


class TestInvoicePaidWithoutSubscriptionIdSkipped:
    def test_no_ride_metadata_and_no_subscription_id_logs_and_skips(self):
        from backend.routes import webhooks as wh

        data_obj = {"id": "in_neither", "amount_paid": 0}  # no metadata.ride_id, no subscription
        raw = _make_stripe_event("invoice.paid", data_obj, event_id="evt_inv_neither")

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(return_value=None)),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True


class TestPayoutFailedPushFailureSwallowed:
    def test_push_failure_does_not_abort_payout_failed_handling(self):
        from backend.routes import webhooks as wh

        data_obj = {"id": "po_push_fail", "failure_message": "account closed"}
        raw = _make_stripe_event("payout.failed", data_obj, event_id="evt_payout_push_fail")

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(__import__("stripe").Webhook, "construct_event", return_value=_event_obj(raw)),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.find_one",
                AsyncMock(
                    side_effect=lambda table, filt: (
                        {"id": "payout_row_1", "driver_id": "drv_payout"}
                        if table == "payouts"
                        else {"id": "drv_payout", "user_id": "user_payout"}
                    )
                ),
            ),
            patch("backend.routes.webhooks.db_supabase.update_one", AsyncMock()),
            patch(
                "backend.routes.webhooks.send_push_notification",
                AsyncMock(side_effect=Exception("push down")),
            ),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_req()))

        assert result["received"] is True


class TestSuppressAddressBlankNormalizedEmail:
    @pytest.mark.anyio
    async def test_blank_normalized_email_returns_without_db_call(self):
        from backend.routes.webhooks import _suppress_address

        find_mock = AsyncMock()
        with (
            patch("backend.utils.email_provider.normalize_email", return_value=""),
            patch("utils.email_provider.normalize_email", return_value=""),
            patch("backend.routes.webhooks.db_supabase.find_one", find_mock),
        ):
            await _suppress_address("garbage", reason="bounce", detail=None, message_id="m1")
        find_mock.assert_not_awaited()


class TestResolveUserIdByPhoneException:
    @pytest.mark.anyio
    async def test_exception_returns_none_never_raises(self):
        from backend.routes.webhooks import _resolve_user_id_by_phone

        with (
            patch("backend.services.marketing_consent.normalize_target", side_effect=Exception("boom")),
            patch("services.marketing_consent.normalize_target", side_effect=Exception("boom")),
        ):
            out = await _resolve_user_id_by_phone("+13065550100")
        assert out is None
