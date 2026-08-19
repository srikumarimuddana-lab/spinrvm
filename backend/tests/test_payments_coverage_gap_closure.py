"""
Closes the genuine coverage gaps identified in
docs/audit/2026-08-19-decision-writeups.md (item #4, `routes/payments.py`
86.13% vs. the 90% CLAUDE.md target) that `test_coverage_payments.py`
doesn't already exercise:

  - confirm_payment: production mock-payment rejection guard
  - confirm_payment: C-3 idempotency early-return (already-settled ride)
  - confirm_payment: mock-path and real-path ownership mismatches (403s)
  - create_payment_intent: requires_action success-path 402 (distinct from
    the synchronous CardError 3DS path already covered elsewhere)
  - create_payment_intent: generic StripeError (502) and unexpected
    Exception (500) handlers
  - add_card: generic stripe.error.StripeError handler specifically (the
    existing test only exercises the broader `except Exception` branch,
    which sits below StripeError in the except-clause order)
  - set_default_card / delete_card: WS-18 card-ownership 404s, both with
    and without a configured Stripe secret

Per the decision-log research: none of these are money-arithmetic gaps —
they're correctness/idempotency/ownership branches. All deps mocked; no
real Stripe or DB calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe
from fastapi import HTTPException

_USER = {"id": "usr-001", "stripe_customer_id": "cus_existing", "phone": "+13065550100"}
_OTHER_USER_ID = "usr-999"
_SK = "sk_test_secret"


def _settings(with_secret: bool = True):
    return {"stripe_secret_key": _SK if with_secret else "", "stripe_publishable_key": "pk_test_pub"}


def _mock_request(body_dict):
    req = MagicMock()
    req.json = AsyncMock(return_value=body_dict)
    return req


def _mock_card_list(*ids: str):
    methods = MagicMock()
    methods.data = [MagicMock(id=i) for i in ids]
    return methods


# ── confirm_payment: production mock-payment rejection guard ───────────────────


@pytest.mark.anyio
async def test_confirm_payment_mock_rejected_in_production():
    """A pi_mock_* confirm in production, from a non-reviewer account, must be
    rejected with 400 BEFORE the ride is claimed into payment_status=processing
    — otherwise any rider could settle their own ride for free."""
    from backend.routes.payments import ConfirmPaymentRequest, confirm_payment

    with (
        patch("backend.routes.payments.core_settings") as mock_core,
        patch("backend.routes.payments.db_supabase") as mock_db,
    ):
        mock_core.ENV = "production"
        mock_core.review_login_map.return_value = {}
        mock_db.claim_ride_payment_processing = AsyncMock()

        with pytest.raises(HTTPException) as exc:
            await confirm_payment(
                body=ConfirmPaymentRequest(**{"payment_intent_id": "pi_mock_free_ride", "ride_id": "ride-1"}),
                current_user=_USER,
            )

    assert exc.value.status_code == 400
    mock_db.claim_ride_payment_processing.assert_not_awaited()


@pytest.mark.anyio
async def test_confirm_payment_mock_allowed_for_reviewer_in_production():
    """The app-store reviewer allow-list bypasses the production mock-payment
    rejection, so a reviewer can still reach a mock 'paid' confirmation."""
    from backend.routes.payments import ConfirmPaymentRequest, confirm_payment

    with (
        patch("backend.routes.payments.core_settings") as mock_core,
        patch("backend.routes.payments.db_supabase") as mock_db,
    ):
        mock_core.ENV = "production"
        mock_core.review_login_map.return_value = {_USER["phone"]: "reviewer"}
        mock_db.claim_ride_payment_processing = AsyncMock(return_value=True)
        mock_db.get_ride = AsyncMock(return_value={"id": "ride-1", "rider_id": _USER["id"], "payment_status": "pending"})
        mock_db.update_ride = AsyncMock()

        result = await confirm_payment(
            body=ConfirmPaymentRequest(**{"payment_intent_id": "pi_mock_reviewer", "ride_id": "ride-1"}),
            current_user=_USER,
        )

    assert result["status"] == "succeeded"
    assert result["mock"] is True


# ── confirm_payment: C-3 idempotency early-return ───────────────────────────────


@pytest.mark.anyio
async def test_confirm_payment_already_paid_returns_early():
    """A ride already payment_status='paid' (settled by a prior webhook)
    short-circuits with 'already_processed' instead of re-entering Stripe."""
    from backend.routes.payments import ConfirmPaymentRequest, confirm_payment

    with patch("backend.routes.payments.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value={"id": "ride-1", "rider_id": _USER["id"], "payment_status": "paid"})
        mock_db.claim_ride_payment_processing = AsyncMock()

        result = await confirm_payment(
            body=ConfirmPaymentRequest(**{"payment_intent_id": "pi_real_002", "ride_id": "ride-1"}),
            current_user=_USER,
        )

    assert result == {"status": "already_processed", "payment_status": "paid"}
    mock_db.claim_ride_payment_processing.assert_not_awaited()


@pytest.mark.anyio
async def test_confirm_payment_already_processing_returns_early():
    """Same idempotency short-circuit for payment_status='processing' (a
    concurrent confirm_payment call already claimed this ride)."""
    from backend.routes.payments import ConfirmPaymentRequest, confirm_payment

    with patch("backend.routes.payments.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(
            return_value={"id": "ride-1", "rider_id": _USER["id"], "payment_status": "processing"}
        )
        mock_db.claim_ride_payment_processing = AsyncMock()

        result = await confirm_payment(
            body=ConfirmPaymentRequest(**{"payment_intent_id": "pi_real_003", "ride_id": "ride-1"}),
            current_user=_USER,
        )

    assert result == {"status": "already_processed", "payment_status": "processing"}
    mock_db.claim_ride_payment_processing.assert_not_awaited()


# ── confirm_payment: ownership mismatches ───────────────────────────────────────


@pytest.mark.anyio
async def test_confirm_payment_mock_ride_ownership_mismatch():
    """Mock path: a ride owned by a different rider must 403, not settle."""
    from backend.routes.payments import ConfirmPaymentRequest, confirm_payment

    with patch("backend.routes.payments.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(
            return_value={"id": "ride-1", "rider_id": _OTHER_USER_ID, "payment_status": "pending"}
        )
        mock_db.claim_ride_payment_processing = AsyncMock(return_value=True)
        mock_db.update_ride = AsyncMock()

        with pytest.raises(HTTPException) as exc:
            await confirm_payment(
                body=ConfirmPaymentRequest(**{"payment_intent_id": "pi_mock_other", "ride_id": "ride-1"}),
                current_user=_USER,
            )

    assert exc.value.status_code == 403
    mock_db.update_ride.assert_not_awaited()


@pytest.mark.anyio
async def test_confirm_payment_real_intent_user_mismatch():
    """Non-mock path: a PaymentIntent whose metadata.user_id doesn't match the
    caller must 403 — a rider cannot confirm someone else's intent."""
    from backend.routes.payments import ConfirmPaymentRequest, confirm_payment

    mock_intent = MagicMock()
    mock_intent.status = "succeeded"
    mock_intent.metadata = {"user_id": _OTHER_USER_ID}

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.stripe.PaymentIntent.retrieve", return_value=mock_intent),
        patch("backend.routes.payments.db_supabase") as mock_db,
    ):
        mock_db.claim_ride_payment_processing = AsyncMock(return_value=True)

        with pytest.raises(HTTPException) as exc:
            await confirm_payment(
                body=ConfirmPaymentRequest(**{"payment_intent_id": "pi_real_004"}),
                current_user=_USER,
            )

    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_confirm_payment_real_ride_ownership_mismatch():
    """Non-mock path: the intent belongs to the caller, but the named ride
    belongs to someone else — must still 403."""
    from backend.routes.payments import ConfirmPaymentRequest, confirm_payment

    mock_intent = MagicMock()
    mock_intent.status = "succeeded"
    mock_intent.metadata = {"user_id": str(_USER["id"])}

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.stripe.PaymentIntent.retrieve", return_value=mock_intent),
        patch("backend.routes.payments.db_supabase") as mock_db,
    ):
        # First get_ride (pre-claim ownership + idempotency check) is the
        # caller's own ride; the second get_ride (post-retrieve, inside the
        # non-mock try block) returns a ride owned by someone else.
        mock_db.get_ride = AsyncMock(
            side_effect=[
                {"id": "ride-1", "rider_id": _USER["id"], "payment_status": "pending"},
                {"id": "ride-1", "rider_id": _OTHER_USER_ID, "payment_status": "pending"},
            ]
        )
        mock_db.claim_ride_payment_processing = AsyncMock(return_value=True)

        with pytest.raises(HTTPException) as exc:
            await confirm_payment(
                body=ConfirmPaymentRequest(**{"payment_intent_id": "pi_real_005", "ride_id": "ride-1"}),
                current_user=_USER,
            )

    assert exc.value.status_code == 403


