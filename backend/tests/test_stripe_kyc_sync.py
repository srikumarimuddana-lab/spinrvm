"""Unit tests for services/stripe_kyc_sync.py's
get_legal_name_and_address_from_stripe — the SIN-free counterpart to
reveal_sin_from_stripe, used by the T4A filer-handoff export
(routes/admin/compliance.py).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from backend.services import stripe_kyc_sync
except ImportError:  # pragma: no cover
    from services import stripe_kyc_sync  # type: ignore

pytestmark = pytest.mark.unit


def _driver(**overrides):
    return {"id": "d1", "stripe_account_id": "acct_123", **overrides}


class TestGetLegalNameAndAddressFromStripe:
    async def test_returns_none_without_stripe_account_id(self):
        assert await stripe_kyc_sync.get_legal_name_and_address_from_stripe({"id": "d1"}) is None

    async def test_returns_none_when_stripe_not_configured(self):
        with patch.object(
            stripe_kyc_sync, "get_app_settings", AsyncMock(return_value={"stripe_secret_key": ""})
        ):
            assert await stripe_kyc_sync.get_legal_name_and_address_from_stripe(_driver()) is None

    async def test_extracts_legal_name_and_address_without_sin(self):
        # Regression: this function must NEVER expand individual.id_number
        # — reveal_sin_from_stripe is the only path allowed to touch the
        # SIN. Verify both the call args and the returned shape.
        account = {
            "individual": {
                "first_name": "Jane",
                "last_name": "Doe",
                "address": {
                    "line1": "123 Main St",
                    "line2": "Unit 4",
                    "city": "Regina",
                    "state": "SK",
                    "postal_code": "S4P 1A1",
                    "country": "CA",
                },
            }
        }
        mock_stripe = MagicMock()
        mock_stripe.Account.retrieve.return_value = account
        with (
            patch.object(
                stripe_kyc_sync, "get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})
            ),
            patch.dict("sys.modules", {"stripe": mock_stripe}),
        ):
            result = await stripe_kyc_sync.get_legal_name_and_address_from_stripe(_driver())

        assert result == {
            "legal_name": "Jane Doe",
            "address_line1": "123 Main St",
            "address_line2": "Unit 4",
            "city": "Regina",
            "province": "SK",
            "postal_code": "S4P 1A1",
            "country": "CA",
        }
        call_kwargs = mock_stripe.Account.retrieve.call_args.kwargs
        assert "expand" not in call_kwargs or "individual.id_number" not in (call_kwargs.get("expand") or [])

    async def test_returns_none_on_stripe_error(self):
        mock_stripe = MagicMock()
        mock_stripe.Account.retrieve.side_effect = RuntimeError("stripe down")
        with (
            patch.object(
                stripe_kyc_sync, "get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})
            ),
            patch.dict("sys.modules", {"stripe": mock_stripe}),
        ):
            assert await stripe_kyc_sync.get_legal_name_and_address_from_stripe(_driver()) is None

    async def test_returns_none_when_individual_has_no_name_or_address(self):
        mock_stripe = MagicMock()
        mock_stripe.Account.retrieve.return_value = {"individual": {}}
        with (
            patch.object(
                stripe_kyc_sync, "get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})
            ),
            patch.dict("sys.modules", {"stripe": mock_stripe}),
        ):
            assert await stripe_kyc_sync.get_legal_name_and_address_from_stripe(_driver()) is None
