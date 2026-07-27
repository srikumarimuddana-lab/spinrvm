"""Dispute refund dollars→cents conversion — HALF_UP, never truncation.

admin_resolve_dispute previously converted the refund with
``int(refund_amount * 100)``, which truncates: a 10.005 refund became
1000 cents instead of 1001. The endpoint now uses
``utils.money.dollars_to_cents`` (Decimal HALF_UP). These tests capture
the ``amount`` actually sent to Stripe.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.anyio

_DISPUTE = {
    "id": "disp_1",
    "ride_id": "ride_1",
    "user_id": "user_1",
    "status": "open",
    # Must cover the largest parametrized refund_amount below (29.99) --
    # admin_resolve_dispute now rejects a refund exceeding original_fare.
    "original_fare": 50.00,
}

_ADMIN = {"id": "admin_1", "email": "ops@spinr.ca", "role": "admin"}


async def _resolve_and_capture_stripe_amount(refund_amount: str) -> dict:
    from backend.routes.disputes import ResolveDisputeRequest, admin_resolve_dispute

    captured: dict = {}

    def fake_refund_create(**kwargs):
        captured.update(kwargs)
        refund = MagicMock()
        refund.status = "succeeded"
        refund.id = "re_123"
        return refund

    req = ResolveDisputeRequest(
        resolution="partial_refund",
        refund_amount=Decimal(refund_amount),
        admin_note="cents conversion test",
    )

    with (
        patch(
            "backend.routes.disputes.db_supabase.get_rows",
            AsyncMock(return_value=[dict(_DISPUTE)]),
        ),
        patch(
            "backend.routes.disputes.db_supabase.get_ride",
            AsyncMock(return_value={"id": "ride_1", "stripe_charge_id": "pi_123"}),
        ),
        patch(
            "backend.routes.disputes.db_supabase.update_one",
            AsyncMock(return_value={"id": "disp_1"}),
        ),
        patch(
            "backend.routes.disputes.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test_x"}),
        ),
        patch("backend.routes.disputes.log_admin_action", AsyncMock()),
        patch("backend.routes.disputes.send_push_notification", AsyncMock()),
        patch("stripe.Refund.create", side_effect=fake_refund_create),
    ):
        result = await admin_resolve_dispute(dispute_id="disp_1", req=req, current_admin=dict(_ADMIN))

    assert result["success"] is True
    return captured


@pytest.mark.parametrize(
    ("refund", "expected_cents"),
    [
        ("10.005", 1001),  # truncation regression: int(10.005 * 100) == 1000
        ("10.004", 1000),  # below the half — rounds down
        ("29.99", 2999),  # the classic int(29.99 * 100) == 2998 float trap
        ("0.01", 1),
        ("19.999", 2000),
    ],
)
async def test_stripe_refund_amount_is_half_up_cents(refund, expected_cents):
    captured = await _resolve_and_capture_stripe_amount(refund)
    assert captured["amount"] == expected_cents, (
        f"refund {refund} must convert to {expected_cents} cents, got {captured['amount']}"
    )


async def test_refund_idempotency_key_carries_dispute_id():
    """The Stripe idempotency key must be stable per dispute so admin retries
    after a 502 reuse the same refund."""
    captured = await _resolve_and_capture_stripe_amount("5.00")
    assert captured["idempotency_key"] == "refund-dispute-disp_1"
