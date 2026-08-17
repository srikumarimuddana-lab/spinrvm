"""Additional coverage for backend/routes/webhooks.py.

A1c Sub-tier C — test-only, no application code changed. This file targets the
largest previously-uncovered blocks in webhooks.py (748 stmts / 184 missing at
75% coverage), prioritizing the money-moving Stripe handlers per the task
brief:

- charge.refunded (matched-ride settle path, both orphan branches, push-
  notification failure swallowed)
- charge.dispute.created / charge.dispute.closed (won/lost, WS broadcast
  failure swallowed, no-existing-row fallback lookup)
- customer.subscription.deleted (primary stripe_subscription_id match,
  customer-id fallback match, "different sub — don't cancel the newer pass"
  guard, and the fully-unmatched warn-only path)
- account.updated (Connect KYC mirror dispatch)
- payment_intent.succeeded receipt-send failure swallowed
- payment_intent.payment_failed ride-not-found (500, Stripe retries) and the
  driver-lookup-exception-swallowed path
- Twilio inbound signature verification (both the valid and invalid-signature
  branches, which no existing test exercises — test_twilio_inbound.py only
  covers the auth-token-unset dev-bypass path) plus the missing-From-phone
  early return and the _resolve_user_id_by_phone exception path

100% was not attempted — this file is large (queue/ledger/receipt/push
fan-out on every branch) and many smaller scattered single-line gaps (mostly
defensive `except Exception: pass`-style guards) are left uncovered by design,
per the task brief's guidance to prioritize the biggest and most consequential
blocks rather than exhaustive line coverage.

FOUND, INVESTIGATED, NOT A BUG (see TestStripeWebhookProcessingRace below) —
the payment_intent.succeeded handler settles a ride whose payment_status is
already 'processing', unlike the sibling _handle_ride_invoice_paid handler,
which raises instead. Initially flagged as a "found not fixed" money-safety
race (same 'processing' status, two different handlers, two different
behaviors) and a matching-the-sibling fix was proposed and approved. Before
applying it, a pre-existing regression test
(TestWebhookTimeoutDivergence::test_finalizes_ride_stuck_in_processing in
test_webhooks_main.py, predates this session) was found that pins the
OPPOSITE behavior as intentional: routes/payments.py's confirm_payment
atomically claims a ride into payment_status='processing' via
claim_ride_payment_processing() BEFORE talking to Stripe; if that
synchronous call then times out or the process crashes, the ride is
permanently stuck in 'processing' with no other recovery path (the
stuck-ride sweeper only handles ride *state*, not payment_status) — this
webhook is the documented safety net that unsticks it. Raising here instead
would reintroduce a real "ride stuck in processing forever" bug on any
client-side confirm timeout. The invoice.paid sibling's 'processing' really
does mean a distinct concurrent charge (no such claim/rescue relationship
exists for invoices), so the two handlers' differing behavior is correct,
not a bug — the fix was reverted. See
docs/change-log/2026-08-03-a1c-found-not-fixed-bugfixes.md Entry 13.
"""

from __future__ import annotations

import asyncio
import builtins
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


def _make_stripe_event(event_type: str, data_object: dict, event_id: str = "evt_test_1") -> dict:
    return {
        "id": event_id,
        "type": event_type,
        "data": {"object": data_object},
    }


def _event_obj(event_type: str, data_object: dict, event_id: str) -> MagicMock:
    raw = _make_stripe_event(event_type, data_object, event_id=event_id)
    obj = MagicMock()
    obj.get = lambda k, d=None: raw.get(k, d)
    obj.to_dict_recursive = lambda: raw
    return obj


def _settings_fn():
    async def f():
        return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

    return f


def _mock_req():
    req = MagicMock()
    req.body = AsyncMock(return_value=b"payload")
    req.headers = {"stripe-signature": "sig"}
    return req


# ---------------------------------------------------------------------------
# charge.refunded — matched ride, both orphan branches, push failure swallowed
# ---------------------------------------------------------------------------


