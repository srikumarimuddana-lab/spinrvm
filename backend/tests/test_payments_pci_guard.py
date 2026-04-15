"""
Tests for the PCI-DSS perimeter on POST /payments/cards.

Pins the C-PAY-01 fix: the backend must refuse to accept raw card data
(PAN, CVC, expiry) under any circumstances. Only a client-tokenized
``payment_method_id`` is allowed.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mock_request(body_dict):
    """Build a minimal FastAPI Request-like object whose .json() returns body_dict."""
    req = MagicMock()
    req.json = AsyncMock(return_value=body_dict)
    return req


@pytest.mark.asyncio
class TestAddCardRejectsRawCardData:
    """Every known raw-card field must trigger a 400 before any Stripe call."""

    @pytest.mark.parametrize(
        "field",
        [
            "card_number",
            "number",
            "cvc",
            "cvv",
            "cvv2",
            "exp_month",
            "exp_year",
            "expiry",
            "expiration",
        ],
    )
    async def test_rejects_each_raw_field(self, field):
        from backend.routes.payments import add_card

        body = {"payment_method_id": "pm_fake", field: "4242424242424242"}
        with pytest.raises(HTTPException) as exc_info:
            await add_card(_mock_request(body), current_user={"id": "user_1"})

        assert exc_info.value.status_code == 400
        assert "tokenize" in exc_info.value.detail.lower()

    async def test_rejects_multiple_raw_fields(self):
        from backend.routes.payments import add_card

        body = {
            "payment_method_id": "pm_fake",
            "card_number": "4242424242424242",
            "cvc": "123",
            "exp_month": 12,
            "exp_year": 2030,
        }
        with pytest.raises(HTTPException) as exc_info:
            await add_card(_mock_request(body), current_user={"id": "user_1"})

        assert exc_info.value.status_code == 400

    async def test_error_detail_does_not_echo_sensitive_values(self):
        """The 400 response must NOT contain the PAN that was submitted."""
        from backend.routes.payments import add_card

        pan = "4242424242424242"
        body = {"card_number": pan}
        with pytest.raises(HTTPException) as exc_info:
            await add_card(_mock_request(body), current_user={"id": "user_1"})

        assert pan not in exc_info.value.detail


@pytest.mark.asyncio
class TestAddCardRequiresPaymentMethodId:
    async def test_rejects_empty_body(self):
        from backend.routes.payments import add_card

        with pytest.raises(HTTPException) as exc_info:
            await add_card(_mock_request({}), current_user={"id": "user_1"})

        assert exc_info.value.status_code == 400

    async def test_rejects_missing_payment_method_id(self):
        from backend.routes.payments import add_card

        with pytest.raises(HTTPException) as exc_info:
            await add_card(
                _mock_request({"unrelated": "field"}),
                current_user={"id": "user_1"},
            )

        assert exc_info.value.status_code == 400

    async def test_rejects_non_object_body(self):
        from backend.routes.payments import add_card

        with pytest.raises(HTTPException) as exc_info:
            await add_card(
                _mock_request(["not", "an", "object"]),
                current_user={"id": "user_1"},
            )

        assert exc_info.value.status_code == 400
        assert "object" in exc_info.value.detail.lower()


@pytest.mark.asyncio
class TestAddCardDemoMode:
    """Demo mode (no stripe_secret_key) still requires payment_method_id."""

    async def test_demo_mode_returns_synthetic_card(self):
        with patch(
            "backend.routes.payments.get_app_settings",
            new=AsyncMock(return_value={"stripe_secret_key": ""}),
        ):
            from backend.routes.payments import add_card

            result = await add_card(
                _mock_request({"payment_method_id": "pm_demo_123"}),
                current_user={"id": "user_1"},
            )

            assert result["id"] == "pm_demo_123"
            assert result["last4"] == "4242"
            assert result["is_default"] is True

    async def test_demo_mode_still_rejects_raw_card_data(self):
        """Demo mode is not a bypass — raw card fields are refused everywhere."""
        with patch(
            "backend.routes.payments.get_app_settings",
            new=AsyncMock(return_value={"stripe_secret_key": ""}),
        ):
            from backend.routes.payments import add_card

            with pytest.raises(HTTPException) as exc_info:
                await add_card(
                    _mock_request({"card_number": "4242424242424242"}),
                    current_user={"id": "user_1"},
                )
            assert exc_info.value.status_code == 400


def test_raw_card_field_list_is_non_empty():
    """Sanity: the guard list must cover at least the common field names."""
    from backend.routes.payments import _RAW_CARD_FIELDS

    # These are the ones an attacker would most likely try.
    must_have = {"card_number", "number", "cvc", "exp_month", "exp_year"}
    assert must_have.issubset(_RAW_CARD_FIELDS)
