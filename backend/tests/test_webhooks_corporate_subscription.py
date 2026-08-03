"""Stripe webhook — corporate flat-SaaS subscription branch.

Covers the early-exit dispatch added to routes/webhooks.py for
customer.subscription.deleted/updated, invoice.paid, and
invoice.payment_failed when the Stripe subscription id belongs to a
corporate_subscriptions row (not a driver_subscriptions/Spinr Pass one).
See docs/change-log for the corresponding Change Impact Log entry.
"""

import json
from unittest.mock import AsyncMock, patch

_SETTINGS = {"stripe_webhook_secret": "whsec_x", "stripe_secret_key": "sk_x"}


def _sub_event(event_type, sub_id="sub_corp_1", **object_overrides):
    obj = {"id": sub_id, "customer": "cus_1", "status": "active", "cancel_at_period_end": False}
    obj.update(object_overrides)
    return {"id": "evt_1", "type": event_type, "data": {"object": obj}}


def _invoice_event(event_type, sub_id="sub_corp_1", **object_overrides):
    obj = {
        "id": "in_1",
        "subscription": sub_id,
        "lines": {"data": [{"period": {"start": 1_700_000_000, "end": 1_702_592_000}}]},
    }
    obj.update(object_overrides)
    return {"id": "evt_1", "type": event_type, "data": {"object": obj}}


def _post(test_client, event):
    return test_client.post(
        "/api/v1/webhooks/stripe",
        content=json.dumps(event).encode(),
        headers={"stripe-signature": "t=1,v1=fake"},
    )


def _corp_sub_row(**extra):
    return {
        "id": "corp-sub-row-1",
        "company_id": "c1",
        "status": "active",
        "stripe_subscription_id": "sub_corp_1",
        **extra,
    }


