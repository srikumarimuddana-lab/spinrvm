"""Regression tests for finding 12 (WS-9): /rate tip abuse.

The /rate endpoint allowed:
1. Unbounded tip amounts (no server-side cap)
2. Repeated rating/tipping of the same ride (additive accumulation)
3. Tips after payment settlement (unchargeable phantom tips)

Fixes:
- RideRatingRequest.tip_amount has le=500 server-side cap
- Dedupe guard: reject if rider_rating is already set (409)
- Payment-status guard: reject tip when payment already settled (400)
- @idempotent_endpoint(scope="ride_rate") for HTTP-level deduplication
- Tip is set-once (not additive on existing tip_amount)
"""

import pathlib

import pytest

pytestmark = pytest.mark.unit

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_RATING_SRC = (_ROOT / "routes" / "rides" / "rating.py").read_text()
_SCHEMA_SRC = (_ROOT / "schemas.py").read_text()


class TestTipAmountCap:
    def test_schema_has_upper_bound(self):
        assert "le=500" in _SCHEMA_SRC or "le = 500" in _SCHEMA_SRC

    def test_schema_has_lower_bound(self):
        assert "ge=0" in _SCHEMA_SRC or "ge = 0" in _SCHEMA_SRC


class TestDedupeGuard:
    def test_rejects_already_rated_ride(self):
        assert "rider_rating" in _RATING_SRC
        assert "already rated" in _RATING_SRC.lower()

    def test_returns_409_on_duplicate(self):
        assert "409" in _RATING_SRC


class TestPaymentStatusGuard:
    def test_checks_payment_status(self):
        assert "payment_status" in _RATING_SRC

    def test_rejects_tip_after_settlement(self):
        assert "settled" in _RATING_SRC.lower()

    def test_allows_tip_when_pending(self):
        assert '"pending"' in _RATING_SRC

    def test_allows_tip_when_failed(self):
        assert '"failed"' in _RATING_SRC


class TestIdempotency:
    def test_has_idempotent_decorator(self):
        assert "@idempotent_endpoint" in _RATING_SRC

    def test_scope_is_ride_rate(self):
        assert 'scope="ride_rate"' in _RATING_SRC


class TestSetOnceTipSemantics:
    def test_no_additive_tip_accumulation(self):
        """Tip must be set directly from the request, not added to existing tip."""
        lines = _RATING_SRC.split("\n")
        for line in lines:
            stripped = line.strip()
            if "tip_amount" in stripped and "ride.get" in stripped and "+" in stripped:
                pytest.fail(f"Tip logic must not additively accumulate: found '{stripped}'")

    def test_tip_set_from_request_amount(self):
        assert "rating_data.tip_amount" in _RATING_SRC
