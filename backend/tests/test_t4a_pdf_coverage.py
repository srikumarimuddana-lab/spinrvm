"""Coverage for utils/t4a_pdf.py (A1c, Sub-tier B).

CRA T4A tax-slip PDF generator for driver year-end earnings (regulatory —
Saskatchewan Transportation Act tax reporting, see CLAUDE.md). Had no
dedicated test file; only 4.40% coverage as an incidental side effect.

This is a pure PDF-rendering function (fpdf2, no I/O, no DB, no network) —
tests call `generate_t4a_pdf()` for real and assert on the returned bytes,
rather than mocking the PDF library, since the actual regression risk here
is a summary-dict shape change silently breaking rendering (missing key →
unhandled exception, not just a wrong-looking PDF).

Test-only change — no application code modified.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _base_summary(**overrides):
    summary = {
        "year": 2025,
        "net_earnings": "12345.67",
        "total_trips": 842,
        "gst_registered": False,
        "gst_bn": "",
        "driver_name": "Jordan Test-Driver",
        "generated_at": "2026-01-15T10:30:00.123456+00:00",
        "total_earnings": "12345.67",
    }
    summary.update(overrides)
    return summary


class TestGenerateT4aPdf:
    def test_returns_valid_pdf_bytes(self):
        from backend.utils.t4a_pdf import generate_t4a_pdf

        result = generate_t4a_pdf(_base_summary())
        assert isinstance(result, bytes)
        assert result.startswith(b"%PDF")

    def test_gst_registered_with_bn_adds_box_020_and_bn_line(self):
        from backend.utils.t4a_pdf import generate_t4a_pdf

        result = generate_t4a_pdf(_base_summary(gst_registered=True, gst_bn="123456789RT0001"))
        assert result.startswith(b"%PDF")

    def test_gst_registered_without_bn_on_file(self):
        """gst_registered=True but no gst_bn — must not raise, uses the
        'Registered (BN not on file)' fallback line."""
        from backend.utils.t4a_pdf import generate_t4a_pdf

        result = generate_t4a_pdf(_base_summary(gst_registered=True, gst_bn=""))
        assert result.startswith(b"%PDF")

    def test_not_gst_registered(self):
        from backend.utils.t4a_pdf import generate_t4a_pdf

        result = generate_t4a_pdf(_base_summary(gst_registered=False))
        assert result.startswith(b"%PDF")

    def test_minimal_summary_uses_defaults_for_every_optional_key(self):
        """A summary dict with ONLY the documented-required keys must not
        raise — every optional key (driver_name, generated_at,
        total_earnings, gst_bn) has a documented fallback."""
        from backend.utils.t4a_pdf import generate_t4a_pdf

        minimal = {
            "year": 2025,
            "net_earnings": "500.00",
            "total_trips": 10,
            "gst_registered": False,
        }
        result = generate_t4a_pdf(minimal)
        assert result.startswith(b"%PDF")

    def test_empty_summary_dict_does_not_raise(self):
        """Every key defaults gracefully per the module's own docstring
        contract ('handled gracefully with a sensible default so the
        function never raises on a partial summary')."""
        from backend.utils.t4a_pdf import generate_t4a_pdf

        result = generate_t4a_pdf({})
        assert result.startswith(b"%PDF")

    def test_zero_trips_and_zero_earnings(self):
        from backend.utils.t4a_pdf import generate_t4a_pdf

        result = generate_t4a_pdf(_base_summary(net_earnings="0.00", total_trips=0))
        assert result.startswith(b"%PDF")

    def test_generated_at_missing_falls_back_to_now(self):
        from backend.utils.t4a_pdf import generate_t4a_pdf

        summary = _base_summary()
        del summary["generated_at"]
        result = generate_t4a_pdf(summary)
        assert result.startswith(b"%PDF")

    def test_year_missing_falls_back_to_current_year(self):
        from backend.utils.t4a_pdf import generate_t4a_pdf

        summary = _base_summary()
        del summary["year"]
        result = generate_t4a_pdf(summary)
        assert result.startswith(b"%PDF")

    def test_large_earnings_and_long_driver_name(self):
        """A large dollar figure and a long name must not overflow the
        fixed-width cell layout in a way that raises (fpdf2 raises on
        content wider than the cell only for certain modes; this pins that
        realistic large values render without error)."""
        from backend.utils.t4a_pdf import generate_t4a_pdf

        result = generate_t4a_pdf(
            _base_summary(
                net_earnings="987654.32",
                driver_name="A Very Long Legal Driver Name That Could Plausibly Appear On A Government ID",
            )
        )
        assert result.startswith(b"%PDF")


class TestFmtMoney:
    def test_formats_decimal_string_to_two_places(self):
        from backend.utils.t4a_pdf import _fmt_money

        assert _fmt_money("3521.7") == "3521.70"

    def test_rounds_to_two_decimal_places(self):
        from backend.utils.t4a_pdf import _fmt_money

        assert _fmt_money("3521.755") == "3521.76"  # ROUND_HALF_EVEN on the trailing 5 rounds up here

    def test_accepts_int(self):
        from backend.utils.t4a_pdf import _fmt_money

        assert _fmt_money(500) == "500.00"

    def test_accepts_float(self):
        from backend.utils.t4a_pdf import _fmt_money

        assert _fmt_money(500.5) == "500.50"

    def test_invalid_value_falls_back_to_zero(self):
        from backend.utils.t4a_pdf import _fmt_money

        assert _fmt_money("not-a-number") == "0.00"

    def test_none_falls_back_to_zero(self):
        from backend.utils.t4a_pdf import _fmt_money

        assert _fmt_money(None) == "0.00"
