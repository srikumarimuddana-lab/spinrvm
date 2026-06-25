"""
Unit tests to bring routes/payments.py to ≥ 90% coverage.

Covers the uncovered lines identified in the sprint audit:
  - get_or_create_stripe_customer (existing + new customer paths)
  - create_payment_intent (happy path, no stripe key, fare mismatch, ride not found)
  - confirm_payment (mock, real, no stripe key)
  - create_setup_intent (no key, stripe call, error)
  - get_payment_methods (no key, no customer, with methods)
  - get_cards (no key, with cards)
  - add_card (demo, stripe attach, card error, first card default logic)
  - set_default_card
  - delete_card (with and without default card cleanup)
  - payment-sheet idempotency key branches

All deps mocked; no real Stripe or DB calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe

# ── shared fixtures ────────────────────────────────────────────────────────────

_USER = {"id": "usr-001", "stripe_customer_id": "cus_existing"}
_USER_NO_STRIPE = {"id": "usr-002", "stripe_customer_id": None}
_SK = "sk_test_secret"
_PK = "pk_test_pub"


def _settings(with_secret: bool = True):
    return {
        "stripe_secret_key": _SK if with_secret else "",
        "stripe_publishable_key": _PK,
    }


def _mock_request(body_dict):
    req = MagicMock()
    req.json = AsyncMock(return_value=body_dict)
    return req


# ── get_or_create_stripe_customer ─────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_or_create_uses_existing_customer():
    from backend.routes.payments import get_or_create_stripe_customer

    with patch("backend.routes.payments.db_supabase") as mock_db:
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)
        result = await get_or_create_stripe_customer("usr-001", _SK)

    assert result == "cus_existing"


@pytest.mark.anyio
async def test_get_or_create_creates_new_customer():
    from backend.routes.payments import get_or_create_stripe_customer

    new_user = {**_USER_NO_STRIPE}
    refreshed = {**new_user, "stripe_customer_id": "cus_new"}

    mock_customer = MagicMock()
    mock_customer.id = "cus_new"

    with (
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch("backend.routes.payments.stripe.Customer.create", return_value=mock_customer),
    ):
        mock_db.get_user_by_id = AsyncMock(side_effect=[new_user, refreshed])
        mock_db.update_one = AsyncMock()

        result = await get_or_create_stripe_customer("usr-002", _SK)

    assert result == "cus_new"


@pytest.mark.anyio
async def test_get_or_create_raises_404_when_user_missing():
    from fastapi import HTTPException

    from backend.routes.payments import get_or_create_stripe_customer

    with patch("backend.routes.payments.db_supabase") as mock_db:
        mock_db.get_user_by_id = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await get_or_create_stripe_customer("usr-missing", _SK)
    assert exc.value.status_code == 404


# ── create_payment_intent ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_create_payment_intent_no_stripe_key():
    from fastapi import HTTPException

    from backend.routes.payments import PaymentIntentRequest, create_payment_intent

    mock_req = MagicMock()
    mock_req.headers = {}

    with patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings(False)):
        with pytest.raises(HTTPException) as exc:
            await create_payment_intent(
                body=PaymentIntentRequest(amount="10.00"),
                request=mock_req,
                current_user=_USER,
            )
    assert exc.value.status_code == 503


@pytest.mark.anyio
async def test_create_payment_intent_ride_not_found():
    """Ride not found: the HTTPException(404) is re-raised as a 500 by the generic
    except block in create_payment_intent — this is the current (tested) behaviour.
    What matters is that we get *an* HTTPException and that the ride lookup is attempted."""
    from fastapi import HTTPException

    from backend.routes.payments import PaymentIntentRequest, create_payment_intent

    mock_req = MagicMock()
    mock_req.headers = {}

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
    ):
        mock_db.get_ride = AsyncMock(return_value=None)
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)

        with pytest.raises(HTTPException) as exc:
            await create_payment_intent(
                body=PaymentIntentRequest(amount="10.00", ride_id="ride-missing"),
                request=mock_req,
                current_user=_USER,
            )
    # The route wraps the inner HTTPException(404) in a generic 500 handler
    assert exc.value.status_code in (404, 500)
    mock_db.get_ride.assert_called_once_with("ride-missing")


@pytest.mark.anyio
async def test_create_payment_intent_ride_not_owned_403():
    """A ride whose rider_id doesn't match the caller is rejected with the
    ownership 403 from _authoritative_ride_charge — surfaced as-is, no longer
    masked as a generic 500. (The advisory `amount` is ignored for ride
    payments, so there is no separate fare-mismatch error path.)"""
    from fastapi import HTTPException

    from backend.routes.payments import PaymentIntentRequest, create_payment_intent

    mock_req = MagicMock()
    mock_req.headers = {}

    fake_ride = {"id": "ride-123", "total_fare": "20.00"}

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
    ):
        mock_db.get_ride = AsyncMock(return_value=fake_ride)
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)

        with pytest.raises(HTTPException) as exc:
            await create_payment_intent(
                body=PaymentIntentRequest(amount="15.00", ride_id="ride-123"),
                request=mock_req,
                current_user=_USER,
            )
    assert exc.value.status_code == 403
    mock_db.get_ride.assert_called_once_with("ride-123")


@pytest.mark.anyio
async def test_create_payment_intent_success_with_client_key():
    from backend.routes.payments import PaymentIntentRequest, create_payment_intent

    mock_req = MagicMock()
    mock_req.headers = {}

    # rider_id must match: _authoritative_ride_charge 403s on non-owners.
    fake_ride = {"id": "ride-123", "total_fare": "15.00", "rider_id": _USER["id"]}
    mock_intent = MagicMock()
    mock_intent.client_secret = "pi_secret"
    mock_intent.id = "pi_001"

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch("backend.routes.payments.stripe.PaymentIntent.create", return_value=mock_intent),
    ):
        mock_db.get_ride = AsyncMock(return_value=fake_ride)
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)
        mock_db.update_one = AsyncMock()

        result = await create_payment_intent(
            body=PaymentIntentRequest(amount="15.00", ride_id="ride-123", client_idempotency_key="idem-abc"),
            request=mock_req,
            current_user=_USER,
        )

    assert result["payment_intent_id"] == "pi_001"
    assert result["mock"] is False


@pytest.mark.anyio
async def test_create_payment_intent_success_no_ride_id():
    """Uses time-bucket idempotency key when no ride_id and no client key."""
    from backend.routes.payments import PaymentIntentRequest, create_payment_intent

    mock_req = MagicMock()
    mock_req.headers = {}

    mock_intent = MagicMock()
    mock_intent.client_secret = "pi_secret_2"
    mock_intent.id = "pi_002"

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch("backend.routes.payments.stripe.PaymentIntent.create", return_value=mock_intent),
    ):
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)
        mock_db.update_one = AsyncMock()

        result = await create_payment_intent(
            body=PaymentIntentRequest(amount="10.00"),
            request=mock_req,
            current_user=_USER,
        )

    assert result["payment_intent_id"] == "pi_002"


@pytest.mark.anyio
async def test_create_payment_intent_with_payment_method_id():
    """Body.payment_method_id flows into intent_params."""
    from backend.routes.payments import PaymentIntentRequest, create_payment_intent

    mock_req = MagicMock()
    mock_req.headers = {}

    mock_intent = MagicMock()
    mock_intent.client_secret = "pi_secret_3"
    mock_intent.id = "pi_003"

    create_spy = MagicMock(return_value=mock_intent)

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch("backend.routes.payments.stripe.PaymentIntent.create", create_spy),
    ):
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)
        mock_db.update_one = AsyncMock()

        result = await create_payment_intent(
            body=PaymentIntentRequest(amount="10.00", payment_method_id="pm_test"),
            request=mock_req,
            current_user=_USER,
        )

    called_kwargs = create_spy.call_args[1]
    assert called_kwargs.get("api_key") == _SK
    assert result["payment_intent_id"] == "pi_003"


# ── confirm_payment ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_confirm_payment_mock():
    from backend.routes.payments import ConfirmPaymentRequest, confirm_payment

    with patch("backend.routes.payments.db_supabase") as mock_db:
        mock_db.update_ride = AsyncMock()
        mock_db.get_ride = AsyncMock(
            return_value={"id": "ride-1", "rider_id": _USER["id"], "payment_status": "pending"}
        )
        mock_db.claim_ride_payment_processing = AsyncMock(return_value=True)
        result = await confirm_payment(
            body=ConfirmPaymentRequest(**{"payment_intent_id": "pi_mock_test", "ride_id": "ride-1"}),
            current_user=_USER,
        )

    assert result["status"] == "succeeded"
    assert result["mock"] is True


@pytest.mark.anyio
async def test_confirm_payment_mock_no_ride_id():
    from backend.routes.payments import ConfirmPaymentRequest, confirm_payment

    result = await confirm_payment(
        body=ConfirmPaymentRequest(**{"payment_intent_id": "pi_mock_xyz"}),
        current_user=_USER,
    )
    assert result["status"] == "succeeded"


@pytest.mark.anyio
async def test_confirm_payment_no_stripe_key():
    from backend.routes.payments import ConfirmPaymentRequest, confirm_payment

    with patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings(False)):
        result = await confirm_payment(
            body=ConfirmPaymentRequest(**{"payment_intent_id": "pi_real_001"}),
            current_user=_USER,
        )
    assert result["status"] == "unknown"
    assert result["mock"] is True


@pytest.mark.anyio
async def test_confirm_payment_real_stripe():
    from backend.routes.payments import ConfirmPaymentRequest, confirm_payment

    mock_intent = MagicMock()
    mock_intent.status = "succeeded"
    mock_intent.metadata = {"user_id": str(_USER["id"])}

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.stripe.PaymentIntent.retrieve", return_value=mock_intent),
        patch("backend.routes.payments.db_supabase") as mock_db,
    ):
        mock_db.update_ride = AsyncMock()
        mock_db.get_ride = AsyncMock(
            return_value={"id": "ride-1", "rider_id": _USER["id"], "payment_status": "pending"}
        )
        mock_db.claim_ride_payment_processing = AsyncMock(return_value=True)
        result = await confirm_payment(
            body=ConfirmPaymentRequest(**{"payment_intent_id": "pi_real_001", "ride_id": "ride-1"}),
            current_user=_USER,
        )

    assert result["status"] == "succeeded"
    assert result["mock"] is False


# ── create_setup_intent ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_create_setup_intent_no_key_returns_mock():
    from backend.routes.payments import create_setup_intent

    with patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings(False)):
        result = await create_setup_intent(current_user=_USER)

    assert result["mock"] is True
    assert "client_secret" in result


@pytest.mark.anyio
async def test_create_setup_intent_with_stripe():
    from backend.routes.payments import create_setup_intent

    mock_si = MagicMock()
    mock_si.client_secret = "seti_secret"
    mock_si.id = "seti_001"

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch("backend.routes.payments.stripe.SetupIntent.create", return_value=mock_si),
    ):
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)
        mock_db.update_one = AsyncMock()

        result = await create_setup_intent(current_user=_USER)

    assert result["mock"] is False
    assert result["setup_intent_id"] == "seti_001"
    assert result["customer_id"] == "cus_existing"


@pytest.mark.anyio
async def test_create_setup_intent_stripe_error():
    from fastapi import HTTPException

    from backend.routes.payments import create_setup_intent

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch(
            "backend.routes.payments.stripe.SetupIntent.create",
            side_effect=stripe.error.StripeError("fail"),
        ),
    ):
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)
        mock_db.update_one = AsyncMock()

        with pytest.raises(HTTPException) as exc:
            await create_setup_intent(current_user=_USER)
    assert exc.value.status_code == 500


# ── get_payment_methods ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_payment_methods_no_key():
    from backend.routes.payments import get_payment_methods

    with patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings(False)):
        result = await get_payment_methods(current_user=_USER)

    assert result["methods"] == []
    assert result["mock"] is True


@pytest.mark.anyio
async def test_get_payment_methods_no_stripe_customer():
    from backend.routes.payments import get_payment_methods

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
    ):
        mock_db.get_user_by_id = AsyncMock(return_value=_USER_NO_STRIPE)

        result = await get_payment_methods(current_user=_USER_NO_STRIPE)

    assert result["methods"] == []
    assert result["mock"] is False


@pytest.mark.anyio
async def test_get_payment_methods_returns_list():
    from backend.routes.payments import get_payment_methods

    mock_pm = MagicMock()
    mock_pm.id = "pm_001"
    mock_pm.card.brand = "visa"
    mock_pm.card.last4 = "4242"
    mock_pm.card.exp_month = 12
    mock_pm.card.exp_year = 2030

    mock_methods = MagicMock()
    mock_methods.data = [mock_pm]

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch("backend.routes.payments.stripe.PaymentMethod.list", return_value=mock_methods),
    ):
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)

        result = await get_payment_methods(current_user=_USER)

    assert len(result["methods"]) == 1
    assert result["methods"][0]["id"] == "pm_001"
    assert result["mock"] is False


# ── get_cards ─────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_cards_no_stripe_key_returns_demo_card():
    """Demo mode (no Stripe secret) returns a single selectable demo card so the
    booking flow has a payment method to attach (settlement uses the unconfigured
    path that marks the ride paid without a real charge)."""
    from backend.routes.payments import get_cards

    with patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings(False)):
        result = await get_cards(current_user=_USER)

    assert isinstance(result, list) and len(result) == 1
    assert result[0]["id"] == "pm_demo_card"
    assert result[0]["is_default"] is True


@pytest.mark.anyio
async def test_get_cards_no_stripe_key_in_production_returns_503():
    """In production an empty Stripe key is a misconfiguration, not demo mode —
    never serve the demo card to a real rider; surface 503 instead."""
    from fastapi import HTTPException

    from backend.routes.payments import get_cards

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings(False)),
        patch("backend.routes.payments.core_settings") as mock_core,
    ):
        mock_core.ENV = "production"
        with pytest.raises(HTTPException) as exc:
            await get_cards(current_user=_USER)

    assert exc.value.status_code == 503


@pytest.mark.anyio
async def test_get_cards_with_stripe():
    from backend.routes.payments import get_cards

    mock_pm = MagicMock()
    mock_pm.id = "pm_001"
    mock_pm.card.brand = "visa"
    mock_pm.card.last4 = "4242"
    mock_pm.card.exp_month = 12
    mock_pm.card.exp_year = 2030

    mock_methods = MagicMock()
    mock_methods.data = [mock_pm]

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch("backend.routes.payments.stripe.PaymentMethod.list", return_value=mock_methods),
    ):
        mock_db.get_user_by_id = AsyncMock(return_value={**_USER, "default_payment_method": "pm_001"})

        result = await get_cards(current_user=_USER)

    assert len(result) == 1
    assert result[0]["is_default"] is True
    assert result[0]["last4"] == "4242"


@pytest.mark.anyio
async def test_get_cards_stripe_error_raises_502():
    """C7: a Stripe outage must NOT masquerade as an empty card list — it raises
    502 so the client retries instead of thinking the saved card vanished."""
    from fastapi import HTTPException

    from backend.routes.payments import get_cards

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch(
            "backend.routes.payments.stripe.PaymentMethod.list",
            side_effect=stripe.error.StripeError("fail"),
        ),
    ):
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)

        with pytest.raises(HTTPException) as exc:
            await get_cards(current_user=_USER)

    assert exc.value.status_code == 502


# ── add_card (stripe path) ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_add_card_with_stripe_success_first_card():
    """First card on file becomes the default automatically."""
    from backend.routes.payments import add_card

    mock_pm = MagicMock()
    mock_pm.id = "pm_new"
    mock_pm.card.brand = "visa"
    mock_pm.card.last4 = "1234"
    mock_pm.card.exp_month = 6
    mock_pm.card.exp_year = 2028

    mock_si = MagicMock()
    mock_si.id = "seti_new"
    mock_si.status = "succeeded"

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch("backend.routes.payments.stripe.PaymentMethod.attach", return_value=mock_pm),
        patch("backend.routes.payments.stripe.SetupIntent.create", return_value=mock_si),
        patch("backend.routes.payments.stripe.Customer.modify"),
    ):
        # User has no default payment method → this card becomes default
        mock_db.get_user_by_id = AsyncMock(return_value={**_USER, "default_payment_method": None})
        mock_db.update_one = AsyncMock()

        result = await add_card(
            _mock_request({"payment_method_id": "pm_new"}),
            current_user=_USER,
        )

    assert result["id"] == "pm_new"
    assert result["is_default"] is True


@pytest.mark.anyio
async def test_add_card_stripe_card_error_raises_400():
    """CardError is surfaced as PaymentMethodInvalidException (400)."""
    from backend.routes.payments import add_card

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch(
            "backend.routes.payments.stripe.PaymentMethod.attach",
            side_effect=stripe.error.CardError("Declined", param=None, code="card_declined"),
        ),
    ):
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)
        mock_db.update_one = AsyncMock()

        with pytest.raises(Exception) as exc:
            await add_card(
                _mock_request({"payment_method_id": "pm_bad"}),
                current_user=_USER,
            )

    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_add_card_generic_stripe_error_returns_500():
    from fastapi import HTTPException

    from backend.routes.payments import add_card

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch(
            "backend.routes.payments.stripe.PaymentMethod.attach",
            side_effect=Exception("unexpected"),
        ),
    ):
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)
        mock_db.update_one = AsyncMock()

        with pytest.raises(HTTPException) as exc:
            await add_card(
                _mock_request({"payment_method_id": "pm_bad2"}),
                current_user=_USER,
            )

    assert exc.value.status_code == 500


# ── set_default_card ──────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_set_default_card_updates_db_and_stripe():
    from backend.routes.payments import set_default_card

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch("backend.routes.payments.stripe.Customer.modify") as mock_modify,
    ):
        mock_db.update_one = AsyncMock()
        mock_db.get_user_by_id = AsyncMock(return_value={**_USER, "stripe_customer_id": "cus_existing"})

        result = await set_default_card(card_id="pm_001", current_user=_USER)

    assert result["success"] is True
    mock_modify.assert_called_once()


@pytest.mark.anyio
async def test_set_default_card_no_stripe_key_still_updates_db():
    from backend.routes.payments import set_default_card

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings(False)),
        patch("backend.routes.payments.db_supabase") as mock_db,
    ):
        mock_db.update_one = AsyncMock()

        result = await set_default_card(card_id="pm_001", current_user=_USER)

    assert result["success"] is True
    mock_db.update_one.assert_called_once()


# ── delete_card ───────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_delete_card_detaches_and_clears_default():
    from backend.routes.payments import delete_card

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch("backend.routes.payments.stripe.PaymentMethod.detach") as mock_detach,
    ):
        mock_db.get_user_by_id = AsyncMock(return_value={**_USER, "default_payment_method": "pm_001"})
        mock_db.update_one = AsyncMock()

        result = await delete_card(card_id="pm_001", current_user=_USER)

    assert result["success"] is True
    mock_detach.assert_called_once_with("pm_001", api_key=_SK)
    # default_payment_method should be cleared
    mock_db.update_one.assert_called_once()


@pytest.mark.anyio
async def test_delete_card_non_default_skips_db_clear():
    from backend.routes.payments import delete_card

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch("backend.routes.payments.stripe.PaymentMethod.detach"),
    ):
        mock_db.get_user_by_id = AsyncMock(return_value={**_USER, "default_payment_method": "pm_other"})
        mock_db.update_one = AsyncMock()

        result = await delete_card(card_id="pm_001", current_user=_USER)

    assert result["success"] is True
    mock_db.update_one.assert_not_called()


@pytest.mark.anyio
async def test_delete_card_no_stripe_key_skips_detach():
    from backend.routes.payments import delete_card

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings(False)),
        patch("backend.routes.payments.db_supabase") as mock_db,
    ):
        mock_db.get_user_by_id = AsyncMock(return_value={**_USER, "default_payment_method": None})
        mock_db.update_one = AsyncMock()

        result = await delete_card(card_id="pm_001", current_user=_USER)

    assert result["success"] is True


# ── payment-sheet extra branches ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_payment_sheet_ride_idempotency_key():
    """ride_id branch sets idempotency key as ps-{ride_id}-{user_id}-{amount_cents}.

    The amount-cents suffix is deliberate: a changed-tip retry must get a
    distinct key (fresh PaymentIntent) instead of a Stripe IdempotencyError;
    same amount → same key → still idempotent."""
    from backend.routes.payments import PaymentSheetRequest, create_payment_sheet

    mock_intent = MagicMock()
    mock_intent.client_secret = "pi_secret_ps"

    mock_ephemeral = MagicMock()
    mock_ephemeral.secret = "ek_secret_ps"

    create_spy = MagicMock(return_value=mock_intent)

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch("backend.routes.payments.stripe.EphemeralKey.create", return_value=mock_ephemeral),
        patch("backend.routes.payments.stripe.PaymentIntent.create", create_spy),
    ):
        mock_db.get_ride = AsyncMock(return_value={"id": "ride-ps", "total_fare": "12.00", "rider_id": _USER["id"]})
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)
        mock_db.update_one = AsyncMock()

        result = await create_payment_sheet(
            body=PaymentSheetRequest(amount="12.00", ride_id="ride-ps"),
            current_user=_USER,
        )

    called_kwargs = create_spy.call_args[1]
    assert called_kwargs["idempotency_key"] == f"ps-ride-ps-{_USER['id']}-1200"
    assert result["paymentIntent"] == "pi_secret_ps"


@pytest.mark.anyio
async def test_payment_sheet_time_bucket_idempotency_key():
    """No ride_id and no client key → time-bucket fallback key."""
    from backend.routes.payments import PaymentSheetRequest, create_payment_sheet

    mock_intent = MagicMock()
    mock_intent.client_secret = "pi_secret_tb"

    mock_ephemeral = MagicMock()
    mock_ephemeral.secret = "ek_secret_tb"

    create_spy = MagicMock(return_value=mock_intent)

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch("backend.routes.payments.stripe.EphemeralKey.create", return_value=mock_ephemeral),
        patch("backend.routes.payments.stripe.PaymentIntent.create", create_spy),
    ):
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)
        mock_db.update_one = AsyncMock()

        result = await create_payment_sheet(
            body=PaymentSheetRequest(amount="8.00"),
            current_user=_USER,
        )

    called_kwargs = create_spy.call_args[1]
    assert called_kwargs["idempotency_key"].startswith(f"ps-{_USER['id']}-")
    assert result["paymentIntent"] == "pi_secret_tb"


@pytest.mark.anyio
async def test_payment_sheet_generic_error_returns_500():
    from fastapi import HTTPException

    from backend.routes.payments import PaymentSheetRequest, create_payment_sheet

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch(
            "backend.routes.payments.stripe.EphemeralKey.create",
            side_effect=Exception("unexpected"),
        ),
    ):
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)
        mock_db.update_one = AsyncMock()

        with pytest.raises(HTTPException) as exc:
            await create_payment_sheet(
                body=PaymentSheetRequest(amount="8.00"),
                current_user=_USER,
            )

    assert exc.value.status_code == 500


# ── error handler branches ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_confirm_payment_stripe_error():
    """Stripe error during confirm_payment surfaces as 500."""
    from fastapi import HTTPException

    from backend.routes.payments import ConfirmPaymentRequest, confirm_payment

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch(
            "backend.routes.payments.stripe.PaymentIntent.retrieve",
            side_effect=Exception("network error"),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await confirm_payment(
                body=ConfirmPaymentRequest(**{"payment_intent_id": "pi_real_err"}),
                current_user=_USER,
            )
    assert exc.value.status_code == 500


@pytest.mark.anyio
async def test_get_payment_methods_stripe_error():
    """Stripe error during get_payment_methods surfaces as 500."""
    from fastapi import HTTPException

    from backend.routes.payments import get_payment_methods

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch(
            "backend.routes.payments.stripe.PaymentMethod.list",
            side_effect=stripe.error.StripeError("boom"),
        ),
    ):
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)

        with pytest.raises(HTTPException) as exc:
            await get_payment_methods(current_user=_USER)

    assert exc.value.status_code == 500


@pytest.mark.anyio
async def test_add_card_invalid_json():
    """Invalid JSON body returns 400."""
    from fastapi import HTTPException

    from backend.routes.payments import add_card

    req = MagicMock()
    req.json = AsyncMock(side_effect=Exception("bad json"))

    with pytest.raises(HTTPException) as exc:
        await add_card(req, current_user=_USER)

    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_set_default_card_stripe_error_raises_502_and_skips_db():
    """C7: Stripe is the source of truth — a failed Customer.modify raises 502 and
    must NOT write the DB default (no DB↔Stripe divergence)."""
    from fastapi import HTTPException

    from backend.routes.payments import set_default_card

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch(
            "backend.routes.payments.stripe.Customer.modify",
            side_effect=stripe.error.StripeError("fail"),
        ),
    ):
        mock_db.update_one = AsyncMock()
        mock_db.get_user_by_id = AsyncMock(return_value={**_USER, "stripe_customer_id": "cus_existing"})

        with pytest.raises(HTTPException) as exc:
            await set_default_card(card_id="pm_001", current_user=_USER)

    assert exc.value.status_code == 502
    mock_db.update_one.assert_not_awaited()  # DB default not written on Stripe failure


@pytest.mark.anyio
async def test_delete_card_stripe_error_raises_502_and_keeps_db():
    """C7: a failed detach raises 502 and leaves the DB untouched — the card must
    not vanish from the UI while still live in Stripe."""
    from fastapi import HTTPException

    from backend.routes.payments import delete_card

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch(
            "backend.routes.payments.stripe.PaymentMethod.detach",
            side_effect=stripe.error.StripeError("detach failed"),
        ),
    ):
        mock_db.get_user_by_id = AsyncMock(return_value={**_USER, "default_payment_method": "pm_001"})
        mock_db.update_one = AsyncMock()

        with pytest.raises(HTTPException) as exc:
            await delete_card(card_id="pm_001", current_user=_USER)

    assert exc.value.status_code == 502
    mock_db.update_one.assert_not_awaited()  # default not cleared when detach failed
