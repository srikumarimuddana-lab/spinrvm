"""Stripe refund webhook behavior around atomic cumulative accounting.

The database RPC serializes ride summary and append-only ledger projection. These
route tests ensure webhook delivery delegates the confirmed aggregate to that
transaction, retries accounting failures, and keeps rider notices tied to a
newly applied delta.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _event_obj(event_type: str, data_object: dict, event_id: str) -> MagicMock:
    raw = {"id": event_id, "type": event_type, "data": {"object": data_object}}
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


def _ride():
    return {"id": "ride_1", "rider_id": "rider_1", "payment_intent_id": "pi_1", "refund_amount": "0"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_charge_refunded_delegates_confirmed_cumulative_to_atomic_projector():
    import stripe
    from backend.routes import webhooks as wh

    event = _event_obj("charge.refunded", {"id": "ch_1", "payment_intent": "pi_1", "amount_refunded": 2000}, "evt_1")
    ride = _ride()
    reconcile = AsyncMock(return_value={"outcome": "applied", "delta_cents": 1000, "refund_amount_cents": 2000})
    push = AsyncMock()
    email = AsyncMock()
    with (
        patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
        patch.object(stripe.Webhook, "construct_event", return_value=event),
        patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
        patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
        patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(return_value=[ride])),
        patch("backend.routes.webhooks.send_push_notification", push),
        patch("backend.routes.webhooks.send_refund_email", email),
        patch("backend.services.payment_service.reconcile_confirmed_stripe_refund", reconcile),
    ):
        result = await wh.stripe_webhook(request=_mock_req())

    assert result["received"] is True
    reconcile.assert_awaited_once_with(ride_id="ride_1", payment_intent_id="pi_1")
    push.assert_awaited_once()
    assert "$10.00 has been confirmed" in push.await_args.args[2]
    email.assert_awaited_once_with("rider_1", 10, ride=ride)


@pytest.mark.unit
@pytest.mark.anyio
async def test_charge_refunded_duplicate_cumulative_has_no_delta_notice():
    import stripe
    from backend.routes import webhooks as wh

    event = _event_obj("charge.refunded", {"id": "ch_1", "payment_intent": "pi_1", "amount_refunded": 2000}, "evt_2")
    reconcile = AsyncMock(return_value={"outcome": "applied", "delta_cents": 0, "refund_amount_cents": 2000})
    with (
        patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
        patch.object(stripe.Webhook, "construct_event", return_value=event),
        patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
        patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
        patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(return_value=[_ride()])),
        patch("backend.routes.webhooks.send_push_notification", AsyncMock()) as push,
        patch("backend.routes.webhooks.send_refund_email", AsyncMock()) as email,
        patch("backend.services.payment_service.reconcile_confirmed_stripe_refund", reconcile),
    ):
        result = await wh.stripe_webhook(request=_mock_req())
    assert result["received"] is True
    push.assert_not_awaited()
    email.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.anyio
async def test_stale_charge_refunded_is_acknowledged_without_notice():
    import stripe
    from backend.routes import webhooks as wh

    event = _event_obj("charge.refunded", {"id": "ch_1", "payment_intent": "pi_1", "amount_refunded": 1000}, "evt_3")
    reconcile = AsyncMock(return_value={"outcome": "stale", "delta_cents": 0})
    with (
        patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
        patch.object(stripe.Webhook, "construct_event", return_value=event),
        patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
        patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
        patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(return_value=[_ride()])),
        patch("backend.routes.webhooks.send_push_notification", AsyncMock()) as push,
        patch("backend.services.payment_service.reconcile_confirmed_stripe_refund", reconcile),
    ):
        result = await wh.stripe_webhook(request=_mock_req())
    assert result["received"] is True
    push.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.anyio
async def test_refund_accounting_failure_unclaims_for_retry_without_partial_ride_write():
    import stripe
    from backend.routes import webhooks as wh

    event = _event_obj("charge.refunded", {"id": "ch_1", "payment_intent": "pi_1", "amount_refunded": 2000}, "evt_4")
    unclaim = AsyncMock()
    with (
        patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
        patch.object(stripe.Webhook, "construct_event", return_value=event),
        patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
        patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
        patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(return_value=[_ride()])),
        patch("backend.routes.webhooks.db_supabase.update_one", AsyncMock()) as ride_write,
        patch("backend.routes.webhooks.unclaim_stripe_event", unclaim),
        patch("backend.services.payment_service.reconcile_confirmed_stripe_refund", AsyncMock(side_effect=RuntimeError("RPC unavailable"))),
    ):
        with pytest.raises(HTTPException) as exc:
            await wh.stripe_webhook(request=_mock_req())
    assert exc.value.status_code == 500
    unclaim.assert_awaited_once_with("evt_4")
    ride_write.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.anyio
async def test_refund_updated_accounting_failure_unclaims_even_without_operation_row():
    """An external Refund may have no durable worker operation. A succeeded
    webhook still has to retry when aggregate accounting cannot be applied."""
    import stripe
    from backend.routes import webhooks as wh

    refund = {
        "id": "re_external", "status": "succeeded", "amount": 1200,
        "payment_intent": "pi_1", "metadata": {"ride_id": "ride_1"},
    }
    event = _event_obj("refund.updated", refund, "evt_refund_updated")
    unclaim = AsyncMock()
    with (
        patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
        patch.object(stripe.Webhook, "construct_event", return_value=event),
        patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
        patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
        patch("backend.routes.webhooks.db_supabase.find_one", AsyncMock(return_value=None)),
        patch("backend.routes.webhooks.db_supabase.update_one", AsyncMock()),
        patch("backend.routes.webhooks.unclaim_stripe_event", unclaim),
        patch("backend.services.payment_service.reconcile_confirmed_stripe_refund", AsyncMock(side_effect=RuntimeError("RPC unavailable"))),
    ):
        with pytest.raises(HTTPException) as exc:
            await wh.stripe_webhook(request=_mock_req())
    assert exc.value.status_code == 500
    unclaim.assert_awaited_once_with("evt_refund_updated")
