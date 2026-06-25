"""Unit tests for the admin "send receipt to a different email" override.

Covers utils.email_receipt.send_receipt_email's recipient_email parameter:
- override address is used as the To: when provided
- rider's on-file email is used when no override is given
- a missing destination (no override, no rider email) returns False

Patching notes:
  send_receipt_email imports the provider inline as
  `from .email_provider import send_transactional_email` (or the top-level
  `utils.email_provider` fallback), so we patch it on the email_provider module.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    from utils.email_receipt import send_receipt_email
except ImportError:
    from backend.utils.email_receipt import send_receipt_email  # type: ignore[no-redef]

_RIDE = {
    "id": "ride-abc",
    "ride_code": "SPIN1234",
    "base_fare": "5.00",
    "distance_fare": "3.00",
    "time_fare": "2.00",
    "booking_fee": "1.00",
    "surge_multiplier": "1.0",
    "total_fare": "11.00",
    "grand_total": None,
    "distance_km": "10.5",
    "duration_minutes": 15,
    "pickup_address": "123 Main St",
    "dropoff_address": "456 Elm St",
}

_RIDER = {"id": "rider-xyz", "email": "rider@example.com", "first_name": "Alice", "last_name": "Rider"}


def _patch_provider(captured: dict):
    async def _capture(**kwargs):
        captured.update(kwargs)
        return True

    return patch("utils.email_provider.send_transactional_email", AsyncMock(side_effect=_capture))


@pytest.mark.anyio
async def test_receipt_override_email_used():
    """When recipient_email is provided it overrides the rider's address."""
    captured: dict = {}
    with _patch_provider(captured):
        ok = await send_receipt_email(_RIDE, _RIDER, None, 0.0, recipient_email="billing@corp.example")
    assert ok is True
    assert captured.get("to") == "billing@corp.example"


@pytest.mark.anyio
async def test_receipt_defaults_to_rider_email():
    """With no override, the receipt goes to the rider's on-file email."""
    captured: dict = {}
    with _patch_provider(captured):
        ok = await send_receipt_email(_RIDE, _RIDER, None, 0.0)
    assert ok is True
    assert captured.get("to") == "rider@example.com"


@pytest.mark.anyio
async def test_receipt_no_destination_returns_false():
    """No override and no rider email → nothing is sent."""
    captured: dict = {}
    rider_no_email = {"id": "rider-xyz"}
    with _patch_provider(captured):
        ok = await send_receipt_email(_RIDE, rider_no_email, None, 0.0)
    assert ok is False
    assert captured == {}
