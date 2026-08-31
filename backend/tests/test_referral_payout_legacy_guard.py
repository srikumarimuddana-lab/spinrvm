"""Legacy-import referee guard on the referral payout path (ACTION_ITEMS.md A34).

A legacy-imported account's referral_applied_at/created_at reflects import
time, not a genuine new signup. Without a guard, an old-app customer could
apply a referral code post-import and collect the "new user" referral bonus
(both sides of the rider $5/$5, or the driver referrer's $10) it was never
eligible for — the same shape as the already-fixed rider_import_service.py
`created_at=now()` gap (PR #4132) that let old-app customers pass as new
signups for promotions (see routes/promotions.py::_is_legacy_imported_rider).

These tests pin `_is_legacy_referral_referee` (pure-function coverage) and
`_process_one` end-to-end: a legacy-imported referee must never open a
referral_payouts claim or trigger a wallet/driver_bonuses credit, while a
genuinely organic referee is unaffected (no regression on the existing
qualification path).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import utils.referral_payout as rp

_RIDER_LEGACY_META = {"rider_csv_import": {"old_rider_id": "abc123"}}
_DRIVER_LEGACY_META_CSV = {"source": rp._DRIVER_LEGACY_SOURCE, "old_driver_id": "d1"}
_DRIVER_LEGACY_META_MONGO = {"source": rp._DRIVER_LEGACY_MONGO_SOURCE, "old_driver_id": "d2"}


# ── _is_legacy_referral_referee — pure-function coverage ────────────────────


class TestIsLegacyReferralReferee:
    def test_rider_legacy_imported_is_blocked(self):
        referee = {"id": "u1", "legacy_import_metadata": _RIDER_LEGACY_META}
        assert rp._is_legacy_referral_referee("rider", referee) is True

    def test_rider_organic_signup_is_not_blocked(self):
        referee = {"id": "u1", "legacy_import_metadata": None}
        assert rp._is_legacy_referral_referee("rider", referee) is False

    def test_rider_missing_metadata_key_is_not_blocked(self):
        referee = {"id": "u1"}
        assert rp._is_legacy_referral_referee("rider", referee) is False

    def test_rider_metadata_present_but_no_rider_csv_import_key_is_not_blocked(self):
        # e.g. a user whose legacy_import_metadata was stamped by a different
        # importer (stripe_mapping_import_service shares the same column).
        referee = {"id": "u1", "legacy_import_metadata": {"some_other_marker": {}}}
        assert rp._is_legacy_referral_referee("rider", referee) is False

    def test_driver_csv_import_source_is_blocked(self):
        assert (
            rp._is_legacy_referral_referee("driver", {"id": "u1"}, {"legacy_import_metadata": _DRIVER_LEGACY_META_CSV})
            is True
        )

    def test_driver_mongo_import_source_is_blocked(self):
        assert (
            rp._is_legacy_referral_referee(
                "driver", {"id": "u1"}, {"legacy_import_metadata": _DRIVER_LEGACY_META_MONGO}
            )
            is True
        )

    def test_driver_organic_signup_is_not_blocked(self):
        assert rp._is_legacy_referral_referee("driver", {"id": "u1"}, {"legacy_import_metadata": None}) is False

    def test_driver_missing_row_is_not_blocked(self):
        assert rp._is_legacy_referral_referee("driver", {"id": "u1"}, None) is False


# ── _process_one end-to-end (ctx=None / exact per-referee path) ─────────────


def _terms(rides: int = 1) -> dict:
    return {"rides": rides, "referrer": Decimal("5.00"), "referee": Decimal("5.00"), "window_days": 0, "terms": None}


def _rider_referee(legacy: bool) -> dict:
    return {
        "id": "referee_u",
        "referred_by": "referrer_u",
        "referral_applied_at": None,
        "referral_code_used": "RIDEABCD1234",
        "legacy_import_metadata": _RIDER_LEGACY_META if legacy else None,
    }


def test_legacy_imported_rider_referee_never_claims_or_credits():
    db = MagicMock()
    db.count_documents = AsyncMock(return_value=5)  # would clearly qualify if not blocked
    db.insert_one = AsyncMock(return_value={"id": "claim1"})
    db.update_one = AsyncMock()
    credit = AsyncMock()

    with (
        patch.object(rp, "db_supabase", db),
        patch.object(rp, "area_id_for_rider", AsyncMock(return_value=None)),
        patch.object(rp, "resolve_referral_terms", AsyncMock(return_value=_terms())),
        patch.object(rp, "_credit", credit),
    ):
        asyncio.run(rp._process_one(_rider_referee(legacy=True), "RIDEABCD1234"))

    db.insert_one.assert_not_awaited()
    credit.assert_not_awaited()


def test_organic_rider_referee_still_qualifies_normally():
    """No-regression check: an organic (non-legacy) referee must still pay out
    exactly as before this guard was added."""
    db = MagicMock()
    db.count_documents = AsyncMock(return_value=1)
    db.insert_one = AsyncMock(return_value={"id": "claim1"})
    db.update_one = AsyncMock()
    credit = AsyncMock()

    with (
        patch.object(rp, "db_supabase", db),
        patch.object(rp, "area_id_for_rider", AsyncMock(return_value=None)),
        patch.object(rp, "resolve_referral_terms", AsyncMock(return_value=_terms())),
        patch.object(rp, "_credit", credit),
    ):
        asyncio.run(rp._process_one(_rider_referee(legacy=False), "RIDEABCD1234"))

    db.insert_one.assert_awaited_once()
    assert credit.await_count == 2


def _driver_referee() -> dict:
    return {
        "id": "referee_u",
        "referred_by": "referrer_driver_id",
        "referral_applied_at": None,
        "referral_code_used": "DRV12345XYZ",
        "legacy_import_metadata": None,
    }


def _driver_db(driver_row: dict, completed_rides: int) -> MagicMock:
    db = MagicMock()

    async def get_rows(table, filters, **kwargs):
        if table == "drivers" and filters.get("user_id") == "referee_u":
            return [driver_row]
        return []

    db.get_rows = AsyncMock(side_effect=get_rows)
    db.get_driver_by_id = AsyncMock(return_value={"id": "referrer_driver_id", "user_id": "referrer_u"})
    db.count_documents = AsyncMock(return_value=completed_rides)
    db.insert_one = AsyncMock(return_value={"id": "claim1"})
    db.update_one = AsyncMock()
    return db


def test_legacy_imported_driver_referee_never_claims_or_credits():
    driver_row = {
        "id": "drv1",
        "user_id": "referee_u",
        "service_area_id": None,
        "legacy_import_metadata": _DRIVER_LEGACY_META_CSV,
    }
    db = _driver_db(driver_row, completed_rides=10)
    credit = AsyncMock()

    with (
        patch.object(rp, "db_supabase", db),
        patch.object(
            rp,
            "resolve_referral_terms",
            AsyncMock(
                return_value={
                    "rides": 10,
                    "referrer": Decimal("10.00"),
                    "referee": Decimal("0.00"),
                    "window_days": 0,
                    "terms": None,
                }
            ),
        ),
        patch.object(rp, "_credit", credit),
    ):
        asyncio.run(rp._process_one(_driver_referee(), "DRV12345XYZ"))

    db.insert_one.assert_not_awaited()
    credit.assert_not_awaited()


def test_organic_driver_referee_still_qualifies_normally():
    driver_row = {"id": "drv1", "user_id": "referee_u", "service_area_id": None, "legacy_import_metadata": None}
    db = _driver_db(driver_row, completed_rides=10)
    credit = AsyncMock()

    with (
        patch.object(rp, "db_supabase", db),
        patch.object(
            rp,
            "resolve_referral_terms",
            AsyncMock(
                return_value={
                    "rides": 10,
                    "referrer": Decimal("10.00"),
                    "referee": Decimal("0.00"),
                    "window_days": 0,
                    "terms": None,
                }
            ),
        ),
        patch.object(rp, "_credit", credit),
    ):
        asyncio.run(rp._process_one(_driver_referee(), "DRV12345XYZ"))

    db.insert_one.assert_awaited_once()
    credit.assert_awaited_once()
    assert credit.await_args.args[0] == "referrer_u"
