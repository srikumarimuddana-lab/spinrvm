"""Unit tests for POST /payments/payment-sheet.

Pins that the endpoint:
  - Returns paymentIntent, ephemeralKey, customer, publishableKey on success
  - Validates amount against the stored ride fare when ride_id is supplied
  - Returns 503 when stripe_secret_key is missing
  - Returns 404 when ride_id references a non-existent ride
  - Returns 400 when amount does not match ride fare
  - Returns 502 on StripeError (not 500)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe

_USER = {"id": "usr-001", "stripe_customer_id": "cus_existing"}
_RIDE_ID = "ride-abc"
_FAKE_RIDE = {"id": _RIDE_ID, "total_fare": "15.50", "rider_id": "usr-001"}
_STRIPE_SECRET = "sk_test_secret"
_STRIPE_PK = "pk_test_pub"


def _settings(with_secret: bool = True):
    return {
        "stripe_secret_key": _STRIPE_SECRET if with_secret else "",
        "stripe_publishable_key": _STRIPE_PK,
    }


@pytest.mark.anyio
async def test_payment_sheet_success():
    from backend.routes.payments import PaymentSheetRequest, create_payment_sheet

    mock_intent = MagicMock()
    mock_intent.client_secret = "pi_secret_xxx"
    mock_ephemeral = MagicMock()
    mock_ephemeral.secret = "ek_secret_yyy"

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch("backend.routes.payments.stripe.EphemeralKey.create", return_value=mock_ephemeral),
        patch("backend.routes.payments.stripe.PaymentIntent.create", return_value=mock_intent),
    ):
        mock_db.get_ride = AsyncMock(return_value=_FAKE_RIDE)
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)
        mock_db.update_one = AsyncMock()

        result = await create_payment_sheet(
            body=PaymentSheetRequest(amount=15.50, ride_id=_RIDE_ID),
            current_user=_USER,
        )

    assert result["paymentIntent"] == "pi_secret_xxx"
    assert result["ephemeralKey"] == "ek_secret_yyy"
    assert result["customer"] == "cus_existing"
    assert result["publishableKey"] == _STRIPE_PK


@pytest.mark.anyio
async def test_payment_sheet_no_stripe_key():
    from fastapi import HTTPException

    from backend.routes.payments import PaymentSheetRequest, create_payment_sheet

    with patch(
        "backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings(with_secret=False)
    ):
        with pytest.raises(HTTPException) as exc_info:
            await create_payment_sheet(
                body=PaymentSheetRequest(amount=10.00),
                current_user=_USER,
            )
    assert exc_info.value.status_code == 503


@pytest.mark.anyio
async def test_payment_sheet_ride_not_found():
    from fastapi import HTTPException

    from backend.routes.payments import PaymentSheetRequest, create_payment_sheet

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
    ):
        mock_db.get_ride = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await create_payment_sheet(
                body=PaymentSheetRequest(amount=15.50, ride_id="non-existent"),
                current_user=_USER,
            )
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_payment_sheet_amount_mismatch():
    from fastapi import HTTPException

    from backend.routes.payments import PaymentSheetRequest, create_payment_sheet

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
    ):
        mock_db.get_ride = AsyncMock(return_value=_FAKE_RIDE)

        with pytest.raises(HTTPException) as exc_info:
            await create_payment_sheet(
                body=PaymentSheetRequest(amount=20.00, ride_id=_RIDE_ID),  # wrong amount
                current_user=_USER,
            )
    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_payment_sheet_stripe_error_returns_502():
    from fastapi import HTTPException

    from backend.routes.payments import PaymentSheetRequest, create_payment_sheet

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch("backend.routes.payments.stripe.EphemeralKey.create", side_effect=stripe.error.StripeError("bad")),
    ):
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)

        with pytest.raises(HTTPException) as exc_info:
            await create_payment_sheet(
                body=PaymentSheetRequest(amount=15.50),
                current_user=_USER,
            )
    assert exc_info.value.status_code == 502