def test_subscription_deleted_cancels_row(test_client):
    event = _sub_event("customer.subscription.deleted")

    with (
        patch("routes.webhooks.get_app_settings", AsyncMock(return_value=_SETTINGS)),
        patch("stripe.Webhook.construct_event", return_value=event),
        patch("routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
        patch("routes.webhooks.mark_stripe_event_processed", AsyncMock()) as m_mark,
        patch(
            "routes.webhooks.db_supabase.get_corporate_subscription_by_stripe_id",
            AsyncMock(return_value=_corp_sub_row()),
        ),
        patch("routes.webhooks.db_supabase.update_corporate_subscription", AsyncMock()) as m_update,
    ):
        resp = _post(test_client, event)

    assert resp.status_code == 200, resp.text
    assert resp.json()["scope"] == "corporate_subscription"
    m_update.assert_awaited_once()
    args, _ = m_update.call_args
    assert args[0] == "corp-sub-row-1"
    assert args[1]["status"] == "cancelled"
    m_mark.assert_awaited_once_with("evt_1")


def test_subscription_deleted_is_noop_when_already_cancelled(test_client):
    event = _sub_event("customer.subscription.deleted")

    with (
        patch("routes.webhooks.get_app_settings", AsyncMock(return_value=_SETTINGS)),
        patch("stripe.Webhook.construct_event", return_value=event),
        patch("routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
        patch("routes.webhooks.mark_stripe_event_processed", AsyncMock()),
        patch(
            "routes.webhooks.db_supabase.get_corporate_subscription_by_stripe_id",
            AsyncMock(return_value=_corp_sub_row(status="cancelled")),
        ),
        patch("routes.webhooks.db_supabase.update_corporate_subscription", AsyncMock()) as m_update,
    ):
        resp = _post(test_client, event)

    assert resp.status_code == 200, resp.text
    m_update.assert_not_awaited()


def test_subscription_updated_past_due(test_client):
    event = _sub_event("customer.subscription.updated", status="past_due", current_period_end=1_702_592_000)

    with (
        patch("routes.webhooks.get_app_settings", AsyncMock(return_value=_SETTINGS)),
        patch("stripe.Webhook.construct_event", return_value=event),
        patch("routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
        patch("routes.webhooks.mark_stripe_event_processed", AsyncMock()),
        patch(
            "routes.webhooks.db_supabase.get_corporate_subscription_by_stripe_id",
            AsyncMock(return_value=_corp_sub_row()),
        ),
        patch("routes.webhooks.db_supabase.update_corporate_subscription", AsyncMock()) as m_update,
    ):
        resp = _post(test_client, event)

    assert resp.status_code == 200, resp.text
    patch_dict = m_update.call_args[0][1]
    assert patch_dict["status"] == "past_due"
    assert "current_period_end" in patch_dict


def test_subscription_updated_canceled_sets_cancelled_at(test_client):
    event = _sub_event("customer.subscription.updated", status="canceled")

    with (
        patch("routes.webhooks.get_app_settings", AsyncMock(return_value=_SETTINGS)),
        patch("stripe.Webhook.construct_event", return_value=event),
        patch("routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
        patch("routes.webhooks.mark_stripe_event_processed", AsyncMock()),
        patch(
            "routes.webhooks.db_supabase.get_corporate_subscription_by_stripe_id",
            AsyncMock(return_value=_corp_sub_row()),
        ),
        patch("routes.webhooks.db_supabase.update_corporate_subscription", AsyncMock()) as m_update,
    ):
        resp = _post(test_client, event)

    assert resp.status_code == 200, resp.text
    patch_dict = m_update.call_args[0][1]
    assert patch_dict["status"] == "cancelled"
    assert patch_dict["cancelled_at"]


def test_invoice_paid_renews_and_clears_past_due(test_client):
    event = _invoice_event("invoice.paid")

    with (
        patch("routes.webhooks.get_app_settings", AsyncMock(return_value=_SETTINGS)),
        patch("stripe.Webhook.construct_event", return_value=event),
        patch("routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
        patch("routes.webhooks.mark_stripe_event_processed", AsyncMock()),
        patch(
            "routes.webhooks.db_supabase.get_corporate_subscription_by_stripe_id",
            AsyncMock(return_value=_corp_sub_row(status="past_due")),
        ),
        patch("routes.webhooks.db_supabase.update_corporate_subscription", AsyncMock()) as m_update,
    ):
        resp = _post(test_client, event)

    assert resp.status_code == 200, resp.text
    patch_dict = m_update.call_args[0][1]
    assert patch_dict["status"] == "active"
    assert "current_period_end" in patch_dict


def test_invoice_paid_ignored_for_cancelled_row(test_client):
    event = _invoice_event("invoice.paid")

    with (
        patch("routes.webhooks.get_app_settings", AsyncMock(return_value=_SETTINGS)),
        patch("stripe.Webhook.construct_event", return_value=event),
        patch("routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
        patch("routes.webhooks.mark_stripe_event_processed", AsyncMock()),
        patch(
            "routes.webhooks.db_supabase.get_corporate_subscription_by_stripe_id",
            AsyncMock(return_value=_corp_sub_row(status="cancelled")),
        ),
        patch("routes.webhooks.db_supabase.update_corporate_subscription", AsyncMock()) as m_update,
    ):
        resp = _post(test_client, event)

    assert resp.status_code == 200, resp.text
    m_update.assert_not_awaited()


def test_invoice_payment_failed_flags_past_due(test_client):
    event = _invoice_event("invoice.payment_failed")

    with (
        patch("routes.webhooks.get_app_settings", AsyncMock(return_value=_SETTINGS)),
        patch("stripe.Webhook.construct_event", return_value=event),
        patch("routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
        patch("routes.webhooks.mark_stripe_event_processed", AsyncMock()),
        patch(
            "routes.webhooks.db_supabase.get_corporate_subscription_by_stripe_id",
            AsyncMock(return_value=_corp_sub_row()),
        ),
        patch("routes.webhooks.db_supabase.update_corporate_subscription", AsyncMock()) as m_update,
    ):
        resp = _post(test_client, event)

    assert resp.status_code == 200, resp.text
    assert m_update.call_args[0][1] == {"status": "past_due"}


def test_driver_subscription_event_untouched_by_corporate_lookup(test_client):
    """A driver Spinr Pass subscription id must never match a corporate
    lookup — confirms the early-exit guard only fires when
    get_corporate_subscription_by_stripe_id actually finds a row, so the
    existing driver_subscriptions dispatch below it is unaffected."""
    event = _sub_event("customer.subscription.deleted", sub_id="sub_driver_1")

    with (
        patch("routes.webhooks.get_app_settings", AsyncMock(return_value=_SETTINGS)),
        patch("stripe.Webhook.construct_event", return_value=event),
        patch("routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
        patch("routes.webhooks.mark_stripe_event_processed", AsyncMock()),
        patch("routes.webhooks.db_supabase.get_corporate_subscription_by_stripe_id", AsyncMock(return_value=None)),
        patch("routes.webhooks.db_supabase.find_one", AsyncMock(return_value=None)) as m_find,
    ):
        resp = _post(test_client, event)

    assert resp.status_code == 200, resp.text
    assert resp.json().get("scope") != "corporate_subscription"
    # Fell through to the existing driver_subscriptions lookup, unmodified.
    m_find.assert_awaited()
