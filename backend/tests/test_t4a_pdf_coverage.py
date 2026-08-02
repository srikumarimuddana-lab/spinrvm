"""Coverage-closure tests for utils/t4a_pdf.py (A1c Sub-tier B).

Existing coverage of T4A flows (tests/test_t4a_email.py) mocks
``generate_t4a_pdf`` entirely (``patch("backend.routes.drivers._deps.
generate_t4a_pdf", return_value=b"%PDF-1.4 fake")``) so the actual PDF
rendering code in this module has never been exercised. These tests call
``generate_t4a_pdf`` directly with realistic ``summary`` dicts shaped like
the real caller (``routes/drivers/tax_exports.py::get_t4a_summary``'s
return value) to render real PDFs via fpdf2 (no mocking needed -- fpdf2 is
a pure-Python dependency already installed).

Money convention (CLAUDE.md): the real caller always sends money fields as
pre-rounded Decimal-derived strings (via ``_money_str``), so fixtures below
pass ``str(Decimal(...))`` rather than float for every dollar amount.
"""

from decimal import Decimal

from utils.t4a_pdf import _fmt_money, generate_t4a_pdf


def _summary(**overrides) -> dict:
    base = {
        "year": 2025,
        "total_earnings": str(Decimal("12345.67")),
        "total_trips": 214,
        "platform_fees": "0.00",
        "net_earnings": str(Decimal("12345.67")),
        "legacy_synced_earnings": "0.00",
        "gst_registered": False,
        "gst_bn": "",
        "generated_at": "2026-01-05T08:30:00+00:00",
        "driver_name": "Jordan Smith",
    }
    base.update(overrides)
    return base


class TestGenerateT4aPdfHappyPaths:
    def test_gst_not_registered(self):
        pdf_bytes = generate_t4a_pdf(_summary(gst_registered=False))

        assert isinstance(pdf_bytes, bytes)
        assert pdf_bytes.startswith(b"%PDF")
        assert len(pdf_bytes) > 500

    def test_gst_registered_with_bn(self):
        pdf_bytes = generate_t4a_pdf(_summary(gst_registered=True, gst_bn="123456789RT0001"))

        assert pdf_bytes.startswith(b"%PDF")

    def test_gst_registered_without_bn(self):
        """gst_registered=True but gst_bn missing/blank -> the elif branch
        ('Registered (BN not on file)')."""
        pdf_bytes = generate_t4a_pdf(_summary(gst_registered=True, gst_bn=""))

        assert pdf_bytes.startswith(b"%PDF")

    def test_zero_trips_and_zero_earnings(self):
        pdf_bytes = generate_t4a_pdf(_summary(total_trips=0, net_earnings="0.00", total_earnings="0.00"))

        assert pdf_bytes.startswith(b"%PDF")


class TestGenerateT4aPdfDefaults:
    def test_minimal_summary_uses_every_fallback_default(self):
        """An almost-empty summary dict exercises every ``.get(...) or
        default`` fallback: missing year -> current year, missing
        driver_name -> 'See driver profile', missing generated_at ->
        current UTC timestamp, missing net_earnings -> '0.00'."""
        pdf_bytes = generate_t4a_pdf({})

        assert pdf_bytes.startswith(b"%PDF")

    def test_generated_at_gets_truncated_and_relabelled(self):
        """generated_at is sliced to 19 chars and 'T' replaced with a space,
        plus ' UTC' appended -- exercise with a realistic ISO string
        (microseconds included, as datetime.isoformat() produces)."""
        pdf_bytes = generate_t4a_pdf(_summary(generated_at="2026-03-14T09:15:42.123456+00:00"))

        assert pdf_bytes.startswith(b"%PDF")

    def test_relative_report_branding_import_falls_back_to_absolute(self):
        """Covers the dual-import fallback (``from . import report_branding``
        except ImportError -> ``from utils import report_branding``) by
        forcing only the relative import to fail via a scoped
        ``builtins.__import__`` patch; the absolute fallback then runs
        through real, unpatched import machinery."""
        import builtins
        from unittest.mock import patch

        real_import = builtins.__import__

        def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level == 1 and name == "" and fromlist == ("report_branding",):
                raise ImportError("blocked relative for test")
            return real_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=_fake_import):
            pdf_bytes = generate_t4a_pdf(_summary())

        assert pdf_bytes.startswith(b"%PDF")


class TestFmtMoneyHelper:
    """_fmt_money is the module's private Decimal-coercion helper; the try
    branch is already covered indirectly via the happy-path tests above,
    but the except->'0.00' fallback needs its own direct case since a
    summary dict full of un-Decimal-able values would still have to render
    successfully (the whole point of the try/except)."""

    def test_valid_decimal_string(self):
        assert _fmt_money("1234.5") == "1234.50"

    def test_valid_decimal_object(self):
        assert _fmt_money(Decimal("99.999")) == "100.00"

    def test_none_falls_back_to_summary_default_before_reaching_fmt(self):
        # generate_t4a_pdf's own `.get("net_earnings") or "0.00"` guard means
        # _fmt_money never actually sees a raw None in practice, but the
        # helper itself must still degrade gracefully if called directly.
        assert _fmt_money(None) == "0.00"

    def test_unparseable_value_hits_except_branch(self):
        class _NotDecimalable:
            def __str__(self):
                return "not-a-number$$$"

        assert _fmt_money(_NotDecimalable()) == "0.00"

    def test_unparseable_net_earnings_in_full_summary_still_renders(self):
        """End-to-end: a garbage net_earnings value must not crash PDF
        generation -- _fmt_money's except branch keeps the document
        renderable with '0.00' shown instead of raising."""

        class _Garbage:
            def __str__(self):
                return "$$$garbage$$$"

        pdf_bytes = generate_t4a_pdf(_summary(net_earnings=_Garbage()))

        assert pdf_bytes.startswith(b"%PDF")
