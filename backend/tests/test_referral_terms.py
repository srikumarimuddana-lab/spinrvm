"""Unit tests for per-service-area referral terms resolution.

Covers the fallback contract (no area / missing row / NULL column → global
default), area overrides winning, Decimal money coercion, the driver referee
reward (off by default, admin-overridable per area like every other side),
and the area-derivation helpers.
"""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from backend.utils import referral_terms

# Fixed stand-in for the global constants so assertions don't drift if the real
# RIDER_*/REFERRAL_* constants are later retuned.
_GLOBAL = {
    "rider": {
        "rides": 1,
        "referrer": Decimal("5.00"),
        "referee": Decimal("5.00"),
        "window_days": 30,
        "terms": None,
    },
    "driver": {
        "rides": 10,
        "referrer": Decimal("10.00"),
        "referee": Decimal("0.00"),
        "window_days": 30,
        "terms": None,
    },
}


def _patch_global():
    return patch.object(referral_terms, "_global_terms", lambda: {k: dict(v) for k, v in _GLOBAL.items()})


def _patch_rows(value=None, side_effect=None):
    mock = AsyncMock(return_value=value) if side_effect is None else AsyncMock(side_effect=side_effect)
    return patch.object(referral_terms.db_supabase, "get_rows", mock)


class TestResolveReferralTerms:
    def test_none_area_returns_global_rider(self):
        with _patch_global():
            t = asyncio.run(referral_terms.resolve_referral_terms(None, "rider"))
        assert t == _GLOBAL["rider"]

    def test_none_area_returns_global_driver(self):
        with _patch_global():
            t = asyncio.run(referral_terms.resolve_referral_terms(None, "driver"))
        assert t == _GLOBAL["driver"]

    def test_area_overrides_win_and_coerce_to_decimal(self):
        area = {
            "id": "area1",
            "rider_referrer_reward": 7,  # int from DB
            "rider_referee_reward": "8.50",  # numeric-as-string from DB
            "rider_referral_rides_required": 3,
        }
        with _patch_global(), _patch_rows([area]):
            t = asyncio.run(referral_terms.resolve_referral_terms("area1", "rider"))
        assert t["referrer"] == Decimal("7.00")
        assert t["referee"] == Decimal("8.50")
        assert t["rides"] == 3

    def test_null_columns_fall_back_per_field(self):
        area = {
            "id": "area1",
            "rider_referrer_reward": None,
            "rider_referee_reward": None,
            "rider_referral_rides_required": None,
        }
        with _patch_global(), _patch_rows([area]):
            t = asyncio.run(referral_terms.resolve_referral_terms("area1", "rider"))
        assert t == _GLOBAL["rider"]

    def test_missing_area_row_returns_global(self):
        with _patch_global(), _patch_rows([]):
            t = asyncio.run(referral_terms.resolve_referral_terms("ghost", "driver"))
        assert t == _GLOBAL["driver"]

    def test_area_terms_override_returned(self):
        # A non-blank per-area T&C string is surfaced so the route shows it
        # verbatim instead of the auto-generated sentence.
        area = {"id": "a", "rider_referral_terms": "  Custom rider terms.  "}
        with _patch_global(), _patch_rows([area]):
            t = asyncio.run(referral_terms.resolve_referral_terms("a", "rider"))
        assert t["terms"] == "Custom rider terms."  # trimmed

    def test_blank_area_terms_fall_back_to_none(self):
        # Whitespace-only / empty override must not suppress the dynamic default.
        area = {"id": "a", "driver_referral_terms": "   "}
        with _patch_global(), _patch_rows([area]):
            t = asyncio.run(referral_terms.resolve_referral_terms("a", "driver"))
        assert t["terms"] is None

    def test_window_days_override_wins(self):
        # A per-area deadline overrides the global default.
        area = {"id": "a", "driver_referral_window_days": 14}
        with _patch_global(), _patch_rows([area]):
            t = asyncio.run(referral_terms.resolve_referral_terms("a", "driver"))
        assert t["window_days"] == 14

    def test_window_days_zero_is_a_valid_override_not_fallback(self):
        # 0 means "no deadline for this area" and must NOT fall back to 30.
        area = {"id": "a", "rider_referral_window_days": 0}
        with _patch_global(), _patch_rows([area]):
            t = asyncio.run(referral_terms.resolve_referral_terms("a", "rider"))
        assert t["window_days"] == 0

    def test_window_days_null_falls_back_to_global(self):
        area = {"id": "a", "driver_referral_window_days": None}
        with _patch_global(), _patch_rows([area]):
            t = asyncio.run(referral_terms.resolve_referral_terms("a", "driver"))
        assert t["window_days"] == 30

    def test_driver_referee_defaults_to_zero_when_area_unset(self):
        # NULL driver_referee_reward column → falls back to the global default
        # (0), same as every other field — "no area override yet" still means
        # "off" for a referee reward that an admin hasn't opted into.
        area = {"id": "a", "driver_referral_reward": 25, "driver_referral_rides_required": 4}
        with _patch_global(), _patch_rows([area]):
            t = asyncio.run(referral_terms.resolve_referral_terms("a", "driver"))
        assert t["referrer"] == Decimal("25.00")
        assert t["rides"] == 4
        assert t["referee"] == Decimal("0.00")

    def test_driver_referee_area_override_wins(self):
        # An admin who opts in by setting driver_referee_reward on the area
        # gets that amount, sharing the SAME ride threshold as the referrer
        # (no separate referee-side rides_required column).
        area = {"id": "a", "driver_referral_reward": 10, "driver_referee_reward": 7.5}
        with _patch_global(), _patch_rows([area]):
            t = asyncio.run(referral_terms.resolve_referral_terms("a", "driver"))
        assert t["referrer"] == Decimal("10.00")
        assert t["referee"] == Decimal("7.50")

    def test_lookup_failure_propagates(self):
        # A real DB error must NOT be swallowed into the global default — it
        # propagates so the payout loop retries instead of paying the wrong
        # amount and locking it in via the unique claim.
        import pytest

        with _patch_global(), _patch_rows(side_effect=RuntimeError("db down")):
            with pytest.raises(RuntimeError):
                asyncio.run(referral_terms.resolve_referral_terms("a", "rider"))


