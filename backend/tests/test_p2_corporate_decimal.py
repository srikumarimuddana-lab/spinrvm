"""
Regression tests for B-P2-1: float() replaced with Decimal in corporate paths.

Covers:
  - _compute_remaining returns Decimal and preserves sub-cent precision.
  - Policy allowance check uses Decimal arithmetic (no float rounding loss).
  - evaluate_policy max_fare comparison uses Decimal.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from unittest.mock import MagicMock

# Stub heavy deps before any backend import.
_STUBS = [
    "supabase",
    "stripe",
    "gotrue",
    "postgrest",
    "realtime",
    "firebase_admin",
    "firebase_admin.auth",
    "firebase_admin.credentials",
    "firebase_admin.messaging",
    "twilio",
    "twilio.rest",
    "slowapi",
    "slowapi.errors",
    "slowapi.util",
    "redis",
    "redis.asyncio",
    "jwt",
]
for _m in _STUBS:
    if _m not in sys.modules:
        sys.modules[_m] = MagicMock()


class TestComputeRemaining:
    """_compute_remaining must use Decimal arithmetic, not float."""

    def _fn(self):
        if "backend.routes.corporate_rider" in sys.modules:
            del sys.modules["backend.routes.corporate_rider"]
        from backend.routes.corporate_rider import _compute_remaining

        return _compute_remaining

    def test_returns_decimal(self):
        fn = self._fn()
        result = fn({"type": "fixed", "amount": "100.00", "used": "10.00"})
        assert isinstance(result, Decimal), f"expected Decimal, got {type(result)}"

    def test_precision_preserved(self):
        """1234.56 - 0.01 must equal 1234.55 exactly, not 1234.5499...."""
        fn = self._fn()
        result = fn({"type": "fixed", "amount": "1234.56", "used": "0.01"})
        assert result == Decimal("1234.55"), f"got {result!r}"

    def test_unlimited_returns_none(self):
        fn = self._fn()
        assert fn({"type": "unlimited"}) is None

    def test_missing_amount_returns_none(self):
        fn = self._fn()
        assert fn({"type": "fixed"}) is None

    def test_negative_used_treated_as_zero(self):
        """Negative used (grants posted) should not inflate remaining above amount."""
        fn = self._fn()
        result = fn({"type": "fixed", "amount": "100", "used": "-5"})
        # max(used=-5, 0) = 0 → remaining = 100 - 0 = 100
        assert result == Decimal("100")


class TestEvaluatePolicyDecimal:
    """evaluate_policy max_fare and allowance checks must use Decimal comparisons."""

    def _mod(self):
        if "backend.services.corporate_policy_service" in sys.modules:
            del sys.modules["backend.services.corporate_policy_service"]
        from backend.services import corporate_policy_service

        return corporate_policy_service

    def test_max_fare_exact_comparison(self):
        """Fare exactly at the cap must PASS (not fail due to float rounding)."""
        mod = self._mod()
        policy = {"max_fare_per_ride": "50.00"}
        ctx = {"estimated_fare": Decimal("50.00"), "allowance": {}}
        result = mod.evaluate_policy(policy, ctx)
        assert result["pass"] is True, f"expected pass, got {result}"

    def test_max_fare_over_cap_fails(self):
        mod = self._mod()
        policy = {"max_fare_per_ride": "50.00"}
        ctx = {"estimated_fare": Decimal("50.01"), "allowance": {}}
        result = mod.evaluate_policy(policy, ctx)
        assert "max_fare_per_ride" in result["failed_rules"]

    def test_allowance_remaining_decimal(self):
        """Allowance check: 0.01 remaining should count as > 0 (rider not blocked)."""
        mod = self._mod()
        policy = {"allowed_payment_source": "allowance_only"}
        ctx = {
            "allowance": {"type": "fixed", "amount": "10.01", "used": "10.00"},
        }
        result = mod.evaluate_policy(policy, ctx)
        assert result["pass"] is True, f"0.01 remaining should allow ride: {result}"


class TestStripeWebhookAllowlist:
    """B-P2-2: Stripe webhook must reject unknown event types with 400."""

    def test_known_events_in_allowlist(self):
        if "backend.routes.webhooks" in sys.modules:
            del sys.modules["backend.routes.webhooks"]
        from backend.routes.webhooks import ALLOWED_STRIPE_EVENTS

        for event in (
            "payment_intent.succeeded",
            "payment_intent.payment_failed",
            "checkout.session.completed",
        ):
            assert event in ALLOWED_STRIPE_EVENTS, f"{event} missing from allowlist"

    def test_unknown_events_not_in_allowlist(self):
        from backend.routes.webhooks import ALLOWED_STRIPE_EVENTS

        # `charge.refunded`, `account.updated` (Stripe Connect account status
        # tracking), and `payout.paid`/`payout.failed` have all since joined
        # the allowlist; test now verifies the genuinely-unknown set only.
        for unknown in ("invoice.voided", "product.created", ""):
            assert unknown not in ALLOWED_STRIPE_EVENTS, f"{unknown} should not be allowed"
