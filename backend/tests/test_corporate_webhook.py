"""Stripe webhook — corporate top-up branch."""

import json
from unittest.mock import AsyncMock, patch


def _event(**overrides):
    base = {
        "id": "evt_1",
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_123",
                "amount_received": 50000,
                "currency": "cad",
                "metadata": {
                    "scope": "corporate_topup",
                    "company_id": "c1",
                    "wallet_id": "w1",
                    "initiated_by": "admin_1",
                },
            }
        },
    }
    base.update(overrides)
    return base


def _post(test_client, event):
    # construct_event is patched, so body and signature values don't matter —
    # but the JSON body still has to be syntactically valid since Starlette
    # buffers it before handing it to the route.
    return test_client.post(
        "/api/v1/webhooks/stripe",
        content=json.dumps(event).encode(),
        headers={"stripe-signature": "t=1,v1=fake"},
    )


def test_corporate_topup_webhook_credits_wallet(test_client):
    event = _event()

    with (
        patch(
            "routes.webhooks.get_app_settings",
            AsyncMock(return_value={"stripe_webhook_secret": "whsec_x", "stripe_secret_key": "sk_x"}),
        ),
        patch("stripe.Webhook.construct_event", return_value=event),
        patch("routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)) as m_claim,
        patch("routes.webhooks.mark_stripe_event_processed", AsyncMock()) as m_mark,
        patch(
            "services.corporate_wallet_service.apply_topup",
            AsyncMock(return_value={"transaction_id": "t1", "balance_after": "500.00"}),
        ) as m_apply,
    ):
        resp = _post(test_client, event)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scope"] == "corporate_topup"
    assert body["event_id"] == "evt_1"

    m_claim.assert_awaited_once()
    m_apply.assert_awaited_once()
    kwargs = m_apply.call_args.kwargs
    assert kwargs["wallet_id"] == "w1"
    assert kwargs["amount"] == 500  # 50000 cents / 100
    assert kwargs["stripe_payment_intent_id"] == "pi_123"
    assert kwargs["actor_user_id"] == "admin_1"
    m_mark.assert_awaited_once_with("evt_1")


def test_corporate_topup_duplicate_event_is_noop(test_client):
    event = _event()

    with (
        patch(
            "routes.webhooks.get_app_settings",
            AsyncMock(return_value={"stripe_webhook_secret": "whsec_x", "stripe_secret_key": "sk_x"}),
        ),
        patch("stripe.Webhook.construct_event", return_value=event),
        patch("routes.webhooks.claim_stripe_event", AsyncMock(return_value=False)),
        patch("services.corporate_wallet_service.apply_topup", AsyncMock()) as m_apply,
    ):
        resp = _post(test_client, event)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["duplicate"] is True
    m_apply.assert_not_awaited()


def test_non_corporate_topup_passes_through(test_client):
    # Regular ride payment — should NOT call apply_topup.
    event = _event()
    event["data"]["object"]["metadata"] = {"ride_id": "r1", "user_id": "u1"}

    with (
        patch(
            "routes.webhooks.get_app_settings",
            AsyncMock(return_value={"stripe_webhook_secret": "whsec_x", "stripe_secret_key": "sk_x"}),
        ),
        patch("stripe.Webhook.construct_event", return_value=event),
        patch("routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
        patch("routes.webhooks.mark_stripe_event_processed", AsyncMock()),
        patch("routes.webhooks.db_supabase.update_ride", AsyncMock()),
        patch("routes.webhooks.send_push_notification", AsyncMock()),
        patch("services.corporate_wallet_service.apply_topup", AsyncMock()) as m_apply,
    ):
        resp = _post(test_client, event)

    assert resp.status_code == 200, resp.text
    m_apply.assert_not_awaited()