class TestAreaIdForRider:
    def test_returns_most_recent_area_skipping_nulls(self):
        # Newest first; the newest area-resolved ride wins.
        rows = [
            {"service_area_id": None, "created_at": "2026-01-03"},
            {"service_area_id": "area9", "created_at": "2026-01-02"},
        ]
        with _patch_rows(rows):
            area = asyncio.run(referral_terms.area_id_for_rider("rider1"))
        assert area == "area9"

    def test_none_when_no_area_rides(self):
        with _patch_rows([]):
            area = asyncio.run(referral_terms.area_id_for_rider("rider1"))
        assert area is None

    def test_lookup_failure_propagates(self):
        import pytest

        with _patch_rows(side_effect=RuntimeError("db down")):
            with pytest.raises(RuntimeError):
                asyncio.run(referral_terms.area_id_for_rider("rider1"))


class TestAreaIdForDriverUser:
    def test_returns_driver_area(self):
        with _patch_rows([{"service_area_id": "areaX"}]):
            area = asyncio.run(referral_terms.area_id_for_driver_user("u1"))
        assert area == "areaX"

    def test_none_when_no_driver_or_area(self):
        with _patch_rows([]):
            area = asyncio.run(referral_terms.area_id_for_driver_user("u1"))
        assert area is None


class TestPaidReferralEarnings:
    def test_none_when_no_paid_rows(self):
        # No paid payout yet → None so the caller falls back to the estimate.
        with _patch_rows([]):
            out = asyncio.run(referral_terms.paid_referral_earnings("u1", "rider"))
        assert out is None

    def test_sums_snapshotted_rewards_as_decimal(self):
        rows = [
            {"referrer_reward": 5},  # int from DB
            {"referrer_reward": "10.50"},  # numeric-as-string from DB
            {"referrer_reward": None},  # defensive: NULL coerces to 0
        ]
        with _patch_rows(rows):
            out = asyncio.run(referral_terms.paid_referral_earnings("u1", "driver"))
        assert out == Decimal("15.50")

    def test_lookup_failure_propagates(self):
        import pytest

        with _patch_rows(side_effect=RuntimeError("db down")):
            with pytest.raises(RuntimeError):
                asyncio.run(referral_terms.paid_referral_earnings("u1", "rider"))


class TestPaidRefereeEarnings:
    def test_none_when_no_paid_rows(self):
        # Not referred / not yet paid → None (caller shows 0).
        with _patch_rows([]):
            out = asyncio.run(referral_terms.paid_referee_earnings("u1", "rider"))
        assert out is None

    def test_sums_referee_rewards_as_decimal(self):
        rows = [
            {"referee_reward": 5},  # int from DB
            {"referee_reward": "2.50"},  # numeric-as-string from DB
            {"referee_reward": None},  # defensive: NULL coerces to 0
        ]
        with _patch_rows(rows):
            out = asyncio.run(referral_terms.paid_referee_earnings("u1", "rider"))
        assert out == Decimal("7.50")