# ── create_payment_intent: requires_action success path, error handlers ────────


@pytest.mark.anyio
async def test_create_payment_intent_requires_action_success_path():
    """When Stripe's PaymentIntent.create itself returns status='requires_action'
    (not a synchronous CardError), the endpoint surfaces a 402 JSONResponse with
    the SDK's action_required envelope — distinct from the synchronous 3DS
    CardError path covered in test_payments_stripe_error_specificity.py."""
    from starlette.responses import JSONResponse

    from backend.routes.payments import PaymentIntentRequest, create_payment_intent

    mock_intent = MagicMock()
    mock_intent.status = "requires_action"
    mock_intent.client_secret = "pi_secret_action"
    mock_intent.id = "pi_action_001"
    mock_intent.next_action = {"type": "use_stripe_sdk"}

    mock_req = MagicMock()
    mock_req.headers = {}

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

    assert isinstance(result, JSONResponse)
    assert result.status_code == 402


@pytest.mark.anyio
async def test_create_payment_intent_generic_stripe_error_502():
    from backend.routes.payments import PaymentIntentRequest, create_payment_intent

    mock_req = MagicMock()
    mock_req.headers = {}

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch(
            "backend.routes.payments.stripe.PaymentIntent.create",
            side_effect=stripe.error.StripeError("provider hiccup"),
        ),
    ):
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)
        mock_db.update_one = AsyncMock()

        with pytest.raises(HTTPException) as exc:
            await create_payment_intent(
                body=PaymentIntentRequest(amount="10.00"),
                request=mock_req,
                current_user=_USER,
            )

    assert exc.value.status_code == 502


