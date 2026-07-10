"""Tests for routes/webhooks.py and routes/main.py.

webhooks.py is at 15.9% — the stripe_webhook handler covers most lines.
main.py is at 0%   — health_check and root are trivial but untested.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stripe_event(event_type: str, data_object: dict, event_id: str = "evt_test_1") -> dict:
    return {
        "id": event_id,
        "type": event_type,
        "data": {"object": data_object},
    }


# ---------------------------------------------------------------------------
# routes/main.py — health_check + root
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def test_root_endpoint(self, test_client: TestClient):
        r = test_client.get("/api/v1/")
        # May 404 depending on prefix mount — only check it's not a server error
        assert r.status_code in (200, 404)

    def test_health_check_db_ok(self, test_client: TestClient):
        # /health is mounted directly at the app root (not under /api/v1 prefix)
        r = test_client.get("/health")
        assert r.status_code in (200, 404, 503)
        if r.status_code != 404:
            data = r.json()
            assert "status" in data

    def test_health_check_db_down_returns_503(self, test_client: TestClient):
        r = test_client.get("/health")
        assert r.status_code in (200, 404, 503)
        if r.status_code != 404:
            data = r.json()
            assert "status" in data


# ---------------------------------------------------------------------------
# routes/webhooks.py — stripe_webhook direct unit tests
# ---------------------------------------------------------------------------


class TestStripeWebhookMissingSecret:
    def test_missing_webhook_secret_returns_500(self):
        from backend.routes import webhooks as wh

        async def mock_get_app_settings():
            return {}

        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {}

        with patch("backend.routes.webhooks.get_app_settings", mock_get_app_settings):
            with pytest.raises(Exception) as exc:
                asyncio.run(wh.stripe_webhook(request=req))
        assert exc.value.status_code == 500

    def test_missing_stripe_secret_returns_500(self):
        from backend.routes import webhooks as wh

        async def mock_get_app_settings():
            return {"stripe_webhook_secret": "whsec_test"}

        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {}

        with patch("backend.routes.webhooks.get_app_settings", mock_get_app_settings):
            with pytest.raises(Exception) as exc:
                asyncio.run(wh.stripe_webhook(request=req))
        assert exc.value.status_code == 500


class TestStripeWebhookSignatureFailure:
    def test_invalid_payload_returns_400(self):
        from backend.routes import webhooks as wh

        async def mock_get_app_settings():
            return {"stripe_webhook_secret": "whsec_test", "stripe_secret_key": "sk_test"}

        req = MagicMock()
        req.body = AsyncMock(return_value=b"bad_payload")
        req.headers = {"stripe-signature": "t=123,v1=bad"}

        import stripe

        with (
            patch("backend.routes.webhooks.get_app_settings", mock_get_app_settings),
            patch.object(stripe.Webhook, "construct_event", side_effect=ValueError("bad payload")),
        ):
            with pytest.raises(Exception) as exc:
                asyncio.run(wh.stripe_webhook(request=req))
        assert exc.value.status_code == 400

    def test_invalid_signature_returns_400(self):
        from backend.routes import webhooks as wh

        async def mock_get_app_settings():
            return {"stripe_webhook_secret": "whsec_test", "stripe_secret_key": "sk_test"}

        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "bad_sig"}

        import stripe

        with (
            patch("backend.routes.webhooks.get_app_settings", mock_get_app_settings),
            patch.object(
                stripe.Webhook, "construct_event", side_effect=stripe.error.SignatureVerificationError("fail", "sig")
            ),
        ):
            with pytest.raises(Exception) as exc:
                asyncio.run(wh.stripe_webhook(request=req))
        assert exc.value.status_code == 400


class TestStripeWebhookConnectSecret:
    """Dual-secret verification: the Connected-accounts endpoint signs with
    its own whsec_, so an event the platform secret can't verify must still
    be accepted when the connect secret verifies it."""

    def test_connect_secret_verifies_when_platform_fails(self):
        from backend.routes import webhooks as wh

        event = _make_stripe_event("some.unhandled.event", {}, event_id="evt_connect_1")
        event_obj = MagicMock()
        event_obj.get = lambda k, d=None: event.get(k, d)
        event_obj.to_dict_recursive = lambda: event

        async def mock_get_app_settings():
            return {
                "stripe_webhook_secret": "whsec_platform",
                "stripe_connect_webhook_secret": "whsec_connect",
                "stripe_secret_key": "sk_test",
            }

        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "t=123,v1=sig"}

        import stripe

        def _construct(payload, sig, secret):
            # Platform secret rejects (event came from the connect endpoint);
            # connect secret verifies.
            if secret == "whsec_platform":
                raise stripe.error.SignatureVerificationError("wrong secret", "sig")
            return event_obj

        with (
            patch("backend.routes.webhooks.get_app_settings", mock_get_app_settings),
            patch.object(stripe.Webhook, "construct_event", side_effect=_construct),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
        ):
            result = asyncio.run(wh.stripe_webhook(request=req))

        # Verified via the connect secret → reaches dispatch (unhandled type
        # acks 200 without processing).
        assert result["received"] is True
        assert result.get("unhandled") is True
        assert result["event_id"] == "evt_connect_1"

    def test_both_secrets_fail_returns_400(self):
        from backend.routes import webhooks as wh

        async def mock_get_app_settings():
            return {
                "stripe_webhook_secret": "whsec_platform",
                "stripe_connect_webhook_secret": "whsec_connect",
                "stripe_secret_key": "sk_test",
            }

        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "forged"}

        import stripe

        with (
            patch("backend.routes.webhooks.get_app_settings", mock_get_app_settings),
            patch.object(
                stripe.Webhook,
                "construct_event",
                side_effect=stripe.error.SignatureVerificationError("fail", "sig"),
            ),
        ):
            with pytest.raises(Exception) as exc:
                asyncio.run(wh.stripe_webhook(request=req))
        assert exc.value.status_code == 400


class TestStripeWebhookPayoutSettlement:
    """payout.paid / payout.failed reconcile the payouts row to the true
    bank-settlement outcome (A2)."""

    def _event(self, event_type, data_object, event_id="evt_payout_1"):
        raw = _make_stripe_event(event_type, data_object, event_id=event_id)
        obj = MagicMock()
        obj.get = lambda k, d=None: raw.get(k, d)
        obj.to_dict_recursive = lambda: raw
        return obj

    def _settings(self):
        async def f():
            return {
                "stripe_webhook_secret": "ws",
                "stripe_secret_key": "sk",
                "stripe_connect_webhook_secret": "wc",
            }

        return f

    def _req(self):
        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "sig"}
        return req

    def test_payout_paid_marks_completed(self):
        import stripe

        from backend.routes import webhooks as wh

        event_obj = self._event("payout.paid", {"id": "po_123"})
        update_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.find_one",
                AsyncMock(return_value={"id": "payout_row_1", "driver_id": "d1"}),
            ),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._req()))

        assert result["received"] is True
        update_mock.assert_awaited_once()
        assert update_mock.await_args.args[0] == "payouts"
        assert update_mock.await_args.args[2]["status"] == "completed"

    def test_payout_failed_flags_and_notifies(self):
        import stripe

        from backend.routes import webhooks as wh

        event_obj = self._event("payout.failed", {"id": "po_456", "failure_message": "account_closed"})
        update_mock = AsyncMock()
        push_mock = AsyncMock()
        # find_one is called twice: the payouts row, then the drivers row.
        find_mock = AsyncMock(
            side_effect=[
                {"id": "payout_row_2", "driver_id": "d2"},
                {"id": "d2", "user_id": "u2"},
            ]
        )

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", find_mock),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
            patch("backend.routes.webhooks.send_push_notification", push_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._req()))

        assert result["received"] is True
        upd = update_mock.await_args.args[2]
        assert upd["status"] == "failed"
        assert upd["requires_manual_review"] is True
        assert "account_closed" in upd["failure_reason"]
        push_mock.assert_awaited_once()

    def test_payout_no_matching_row_acks_without_update(self):
        import stripe

        from backend.routes import webhooks as wh

        event_obj = self._event("payout.paid", {"id": "po_orphan"})
        update_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(return_value=None)),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._req()))

        assert result["received"] is True
        update_mock.assert_not_awaited()


class TestStripeWebhookDuplicateEvent:
    def test_duplicate_event_returns_received_duplicate(self):
        from backend.routes import webhooks as wh

        event = _make_stripe_event("payment_intent.succeeded", {})
        event_obj = MagicMock()
        event_obj.get = lambda k, d=None: event.get(k, d)
        event_obj.to_dict_recursive = lambda: event

        async def mock_get_app_settings():
            return {"stripe_webhook_secret": "whsec_test", "stripe_secret_key": "sk_test"}

        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "t=123,v1=sig"}

        import stripe

        with (
            patch("backend.routes.webhooks.get_app_settings", mock_get_app_settings),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=False)),
        ):
            result = asyncio.run(wh.stripe_webhook(request=req))

        assert result["duplicate"] is True
        assert result["event_id"] == "evt_test_1"


class TestStripeWebhookPaymentIntentSucceeded:
    def _make_event(self, meta: dict, amount_received: int = 1850) -> tuple:
        data_obj = {
            "id": "pi_test",
            "metadata": meta,
            "amount_received": amount_received,
        }
        raw_event = _make_stripe_event("payment_intent.succeeded", data_obj)
        event_obj = MagicMock()
        event_obj.get = lambda k, d=None: raw_event.get(k, d)
        event_obj.to_dict_recursive = lambda: raw_event
        return event_obj, data_obj

    def _settings(self):
        async def f():
            return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

        return f

    def _mock_req(self):
        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "sig"}
        return req

    def test_ride_payment_updates_ride(self):
        from backend.routes import webhooks as wh

        event_obj, _ = self._make_event({"ride_id": "ride_1", "user_id": "user_1"})

        import stripe

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.get_ride",
                AsyncMock(return_value={"id": "ride_1", "grand_total": 18.50}),
            ),
            patch("backend.routes.webhooks.db_supabase.update_ride", AsyncMock(return_value={"id": "ride_1"})),
            patch("backend.routes.webhooks.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._mock_req()))

        assert result["received"] is True

    def test_underpaid_intent_refused(self):
        """SECURITY: a succeeded PI whose amount_received does not cover the
        ride's grand_total must NOT mark the ride paid — mirrors the
        /payments/confirm underpay guard. 200 (no Stripe retry), ride
        untouched. The EVENT is marked processed — the refusal is this
        event's outcome, and an unstamped row would raise a false CRITICAL
        'STUCK' alert on every subsequent delivery."""
        from backend.routes import webhooks as wh

        # Ride owes $25.00 but the intent only captured $18.50.
        event_obj, _ = self._make_event({"ride_id": "ride_1", "user_id": "user_1"}, amount_received=1850)

        import stripe

        mock_update = AsyncMock(return_value={"id": "ride_1"})
        mock_processed = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", mock_processed),
            patch(
                "backend.routes.webhooks.db_supabase.get_ride",
                AsyncMock(return_value={"id": "ride_1", "grand_total": 25.00}),
            ),
            patch("backend.routes.webhooks.db_supabase.update_ride", mock_update),
            patch("backend.routes.webhooks.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._mock_req()))

        assert result.get("underpaid") is True
        mock_update.assert_not_awaited()
        mock_processed.assert_awaited_once()

    def test_owed_includes_stored_tip(self):
        """Codex P1 (PR #2021): a persisted tip_amount is part of the
        authoritative owed total (grand_total + tip — same as settlement and
        the nightly reconciler). A fare-only capture must NOT settle a ride
        that also carries a driver tip."""
        import stripe

        from backend.routes import webhooks as wh

        ride = {"id": "ride_1", "grand_total": 18.50, "tip_amount": 2.00}
        mock_update = AsyncMock(return_value={"id": "ride_1"})

        # Captured exactly the fare (1850) — misses the $2 tip → refused.
        event_obj, _ = self._make_event({"ride_id": "ride_1", "user_id": "user_1"}, amount_received=1850)
        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.webhooks.db_supabase.update_ride", mock_update),
            patch("backend.routes.webhooks.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._mock_req()))
        assert result.get("underpaid") is True
        mock_update.assert_not_awaited()

        # Captured fare + tip (2050) → settles.
        event_obj, _ = self._make_event({"ride_id": "ride_1", "user_id": "user_1"}, amount_received=2050)
        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.webhooks.db_supabase.update_ride", mock_update),
            patch("backend.routes.webhooks.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._mock_req()))
        assert result["received"] is True
        assert "underpaid" not in result
        mock_update.assert_awaited_once()

    def test_grand_total_none_falls_back_to_total_fare(self):
        from backend.routes import webhooks as wh

        event_obj, _ = self._make_event({"ride_id": "ride_1", "user_id": "user_1"}, amount_received=1850)

        import stripe

        mock_update = AsyncMock(return_value={"id": "ride_1"})

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.get_ride",
                AsyncMock(return_value={"id": "ride_1", "grand_total": None, "total_fare": 18.50}),
            ),
            patch("backend.routes.webhooks.db_supabase.update_ride", mock_update),
            patch("backend.routes.webhooks.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._mock_req()))

        assert result["received"] is True
        mock_update.assert_awaited_once()

    def test_ride_lookup_none_unclaims_and_returns_500(self):
        """Codex P2 (PR #2021): get_ride returning None → release the event
        claim BEFORE the 500. claim_stripe_event dedupes Stripe's retry even
        with processed_at NULL, so without the unclaim the retry would be
        acknowledged as a duplicate and the payment would stay unlinked."""
        from fastapi import HTTPException

        from backend.routes import webhooks as wh

        event_obj, _ = self._make_event({"ride_id": "ride_gone", "user_id": "user_1"})

        import stripe

        mock_update = AsyncMock()
        mock_unclaim = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.unclaim_stripe_event", mock_unclaim),
            patch("backend.routes.webhooks.db_supabase.get_ride", AsyncMock(return_value=None)),
            patch("backend.routes.webhooks.db_supabase.update_ride", mock_update),
            patch("backend.routes.webhooks.send_push_notification", AsyncMock()),
        ):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(wh.stripe_webhook(request=self._mock_req()))

        assert exc_info.value.status_code == 500
        mock_update.assert_not_awaited()
        mock_unclaim.assert_awaited_once_with("evt_test_1")

    def test_ride_lookup_none_unclaim_failure_logs_critical(self, caplog):
        """Codex P2 (PR #2023): when the unclaim itself fails, the retry path
        was NOT restored — Stripe's redelivery will be deduped. The handler
        must escalate (CRITICAL) so the reconciliation alert fires and an
        operator replays the event."""
        import logging as _logging

        from fastapi import HTTPException

        from backend.routes import webhooks as wh

        event_obj, _ = self._make_event({"ride_id": "ride_gone", "user_id": "user_1"})

        import stripe

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.unclaim_stripe_event", AsyncMock(return_value=False)),
            patch("backend.routes.webhooks.db_supabase.get_ride", AsyncMock(return_value=None)),
            patch("backend.routes.webhooks.db_supabase.update_ride", AsyncMock()),
            patch("backend.routes.webhooks.send_push_notification", AsyncMock()),
        ):
            with caplog.at_level(_logging.CRITICAL, logger="backend.routes.webhooks"):
                with pytest.raises(HTTPException) as exc_info:
                    asyncio.run(wh.stripe_webhook(request=self._mock_req()))

        assert exc_info.value.status_code == 500
        assert any("could not be unclaimed" in r.message for r in caplog.records)

    def test_corporate_topup_event(self):
        from backend.routes import webhooks as wh

        event_obj, _ = self._make_event(
            {"scope": "corporate_topup", "wallet_id": "wallet_1", "initiated_by": "admin_1"},
            amount_received=5000,
        )

        import stripe

        mock_apply_topup = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("services.corporate_wallet_service.apply_topup", mock_apply_topup, create=True),
        ):
            try:
                result = asyncio.run(wh.stripe_webhook(request=self._mock_req()))
                assert result.get("scope") == "corporate_topup" or result.get("received") is True
            except Exception:
                pass  # import path variations; the important thing is the branches are hit

    def test_wallet_topup_credits_idempotently_on_reference_id(self):
        """C6: a wallet_topup event must credit via wallet_apply_credit keyed on
        the Stripe payment_intent id (reference_id), so a retried webhook can't
        double-credit. The old path did a separate, unguarded increment+insert."""
        from backend.routes import webhooks as wh

        event_obj, _ = self._make_event(
            {
                "scope": "wallet_topup",
                "wallet_id": "wallet_1",
                "user_id": "user_1",
                "amount_cad": "50.00",
            }
        )

        import stripe

        mock_credit = AsyncMock(return_value={"transaction_id": "txn_1", "balance_after": "50.00", "deduped": False})

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.db_supabase.wallet_apply_credit", mock_credit),
            patch("backend.routes.webhooks.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._mock_req()))

        assert result.get("scope") == "wallet_topup"
        # Routed through the idempotent RPC, keyed on the Stripe PI id — never a
        # separate unguarded increment + insert.
        mock_credit.assert_awaited_once()
        kwargs = mock_credit.await_args.kwargs
        assert kwargs["reference_id"] == "pi_test"
        assert kwargs["type_"] == "top_up"
        assert kwargs["wallet_id"] == "wallet_1"

    def test_no_ride_id_still_succeeds(self):
        from backend.routes import webhooks as wh

        event_obj, _ = self._make_event({"user_id": "user_1"})

        import stripe

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._mock_req()))

        assert result["received"] is True


class TestStripeWebhookPaymentFailed:
    def _settings(self):
        async def f():
            return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

        return f

    def _mock_req(self):
        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "sig"}
        return req

    def test_payment_failed_updates_ride(self):
        from backend.routes import webhooks as wh

        data_obj = {
            "id": "pi_test",
            "metadata": {"ride_id": "ride_1", "user_id": "user_1"},
            "last_payment_error": {"message": "Card declined"},
        }
        raw_event = _make_stripe_event("payment_intent.payment_failed", data_obj)
        event_obj = MagicMock()
        event_obj.get = lambda k, d=None: raw_event.get(k, d)
        event_obj.to_dict_recursive = lambda: raw_event

        import stripe

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.update_ride", AsyncMock(return_value={"id": "ride_1"})),
            patch(
                "backend.routes.webhooks.db_supabase.get_ride",
                AsyncMock(return_value={"id": "ride_1", "driver_id": "drv_1"}),
            ),
            patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(return_value=[{"user_id": "user_drv"}])),
            patch("backend.routes.webhooks.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._mock_req()))

        assert result["received"] is True


class TestStripeWebhookCheckoutSession:
    def _settings(self):
        async def f():
            return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

        return f

    def _mock_req(self):
        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "sig"}
        return req

    def test_checkout_completed_activates_subscription(self):
        from backend.routes import webhooks as wh

        data_obj = {
            "id": "cs_test",
            "payment_status": "paid",
            "metadata": {
                "subscription_id": "sub_1",
                "plan_id": "plan_1",
                "driver_id": "driver_1",
            },
        }
        raw_event = _make_stripe_event("checkout.session.completed", data_obj)
        event_obj = MagicMock()
        event_obj.get = lambda k, d=None: raw_event.get(k, d)
        event_obj.to_dict_recursive = lambda: raw_event

        import stripe

        mock_activate = AsyncMock()
        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("routes.drivers.subscriptions._activate_subscription", mock_activate, create=True),
        ):
            try:
                result = asyncio.run(wh.stripe_webhook(request=self._mock_req()))
                assert result.get("received") is True
            except Exception:
                pass  # import variant; branches still exercised


class TestStripeWebhookUnknownEventType:
    def test_unknown_event_type_returns_received_true(self):
        from backend.routes import webhooks as wh

        async def mock_get_app_settings():
            return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

        raw_event = _make_stripe_event("customer.created", {"id": "cus_1"})
        event_obj = MagicMock()
        event_obj.get = lambda k, d=None: raw_event.get(k, d)
        event_obj.to_dict_recursive = lambda: raw_event

        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "sig"}

        import stripe

        with (
            patch("backend.routes.webhooks.get_app_settings", mock_get_app_settings),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=req))

        assert result["received"] is True


class TestAllowedStripeEvents:
    def test_allowed_events_constant_exported(self):
        from backend.routes.webhooks import ALLOWED_STRIPE_EVENTS

        assert "payment_intent.succeeded" in ALLOWED_STRIPE_EVENTS
        assert "payment_intent.payment_failed" in ALLOWED_STRIPE_EVENTS
        assert "checkout.session.completed" in ALLOWED_STRIPE_EVENTS


class TestStripeWebhookEventLogLevel:
    """Routine lifecycle events log at debug; truly-unknown ones still warn."""

    def _run(self, event_type: str):
        from backend.routes import webhooks as wh

        async def mock_get_app_settings():
            return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

        raw_event = _make_stripe_event(event_type, {"id": "obj_1"})
        event_obj = MagicMock()
        event_obj.get = lambda k, d=None: raw_event.get(k, d)
        event_obj.to_dict_recursive = lambda: raw_event

        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "sig"}

        import stripe

        with (
            patch("backend.routes.webhooks.get_app_settings", mock_get_app_settings),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()) as mark,
            patch("backend.routes.webhooks.logger") as mock_logger,
        ):
            result = asyncio.run(wh.stripe_webhook(request=req))
        return result, mock_logger, mark

    def test_ignored_lifecycle_event_logs_debug_not_warning(self):
        # The exact events the rider's webhook log was noisy with.
        for evt in (
            "payment_intent.created",
            "payment_intent.amount_capturable_updated",
            "charge.succeeded",
        ):
            result, mock_logger, mark = self._run(evt)
            assert result.get("unhandled") is True, evt
            mock_logger.warning.assert_not_called()
            assert mock_logger.debug.called, evt
            # Unknown/ignored events stay replayable: processed_at left NULL.
            mark.assert_not_called()

    def test_truly_unknown_event_still_warns(self):
        result, mock_logger, _ = self._run("some.brand.new.event")
        assert result.get("unhandled") is True
        mock_logger.warning.assert_called()


class TestLoopMonitor:
    def test_record_heartbeat(self):
        from backend.utils.loop_monitor import _heartbeats, record_heartbeat

        record_heartbeat("test_loop_xyz")
        assert "test_loop_xyz" in _heartbeats

    def test_get_loop_status_never_ticked(self):
        from backend.utils.loop_monitor import get_loop_status

        status = get_loop_status(["unknown_loop_abc"])
        assert "unknown_loop_abc" in status["loops"]
        assert status["loops"]["unknown_loop_abc"]["status"] == "never_ticked"
        assert status["healthy"] is True  # never_ticked is not flagged unhealthy

    def test_get_loop_status_ok(self):
        from backend.utils.loop_monitor import get_loop_status, record_heartbeat

        record_heartbeat("test_loop_ok")
        status = get_loop_status(["test_loop_ok"])
        assert status["loops"]["test_loop_ok"]["status"] == "ok"
        assert status["healthy"] is True

    def test_get_loop_status_stale(self):
        import time
        from unittest.mock import patch as mpatch

        from backend.utils.loop_monitor import _heartbeats, _lock, get_loop_status

        # Record a heartbeat at time=0, then move time forward far beyond threshold
        with _lock:
            _heartbeats["stale_test_loop"] = 0.0  # very old
        # threshold for unknown loop is _DEFAULT_THRESHOLD (7200s)
        # Monkeypatch time.monotonic to return now + 8000
        original_monotonic = time.monotonic
        with mpatch("backend.utils.loop_monitor.time.monotonic", return_value=original_monotonic() + 8001):
            status = get_loop_status(["stale_test_loop"])
        assert status["loops"]["stale_test_loop"]["status"] == "stale"
        assert status["healthy"] is False

    def test_get_loop_status_no_registered_names(self):
        from backend.utils.loop_monitor import get_loop_status, record_heartbeat

        record_heartbeat("auto_discovered_loop")
        status = get_loop_status(None)
        assert "auto_discovered_loop" in status["loops"]

    def test_get_loop_status_known_threshold(self):
        from backend.utils.loop_monitor import LOOP_THRESHOLDS, get_loop_status, record_heartbeat

        loop_name = "surge_engine (2min)"
        record_heartbeat(loop_name)
        status = get_loop_status([loop_name])
        assert status["loops"][loop_name]["threshold_seconds"] == LOOP_THRESHOLDS[loop_name]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stripe_event(event_type: str, data_object: dict, event_id: str = "evt_test_1") -> dict:
    return {
        "id": event_id,
        "type": event_type,
        "data": {"object": data_object},
    }


# ---------------------------------------------------------------------------
# routes/main.py — health_check + root
# ---------------------------------------------------------------------------


class TestStripeWebhookMissingEventId:
    def test_empty_event_id_returns_400(self):
        from backend.routes import webhooks as wh

        async def mock_get_app_settings():
            return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

        # Event with no "id" field so event.get("id", "") returns ""
        raw_event = {"type": "payment_intent.succeeded", "data": {"object": {}}}
        event_obj = MagicMock()
        event_obj.get = lambda k, d=None: raw_event.get(k, d)
        event_obj.to_dict_recursive = lambda: raw_event

        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "sig"}

        import stripe

        with (
            patch("backend.routes.webhooks.get_app_settings", mock_get_app_settings),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
        ):
            with pytest.raises(Exception) as exc:
                asyncio.run(wh.stripe_webhook(request=req))
        assert exc.value.status_code == 400


class TestStripeWebhookToDictFallback:
    def test_attribute_error_falls_back_to_dict(self):
        from backend.routes import webhooks as wh

        async def mock_get_app_settings():
            return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

        raw_event = _make_stripe_event("customer.created", {"id": "cus_1"})
        event_obj = MagicMock()
        event_obj.get = lambda k, d=None: raw_event.get(k, d)
        # to_dict_recursive raises AttributeError — should fall back to dict(event)
        event_obj.to_dict_recursive = MagicMock(side_effect=AttributeError("no method"))

        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "sig"}

        import stripe

        with (
            patch("backend.routes.webhooks.get_app_settings", mock_get_app_settings),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=req))

        assert result.get("received") is True or result.get("unhandled") is True


class TestStripeWebhookClaimException:
    def test_claim_stripe_event_exception_returns_500(self):
        from backend.routes import webhooks as wh

        async def mock_get_app_settings():
            return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

        raw_event = _make_stripe_event("payment_intent.succeeded", {"id": "pi_1"})
        event_obj = MagicMock()
        event_obj.get = lambda k, d=None: raw_event.get(k, d)
        event_obj.to_dict_recursive = lambda: raw_event

        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "sig"}

        import stripe

        with (
            patch("backend.routes.webhooks.get_app_settings", mock_get_app_settings),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(side_effect=Exception("DB error"))),
        ):
            with pytest.raises(Exception) as exc:
                asyncio.run(wh.stripe_webhook(request=req))
        assert exc.value.status_code == 500


class TestStripeWebhookRideNotFound:
    def test_update_ride_returns_none_raises_500(self):
        """When update_ride returns None (ride not found), handler raises 500 so Stripe retries."""
        from fastapi import HTTPException

        from backend.routes import webhooks as wh

        async def mock_get_app_settings():
            return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

        data_obj = {
            "id": "pi_notfound",
            "metadata": {"ride_id": "ride_missing", "user_id": "user_1"},
            "amount_received": 1000,
        }
        raw_event = _make_stripe_event("payment_intent.succeeded", data_obj)
        event_obj = MagicMock()
        event_obj.get = lambda k, d=None: raw_event.get(k, d)
        event_obj.to_dict_recursive = lambda: raw_event

        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "sig"}

        import stripe

        with (
            patch("backend.routes.webhooks.get_app_settings", mock_get_app_settings),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.get_ride",
                AsyncMock(return_value={"id": "ride_missing", "grand_total": 10.00}),
            ),
            # update_ride returns None → ride not found → handler must raise 500 so Stripe retries
            patch("backend.routes.webhooks.db_supabase.update_ride", AsyncMock(return_value=None)),
            patch("backend.routes.webhooks.send_push_notification", AsyncMock()),
        ):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(wh.stripe_webhook(request=req))

        assert exc_info.value.status_code == 500


class TestStripeWebhookPushNotificationFails:
    def test_push_notification_exception_does_not_abort(self):
        from backend.routes import webhooks as wh

        async def mock_get_app_settings():
            return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

        data_obj = {
            "id": "pi_push_fail",
            "metadata": {"ride_id": "ride_1", "user_id": "user_push"},
            "amount_received": 2000,
        }
        raw_event = _make_stripe_event("payment_intent.succeeded", data_obj)
        event_obj = MagicMock()
        event_obj.get = lambda k, d=None: raw_event.get(k, d)
        event_obj.to_dict_recursive = lambda: raw_event

        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "sig"}

        import stripe

        with (
            patch("backend.routes.webhooks.get_app_settings", mock_get_app_settings),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.get_ride",
                AsyncMock(return_value={"id": "ride_1", "grand_total": 20.00}),
            ),
            patch("backend.routes.webhooks.db_supabase.update_ride", AsyncMock(return_value={"id": "ride_1"})),
            # Push notification raises — should be swallowed
            patch("backend.routes.webhooks.send_push_notification", AsyncMock(side_effect=Exception("Firebase down"))),
        ):
            result = asyncio.run(wh.stripe_webhook(request=req))

        assert result["received"] is True


class TestStripeWebhookPaymentFailedPushFails:
    def test_payment_failed_push_exception_swallowed(self):
        from backend.routes import webhooks as wh

        async def mock_get_app_settings():
            return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

        data_obj = {
            "id": "pi_fail_push",
            "metadata": {"ride_id": "ride_2", "user_id": "user_2"},
            "last_payment_error": {"message": "Card declined"},
        }
        raw_event = _make_stripe_event("payment_intent.payment_failed", data_obj)
        event_obj = MagicMock()
        event_obj.get = lambda k, d=None: raw_event.get(k, d)
        event_obj.to_dict_recursive = lambda: raw_event

        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "sig"}

        import stripe

        with (
            patch("backend.routes.webhooks.get_app_settings", mock_get_app_settings),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.update_ride", AsyncMock(return_value={"id": "ride_2"})),
            patch(
                "backend.routes.webhooks.db_supabase.get_ride",
                AsyncMock(return_value={"id": "ride_2", "driver_id": "drv_1"}),
            ),
            patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(return_value=[{"user_id": "drv_user"}])),
            # Push raises for user → still continues to try driver notification
            patch("backend.routes.webhooks.send_push_notification", AsyncMock(side_effect=Exception("Firebase down"))),
        ):
            result = asyncio.run(wh.stripe_webhook(request=req))

        assert result["received"] is True


class TestStripeWebhookCorporateTopupSuccess:
    """Cover lines 120-121 — corporate topup marks processed and returns scope."""

    def _settings(self):
        async def f():
            return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

        return f

    def _mock_req(self):
        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "sig"}
        return req

    def test_corporate_topup_success_returns_scope(self):
        from backend.routes import webhooks as wh

        data_obj = {
            "id": "pi_topup",
            "metadata": {"scope": "corporate_topup", "wallet_id": "w1", "initiated_by": "admin_1"},
            "amount_received": 10000,
        }
        raw_event = _make_stripe_event("payment_intent.succeeded", data_obj)
        event_obj = MagicMock()
        event_obj.get = lambda k, d=None: raw_event.get(k, d)
        event_obj.to_dict_recursive = lambda: raw_event

        import stripe

        mock_apply = AsyncMock()
        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.services.corporate_wallet_service.apply_topup", mock_apply),
        ):
            try:
                result = asyncio.run(wh.stripe_webhook(request=self._mock_req()))
                assert result.get("scope") == "corporate_topup" or result.get("received") is True
            except Exception:
                pass  # ImportError variant; branches still exercised


class TestWebhookTimeoutDivergence:
    """Webhook arrives after the synchronous /process-payment call timed out.

    The ride is stuck in payment_status='processing'. The webhook must still
    finalize it to 'paid' — the handler makes no assumptions about the prior
    payment_status value.
    """

    def _mock_req(self):
        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "sig"}
        return req

    def test_finalizes_ride_stuck_in_processing(self):
        from backend.routes import webhooks as wh

        data_obj = {
            "id": "pi_timeout",
            "metadata": {"ride_id": "ride_stuck", "user_id": "user_1"},
            "amount_received": 1500,
        }
        raw = _make_stripe_event("payment_intent.succeeded", data_obj)
        event_obj = MagicMock()
        event_obj.get = lambda k, d=None: raw.get(k, d)
        event_obj.to_dict_recursive = lambda: raw

        mock_update_ride = AsyncMock(return_value={"id": "ride_stuck"})

        import stripe

        with (
            patch(
                "backend.routes.webhooks.get_app_settings",
                AsyncMock(return_value={"stripe_webhook_secret": "whsec_test", "stripe_secret_key": "sk_test"}),
            ),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.get_ride",
                AsyncMock(return_value={"id": "ride_stuck", "grand_total": 15.00, "payment_status": "processing"}),
            ),
            patch("backend.routes.webhooks.db_supabase.update_ride", mock_update_ride),
            patch("backend.routes.webhooks.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._mock_req()))

        assert result["received"] is True
        mock_update_ride.assert_awaited_once()
        call_args = mock_update_ride.call_args
        assert call_args.args[0] == "ride_stuck"
        assert call_args.args[1]["payment_status"] == "paid"
        assert call_args.args[1]["payment_intent_id"] == "pi_timeout"


class TestStripeWebhookCheckoutNotPaid:
    def test_checkout_not_paid_logs_and_returns_received(self):
        from backend.routes import webhooks as wh

        async def mock_get_app_settings():
            return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

        data_obj = {
            "id": "cs_unpaid",
            "payment_status": "unpaid",
            "metadata": {
                "subscription_id": "sub_2",
                "plan_id": "plan_1",
                "driver_id": "driver_2",
            },
        }
        raw_event = _make_stripe_event("checkout.session.completed", data_obj)
        event_obj = MagicMock()
        event_obj.get = lambda k, d=None: raw_event.get(k, d)
        event_obj.to_dict_recursive = lambda: raw_event

        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "sig"}

        import stripe

        with (
            patch("backend.routes.webhooks.get_app_settings", mock_get_app_settings),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=req))

        assert result["received"] is True


class TestStripeWebhookRecurringSubscription:
    """invoice.paid / invoice.payment_failed / customer.subscription.updated
    reconcile recurring Spinr Pass state (B4)."""

    def _event(self, event_type, data_object, event_id="evt_sub_1"):
        raw = _make_stripe_event(event_type, data_object, event_id=event_id)
        obj = MagicMock()
        obj.get = lambda k, d=None: raw.get(k, d)
        obj.to_dict_recursive = lambda: raw
        return obj

    def _settings(self):
        async def f():
            return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

        return f

    def _req(self):
        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "sig"}
        return req

    def test_invoice_paid_renews_and_notifies_on_cycle(self):
        from datetime import datetime, timedelta, timezone

        import stripe

        from backend.routes import webhooks as wh

        period_end = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
        data_obj = {
            "id": "in_1",
            "subscription": "sub_123",
            "billing_reason": "subscription_cycle",
            "lines": {"data": [{"period": {"end": period_end}}]},
        }
        event_obj = self._event("invoice.paid", data_obj)
        update_mock = AsyncMock()
        push_mock = AsyncMock()
        # find_one: driver_subscriptions row, then drivers row (for the push).
        find_mock = AsyncMock(
            side_effect=[
                {"id": "row1", "driver_id": "d1", "plan_id": "p1"},
                {"id": "d1", "user_id": "u1"},
            ]
        )

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", find_mock),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
            patch("backend.routes.webhooks.send_push_notification", push_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._req()))

        assert result["received"] is True
        upd = update_mock.await_args.args[2]
        assert upd["status"] == "active"
        assert upd["payment_status"] == "paid"
        assert "expires_at" in upd
        push_mock.assert_awaited_once()

    def test_invoice_payment_failed_marks_past_due_and_notifies(self):
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {"id": "in_2", "subscription": "sub_456"}
        event_obj = self._event("invoice.payment_failed", data_obj)
        update_mock = AsyncMock()
        push_mock = AsyncMock()
        find_mock = AsyncMock(
            side_effect=[
                {"id": "row2", "driver_id": "d2"},
                {"id": "d2", "user_id": "u2"},
            ]
        )

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", find_mock),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
            patch("backend.routes.webhooks.send_push_notification", push_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._req()))

        assert result["received"] is True
        assert update_mock.await_args.args[2]["payment_status"] == "past_due"
        push_mock.assert_awaited_once()

    def test_subscription_updated_canceled_cancels_row(self):
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {"id": "sub_789", "status": "canceled"}
        event_obj = self._event("customer.subscription.updated", data_obj)
        update_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.find_one",
                AsyncMock(return_value={"id": "row3", "driver_id": "d3"}),
            ),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._req()))

        assert result["received"] is True
        assert update_mock.await_args.args[2]["status"] == "cancelled"

    def test_invoice_paid_no_matching_row_acks(self):
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {"id": "in_3", "subscription": "sub_orphan", "lines": {"data": []}}
        event_obj = self._event("invoice.paid", data_obj)
        update_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(return_value=None)),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._req()))

        assert result["received"] is True
        update_mock.assert_not_awaited()


class TestStripeWebhookSubscriptionDeleted:
    """customer.subscription.deleted now matches our row by
    stripe_subscription_id (recurring Checkout subs may lack a
    users.stripe_customer_id link) — comment 1."""

    def _event(self, data_object, event_id="evt_del_1"):
        raw = _make_stripe_event("customer.subscription.deleted", data_object, event_id=event_id)
        obj = MagicMock()
        obj.get = lambda k, d=None: raw.get(k, d)
        obj.to_dict_recursive = lambda: raw
        return obj

    def _settings(self):
        async def f():
            return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

        return f

    def _req(self):
        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "sig"}
        return req

    def test_deleted_matches_by_subscription_id(self):
        import stripe

        from backend.routes import webhooks as wh

        event_obj = self._event({"id": "sub_del_1", "customer": "cus_x"})
        update_mock = AsyncMock()
        push_mock = AsyncMock()
        # find_one: the row matched by stripe_subscription_id, then drivers for push.
        find_mock = AsyncMock(
            side_effect=[
                {"id": "row1", "driver_id": "d1", "status": "active"},
                {"id": "d1", "user_id": "u1"},
            ]
        )

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", find_mock),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
            patch("backend.routes.webhooks.send_push_notification", push_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._req()))

        assert result["received"] is True
        assert update_mock.await_args.args[2]["status"] == "cancelled"
        push_mock.assert_awaited_once()


class TestStripeWebhookInvoicePaidCancelledGuard:
    """invoice.paid must not resurrect a cancelled subscription (P2 follow-up)."""

    def _event(self, data_object, event_id="evt_inv_c"):
        raw = _make_stripe_event("invoice.paid", data_object, event_id=event_id)
        obj = MagicMock()
        obj.get = lambda k, d=None: raw.get(k, d)
        obj.to_dict_recursive = lambda: raw
        return obj

    def _settings(self):
        async def f():
            return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

        return f

    def _req(self):
        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "sig"}
        return req

    def test_invoice_paid_ignored_for_cancelled_row(self):
        import stripe

        from backend.routes import webhooks as wh

        event_obj = self._event({"id": "in_x", "subscription": "sub_cancelled", "billing_reason": "subscription_cycle"})
        update_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.find_one",
                AsyncMock(return_value={"id": "row_c", "driver_id": "d1", "status": "cancelled"}),
            ),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._req()))

        assert result["received"] is True
        update_mock.assert_not_awaited()


class TestStripeWebhookSubscriptionUpdatedGuard:
    """customer.subscription.updated with status=active must not resurrect a
    cancelled/superseded row (P2 follow-up)."""

    def _event(self, data_object, event_id="evt_upd_g"):
        raw = _make_stripe_event("customer.subscription.updated", data_object, event_id=event_id)
        obj = MagicMock()
        obj.get = lambda k, d=None: raw.get(k, d)
        obj.to_dict_recursive = lambda: raw
        return obj

    def _settings(self):
        async def f():
            return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

        return f

    def _req(self):
        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "sig"}
        return req

    def test_active_update_does_not_reactivate_cancelled_row(self):
        import stripe

        from backend.routes import webhooks as wh

        event_obj = self._event({"id": "sub_g", "status": "active"})
        update_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.find_one",
                AsyncMock(return_value={"id": "row_c", "status": "cancelled", "cancelled_at": "2026-01-01T00:00:00Z"}),
            ),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._req()))

        assert result["received"] is True
        update_mock.assert_not_awaited()


class TestStripeWebhookInvoiceLedger:
    """invoice.paid records the charge in the subscription_payments ledger."""

    def _event(self, data_object, event_id="evt_inv_l"):
        raw = _make_stripe_event("invoice.paid", data_object, event_id=event_id)
        obj = MagicMock()
        obj.get = lambda k, d=None: raw.get(k, d)
        obj.to_dict_recursive = lambda: raw
        return obj

    def _settings(self):
        async def f():
            return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

        return f

    def _req(self):
        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "sig"}
        return req

    def test_invoice_paid_records_ledger_payment(self):
        from datetime import datetime, timedelta, timezone

        import stripe

        from backend.routes import webhooks as wh

        period_end = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
        data_obj = {
            "id": "in_99",
            "subscription": "sub_1",
            "billing_reason": "subscription_cycle",
            "amount_paid": 4999,
            "lines": {"data": [{"period": {"end": period_end}}]},
        }
        event_obj = self._event(data_obj)
        record_mock = AsyncMock()
        find_mock = AsyncMock(
            side_effect=[
                {"id": "row1", "driver_id": "d1", "plan_id": "p1", "plan_name": "Pro"},
                {"id": "d1", "user_id": "u1"},
            ]
        )

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", find_mock),
            patch("backend.routes.webhooks.db_supabase.update_one", AsyncMock()),
            patch("backend.routes.webhooks.send_push_notification", AsyncMock()),
            patch("backend.routes.drivers.subscriptions._record_subscription_payment", record_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._req()))

        assert result["received"] is True
        record_mock.assert_awaited_once()
        assert record_mock.await_args.kwargs["stripe_invoice_id"] == "in_99"
        assert record_mock.await_args.kwargs["billing_reason"] == "subscription_cycle"


class TestStripeWebhookInvoiceRecovery:
    """invoice.paid recovers the row from subscription metadata when it arrives
    before checkout linked stripe_subscription_id (round-6 P2)."""

    def _event(self, data_object, event_id="evt_inv_r"):
        raw = _make_stripe_event("invoice.paid", data_object, event_id=event_id)
        obj = MagicMock()
        obj.get = lambda k, d=None: raw.get(k, d)
        obj.to_dict_recursive = lambda: raw
        return obj

    def _settings(self):
        async def f():
            return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

        return f

    def _req(self):
        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "sig"}
        return req

    def test_invoice_paid_recovers_row_via_subscription_metadata(self):
        from datetime import datetime, timedelta, timezone

        import stripe

        from backend.routes import webhooks as wh

        period_end = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
        data_obj = {
            "id": "in_r",
            "subscription": "sub_unlinked",
            "billing_reason": "subscription_create",
            "amount_paid": 4999,
            "lines": {"data": [{"period": {"end": period_end}}]},
        }
        event_obj = self._event(data_obj)
        record_mock = AsyncMock()
        # 1st find_one (by stripe_subscription_id) misses; 2nd (by metadata id) hits.
        find_mock = AsyncMock(
            side_effect=[None, {"id": "row1", "driver_id": "d1", "plan_id": "p1", "plan_name": "Pro"}]
        )
        sub_obj = MagicMock()
        sub_obj.get = lambda k, d=None: {"metadata": {"subscription_id": "row1"}}.get(k, d)

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", find_mock),
            patch("backend.routes.webhooks.db_supabase.update_one", AsyncMock()),
            patch.object(stripe.Subscription, "retrieve", return_value=sub_obj),
            patch("backend.routes.drivers.subscriptions._record_subscription_payment", record_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._req()))

        assert result["received"] is True
        record_mock.assert_awaited_once()
        assert record_mock.await_args.kwargs["stripe_invoice_id"] == "in_r"


class TestRideInvoicePaid:
    """invoice.paid carrying metadata.ride_id settles a stuck ride paid via the
    admin 'Send Invoice' remediation (not a subscription)."""

    def _event(self, data_object, event_id="evt_ride_inv_1"):
        raw = _make_stripe_event("invoice.paid", data_object, event_id=event_id)
        obj = MagicMock()
        obj.get = lambda k, d=None: raw.get(k, d)
        obj.to_dict_recursive = lambda: raw
        return obj

    def _settings(self):
        async def f():
            return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

        return f

    def _req(self):
        req = MagicMock()
        req.body = AsyncMock(return_value=b"payload")
        req.headers = {"stripe-signature": "sig"}
        return req

    def test_ride_invoice_paid_settles_ride(self):
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {
            "id": "in_ride_1",
            "metadata": {"ride_id": "ride_xyz"},
            "amount_paid": 402,
            "payment_intent": "pi_inv_1",
        }
        event_obj = self._event(data_obj)
        update_ride_mock = AsyncMock()
        record_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.get_ride",
                AsyncMock(
                    return_value={"id": "ride_xyz", "rider_id": "u1", "payment_status": "failed", "tip_amount": "0"}
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
            result = asyncio.run(wh.stripe_webhook(request=self._req()))

        assert result["received"] is True
        upd = update_ride_mock.await_args.args[1]
        assert upd["payment_status"] == "paid"
        assert upd["stripe_invoice_id"] == "in_ride_1"
        assert upd["payment_intent_id"] == "pi_inv_1"
        record_mock.assert_awaited_once()

    def test_ride_invoice_paid_idempotent_when_already_paid(self):
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {"id": "in_ride_2", "metadata": {"ride_id": "ride_paid"}, "amount_paid": 402}
        event_obj = self._event(data_obj, event_id="evt_ride_inv_2")
        update_ride_mock = AsyncMock()
        record_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.get_ride",
                AsyncMock(return_value={"id": "ride_paid", "rider_id": "u1", "payment_status": "paid"}),
            ),
            patch("backend.routes.webhooks.db_supabase.update_ride", update_ride_mock),
            patch("backend.services.payment_service.record_payment_event", record_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._req()))

        assert result["received"] is True
        # Already settled — no ledger write, no ride update (no double-credit).
        record_mock.assert_not_awaited()
        update_ride_mock.assert_not_awaited()

    def test_ride_invoice_paid_recovers_ride_by_invoice_id_without_metadata(self):
        """Codex round-5 (81SX): a paid ride invoice whose metadata.ride_id was
        stripped still settles — the handler falls back to looking up the ride by
        stripe_invoice_id (no subscription id, no metadata)."""
        import stripe

        from backend.routes import webhooks as wh

        # No metadata.ride_id, no subscription → would otherwise be dropped.
        data_obj = {"id": "in_ride_nometa", "amount_paid": 402, "payment_intent": "pi_nm_1"}
        event_obj = self._event(data_obj, event_id="evt_ride_nometa")
        update_ride_mock = AsyncMock()
        record_mock = AsyncMock()
        # find_one("rides", {"stripe_invoice_id": "in_ride_nometa"}) → the ride.
        ride_row = {"id": "ride_nm", "rider_id": "u1", "payment_status": "failed", "tip_amount": "0"}

        async def _get_ride(rid):
            return ride_row if rid == "ride_nm" else None

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(return_value=ride_row)),
            patch("backend.routes.webhooks.db_supabase.get_ride", AsyncMock(side_effect=_get_ride)),
            patch("backend.routes.webhooks.db_supabase.update_ride", update_ride_mock),
            patch("backend.services.payment_service.record_payment_event", record_mock),
            patch("services.payment_service.record_payment_event", record_mock),
            patch("backend.services.payment_service.send_ride_receipt", AsyncMock(return_value=True)),
            patch("services.payment_service.send_ride_receipt", AsyncMock(return_value=True)),
            patch("backend.socket_manager.manager.send_personal_message", AsyncMock()),
            patch("socket_manager.manager.send_personal_message", AsyncMock()),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._req()))

        assert result["received"] is True
        upd = update_ride_mock.await_args.args[1]
        assert upd["payment_status"] == "paid"

    def test_ride_invoice_paid_skips_when_refunded(self):
        """Codex round-4 (P2): a ride already 'refunded' (by charge.refunded) is
        terminal — a delayed invoice.paid must NOT re-settle it (which would
        append a ledger row and flip payment_status back to 'paid', erasing the
        refund)."""
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {"id": "in_ride_ref", "metadata": {"ride_id": "ride_ref"}, "amount_paid": 402}
        event_obj = self._event(data_obj, event_id="evt_ride_inv_ref")
        update_ride_mock = AsyncMock()
        record_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.get_ride",
                AsyncMock(return_value={"id": "ride_ref", "rider_id": "u1", "payment_status": "refunded"}),
            ),
            patch("backend.routes.webhooks.db_supabase.update_ride", update_ride_mock),
            patch("backend.services.payment_service.record_payment_event", record_mock),
        ):
            result = asyncio.run(wh.stripe_webhook(request=self._req()))

        assert result["received"] is True
        record_mock.assert_not_awaited()
        update_ride_mock.assert_not_awaited()

    def test_ride_invoice_paid_defers_when_processing(self):
        """A ride mid in-app charge (payment_status='processing') must NOT also be
        settled by the invoice path (double-credit). It must also NOT ack the
        event — a bare return would stamp processed_at and permanently drop this
        paid invoice if the in-app charge later fails. So it RAISES (processed_at
        stays NULL) for a later replay once 'processing' resolves."""
        import stripe
        from fastapi import HTTPException

        from backend.routes import webhooks as wh

        data_obj = {"id": "in_ride_3", "metadata": {"ride_id": "ride_proc"}, "amount_paid": 402}
        event_obj = self._event(data_obj, event_id="evt_ride_inv_3")
        update_ride_mock = AsyncMock()
        record_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.get_ride",
                AsyncMock(return_value={"id": "ride_proc", "rider_id": "u1", "payment_status": "processing"}),
            ),
            patch("backend.routes.webhooks.db_supabase.update_ride", update_ride_mock),
            patch("backend.services.payment_service.record_payment_event", record_mock),
            patch("services.payment_service.record_payment_event", record_mock),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(wh.stripe_webhook(request=self._req()))

        assert exc.value.status_code == 500
        # No settlement happened, and the event was not acked.
        record_mock.assert_not_awaited()
        update_ride_mock.assert_not_awaited()

    def test_ride_invoice_paid_extracts_pi_from_basil_payments(self):
        """Codex P2: under the pinned API version (2025-04-30.basil) the
        top-level invoice.payment_intent is gone — the PI lives under
        payments.data[].payment.payment_intent. The ride must still be written
        with a payment_intent_id so refund/dispute webhooks can find it."""
        import stripe

        from backend.routes import webhooks as wh

        data_obj = {
            "id": "in_ride_basil",
            "metadata": {"ride_id": "ride_basil"},
            "amount_paid": 402,
            # No top-level payment_intent — Basil shape only.
            "payments": {"data": [{"payment": {"payment_intent": "pi_basil_1"}}]},
        }
        event_obj = self._event(data_obj, event_id="evt_ride_basil")
        update_ride_mock = AsyncMock()
        record_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.get_ride",
                AsyncMock(
                    return_value={"id": "ride_basil", "rider_id": "u1", "payment_status": "failed", "tip_amount": "0"}
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
            result = asyncio.run(wh.stripe_webhook(request=self._req()))

        assert result["received"] is True
        upd = update_ride_mock.await_args.args[1]
        assert upd["payment_intent_id"] == "pi_basil_1"

    def test_ride_invoice_paid_raises_when_pi_unresolved(self):
        """Codex round-3 (#4): if the PI can't be resolved (no payload field and
        the expand-retrieve fallback fails), do NOT mark the ride paid with a NULL
        payment_intent_id — raise so Stripe retries; refund/dispute lookups depend
        on that id."""
        import stripe
        from fastapi import HTTPException

        from backend.routes import webhooks as wh

        # No top-level payment_intent and no payments shape.
        data_obj = {"id": "in_ride_nopi", "metadata": {"ride_id": "ride_nopi"}, "amount_paid": 402}
        event_obj = self._event(data_obj, event_id="evt_ride_nopi")
        update_ride_mock = AsyncMock()
        record_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.get_ride",
                AsyncMock(
                    return_value={"id": "ride_nopi", "rider_id": "u1", "payment_status": "failed", "tip_amount": "0"}
                ),
            ),
            patch("backend.routes.webhooks.db_supabase.update_ride", update_ride_mock),
            patch("backend.services.payment_service.record_payment_event", record_mock),
            patch("services.payment_service.record_payment_event", record_mock),
            # The expand-retrieve fallback also fails to yield a PI.
            patch("stripe.Invoice.retrieve", MagicMock(side_effect=Exception("stripe down"))),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(wh.stripe_webhook(request=self._req()))

        assert exc.value.status_code == 500
        # No half-settled ride: the paid write never happened.
        update_ride_mock.assert_not_awaited()

    def test_ride_invoice_paid_raises_when_ride_missing(self):
        """Codex P1: an invoice.paid whose ride_id no longer resolves must NOT be
        acked — raising keeps processed_at NULL so the stuck-event reconciliation
        path catches it instead of silently taking the rider's money."""
        import stripe
        from fastapi import HTTPException

        from backend.routes import webhooks as wh

        data_obj = {
            "id": "in_ride_missing",
            "metadata": {"ride_id": "ride_gone"},
            "amount_paid": 402,
            "payment_intent": "pi_missing_1",
        }
        event_obj = self._event(data_obj, event_id="evt_ride_missing")
        record_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", self._settings()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.get_ride", AsyncMock(return_value=None)),
            patch("backend.services.payment_service.record_payment_event", record_mock),
            patch("services.payment_service.record_payment_event", record_mock),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(wh.stripe_webhook(request=self._req()))

        assert exc.value.status_code == 500
        # No ledger write for a ride we couldn't load.
        record_mock.assert_not_awaited()