class TestStripeWebhookChargeRefunded:
    def test_matched_ride_marks_refunded_and_records_ledger(self):
        import stripe

        from backend.routes import webhooks as wh

        charge = {"id": "ch_1", "payment_intent": "pi_refund_1", "amount_refunded": 1500, "currency": "cad"}
        event_obj = _event_obj("charge.refunded", charge, "evt_refund_1")
        ride = {"id": "ride_r1", "rider_id": "rider_1"}
        update_mock = AsyncMock()
        record_refund_mock = AsyncMock()
        push_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(return_value=[ride])),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
            patch("backend.services.payment_service.record_refund_event", record_refund_mock),
            patch("backend.routes.webhooks.send_push_notification", push_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True
        update_mock.assert_awaited_once()
        assert update_mock.await_args.args[0] == "rides"
        assert update_mock.await_args.args[2]["payment_status"] == "refunded"
        assert update_mock.await_args.args[2]["refund_amount"] == "15.00"
        record_refund_mock.assert_awaited_once()
        assert record_refund_mock.await_args.kwargs["refund_cents"] == 1500
        push_mock.assert_awaited_once()

    def test_refund_push_notification_failure_swallowed(self):
        import stripe

        from backend.routes import webhooks as wh

        charge = {"id": "ch_1b", "payment_intent": "pi_refund_1b", "amount_refunded": 500}
        event_obj = _event_obj("charge.refunded", charge, "evt_refund_1b")
        ride = {"id": "ride_r1b", "rider_id": "rider_1b"}

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(return_value=[ride])),
            patch("backend.routes.webhooks.db_supabase.update_one", AsyncMock()),
            patch("backend.services.payment_service.record_refund_event", AsyncMock()),
            patch("backend.routes.webhooks.send_push_notification", AsyncMock(side_effect=Exception("fcm down"))),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True

    def test_no_payment_intent_records_orphan(self):
        import stripe

        from backend.routes import webhooks as wh

        charge = {"id": "ch_2", "payment_intent": None, "amount_refunded": 500}
        event_obj = _event_obj("charge.refunded", charge, "evt_refund_2")
        orphan_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks._record_orphan_refund", orphan_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True
        orphan_mock.assert_awaited_once()
        assert orphan_mock.await_args.kwargs["reason"] == "no_payment_intent"
        assert orphan_mock.await_args.kwargs["payment_intent_id"] is None

    def test_no_ride_for_payment_intent_records_orphan(self):
        import stripe

        from backend.routes import webhooks as wh

        charge = {"id": "ch_3", "payment_intent": "pi_orphan_1", "amount_refunded": 700}
        event_obj = _event_obj("charge.refunded", charge, "evt_refund_3")
        orphan_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.webhooks._record_orphan_refund", orphan_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True
        orphan_mock.assert_awaited_once()
        assert orphan_mock.await_args.kwargs["reason"] == "no_ride_for_pi"
        assert orphan_mock.await_args.kwargs["payment_intent_id"] == "pi_orphan_1"


# ---------------------------------------------------------------------------
# charge.dispute.created — chargeback record, ride flag, admin WS broadcast
# ---------------------------------------------------------------------------


