"""Minimum tip ($1.00 default, settings.min_tip_amount, migration 438).

A rider may give no tip or at least the minimum; anything in between is
rejected with "Minimum tip is $X.XX" so the rider fixes it on the tip screen.
Background: a $0.05 tip on a card ride became a separate Stripe charge under
Stripe's $0.50 minimum and silently failed (never charged, never paid out).
"""

import asyncio
import sys
from contextlib import ExitStack, contextmanager
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

RIDER = {"id": "rider_1", "role": "rider", "phone": "+13065550000"}


@contextmanager
def _min_tip(value):
    """Patch the settings the policy reads, under whichever module path loaded it
    (dual-import pattern: backend.utils.tip_policy vs utils.tip_policy).
    ExitStack unwinds LIFO, so a target reachable via both paths is restored."""
    import utils.tip_policy  # noqa: F401  (ensure at least one path is loaded)

    settings = AsyncMock(return_value={"min_tip_amount": value})
    with ExitStack() as stack:
        for name in ("utils.tip_policy", "backend.utils.tip_policy"):
            if name in sys.modules:
                stack.enter_context(patch(f"{name}.get_app_settings", settings))
        yield settings


def _enforce(tip):
    from utils.tip_policy import enforce_min_tip

    asyncio.run(enforce_min_tip(tip))


class TestPolicy:
    @pytest.mark.parametrize("tip", [0, "0", Decimal("0.00"), None, 1, "1.00", Decimal("1.00"), 25])
    def test_no_tip_or_at_least_the_minimum_is_allowed(self, tip):
        with _min_tip("1.00"):
            _enforce(tip)  # no exception

    @pytest.mark.parametrize("tip", [Decimal("0.05"), "0.50", 0.99, Decimal("0.01")])
    def test_between_zero_and_the_minimum_is_rejected_with_the_message(self, tip):
        with _min_tip("1.00"), pytest.raises(HTTPException) as exc:
            _enforce(tip)
        assert exc.value.status_code == 400
        assert exc.value.detail == "Minimum tip is $1.00"

    def test_minimum_is_configurable(self):
        with _min_tip("2.00"), pytest.raises(HTTPException) as exc:
            _enforce(Decimal("1.50"))
        assert exc.value.detail == "Minimum tip is $2.00"

    def test_tip_is_judged_cent_rounded_like_the_charge(self):
        with _min_tip("1.00"):
            _enforce(Decimal("0.995"))  # rounds to $1.00 — allowed, same as POST /tip
        with _min_tip("1.00"), pytest.raises(HTTPException):
            _enforce(Decimal("0.994"))

    def test_zero_minimum_switches_the_rule_off(self):
        with _min_tip("0"):
            _enforce(Decimal("0.05"))  # no exception

    def test_no_tip_never_reads_settings(self):
        with _min_tip("1.00") as settings:
            _enforce(0)
        settings.assert_not_awaited()


@pytest.fixture
def rider_client():
    from fastapi.testclient import TestClient

    import dependencies
    from backend.server import app

    app.dependency_overrides[dependencies.get_current_user] = lambda: RIDER
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


class TestEndpointsRejectBeforeAnySideEffect:
    """Every tip entry point rejects a sub-minimum tip before it reads the ride,
    writes a rating, or charges anything."""

    @pytest.mark.parametrize(
        "path,body",
        [
            ("/api/v1/rides/ride_1/tip", {"amount": 0.05}),
            ("/api/v1/rides/ride_1/process-payment", {"tip_amount": 0.5}),
            ("/api/v1/rides/ride_1/rate", {"rating": 5, "tip_amount": 0.99}),
        ],
    )
    def test_sub_minimum_tip_is_400_with_message(self, rider_client, path, body):
        get_ride = AsyncMock(return_value={"id": "ride_1", "rider_id": RIDER["id"], "status": "completed"})
        with _min_tip("1.00"), patch("backend.routes.rides._deps.db_supabase.get_ride", get_ride):
            resp = rider_client.post(path, json=body)

        assert resp.status_code == 400, resp.text
        assert "Minimum tip is $1.00" in resp.text
        if path.endswith("/tip"):
            get_ride.assert_not_awaited()

    def test_process_payment_rejects_before_claiming_or_charging(self, rider_client):
        ride = {"id": "ride_1", "rider_id": RIDER["id"], "status": "completed", "payment_status": "pending"}
        writes = AsyncMock()
        with (
            _min_tip("1.00"),
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides._deps.db_supabase.update_ride", writes),
            patch("backend.routes.rides._deps.db_supabase.update_one", writes),
        ):
            resp = rider_client.post("/api/v1/rides/ride_1/process-payment", json={"tip_amount": 0.5})

        assert resp.status_code == 400, resp.text
        assert "Minimum tip is $1.00" in resp.text
        writes.assert_not_awaited()  # no atomic payment claim, no charge

    def test_process_payment_replay_on_paid_ride_still_reports_already_paid(self, rider_client):
        ride = {"id": "ride_1", "rider_id": RIDER["id"], "status": "completed", "payment_status": "paid"}
        with _min_tip("1.00"), patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)):
            resp = rider_client.post("/api/v1/rides/ride_1/process-payment", json={"tip_amount": 0.5})

        assert resp.status_code == 200, resp.text
        assert resp.json()["already_paid"] is True

    def test_rating_with_sub_minimum_tip_writes_nothing(self, rider_client):
        # rate reads the ride first (auth/state checks), but must not write the
        # rating or the tip when the tip is under the minimum.
        ride = {"id": "ride_1", "rider_id": RIDER["id"], "status": "completed", "payment_status": "pending"}
        update = AsyncMock()
        with (
            _min_tip("1.00"),
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride)),
            patch("backend.routes.rides._deps.db_supabase.update_ride", update),
            patch("backend.routes.rides._deps.db_supabase.update_one", update),
        ):
            resp = rider_client.post("/api/v1/rides/ride_1/rate", json={"rating": 5, "tip_amount": 0.5})

        assert resp.status_code == 400, resp.text
        update.assert_not_awaited()


