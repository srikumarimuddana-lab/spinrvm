"""CR-4108 (issue #4108, D1 decision): unit tests for the tax_basis /
legacy_tax_note helpers in utils/legacy_rides.py.

Product-owner decision, option (a) "re-label, don't rewrite": for the 186
legacy-imported rides, ``rides.tax_amount`` / ``tax_breakdown`` hold real
money, but the wrong base (commission-GST, not fare-GST) — see
services/booking_import_service.py's comment on the mismatch. These
functions compute a display-only label distinguishing the two meanings;
they must never read or write ``tax_amount`` itself.
"""

from __future__ import annotations

import pytest

from backend.utils.legacy_rides import (
    LEGACY_TAX_NOTE,
    TAX_BASIS_COMMISSION_GST_LEGACY_IMPORT,
    TAX_BASIS_FARE_GST,
    is_legacy_ride,
    legacy_tax_note_for_ride,
    tax_basis_for_ride,
)

pytestmark = pytest.mark.unit

_LEGACY_METADATA = {
    "source": "legacy_mongo_booking_import",
    "old_booking_id": "6a023ea1173f9129709e2a64",
}


class TestTaxBasisForRide:
    def test_normal_ride_is_fare_gst(self):
        ride = {"id": "r1", "tax_amount": 1.65, "legacy_import_metadata": {}}
        assert tax_basis_for_ride(ride) == TAX_BASIS_FARE_GST == "fare_gst"

    def test_ride_missing_the_key_entirely_is_fare_gst(self):
        """A ride row that never had legacy_import_metadata selected/set at
        all (e.g. a narrow `columns=` fetch) must not accidentally read as
        legacy — is_legacy_ride()/.get() treats it the same as {}."""
        ride = {"id": "r1", "tax_amount": 1.65}
        assert tax_basis_for_ride(ride) == "fare_gst"

    def test_legacy_imported_ride_is_commission_gst(self):
        ride = {"id": "legacy-1", "tax_amount": 0.73, "legacy_import_metadata": _LEGACY_METADATA}
        assert tax_basis_for_ride(ride) == TAX_BASIS_COMMISSION_GST_LEGACY_IMPORT == "commission_gst_legacy_import"

    def test_matches_is_legacy_ride_exactly(self):
        """tax_basis_for_ride must never diverge from the canonical
        is_legacy_ride() check that every other legacy-ride surface uses."""
        legacy = {"id": "r1", "legacy_import_metadata": _LEGACY_METADATA}
        normal = {"id": "r2", "legacy_import_metadata": {}}
        assert is_legacy_ride(legacy) is True
        assert tax_basis_for_ride(legacy) == TAX_BASIS_COMMISSION_GST_LEGACY_IMPORT
        assert is_legacy_ride(normal) is False
        assert tax_basis_for_ride(normal) == TAX_BASIS_FARE_GST

    def test_never_touches_tax_amount(self):
        """The whole point of option (a): the numeric value must be
        byte-for-byte identical before and after computing the label."""
        ride = {"id": "legacy-1", "tax_amount": 0.73, "legacy_import_metadata": _LEGACY_METADATA}
        before = ride["tax_amount"]
        tax_basis_for_ride(ride)
        legacy_tax_note_for_ride(ride)
        assert ride["tax_amount"] == before == 0.73


class TestLegacyTaxNoteForRide:
    def test_normal_ride_has_no_note(self):
        ride = {"id": "r1", "legacy_import_metadata": {}}
        assert legacy_tax_note_for_ride(ride) is None

    def test_legacy_ride_has_the_note(self):
        ride = {"id": "legacy-1", "legacy_import_metadata": _LEGACY_METADATA}
        note = legacy_tax_note_for_ride(ride)
        assert note == LEGACY_TAX_NOTE
        assert "commission-GST" in note
        assert "previous app" in note
