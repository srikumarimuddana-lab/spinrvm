"""Unit tests for the Stripe card branch in process_payment().

Covers:
  - Success path: PaymentIntent succeeds → ride marked paid, WS event emitted.
  - Card-declined failure: CardError → ride marked failed, HTTP 402.
  - Generic StripeError → ride marked failed, HTTP 402.
  - No saved payment method → 400, Stripe never called.
  - Unexpected intent status (requires_action) → ride marked failed, HTTP 402.

Pattern follows test_corporate_wallet_routes.py: patch the real stripe module's
methods rather than replacing the whole module.
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_FAKE_USER = {"id": "rider_1", "phone": "+15550001111", "status": "active"}
_RIDE_ID = "ride_stripe_test"

_FAKE_RIDE = {
    "id": _RIDE_ID,
    "rider_id": "rider_1",
    "total_fare": 20.00,
    "payment_method": "card",
    "payment_status": "pending",
    "status": "completed",
    "tip_amount": 0,
    "corporate_account_id": None,
}

_FAKE_RIDER_WITH_PM = {
    "id": "rider_1",
    "default_payment_method": "pm_test_abc123",
    "stripe_customer_id": "cus_test_xyz",
}

_APP_CHECK_HEADERS = {"X-Firebase-AppCheck": "test-token"}

_mock_app_check = MagicMock()
_mock_app_check.verify_token = MagicMock(return_value=None)


@pytest.fixture
def rider_override():
    """Install a fake current_user and bypass Firebase App Check."""
    from backend.server import app
    from dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    _orig = sys.modules.get("firebase_admin.app_check")
    sys.modules["firebase_admin.app_check"] = _mock_app_check
    yield
    app.dependency_overrides.pop(get_current_user, None)
    if _orig is None:
        sys.modules.pop("firebase_admin.app_check", None)
    else:
        sys.modules["firebase_admin.app_check"] = _orig


def _common_patches(*, rider=None):
    """Patches shared by all card-branch tests."""
    _rider = rider if rider is not None else _FAKE_RIDER_WITH_PM
    _settings = AsyncMock(return_value={"stripe_secret_key": "sk_test_fake"})
    return {
        "routes.rides.db_supabase.get_ride": AsyncMock(return_value=_FAKE_RIDE),
        "routes.rides.db.update_one": AsyncMock(return_value=MagicMock(modified_count=1)),
        "routes.rides.db_supabase.update_ride": AsyncMock(return_value=None),
        "routes.rides.db_supabase.get_user_by_id": AsyncMock(return_value=_rider),
        "routes.rides.db_supabase.get_driver_by_id": AsyncMock(return_value=None),
        "routes.rides.manager.send_personal_message": AsyncMock(return_value=None),
        "routes.rides.get_app_settings": _settings,
        "backend.utils.stripe_charge.get_app_settings": _settings,
        "utils.stripe_charge.get_app_settings": _settings,
    }


def _start_patches(patch_dict):
    patchers = []
    for target, mock_obj in patch_dict.items():
        p = patch(target, mock_obj)
        p.start()
        patchers.append(p)
    return patchers


def test_success_marks_ride_paid_and_emits_ws(test_client, rider_override):
    """Successful Stripe charge: ride.payment_status='paid', WS event sent, correct args."""
    patches = _common_patches()
    intent = MagicMock()
    intent.id = "pi_test_success"
    intent.status = "succeeded"

    patchers = _start_patches(patches)
    try:
        with patch("stripe.PaymentIntent.create", return_value=intent):
            resp = test_client.post(
                f"/api/v1/rides/{_RIDE_ID}/process-payment",
                json={"tip_amount": "0"},
                headers=_APP_CHECK_HEADERS,
            )
    finally:
        for p in patchers:
            p.stop()

    assert resp.status_code == 200
    assert resp.json()["success"] is True

    update_ride = patches["routes.rides.db_supabase.update_ride"]
    paid_calls = [c for c in update_ride.call_args_list if c.args[1].get("payment_status") == "paid"]
    assert paid_calls, "expected update_ride call with payment_status='paid'"

    ws = patches["routes.rides.manager.send_personal_message"]
    ws.assert_called()
    ws_payload = ws.call_args.args[0]
    assert ws_payload["type"] == "payment_completed"
    assert ws_payload["ride_id"] == _RIDE_ID


def test_stripe_create_called_with_correct_args(test_client, rider_override):
    """Verify idempotency key, amount in cents, currency, pm, confirm, off_session."""
    patches = _common_patches()
    intent = MagicMock(id="pi_x", status="succeeded")

    patchers = _start_patches(patches)
    try:
        with patch("stripe.PaymentIntent.create", return_value=intent) as mock_create:
            test_client.post(
                f"/api/v1/rides/{_RIDE_ID}/process-payment",
                json={"tip_amount": "0"},
                headers=_APP_CHECK_HEADERS,
            )
    finally:
        for p in patchers:
            p.stop()

    mock_create.assert_called_once()
    kw = mock_create.call_args.kwargs
    assert kw["idempotency_key"] == f"ride-charge-{_RIDE_ID}"
    assert kw["amount"] == 2000  # $20.00 → 2000 cents
    assert kw["currency"] == "cad"
    assert kw["payment_method"] == "pm_test_abc123"
    assert kw["customer"] == "cus_test_xyz"
    assert kw["confirm"] is True
    assert kw["off_session"] is True


def test_card_declined_returns_402_and_marks_failed(test_client, rider_override):
    """Stripe CardError → HTTP 402, ride flagged payment_status='failed'."""
    import stripe

    patches = _common_patches()
    patchers = _start_patches(patches)
    try:
        with patch(
            "stripe.PaymentIntent.create",
            side_effect=stripe.CardError("Your card was declined.", param=None, code="card_declined"),
        ):
            resp = test_client.post(
                f"/api/v1/rides/{_RIDE_ID}/process-payment",
                json={"tip_amount": "0"},
                headers=_APP_CHECK_HEADERS,
            )
    finally:
        for p in patchers:
            p.stop()

    assert resp.status_code == 402

    failed = [
        c
        for c in patches["routes.rides.db_supabase.update_ride"].call_args_list
        if c.args[1].get("payment_status") == "failed"
    ]
    assert failed, "expected update_ride with payment_status='failed'"


def test_generic_stripe_error_returns_402(test_client, rider_override):
    """Generic StripeError → HTTP 402, ride marked failed."""
    import stripe

    patches = _common_patches()
    patchers = _start_patches(patches)
    try:
        with patch(
            "stripe.PaymentIntent.create",
            side_effect=stripe.APIConnectionError("network error"),
        ):
            resp = test_client.post(
                f"/api/v1/rides/{_RIDE_ID}/process-payment",
                json={"tip_amount": "0"},
                headers=_APP_CHECK_HEADERS,
            )
    finally:
        for p in patchers:
            p.stop()

    assert resp.status_code == 402
    failed = [
        c
        for c in patches["routes.rides.db_supabase.update_ride"].call_args_list
        if c.args[1].get("payment_status") == "failed"
    ]
    assert failed


def test_no_saved_payment_method_returns_400(test_client, rider_override):
    """Rider has no default_payment_method → 400 before any Stripe call."""
    rider_no_pm = {"id": "rider_1", "stripe_customer_id": "cus_test", "default_payment_method": None}
    patches = _common_patches(rider=rider_no_pm)

    patchers = _start_patches(patches)
    try:
        with patch("stripe.PaymentIntent.create") as mock_create:
            resp = test_client.post(
                f"/api/v1/rides/{_RIDE_ID}/process-payment",
                json={"tip_amount": "0"},
                headers=_APP_CHECK_HEADERS,
            )
    finally:
        for p in patchers:
            p.stop()

    assert resp.status_code == 400
    mock_create.assert_not_called()


def test_requires_action_intent_returns_402(test_client, rider_override):
    """Intent status 'requires_action' (3DS) → 402, ride marked failed."""
    patches = _common_patches()
    intent = MagicMock(id="pi_3ds", status="requires_action")

    patchers = _start_patches(patches)
    try:
        with patch("stripe.PaymentIntent.create", return_value=intent):
            resp = test_client.post(
                f"/api/v1/rides/{_RIDE_ID}/process-payment",
                json={"tip_amount": "0"},
                headers=_APP_CHECK_HEADERS,
            )
    finally:
        for p in patchers:
            p.stop()

    assert resp.status_code == 402
    failed = [
        c
        for c in patches["routes.rides.db_supabase.update_ride"].call_args_list
        if c.args[1].get("payment_status") == "failed"
    ]
    assert failed
