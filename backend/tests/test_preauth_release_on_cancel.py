"""Regression tests for finding 11 (WS-8): pre-auth hold release on cancel.

The booking-time pre-auth hold (PaymentIntent with capture_method=manual)
was never released when a ride was cancelled — by the rider, the driver,
or the search-timeout auto-cancel. The rider's card showed a phantom
charge for up to 7 days until Stripe's auto-expiry. Worse, the rider
cancellation flow overwrote payment_intent_id with the cancellation-fee
PI, making the original hold's PI unrecoverable.

Fix: cancel_authorization is called on every cancel path when auth_status
is 'authorized' or 'fare_only'. The cancellation fee PI is stored in a
new cancel_fee_payment_intent_id column (migration 251) instead of
clobbering payment_intent_id.
"""

import pathlib

import pytest

pytestmark = pytest.mark.unit

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CANCEL_RIDER = (_ROOT / "routes" / "rides" / "cancellation.py").read_text()
_CANCEL_DRIVER = (_ROOT / "routes" / "drivers" / "ride_cancel.py").read_text()
_MATCHING = (_ROOT / "routes" / "rides" / "matching.py").read_text()
_MIG_251 = (_ROOT / "migrations" / "251_cancel_fee_payment_intent.sql").read_text()
_RIDES_DEPS = (_ROOT / "routes" / "rides" / "_deps.py").read_text()
_DRIVER_DEPS = (_ROOT / "routes" / "drivers" / "_deps.py").read_text()


class TestMigration251:
    def test_adds_cancel_fee_payment_intent_id_column(self):
        assert "cancel_fee_payment_intent_id" in _MIG_251

    def test_column_is_on_rides_table(self):
        assert "ALTER TABLE rides" in _MIG_251

    def test_has_rollback_marker(self):
        assert "-- rollback:" in _MIG_251.lower()


class TestRiderCancelReleasesHold:
    def test_calls_cancel_authorization(self):
        assert "cancel_authorization" in _CANCEL_RIDER

    def test_checks_auth_status_before_release(self):
        assert '"authorized"' in _CANCEL_RIDER
        assert '"fare_only"' in _CANCEL_RIDER

    def test_sets_auth_status_released(self):
        assert '"released"' in _CANCEL_RIDER

    def test_does_not_overwrite_payment_intent_id(self):
        """The cancel fee PI must go to cancel_fee_payment_intent_id,
        not clobber the booking-time payment_intent_id."""
        assert "cancel_fee_payment_intent_id" in _CANCEL_RIDER
        # The old pattern was _base_update["payment_intent_id"] = cancel_fee_...
        # which must no longer appear
        lines = _CANCEL_RIDER.split("\n")
        for line in lines:
            if '_base_update["payment_intent_id"]' in line:
                pytest.fail(
                    "Rider cancel must not overwrite payment_intent_id; use cancel_fee_payment_intent_id column instead"
                )

    def test_cancel_authorization_imported_in_deps(self):
        assert "cancel_authorization" in _RIDES_DEPS


class TestDriverCancelReleasesHold:
    def test_calls_cancel_authorization(self):
        assert "cancel_authorization" in _CANCEL_DRIVER

    def test_checks_auth_status_before_release(self):
        assert '"authorized"' in _CANCEL_DRIVER
        assert '"fare_only"' in _CANCEL_DRIVER

    def test_sets_auth_status_released(self):
        assert '"released"' in _CANCEL_DRIVER

    def test_cancel_authorization_imported_in_deps(self):
        assert "cancel_authorization" in _DRIVER_DEPS


class TestSearchTimeoutReleasesHold:
    def test_calls_cancel_authorization(self):
        # Find the ride_search_timeout function section
        idx = _MATCHING.index("ride_search_timeout")
        section = _MATCHING[idx:]
        assert "cancel_authorization" in section

    def test_checks_auth_status_before_release(self):
        idx = _MATCHING.index("ride_search_timeout")
        section = _MATCHING[idx:]
        assert '"authorized"' in section
        assert '"fare_only"' in section

    def test_sets_auth_status_released_in_update(self):
        idx = _MATCHING.index("ride_search_timeout")
        section = _MATCHING[idx:]
        assert '"released"' in section
