"""Unit tests for scripts/build_legacy_tax_id_csv.py — builds the
`phone,sin,gst_bn` CSV backend/routes/admin/tax_id_import.py requires, from
the raw MongoDB export's banks.csv + drivers.csv.

All phone/SIN/GST values here are synthetic test fixtures, not real driver
data (VALID_SIN passes Luhn but is not a real person's number, matching the
convention already used in test_legacy_sin_dob_import_service.py).
"""

from __future__ import annotations

import importlib
import os
import sys

import pytest

pytestmark = pytest.mark.unit

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
mod = importlib.import_module("build_legacy_tax_id_csv")

VALID_SIN = "130692544"  # passes Luhn; not a real person's number
VALID_GST = "123456789RT0001"


def _bank_row(**overrides):
    row = {"_id": "bank-1", "driver_id": "mongo-driver-1", "sin": VALID_SIN, "gst": VALID_GST}
    row.update(overrides)
    return row


def _mongo_driver_row(**overrides):
    row = {"_id": "mongo-driver-1", "phone": "3065551234"}
    row.update(overrides)
    return row


class TestBuildRows:
    def test_happy_path_writes_normalized_phone_and_gst(self):
        out_rows, stats = mod.build_rows([_bank_row(gst=" 123456789rt0001 ")], [_mongo_driver_row()])
        assert out_rows == [{"phone": "+13065551234", "sin": VALID_SIN, "gst_bn": "123456789RT0001"}]
        assert stats["rows_written"] == 1
        assert stats["unmatched_no_phone"] == 0
        assert stats["skipped_no_sin_or_gst"] == 0

    def test_unmatched_driver_id_is_counted_and_excluded(self):
        out_rows, stats = mod.build_rows([_bank_row(driver_id="no-such-mongo-id")], [_mongo_driver_row()])
        assert out_rows == []
        assert stats["unmatched_no_phone"] == 1
        assert stats["banks_rows"] == 1

    def test_row_with_neither_sin_nor_gst_is_excluded(self):
        out_rows, stats = mod.build_rows([_bank_row(sin="", gst="")], [_mongo_driver_row()])
        assert out_rows == []
        assert stats["skipped_no_sin_or_gst"] == 1

    def test_sin_only_row_still_written(self):
        out_rows, stats = mod.build_rows([_bank_row(gst="")], [_mongo_driver_row()])
        assert out_rows == [{"phone": "+13065551234", "sin": VALID_SIN, "gst_bn": ""}]
        assert stats["sin_present"] == 1
        assert stats["gst_present"] == 0

    def test_gst_only_row_still_written(self):
        out_rows, stats = mod.build_rows([_bank_row(sin="")], [_mongo_driver_row()])
        assert out_rows == [{"phone": "+13065551234", "sin": "", "gst_bn": VALID_GST}]
        assert stats["sin_present"] == 0
        assert stats["gst_present"] == 1

    def test_malformed_sin_and_gst_are_still_written_but_counted_as_format_invalid(self):
        """Format checking here is advisory only (a heads-up count) -- the
        admin tool's own validate step is the single source of truth for
        whether a row is actually rejected, so a malformed value must not be
        silently dropped by this script."""
        out_rows, stats = mod.build_rows([_bank_row(sin="not-a-sin", gst="not-a-bn")], [_mongo_driver_row()])
        assert out_rows == [{"phone": "+13065551234", "sin": "not-a-sin", "gst_bn": "NOT-A-BN"}]
        assert stats["sin_fails_format"] == 1
        assert stats["gst_fails_format"] == 1

    def test_stats_never_carry_a_phone_sin_or_gst_value(self):
        """PIPEDA: this script's own summary output must be aggregate counts
        only -- assert no stat value is ever a string (a value that could
        leak a phone/SIN/GST into a log or terminal)."""
        _out_rows, stats = mod.build_rows([_bank_row()], [_mongo_driver_row()])
        assert all(isinstance(v, int) for v in stats.values())