class TestStripeWebhookDisputeCreated:
    def test_dispute_created_with_matched_ride(self):
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {
            "id": "dp_1",
            "payment_intent": "pi_dispute_1",
            "amount": 2500,
            "reason": "fraudulent",
            "status": "warning_needs_response",
        }
        event_obj = _event_obj("charge.dispute.created", data_obj, "evt_dispute_1")
        ride = {"id": "ride_dispute_1"}
        insert_mock = AsyncMock()
        update_mock = AsyncMock()
        broadcast_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(return_value=[ride])),
            patch("backend.routes.webhooks.db_supabase.insert_one", insert_mock),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
            patch("backend.socket_manager.manager.broadcast_to_admins", broadcast_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True
        insert_mock.assert_awaited_once()
        assert insert_mock.await_args.args[0] == "stripe_disputes"
        assert insert_mock.await_args.args[1]["ride_id"] == "ride_dispute_1"
        update_mock.assert_awaited_once()
        assert update_mock.await_args.args[0] == "rides"
        assert update_mock.await_args.args[2]["payment_status"] == "disputed"
        broadcast_mock.assert_awaited_once()

    def test_dispute_created_no_matched_ride_skips_ride_update(self):
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {"id": "dp_2", "payment_intent": "", "amount": 100, "reason": "duplicate", "status": "won"}
        event_obj = _event_obj("charge.dispute.created", data_obj, "evt_dispute_2")
        update_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.insert_one", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
            patch("backend.socket_manager.manager.broadcast_to_admins", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True
        update_mock.assert_not_awaited()

    def test_dispute_created_ws_broadcast_failure_swallowed(self):
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {
            "id": "dp_3",
            "payment_intent": "pi_dispute_3",
            "amount": 500,
            "reason": "other",
            "status": "needs_response",
        }
        event_obj = _event_obj("charge.dispute.created", data_obj, "evt_dispute_3")

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.webhooks.db_supabase.insert_one", AsyncMock()),
            patch("backend.socket_manager.manager.broadcast_to_admins", AsyncMock(side_effect=Exception("ws down"))),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True


# ---------------------------------------------------------------------------
# charge.dispute.closed — won restores paid, lost flags dispute_lost, and the
# no-existing-row fallback that resolves the ride via the rides table.
# ---------------------------------------------------------------------------


class TestStripeWebhookDisputeClosed:
    def test_dispute_won_restores_paid(self):
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {"id": "dp_close_1", "payment_intent": "pi_close_1", "status": "won"}
        event_obj = _event_obj("charge.dispute.closed", data_obj, "evt_close_1")
        existing = {"id": "disp_row_1", "ride_id": "ride_close_1"}
        update_mock = AsyncMock()
        find_one_mock = AsyncMock(return_value=existing)

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", find_one_mock),
            patch(
                "backend.routes.webhooks.db_supabase.get_rows",
                AsyncMock(return_value=[{"id": "ride_close_1", "rider_id": "rider_close_1"}]),
            ),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True
        calls = update_mock.await_args_list
        assert any(c.args[0] == "stripe_disputes" and c.args[2]["status"] == "won" for c in calls)
        assert any(c.args[0] == "rides" and c.args[2]["payment_status"] == "paid" for c in calls)
        # B27: lookup must be keyed on the dispute's own id, not payment_intent_id.
        find_one_call = find_one_mock.await_args
        assert find_one_call.args[0] == "stripe_disputes"
        assert find_one_call.args[1] == {"stripe_dispute_id": "dp_close_1"}

    def test_dispute_lost_marks_ride_dispute_lost(self):
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {"id": "dp_close_2", "payment_intent": "pi_close_2", "status": "lost"}
        event_obj = _event_obj("charge.dispute.closed", data_obj, "evt_close_2")
        existing = {"id": "disp_row_2", "ride_id": "ride_close_2"}
        update_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(return_value=existing)),
            patch(
                "backend.routes.webhooks.db_supabase.get_rows",
                AsyncMock(return_value=[{"id": "ride_close_2", "rider_id": "rider_close_2"}]),
            ),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True
        calls = update_mock.await_args_list
        assert any(c.args[0] == "rides" and c.args[2]["payment_status"] == "dispute_lost" for c in calls)

    def test_warning_closed_restores_paid_not_lost(self):
        """B27 regression pin: `warning_closed` is an inquiry that resolved
        without becoming a real chargeback — the charge stands, same as
        `won`. Must NOT fall into the `dispute_lost` branch."""
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {"id": "dp_close_warn", "payment_intent": "pi_close_warn", "status": "warning_closed"}
        event_obj = _event_obj("charge.dispute.closed", data_obj, "evt_close_warn")
        existing = {"id": "disp_row_warn", "ride_id": "ride_close_warn"}
        update_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(return_value=existing)),
            patch(
                "backend.routes.webhooks.db_supabase.get_rows",
                AsyncMock(return_value=[{"id": "ride_close_warn", "rider_id": "rider_close_warn"}]),
            ),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True
        calls = update_mock.await_args_list
        ride_calls = [c for c in calls if c.args[0] == "rides"]
        assert len(ride_calls) == 1
        assert ride_calls[0].args[2]["payment_status"] == "paid"

    def test_no_existing_dispute_row_falls_back_to_ride_lookup(self):
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {"id": "dp_close_3", "payment_intent": "pi_close_3", "status": "won"}
        event_obj = _event_obj("charge.dispute.closed", data_obj, "evt_close_3")
        update_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(return_value=None)),
            patch(
                "backend.routes.webhooks.db_supabase.get_rows",
                AsyncMock(return_value=[{"id": "ride_close_3"}]),
            ),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True
        # No pre-existing stripe_disputes row → only the rides update fires.
        update_mock.assert_awaited_once()
        assert update_mock.await_args.args[0] == "rides"
        assert update_mock.await_args.args[1] == {"id": "ride_close_3"}

    def test_pi_less_close_updates_only_its_own_dispute_row(self):
        """B27: two disputes with no payment_intent (Stripe sends "" for a
        PI-less charge) must not collide via a payment_intent_id lookup —
        keying on stripe_dispute_id (the table's real unique index) means
        each closes independently."""
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {"id": "dp_close_no_pi", "payment_intent": "", "status": "lost"}
        event_obj = _event_obj("charge.dispute.closed", data_obj, "evt_close_no_pi")
        existing = {"id": "disp_row_no_pi", "ride_id": None}
        update_mock = AsyncMock()
        find_one_mock = AsyncMock(return_value=existing)

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", find_one_mock),
            patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True
        find_one_call = find_one_mock.await_args
        assert find_one_call.args[1] == {"stripe_dispute_id": "dp_close_no_pi"}
        # No ride linked (ride_id None, PI empty) → only the dispute row updates.
        update_mock.assert_awaited_once()
        assert update_mock.await_args.args[0] == "stripe_disputes"
        assert update_mock.await_args.args[1] == {"id": "disp_row_no_pi"}

    def test_balance_transactions_recorded_as_financial_events(self):
        """B27: the disputed-amount debit and Stripe's own dispute fee must
        reach the ledger — previously neither did."""
        import stripe

        from backend.routes import webhooks as wh

        balance_transactions = [
            {"id": "txn_1", "type": "adjustment", "amount": -2500, "fee": 0, "currency": "cad"},
            {"id": "txn_2", "type": "stripe_fee", "amount": -1500, "fee": 1500, "currency": "cad"},
        ]
        data_obj = {
            "id": "dp_close_bt",
            "payment_intent": "pi_close_bt",
            "status": "lost",
            "balance_transactions": balance_transactions,
        }
        event_obj = _event_obj("charge.dispute.closed", data_obj, "evt_close_bt")
        existing = {"id": "disp_row_bt", "ride_id": "ride_close_bt"}
        ledger_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(return_value=existing)),
            patch(
                "backend.routes.webhooks.db_supabase.get_rows",
                AsyncMock(return_value=[{"id": "ride_close_bt", "rider_id": "rider_close_bt"}]),
            ),
            patch("backend.routes.webhooks.db_supabase.update_one", AsyncMock()),
            patch("backend.services.payment_service.record_dispute_close_events", ledger_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True
        ledger_mock.assert_awaited_once()
        kwargs = ledger_mock.await_args.kwargs
        assert kwargs["dispute_id"] == "dp_close_bt"
        assert kwargs["ride_id"] == "ride_close_bt"
        assert kwargs["user_id"] == "rider_close_bt"
        assert kwargs["balance_transactions"] == balance_transactions
        assert kwargs["dispute_status"] == "lost"

    def test_no_balance_transactions_skips_ledger_call(self):
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {"id": "dp_close_no_bt", "payment_intent": "pi_close_no_bt", "status": "won"}
        event_obj = _event_obj("charge.dispute.closed", data_obj, "evt_close_no_bt")
        existing = {"id": "disp_row_no_bt", "ride_id": "ride_close_no_bt"}
        ledger_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(return_value=existing)),
            patch(
                "backend.routes.webhooks.db_supabase.get_rows",
                AsyncMock(return_value=[{"id": "ride_close_no_bt", "rider_id": "rider_x"}]),
            ),
            patch("backend.routes.webhooks.db_supabase.update_one", AsyncMock()),
            patch("backend.services.payment_service.record_dispute_close_events", ledger_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True
        ledger_mock.assert_not_awaited()

    def test_balance_transactions_present_but_no_rider_id_skips_ledger_call(self):
        """B27: financial_events.user_id is NOT NULL REFERENCES users(id) --
        a dispute whose ride/rider can't be resolved must not even attempt
        the ledger write (the call/import is skipped at the webhook layer,
        on top of record_dispute_close_events's own no-op-if-falsy guard)."""
        import stripe

        from backend.routes import webhooks as wh

        balance_transactions = [{"id": "txn_orphan", "type": "adjustment", "amount": -1000, "fee": 0}]
        data_obj = {
            "id": "dp_close_orphan",
            "payment_intent": "",
            "status": "lost",
            "balance_transactions": balance_transactions,
        }
        event_obj = _event_obj("charge.dispute.closed", data_obj, "evt_close_orphan")
        existing = {"id": "disp_row_orphan", "ride_id": None}
        ledger_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(return_value=existing)),
            patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.webhooks.db_supabase.update_one", AsyncMock()),
            patch("backend.services.payment_service.record_dispute_close_events", ledger_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True
        ledger_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# charge.dispute.updated — intermediate status transitions mirrored onto the
# stripe_disputes row (B27); no ride/ledger side effects.
# ---------------------------------------------------------------------------


class TestStripeWebhookDisputeUpdated:
    def test_status_mirrored_onto_existing_row(self):
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {"id": "dp_updated_1", "status": "under_review"}
        event_obj = _event_obj("charge.dispute.updated", data_obj, "evt_updated_1")
        existing = {"id": "disp_row_updated_1"}
        update_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(return_value=existing)),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True
        update_mock.assert_awaited_once()
        assert update_mock.await_args.args[0] == "stripe_disputes"
        assert update_mock.await_args.args[1] == {"id": "disp_row_updated_1"}
        assert update_mock.await_args.args[2]["status"] == "under_review"

    def test_no_matching_row_is_a_noop(self):
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {"id": "dp_updated_2", "status": "under_review"}
        event_obj = _event_obj("charge.dispute.updated", data_obj, "evt_updated_2")
        update_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(return_value=None)),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True
        update_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# customer.subscription.deleted — primary match, customer-id fallback (both
