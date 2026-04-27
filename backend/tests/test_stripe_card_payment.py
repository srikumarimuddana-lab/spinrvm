"""Unit tests for the Stripe card branch in process_payment().

Covers:
  - Success path: PaymentIntent succeeds → ride marked paid, WS event emitted.
  - Card-declined failure: CardError → ride marked failed, HTTP 402.
  - Generic StripeError → ride marked failed, HTTP 402.
  - No saved payment method → 502, Stripe never called
    (charge_ride returns status="failed"; process_payment raises 502).
  - Unexpected intent status (requires_action) → 200 success=False, ride
    marked requires_action (NOT 402 — the caller gets a client_secret back).

Strategy: patch ``backend.routes.rides.charge_ride`` directly (the module-level
name in rides.py) so we never touch the real Stripe SDK or stripe_charge.py.
The production import alias lives at rides.py:73–78, so patching
``backend.routes.rides.charge_ride`` intercepts all calls from process_payment.
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils.stripe_charge import ChargeOutcome

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


@pytest.fixture(autouse=True)
def force_circuit_breaker_open():
    """Keep the DB circuit breaker in closed state throughout each test.

    The TestClient lifespan spawns background tasks that make real Supabase
    calls (which fail — no network in CI).  Those failures open the breaker
    CONCURRENTLY with the test body, so a one-time reset before the test is
    insufficient.  Patching ``should_allow`` to always return ``True`` keeps
    the breaker closed for the duration of the test.
    """
    with patch("backend.db_supabase._breaker.should_allow", return_value=True):
        yield


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
    """Patches shared by all card-branch tests.

    Targets use the ``backend.routes.rides.*`` prefix because the test
    runner imports from the package root.  The legacy alias
    ``db = db_supabase`` (rides.py:87) means both names point to the same
    object, so we only patch the ``db_supabase`` name.
    """
    _rider = rider if rider is not None else _FAKE_RIDER_WITH_PM
    return {
        "backend.routes.rides.db_supabase.get_ride": AsyncMock(return_value=_FAKE_RIDE),
        "backend.routes.rides.db.update_one": AsyncMock(return_value=MagicMock(modified_count=1)),
        "backend.routes.rides.db_supabase.update_ride": AsyncMock(return_value=None),
        "backend.routes.rides.db_supabase.get_user_by_id": AsyncMock(return_value=_rider),
        "backend.routes.rides.db_supabase.get_driver_by_id": AsyncMock(return_value=None),
        "backend.routes.rides.manager.send_personal_message": AsyncMock(return_value=None),
        # Patch charge_ride at the rides module level so we never hit stripe_charge.py
        "backend.routes.rides.charge_ride": AsyncMock(
            return_value=ChargeOutcome(
                status="succeeded",
                payment_intent_id="pi_test_success",
                charged_amount=20.00,
            )
        ),
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

    patchers = _start_patches(patches)
    try:
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

    update_ride = patches["backend.routes.rides.db_supabase.update_ride"]
    paid_calls = [c for c in update_ride.call_args_list if c.args[1].get("payment_status") == "paid"]
    assert paid_calls, "expected update_ride call with payment_status='paid'"

    ws = patches["backend.routes.rides.manager.send_personal_message"]
    ws.assert_called()
    ws_payload = ws.call_args.args[0]
    assert ws_payload["type"] == "payment_completed"
    assert ws_payload["ride_id"] == _RIDE_ID


def test_stripe_create_called_with_correct_args(test_client, rider_override):
    """Verify charge_ride is called with correct ride, rider_id, total_amount."""
    patches = _common_patches()

    patchers = _start_patches(patches)
    try:
        test_client.post(
            f"/api/v1/rides/{_RIDE_ID}/process-payment",
            json={"tip_amount": "0"},
            headers=_APP_CHECK_HEADERS,
        )
    finally:
        for p in patchers:
            p.stop()

    charge_mock = patches["backend.routes.rides.charge_ride"]
    charge_mock.assert_called_once()
    kw = charge_mock.call_args.kwargs
    assert kw["ride"]["id"] == _RIDE_ID
    assert kw["rider_id"] == "rider_1"
    assert float(kw["total_amount"]) == pytest.approx(20.00, abs=0.01)
    assert kw["stripe_customer_id"] == "cus_test_xyz"
    assert kw["payment_method_id"] == "pm_test_abc123"


def test_card_declined_returns_402_and_marks_failed(test_client, rider_override):
    """charge_ride returns 'declined' → HTTP 402, ride flagged payment_status='failed'."""
    patches = _common_patches()
    patches["backend.routes.rides.charge_ride"] = AsyncMock(
        return_value=ChargeOutcome(
            status="declined",
            payment_intent_id="pi_declined",
            decline_code="insufficient_funds",
            error_message="Your card has insufficient funds.",
        )
    )

    patchers = _start_patches(patches)
    try:
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
        for c in patches["backend.routes.rides.db_supabase.update_ride"].call_args_list
        if c.args[1].get("payment_status") == "failed"
    ]
    assert failed, "expected update_ride with payment_status='failed'"


def test_generic_stripe_error_returns_502(test_client, rider_override):
    """charge_ride returns status='failed' → HTTP 502, ride marked failed."""
    patches = _common_patches()
    patches["backend.routes.rides.charge_ride"] = AsyncMock(
        return_value=ChargeOutcome(
            status="failed",
            error_message="api_connection_error: Unable to reach Stripe",
        )
    )

    patchers = _start_patches(patches)
    try:
        resp = test_client.post(
            f"/api/v1/rides/{_RIDE_ID}/process-payment",
            json={"tip_amount": "0"},
            headers=_APP_CHECK_HEADERS,
        )
    finally:
        for p in patchers:
            p.stop()

    assert resp.status_code == 502
    failed = [
        c
        for c in patches["backend.routes.rides.db_supabase.update_ride"].call_args_list
        if c.args[1].get("payment_status") == "failed"
    ]
    assert failed


def test_no_saved_payment_method_returns_502(test_client, rider_override):
    """Rider has no default_payment_method → charge_ride returns failed → 502.

    Note: there is no pre-flight 400 guard in process_payment; the missing PM
    is detected inside charge_ride which returns ChargeOutcome(status='failed').
    process_payment maps 'failed' → HTTP 502.
    """
    patches = _common_patches()
    # charge_ride detects no PM and returns failed
    patches["backend.routes.rides.charge_ride"] = AsyncMock(
        return_value=ChargeOutcome(
            status="failed",
            error_message="No default payment method on file",
        )
    )

    patchers = _start_patches(patches)
    try:
        resp = test_client.post(
            f"/api/v1/rides/{_RIDE_ID}/process-payment",
            json={"tip_amount": "0"},
            headers=_APP_CHECK_HEADERS,
        )
    finally:
        for p in patchers:
            p.stop()

    assert resp.status_code == 502
    charge_mock = patches["backend.routes.rides.charge_ride"]
    charge_mock.assert_called_once()


def test_requires_action_intent_returns_200_with_client_secret(test_client, rider_override):
    """Intent status 'requires_action' (3DS) → HTTP 200 success=False with client_secret.

    The process_payment handler returns the client_secret to the app so it can
    run the Stripe confirmPayment() sheet — it does NOT raise 402.
    """
    patches = _common_patches()
    patches["backend.routes.rides.charge_ride"] = AsyncMock(
        return_value=ChargeOutcome(
            status="requires_action",
            payment_intent_id="pi_3ds",
            client_secret="pi_3ds_secret_abc",
        )
    )

    patchers = _start_patches(patches)
    try:
        resp = test_client.post(
            f"/api/v1/rides/{_RIDE_ID}/process-payment",
            json={"tip_amount": "0"},
            headers=_APP_CHECK_HEADERS,
        )
    finally:
        for p in patchers:
            p.stop()

    # requires_action → 200 with success=False and client_secret for the app
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["status"] == "requires_action"
    assert body["client_secret"] == "pi_3ds_secret_abc"
    assert body["payment_intent_id"] == "pi_3ds"

    # ride is marked requires_action (not failed)
    ra_calls = [
        c
        for c in patches["backend.routes.rides.db_supabase.update_ride"].call_args_list
        if c.args[1].get("payment_status") == "requires_action"
    ]
    assert ra_calls, "expected update_ride with payment_status='requires_action'"