@pytest.mark.anyio
async def test_create_payment_intent_unexpected_exception_500():
    from backend.routes.payments import PaymentIntentRequest, create_payment_intent

    mock_req = MagicMock()
    mock_req.headers = {}

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch(
            "backend.routes.payments.stripe.PaymentIntent.create",
            side_effect=RuntimeError("boom"),
        ),
    ):
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)
        mock_db.update_one = AsyncMock()

        with pytest.raises(HTTPException) as exc:
            await create_payment_intent(
                body=PaymentIntentRequest(amount="10.00"),
                request=mock_req,
                current_user=_USER,
            )

    assert exc.value.status_code == 500


# ── add_card: StripeError specifically (distinct from generic Exception) ───────


@pytest.mark.anyio
async def test_add_card_stripe_error_specifically_502():
    """StripeError must be caught by its own handler (502), not fall through to
    the broader `except Exception` branch (500) that an existing test covers —
    the two must not be conflated since the except clauses are ordered."""
    from backend.routes.payments import add_card

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch(
            "backend.routes.payments.stripe.PaymentMethod.attach",
            side_effect=stripe.error.StripeError("provider unavailable"),
        ),
    ):
        mock_db.get_user_by_id = AsyncMock(return_value=_USER)
        mock_db.update_one = AsyncMock()

        with pytest.raises(HTTPException) as exc:
            await add_card(
                _mock_request({"payment_method_id": "pm_bad3"}),
                current_user=_USER,
            )

    assert exc.value.status_code == 502


# ── set_default_card / delete_card: WS-18 ownership 404s ───────────────────────


@pytest.mark.anyio
async def test_set_default_card_not_owned_with_stripe_404():
    """A card_id not present in the caller's Stripe-listed payment methods must
    404 before Stripe.Customer.modify is ever called."""
    from backend.routes.payments import set_default_card

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch("backend.routes.payments.stripe.PaymentMethod.list", return_value=_mock_card_list("pm_001")),
        patch("backend.routes.payments.stripe.Customer.modify") as mock_modify,
    ):
        mock_db.get_user_by_id = AsyncMock(return_value={**_USER, "stripe_customer_id": "cus_existing"})

        with pytest.raises(HTTPException) as exc:
            await set_default_card(card_id="pm_not_mine", current_user=_USER)

    assert exc.value.status_code == 404
    mock_modify.assert_not_called()


@pytest.mark.anyio
async def test_set_default_card_not_owned_no_stripe_404():
    """Without a Stripe secret, a card_id absent from the user's own
    saved_payment_methods/default_payment_method must still 404 rather than
    writing an arbitrary caller-supplied string into the DB."""
    from backend.routes.payments import set_default_card

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings(False)),
        patch("backend.routes.payments.db_supabase") as mock_db,
    ):
        mock_db.get_user_by_id = AsyncMock(
            return_value={**_USER, "default_payment_method": "pm_001", "saved_payment_methods": ["pm_001"]}
        )
        mock_db.update_one = AsyncMock()

        with pytest.raises(HTTPException) as exc:
            await set_default_card(card_id="pm_not_mine", current_user=_USER)

    assert exc.value.status_code == 404
    mock_db.update_one.assert_not_awaited()


@pytest.mark.anyio
async def test_delete_card_not_owned_404():
    """A card_id not in the caller's Stripe-listed methods must 404 before
    PaymentMethod.detach is ever called — prevents cross-account detach via a
    known pm_... ID."""
    from backend.routes.payments import delete_card

    with (
        patch("backend.routes.payments.get_app_settings", new_callable=AsyncMock, return_value=_settings()),
        patch("backend.routes.payments.db_supabase") as mock_db,
        patch(
            "backend.routes.payments.stripe.PaymentMethod.list",
            return_value=_mock_card_list("pm_001", "pm_002"),
        ),
        patch("backend.routes.payments.stripe.PaymentMethod.detach") as mock_detach,
    ):
        mock_db.get_user_by_id = AsyncMock(
            return_value={**_USER, "default_payment_method": "pm_001", "stripe_customer_id": "cus_existing"}
        )

        with pytest.raises(HTTPException) as exc:
            await delete_card(card_id="pm_not_mine", current_user=_USER)

    assert exc.value.status_code == 404
    mock_detach.assert_not_called()