# the "unlinked → cancel" and "linked to a newer sub → don't cancel" arms),
# and the fully-unmatched warn-only path.
# ---------------------------------------------------------------------------


class TestStripeWebhookSubscriptionDeleted:
    def test_primary_match_by_stripe_sub_id_cancels(self):
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {"id": "sub_del_1", "customer": "cus_1"}
        event_obj = _event_obj("customer.subscription.deleted", data_obj, "evt_del_1")
        active_sub = {"id": "row_1", "driver_id": "drv_1", "status": "active"}
        find_mock = AsyncMock(side_effect=[active_sub, {"id": "drv_1", "user_id": "u1"}])
        update_mock = AsyncMock()
        push_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", find_mock),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
            patch("backend.routes.webhooks.send_push_notification", push_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True
        update_mock.assert_awaited_once()
        assert update_mock.await_args.args[0] == "driver_subscriptions"
        assert update_mock.await_args.args[2]["status"] == "cancelled"
        push_mock.assert_awaited_once()

    def test_customer_fallback_cancels_when_row_unlinked(self):
        """Legacy row with no stripe_subscription_id yet: the customer-id
        fallback lookup finds it and cancels it."""
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {"id": "sub_del_2", "customer": "cus_2"}
        event_obj = _event_obj("customer.subscription.deleted", data_obj, "evt_del_2")
        user_row = {"id": "user_2"}
        driver_row = {"id": "drv_2"}
        candidate = {"id": "row_2", "driver_id": "drv_2", "status": "active"}  # no stripe_subscription_id key
        find_mock = AsyncMock(
            side_effect=[
                None,  # primary lookup by stripe_sub_id — miss
                user_row,  # users by stripe_customer_id
                driver_row,  # drivers by user_id
                candidate,  # active driver_subscriptions row
                {"id": "drv_2", "user_id": "u2"},  # push-notification driver lookup
            ]
        )
        update_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", find_mock),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
            patch("backend.routes.webhooks.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True
        update_mock.assert_awaited_once()
        assert update_mock.await_args.args[1] == {"id": "row_2"}
        assert update_mock.await_args.args[2]["status"] == "cancelled"

    def test_customer_fallback_does_not_cancel_newer_linked_sub(self):
        """The active row is already linked to a DIFFERENT Stripe subscription
        (driver switched plans) — deleting the OLD sub must not touch it."""
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {"id": "sub_del_3", "customer": "cus_3"}
        event_obj = _event_obj("customer.subscription.deleted", data_obj, "evt_del_3")
        user_row = {"id": "user_3"}
        driver_row = {"id": "drv_3"}
        candidate = {"id": "row_3", "driver_id": "drv_3", "status": "active", "stripe_subscription_id": "sub_newer"}
        find_mock = AsyncMock(side_effect=[None, user_row, driver_row, candidate])
        update_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", find_mock),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True
        update_mock.assert_not_awaited()

    def test_no_match_at_all_logs_warning_and_acks(self):
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {"id": "sub_del_4"}  # no customer field either
        event_obj = _event_obj("customer.subscription.deleted", data_obj, "evt_del_4")
        update_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(return_value=None)),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True
        update_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# account.updated — Stripe Connect Express KYC mirror dispatch
# ---------------------------------------------------------------------------


class TestStripeWebhookAccountUpdated:
    def test_dispatches_to_apply_account_update(self):
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {"id": "acct_1", "charges_enabled": True, "payouts_enabled": False}
        event_obj = _event_obj("account.updated", data_obj, "evt_acct_1")
        apply_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.services.stripe_kyc_sync.apply_account_update", apply_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True
        apply_mock.assert_awaited_once()
        assert apply_mock.await_args.args[0] == data_obj
        assert apply_mock.await_args.kwargs["event_id"] == "evt_acct_1"


# ---------------------------------------------------------------------------
# payment_intent.succeeded — receipt-send failure swallowed (money already
# recorded; the GST receipt email is best-effort)
# ---------------------------------------------------------------------------


class TestStripeWebhookReceiptSendFails:
    def test_receipt_send_exception_does_not_abort_webhook(self):
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {
            "id": "pi_receipt_fail",
            "metadata": {"ride_id": "ride_rcpt", "user_id": "user_1"},
            "amount_received": 2000,
        }
        event_obj = _event_obj("payment_intent.succeeded", data_obj, "evt_rcpt_1")
        record_mock = AsyncMock()
        receipt_mock = AsyncMock(side_effect=Exception("email provider down"))

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.get_ride",
                AsyncMock(return_value={"id": "ride_rcpt", "grand_total": 20.00, "rider_id": "rider_1"}),
            ),
            patch(
                "backend.routes.webhooks.db_supabase.update_ride",
                AsyncMock(return_value={"id": "ride_rcpt", "rider_id": "rider_1"}),
            ),
            patch("backend.services.payment_service.record_payment_event", record_mock),
            patch("backend.services.payment_service.send_ride_receipt", receipt_mock),
            patch("backend.routes.webhooks.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True
        record_mock.assert_awaited_once()
        receipt_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# payment_intent.payment_failed — ride-not-found raises 500 (Stripe retries),
# and the driver-lookup-exception path is swallowed (best-effort notify)
# ---------------------------------------------------------------------------


class TestStripeWebhookPaymentFailedRideUpdateNone:
    def test_update_ride_none_raises_500(self):
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {
            "id": "pi_fail_notfound",
            "metadata": {"ride_id": "ride_missing_2", "user_id": "user_1"},
            "last_payment_error": {"message": "Card declined"},
        }
        event_obj = _event_obj("payment_intent.payment_failed", data_obj, "evt_fail_notfound")

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.update_ride", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert exc_info.value.status_code == 500


class TestStripeWebhookPaymentFailedDriverLookupException:
    def test_driver_lookup_exception_is_swallowed(self):
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {
            "id": "pi_fail_lookup",
            "metadata": {"ride_id": "ride_x", "user_id": "user_1"},
            "last_payment_error": {"message": "Card declined"},
        }
        event_obj = _event_obj("payment_intent.payment_failed", data_obj, "evt_fail_lookup")

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.update_ride", AsyncMock(return_value={"id": "ride_x"})),
            patch("backend.routes.webhooks.db_supabase.get_ride", AsyncMock(side_effect=Exception("db down"))),
            patch("backend.routes.webhooks.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True


# ---------------------------------------------------------------------------
# INVESTIGATED, NOT A BUG — payment_intent.succeeded intentionally settles a
# ride mid-flight in payment_status='processing' rather than raising; this is
# the documented recovery path for a ride stuck after a confirm_payment
# timeout (see TestWebhookTimeoutDivergence in test_webhooks_main.py). The
# ledger write and tax receipt are skipped on the (documented) assumption
# that the concurrent settle_card() call performs them — this remains an
# unverified assumption and a real gap if that assumption is ever violated,
# but "raise instead" is not the fix (it breaks the stuck-ride rescue path).
# Left as-is; pinned so the actual behavior is visible.
# ---------------------------------------------------------------------------


class TestStripeWebhookProcessingRace:
    """FOUND, INVESTIGATED, NOT FIXED — see module docstring for the full
    investigation. A "match the sibling: raise on processing" fix was
    proposed and user-approved, then reverted after finding
    TestWebhookTimeoutDivergence::test_finalizes_ride_stuck_in_processing
    (test_webhooks_main.py, predates this session), which pins the opposite
    behavior as intentional: this webhook is the only recovery path for a
    ride claimed into payment_status='processing' by
    routes/payments.py::confirm_payment whose synchronous Stripe call then
    times out. Raising here would strand that ride in 'processing' forever.

    The remaining gap (ledger write + GST/PST receipt skipped whenever
    payment_status was already 'processing', relying on an unverified
    assumption that a concurrent settle_card() call performs them) is real
    but distinct from the raise-vs-settle question, and is left as a
    "found not fixed" item — see docs/change-log/2026-08-03-a1c-found-not-fixed-bugfixes.md
    Entry 13 for the full before/after reasoning.
    """

    def test_processing_ride_settles_paid_but_skips_ledger_and_receipt(self):
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {
            "id": "pi_race_1",
            "metadata": {"ride_id": "ride_race_1", "user_id": "user_1"},
            "amount_received": 1500,
        }
        event_obj = _event_obj("payment_intent.succeeded", data_obj, "evt_race_1")
        record_mock = AsyncMock()
        receipt_mock = AsyncMock()
        update_mock = AsyncMock(return_value={"id": "ride_race_1"})

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.get_ride",
                AsyncMock(return_value={"id": "ride_race_1", "grand_total": 15.00, "payment_status": "processing"}),
            ),
            patch("backend.routes.webhooks.db_supabase.update_ride", update_mock),
            patch("backend.services.payment_service.record_payment_event", record_mock),
            patch("backend.services.payment_service.send_ride_receipt", receipt_mock),
            patch("backend.routes.webhooks.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=_mock_req()))

        assert result["received"] is True
        # The webhook DOES flip payment_status to 'paid' unconditionally...
        update_mock.assert_awaited_once()
        assert update_mock.call_args.args[1]["payment_status"] == "paid"
        # ...but the ledger write and the tax receipt are both skipped,
        # relying entirely on an unverified assumption that a concurrent
        # settle_card() call will perform them instead. See class docstring.
        record_mock.assert_not_awaited()
        receipt_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# Twilio inbound — signature verification (valid + invalid), missing From,
# and the _resolve_user_id_by_phone lookup-exception path.
# ---------------------------------------------------------------------------

_TWILIO_URL = "/api/v1/webhooks/twilio-inbound"


def _patch_get_app_settings(return_value):
    """Patch `get_app_settings` under both the bare and `backend.`-qualified
    module paths for routes.webhooks.

    webhooks.py binds `get_app_settings` by name at import time (dual-import
    pattern), so which of `routes.webhooks`/`backend.routes.webhooks` is the
    module object actually wired into the live `test_client` app is not
    guaranteed stable across the full suite (another coverage file's
    sys.modules-reload trick to force an ImportError-fallback branch can
    flip which one is live for later tests in the same process). Patching
    both, tolerating whichever one doesn't exist, makes this deterministic.
    """
    import contextlib

    patches = []
    for target in ("routes.webhooks.get_app_settings", "backend.routes.webhooks.get_app_settings"):
        try:
            p = patch(target, AsyncMock(return_value=return_value))
            p.start()
            patches.append(p)
        except (AttributeError, ModuleNotFoundError):
            continue
    stack = contextlib.ExitStack()
    for p in patches:
        stack.callback(p.stop)
    return stack


@pytest.fixture
def _real_twilio():
    """Undo a session-wide `sys.modules["twilio"] = MagicMock()` for this test.

    test_data_export_purge.py, test_data_export_purge_loop_coverage.py and
    test_dsar_export.py each stub `twilio` (and `twilio.rest`) in a module-level
    `_STUBS` loop. pytest imports every test module during collection, so once
    any of them is collected the stub is in place for the whole session — and
    all three sort before this file, which is why the full-suite run was
    deterministic while running this class alone passed.

    The consequence is not a mere import error in the test: `routes/webhooks.py`
    does `from twilio.request_validator import RequestValidator` at request
    time, which against a MagicMock raises ModuleNotFoundError ("twilio is not
    a package"). The source then took its ImportError branch and skipped
    signature verification entirely, returning 200 for a request the test had
    set up to be rejected. The red test was reporting a real fail-open, which
    has now been closed in webhooks.py — but the stub still has to be lifted
    here or these tests exercise the 503 path instead of the validator.

    test_webhooks_coverage_gap.py carries the same workaround
    (`_ensure_real_twilio_imported`) and notes the root cause was not chased;
    it is named above now.
    """
    saved = {k: v for k, v in sys.modules.items() if k == "twilio" or k.startswith("twilio.")}
    for key in saved:
        del sys.modules[key]
    import twilio.request_validator  # noqa: F401

    yield
    # Leave the genuine modules in place rather than restoring the stubs: a real
    # package is never the wrong thing for a later test to find, and restoring a
    # MagicMock would just re-arm the landmine for whatever runs next.


@pytest.mark.usefixtures("_real_twilio")
class TestTwilioInboundSignatureVerification:
    def test_valid_signature_processes_stop(self, test_client: TestClient):
        with (
            _patch_get_app_settings({"twilio_auth_token": "tok123"}),
            patch("db_supabase.find_one", AsyncMock(return_value={"id": "u1"})),
            patch("services.marketing_consent.set_consent", AsyncMock(return_value=None)),
            patch("services.marketing_consent.add_marketing_suppression", AsyncMock(return_value=True)),
            patch("twilio.request_validator.RequestValidator.validate", return_value=True),
        ):
            r = test_client.post(
                _TWILIO_URL,
                data={"Body": "STOP", "From": "+13065551234"},
                headers={"X-Twilio-Signature": "sig123"},
            )

        assert r.status_code == 200
        assert "<Response>" in r.text

    def test_invalid_signature_returns_403(self, test_client: TestClient):
        with (
            _patch_get_app_settings({"twilio_auth_token": "tok123"}),
            patch("twilio.request_validator.RequestValidator.validate", return_value=False),
        ):
            r = test_client.post(
                _TWILIO_URL,
                data={"Body": "STOP", "From": "+13065551234"},
                headers={"X-Twilio-Signature": "bad_sig"},
            )

        assert r.status_code == 403


class TestTwilioInboundValidatorUnimportable:
    """An unimportable validator must fail closed, not process the webhook.

    The handler used to catch ImportError, set RequestValidator = None and fall
    straight through to the STOP/START logic. An admin who has configured
    twilio_auth_token has asked for verification; silently not performing it
    while still honouring the message body means anyone able to reach the
    endpoint can opt an arbitrary phone number in or out of marketing SMS.

    Deliberately NOT using the _real_twilio fixture — this test wants the
    broken-import state.
    """

    def test_import_error_returns_503_and_does_not_process(self, test_client: TestClient):
        real_import = builtins.__import__

        def _no_twilio(name, *args, **kwargs):
            if name == "twilio.request_validator" or name == "twilio":
                raise ImportError("simulated: twilio not installed")
            return real_import(name, *args, **kwargs)

        with (
            _patch_get_app_settings({"twilio_auth_token": "tok123"}),
            patch("services.marketing_consent.set_consent", AsyncMock()) as consent_mock,
            patch("services.marketing_consent.add_marketing_suppression", AsyncMock()) as suppress_mock,
            patch.object(builtins, "__import__", _no_twilio),
        ):
            r = test_client.post(
                _TWILIO_URL,
                data={"Body": "STOP", "From": "+13065551234"},
                headers={"X-Twilio-Signature": "sig123"},
            )

        assert r.status_code == 503, "unverifiable webhook must fail closed"
        # The point of failing closed: the STOP must NOT have taken effect.
        consent_mock.assert_not_awaited()
        suppress_mock.assert_not_awaited()


class TestTwilioInboundMissingFromPhone:
    def test_missing_from_phone_returns_empty_twiml_without_processing(self, test_client: TestClient):
        with (
            patch("routes.webhooks.get_app_settings", AsyncMock(return_value={})),
            patch("services.marketing_consent.set_consent", AsyncMock()) as consent_mock,
        ):
            r = test_client.post(_TWILIO_URL, data={"Body": "STOP", "From": ""})

        assert r.status_code == 200
        assert "<Response>" in r.text
        consent_mock.assert_not_awaited()


class TestResolveUserIdByPhoneException:
    def test_lookup_exception_is_swallowed_and_returns_none(self, test_client: TestClient):
        with (
            patch("routes.webhooks.get_app_settings", AsyncMock(return_value={})),
            patch("db_supabase.find_one", AsyncMock(side_effect=Exception("db down"))),
            patch("services.marketing_consent.set_consent", AsyncMock()) as consent_mock,
            patch("services.marketing_consent.add_marketing_suppression", AsyncMock()) as suppress_mock,
        ):
            r = test_client.post(_TWILIO_URL, data={"Body": "STOP", "From": "+13065551234"})

        assert r.status_code == 200
        # user_id resolution failed → set_consent is skipped (no user_id to
        # key it on), but the number is still suppressed by phone directly.
        consent_mock.assert_not_awaited()
        suppress_mock.assert_awaited_once()
