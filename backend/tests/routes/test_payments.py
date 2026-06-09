"""Unit tests for POST /payments/confirm security fixes.

S-1: Ownership check — authenticated user must own the ride.
R-1: Concurrent race guard — atomic DB claim prevents double-charge.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

_OWNER_ID = "usr-owner"
_OTHER_ID = "usr-other"
_RIDE_ID = "ride-xyz"

_RIDE_OWNED = {"id": _RIDE_ID, "rider_id": _OWNER_ID, "payment_status": "pending"}

_OWNER_USER = {"id": _OWNER_ID}
_OTHER_USER = {"id": _OTHER_ID}


@pytest.mark.anyio
async def test_confirm_payment_ownership_check_rejects_non_owner():
    """S-1: a rider who does not own the ride receives 403."""
    from fastapi import HTTPException

    from backend.routes.payments import ConfirmPaymentRequest, confirm_payment

    with patch("backend.routes.payments.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=_RIDE_OWNED)

        with pytest.raises(HTTPException) as exc_info:
            await confirm_payment(
                body=ConfirmPaymentRequest(**{"payment_intent_id": "pi_123", "ride_id": _RIDE_ID}),
                request=None,
                current_user=_OTHER_USER,
            )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "forbidden"


@pytest.mark.anyio
async def test_confirm_payment_ownership_check_allows_owner():
    """S-1: the ride owner passes the ownership check and proceeds normally."""
    from backend.routes.payments import ConfirmPaymentRequest, confirm_payment

    with (
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value={}),
    ):
        mock_db.get_ride = AsyncMock(return_value=_RIDE_OWNED)
        mock_db.claim_ride_payment_processing = AsyncMock(return_value=True)
        mock_db.update_ride = AsyncMock()

        result = await confirm_payment(
            body=ConfirmPaymentRequest(**{"payment_intent_id": "pi_mock_abc", "ride_id": _RIDE_ID}),
            request=None,
            current_user=_OWNER_USER,
        )

    assert result["status"] == "succeeded"
    assert result["mock"] is True


@pytest.mark.anyio
async def test_confirm_payment_returns_404_when_ride_missing():
    """S-1: non-existent ride_id raises 404 before ownership or Stripe call."""
    from fastapi import HTTPException

    from backend.routes.payments import ConfirmPaymentRequest, confirm_payment

    with patch("backend.routes.payments.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await confirm_payment(
                body=ConfirmPaymentRequest(**{"payment_intent_id": "pi_123", "ride_id": "nonexistent"}),
                request=None,
                current_user=_OWNER_USER,
            )

    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_confirm_payment_race_guard_returns_409():
    """R-1: a concurrent second request loses the atomic claim and gets 409."""
    from fastapi import HTTPException

    from backend.routes.payments import ConfirmPaymentRequest, confirm_payment

    with patch("backend.routes.payments.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=_RIDE_OWNED)
        mock_db.claim_ride_payment_processing = AsyncMock(return_value=False)

        with pytest.raises(HTTPException) as exc_info:
            await confirm_payment(
                body=ConfirmPaymentRequest(**{"payment_intent_id": "pi_123", "ride_id": _RIDE_ID}),
                request=None,
                current_user=_OWNER_USER,
            )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "payment_already_processing"


@pytest.mark.anyio
async def test_confirm_payment_body_rejects_unknown_keys():
    """The /payments/confirm body is a typed model (extra='forbid') so
    attacker-supplied extra keys are rejected at validation instead of
    being silently carried through the mock/Stripe branches."""
    from pydantic import ValidationError

    from backend.routes.payments import ConfirmPaymentRequest

    with pytest.raises(ValidationError):
        ConfirmPaymentRequest(payment_intent_id="pi_123", ride_id=_RIDE_ID, is_admin=True)

    with pytest.raises(ValidationError):
        ConfirmPaymentRequest(payment_intent_id="x" * 256)
