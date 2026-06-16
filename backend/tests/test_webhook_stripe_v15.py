"""Regression test for the stripe-python v15 webhook AttributeError.

In stripe==15.1.0 the Event returned by Webhook.construct_event is NOT a dict
subclass: ``event.get(...)`` and ``event.to_dict_recursive()`` raise
``AttributeError: get`` via __getattr__, which 500'd every webhook delivery
(all event types, table stayed at 0 rows). _event_to_plain_dict normalizes the
Event to a plain dict so field access + jsonb storage work again.
"""

from __future__ import annotations

from unittest.mock import MagicMock

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