class TestSettingsPlumbing:
    """The rider app learns the minimum from GET /api/v1/settings; admins set it
    through the admin settings update (0 = rule off)."""

    @pytest.mark.parametrize(
        "stored,expected",
        [(None, "0.00"), (Decimal("1.00"), "1.00"), (2.5, "2.50"), ("0", "0.00")],
    )
    def test_public_settings_expose_the_minimum_as_money_string(self, test_client, stored, expected):
        row = {} if stored is None else {"min_tip_amount": stored}
        settings = AsyncMock(return_value=row)
        with ExitStack() as stack:
            for name in ("routes.settings", "backend.routes.settings"):
                if name in sys.modules:
                    stack.enter_context(patch(f"{name}.get_app_settings", settings))
            resp = test_client.get("/api/v1/settings")

        assert resp.status_code == 200, resp.text
        assert resp.json()["min_tip_amount"] == expected

    def test_admin_update_accepts_zero_to_fifty(self):
        from routes.admin.settings import SettingsUpdateRequest

        for value in ("0", "1.00", "50"):
            model = SettingsUpdateRequest(min_tip_amount=Decimal(value))
            assert model.model_dump(exclude_none=True)["min_tip_amount"] == Decimal(value)

    @pytest.mark.parametrize("value", ["-0.01", "50.01", "1.005"])
    def test_admin_update_rejects_out_of_range_or_sub_cent(self, value):
        from pydantic import ValidationError

        from routes.admin.settings import SettingsUpdateRequest

        with pytest.raises(ValidationError):
            SettingsUpdateRequest(min_tip_amount=Decimal(value))


@contextmanager
def _payments_module(settings, get_ride):
    """Patch routes/payments.py (Stripe PaymentSheet / create-intent) under both
    import paths. Both paths share ONE db_supabase module, so get_ride is patched
    twice — ExitStack's LIFO unwind is what restores the real function (FIFO
    stops left a mock behind and broke later tests)."""
    with ExitStack() as stack:
        for name in ("routes.payments", "backend.routes.payments"):
            if name in sys.modules:
                stack.enter_context(patch(f"{name}.get_app_settings", AsyncMock(return_value=settings)))
                stack.enter_context(patch(f"{name}.db_supabase.get_ride", get_ride))
        yield


class TestGooglePayAndIntentPaths:
    """The PaymentSheet (Google Pay) and create-intent paths charge grand_total +
    tip themselves. A sub-minimum tip charged there would then be rejected by
    /rate — money collected, tip never recorded for the driver — so they must
    reject it before the ride is read or any Stripe object is created."""

    @pytest.mark.parametrize("path", ["/api/v1/payments/payment-sheet", "/api/v1/payments/create-intent"])
    def test_sub_minimum_tip_rejected_before_any_stripe_call(self, rider_client, path):
        get_ride = AsyncMock(return_value={"id": "ride_1", "rider_id": RIDER["id"], "grand_total": "3.10"})
        with (
            _min_tip("1.00"),
            _payments_module({"stripe_secret_key": "sk_test_x", "stripe_publishable_key": "pk_test_x"}, get_ride),
            patch("stripe.PaymentIntent.create") as intent_create,
            patch("stripe.EphemeralKey.create") as key_create,
        ):
            resp = rider_client.post(
                path,
                json={"amount": 3.60, "ride_id": "ride_1", "tip_amount": 0.5},
                headers={"Idempotency-Key": f"test-{path}"},
            )

        assert resp.status_code == 400, resp.text
        assert "Minimum tip is $1.00" in resp.text
        get_ride.assert_not_awaited()
        intent_create.assert_not_called()
        key_create.assert_not_called()


class TestAdminMinimumIsOffOrChargeable:
    @pytest.mark.parametrize("value", ["0", "0.50", "1.00"])
    def test_zero_or_at_least_fifty_cents_accepted(self, value):
        from routes.admin.settings import SettingsUpdateRequest

        assert SettingsUpdateRequest(min_tip_amount=Decimal(value)).min_tip_amount == Decimal(value)

    @pytest.mark.parametrize("value", ["0.01", "0.25", "0.49"])
    def test_between_zero_and_fifty_cents_rejected(self, value):
        # Would let through tips Stripe can't charge on their own.
        from pydantic import ValidationError

        from routes.admin.settings import SettingsUpdateRequest

        with pytest.raises(ValidationError):
            SettingsUpdateRequest(min_tip_amount=Decimal(value))
