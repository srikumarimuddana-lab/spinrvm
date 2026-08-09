"""Regression tests for stripe.EphemeralKey.create() call sites.

Background
----------
stripe-python 15.1.0 defines ``EphemeralKey.create`` as a classmethod with the
signature ``create(cls, **params)`` — it accepts **no positional params dict**,
and the request option that pins the Stripe API version is ``stripe_version``
(``api_version`` is the *module-global* read, not a valid per-call kwarg).

Two production call sites (POST /wallet/top-up and POST /payments/payment-sheet)
were calling it like::

    stripe.EphemeralKey.create(
        {"customer": customer_id},        # positional dict -> TypeError
        api_version=stripe.api_version,   # unknown request option
        api_key=stripe_secret,
    )

which raised at runtime::

    TypeError: EphemeralKey.create() takes 1 positional argument but 2 were given

The existing endpoint tests mocked ``EphemeralKey.create`` with a bare
``MagicMock`` (no spec), so they accepted any call shape and never caught it.
These tests assert the *call contract* directly, so a regression to the
positional-dict / ``api_version=`` form fails here.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_USER = {"id": "usr-001", "stripe_customer_id": "cus_existing"}
_STRIPE_SECRET = "sk_test_secret"
_STRIPE_PK = "pk_test_pub"


def _settings() -> dict:
    return {"stripe_secret_key": _STRIPE_SECRET, "stripe_publishable_key": _STRIPE_PK}


def _assert_ephemeral_key_contract(mock_create: MagicMock) -> None:
    """Assert EphemeralKey.create was called the way stripe-python 15.x requires.

    Guards the exact regression that broke wallet top-up:
      * keyword-only params (no positional dict)
      * customer passed as a keyword
      * version pinned via ``stripe_version`` (NOT ``api_version``)
    """
    mock_create.assert_called_once()
    args, kwargs = mock_create.call_args
    assert args == (), (
        "EphemeralKey.create() takes no positional params in stripe-python 15.x; "
        f"got positional args {args!r}"
    )
    assert kwargs.get("customer"), "customer must be passed as a keyword argument"
    assert "stripe_version" in kwargs, (
        "API version must be pinned via the stripe_version= request option"
    )
    assert "api_version" not in kwargs, (
        "api_version is not a valid stripe-python request option; use stripe_version="
    )


@pytest.mark.anyio
async def test_wallet_top_up_ephemeral_key_call_contract():
    from backend.routes.wallet import TopUpRequest, top_up_wallet

    wallet = {"id": "wallet_1", "user_id": _USER["id"], "is_active": True}
    mock_ephemeral = MagicMock(secret="ek_secret_yyy")
    mock_intent = MagicMock(client_secret="pi_secret_xxx")

    mock_db = MagicMock()
    mock_db.get_user_by_id = AsyncMock(return_value=_USER)
    mock_db.update_one = AsyncMock()

    # idempotency decorator runs normally when no Idempotency-Key header is set.
    request = MagicMock()
    request.headers = {}

    with (
        patch("backend.routes.wallet.get_or_create_wallet", AsyncMock(return_value=wallet)),
        patch("backend.routes.wallet.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase", mock_db),
        patch("backend.routes.wallet.stripe.EphemeralKey.create", return_value=mock_ephemeral) as ek_create,
        patch("backend.routes.wallet.stripe.PaymentIntent.create", return_value=mock_intent),
    ):
        result = await top_up_wallet(
            req=TopUpRequest(amount=Decimal("25.00")),
            request=request,
            current_user=_USER,
        )

    _assert_ephemeral_key_contract(ek_create)
    assert result["ephemeralKey"] == "ek_secret_yyy"


@pytest.mark.anyio
async def test_payment_sheet_ephemeral_key_call_contract():
    from backend.routes.payments import PaymentSheetRequest, create_payment_sheet

    mock_ephemeral = MagicMock(secret="ek_secret_yyy")
    mock_intent = MagicMock(client_secret="pi_secret_xxx")

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch(
            "backend.routes.payments.get_or_create_stripe_customer",
            new_callable=AsyncMock,
            return_value="cus_existing",
        ),
        patch("backend.routes.payments.stripe.EphemeralKey.create", return_value=mock_ephemeral) as ek_create,
        patch("backend.routes.payments.stripe.PaymentIntent.create", return_value=mock_intent),
    ):
        result = await create_payment_sheet(
            body=PaymentSheetRequest(amount=15.50),
            current_user=_USER,
        )

    _assert_ephemeral_key_contract(ek_create)
    assert result["ephemeralKey"] == "ek_secret_yyy"
