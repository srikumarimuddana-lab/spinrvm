"""Regression test for the stripe-python v15 webhook AttributeError.

In stripe==15.1.0 the Event returned by Webhook.construct_event is NOT a dict
subclass: ``event.get(...)`` and ``event.to_dict_recursive()`` raise
``AttributeError: get`` via __getattr__, which 500'd every webhook delivery
(all event types, table stayed at 0 rows). _event_to_plain_dict normalizes the
Event to a plain dict so field access + jsonb storage work again.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_real_stripe_v15_event_is_normalized():
    """A real stripe v15 Event (no .get, has _to_dict_recursive) → plain dict."""
    stripe = pytest.importorskip("stripe")
    from backend.routes.webhooks import _event_to_plain_dict

    raw = {
        "id": "evt_1",
        "type": "payment_intent.succeeded",
        "data": {"object": {"id": "pi_1", "metadata": {"k": "v"}}},
    }
    event = stripe.Event.construct_from(raw, "sk_test")

    # Sanity: this is exactly the shape that crashed production.
    assert not isinstance(event, dict)
    with pytest.raises(AttributeError):
        event.get("id")

    out = _event_to_plain_dict(event)
    assert isinstance(out, dict)
    assert out["id"] == "evt_1"
    assert out["type"] == "payment_intent.succeeded"
    # Nested objects must be plain dicts too (for jsonb storage + .get access).
    assert out["data"]["object"]["metadata"]["k"] == "v"
    assert isinstance(out["data"]["object"], dict)
    # And the normalized event supports .get() (the call that was crashing).
    assert out.get("type") == "payment_intent.succeeded"


def test_plain_dict_passthrough():
    from backend.routes.webhooks import _event_to_plain_dict

    d = {"id": "evt_2", "type": "charge.succeeded", "data": {"object": {}}}
    assert _event_to_plain_dict(d) is d


def test_configured_mock_with_get_is_not_transformed():
    """A test mock that already exposes a working .get must be returned as-is
    (so existing webhook tests keep working)."""
    from backend.routes.webhooks import _event_to_plain_dict

    m = MagicMock()
    m.get.side_effect = lambda k, default=None: {"id": "evt_3", "type": "t"}.get(k, default)
    out = _event_to_plain_dict(m)
    assert out is m
    assert out.get("id") == "evt_3"


def test_handler_processes_real_stripe_event_end_to_end():
    """CI GUARD: drive a REAL stripe.Event (from the installed library) through
    the actual stripe_webhook handler. If a future stripe version bump changes
    the Event API so the webhook can't read/normalize it, this fails in CI
    instead of 500'ing every webhook in production (as stripe v15 did)."""
    stripe = pytest.importorskip("stripe")
    from backend.routes import webhooks as wh

    raw = {"id": "evt_ci_1", "type": "balance.available", "data": {"object": {"id": "ba_1"}}}
    real_event = stripe.Event.construct_from(raw, "sk_test")
    # Sanity: the not-a-dict, no-.get shape the handler must survive.
    assert not isinstance(real_event, dict)

    async def _settings():
        return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

    req = MagicMock()
    req.body = AsyncMock(return_value=b'{"id":"evt_ci_1"}')
    req.headers = {"stripe-signature": "sig"}

    claim = AsyncMock(return_value=True)
    with (
        patch("backend.routes.webhooks.get_app_settings", _settings),
        patch.object(stripe.Webhook, "construct_event", return_value=real_event),
        patch("backend.routes.webhooks.claim_stripe_event", claim),
        patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
    ):
        result = asyncio.run(wh.stripe_webhook(request=req))

    # Processed without AttributeError (the v15 crash) and acknowledged.
    assert result["received"] is True
    # Event was normalized to a PLAIN dict before persistence, and id/type were
    # read via .get() (the exact calls that crashed on a raw v15 Event).
    event_id_arg, event_type_arg, payload_arg = claim.call_args.args[:3]
    assert event_id_arg == "evt_ci_1"
    assert event_type_arg == "balance.available"
    assert isinstance(payload_arg, dict) and payload_arg["id"] == "evt_ci_1"
