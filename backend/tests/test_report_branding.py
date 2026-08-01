"""Unit tests for utils/report_branding.py — the shared Spinr-branded
report template layer (PDF/Excel/Word/CSV) used by the Compliance & Tax
Reporting module.
"""

from __future__ import annotations

import io

import pytest

try:
    from backend.utils import report_branding
except ImportError:  # pragma: no cover
    from utils import report_branding  # type: ignore

pytestmark = pytest.mark.unit


class TestReportFormatRegistry:
    def test_sgi_forms_are_fixed_format(self):
        assert report_branding.report_mode("sgi_driver_details") == "fixed_format"
        assert report_branding.report_mode("sgi_vehicle_details") == "fixed_format"

    def test_compliance_reports_are_branded(self):
        assert report_branding.report_mode("gst_pst_remittance") == "branded"
        assert report_branding.report_mode("insurance_billing_sgi") == "branded"
        assert report_branding.report_mode("insurance_billing_knight_archer") == "branded"
        assert report_branding.report_mode("driver_roster") == "branded"
        assert report_branding.report_mode("dsar_lookup") == "branded"

    def test_unregistered_report_type_raises(self):
        # An unregistered compliance report is a bug, not something to
        # silently default — must raise, not guess.
        with pytest.raises(KeyError):
            report_branding.report_mode("some_future_report_nobody_registered")


class TestLogoFallback:
    def test_has_logo_asset_true_with_real_file(self):
        # The real Spinr logo (bullseye mark + wordmark) is checked in at
        # backend/static/branding/spinr_logo.png.
        assert report_branding.has_logo_asset() is True

    def test_has_logo_asset_false_for_missing_path(self, monkeypatch):
        # Fallback behavior must still hold for a genuinely missing file —
        # report False, not crash looking for it.
        monkeypatch.setattr(report_branding, "LOGO_PATH", report_branding.LOGO_PATH.parent / "nonexistent.png")
        assert report_branding.has_logo_asset() is False


class TestCsvInjectionSanitization:
    @pytest.mark.parametrize("lead", ["=", "+", "-", "@", "\t", "\r"])
    def test_formula_leads_are_neutralized(self, lead):
        value = f"{lead}SUM(A1)"
        assert report_branding._sanitize_csv_cell(value) == f"'{value}"

    def test_safe_string_is_unchanged(self):
        assert report_branding._sanitize_csv_cell("2026-07") == "2026-07"

    def test_non_string_passthrough(self):
        assert report_branding._sanitize_csv_cell(42) == 42
        assert report_branding._sanitize_csv_cell(None) is None


class TestPdfSafe:
    def test_em_dash_and_warning_glyph_normalized(self):
        # Regression: fpdf2's core fonts only support Latin-1. Text built
        # elsewhere with em dashes ("—", used throughout this module's own
        # docstrings/subtitles) or a warning glyph ("⚠", used by the
        # compliance endpoints' truncation notice) crashed
        # FPDFUnicodeEncodingException at render time instead of failing at
        # build time — caught by test_new_branded_pdf_renders below, fixed
        # by routing all PDF text through pdf_safe() first.
        assert report_branding.pdf_safe("a — b") == "a - b"
        assert report_branding.pdf_safe("⚠ TRUNCATED") == "! TRUNCATED"

    def test_unknown_unicode_falls_back_to_replacement_not_crash(self):
        # Any other non-Latin-1 character we haven't explicitly mapped must
        # degrade gracefully (replaced) rather than raise.
        result = report_branding.pdf_safe("emoji \U0001f600 test")
        assert isinstance(result, str)


class TestFormatReportTimestamp:
    def test_formats_with_space_between_date_and_time(self):
        # Regression: insurance-period audit's started_at/ended_at and
        # Knight Archer's onboarded_at previously passed the raw DB ISO
        # string straight into the report cell, rendering as one unbroken
        # run of digits/punctuation ("continuous", per the reported bug) —
        # no space between the date and time.
        assert report_branding.format_report_timestamp("2026-07-29T14:32:10.123456+00:00") == "2026-07-29 14:32 UTC"

    def test_handles_z_suffix(self):
        assert report_branding.format_report_timestamp("2026-07-29T14:32:10Z") == "2026-07-29 14:32 UTC"

    def test_none_uses_empty_default(self):
        assert report_branding.format_report_timestamp(None) == ""
        assert report_branding.format_report_timestamp(None, empty="(open)") == "(open)"

    def test_unparseable_value_falls_back_to_raw_string(self):
        # Degrade gracefully rather than hide a real data problem behind a
        # blank cell.
        assert report_branding.format_report_timestamp("not-a-real-date") == "not-a-real-date"


class TestBrandedPdf:
    def test_new_branded_pdf_renders(self):
        pytest.importorskip("fpdf")
        pdf = report_branding.new_branded_pdf("Test Report", "subtitle text")
        pdf.set_font(report_branding.BRAND_FONT, "", 10)
        pdf.cell(0, 6, "body", ln=True)
        report_branding.render_branded_pdf_footer(pdf, {"name": "Saskatchewan", "default_regulatory_authority": "SGI"})
        out = bytes(pdf.output())
        assert out.startswith(b"%PDF")

    def test_title_and_subtitle_with_em_dash_do_not_crash(self):
        # Regression for the exact bug: a subtitle built with " — " (used by
        # both compliance endpoints) must render, not raise.
        pytest.importorskip("fpdf")
        pdf = report_branding.new_branded_pdf(
            "GST/PST Remittance Summary", "2026-07-01 to 2026-07-31 — Total GST $5.00 — ⚠ TRUNCATED at 10000 rows"
        )
        out = bytes(pdf.output())
        assert out.startswith(b"%PDF")

    def test_footer_renders_company_line_without_letterhead(self):
        # The company contact line is unconditional — only the
        # province/regulator line is gated on province_letterhead being
        # supplied. Must not raise either way.
        pytest.importorskip("fpdf")
        pdf = report_branding.new_branded_pdf("Test Report")
        report_branding.render_branded_pdf_footer(pdf, None)
        out = bytes(pdf.output())
        assert out.startswith(b"%PDF")

    def test_fill_color_reset_to_white_after_header_band(self):
        # Regression: new_branded_pdf() sets fill_color to brand red to draw
        # the header band rectangle, then never reset it — every cell drawn
        # afterward (e.g. via render_pdf_table's Table API) inherited that
        # red as its background, including data rows that should stay
        # white. Caught visually: a real downloaded report showed every
        # table row solid red instead of just the header row.
        pytest.importorskip("fpdf")
        pdf = report_branding.new_branded_pdf("Test Report")
        assert pdf.fill_color.colors255 == (255.0, 255.0, 255.0)

    def test_landscape_orientation(self):
        pytest.importorskip("fpdf")
        pdf = report_branding.new_branded_pdf("Test Report", landscape=True)
        assert pdf.w > pdf.h  # landscape: wider than tall

    def test_portrait_is_default(self):
        pytest.importorskip("fpdf")
        pdf = report_branding.new_branded_pdf("Test Report")
        assert pdf.h > pdf.w  # portrait: taller than wide

    def test_multiline_subtitle_renders_without_error(self):
        # Regression: GST/PST remittance's subtitle previously crammed a
        # date range and three dollar totals onto one line — reported as
        # reading unprofessionally. subtitle now accepts a list of lines.
        pytest.importorskip("fpdf")
        pdf = report_branding.new_branded_pdf(
            "GST/PST Remittance Summary",
            ["2026-06-29 to 2026-07-29", "GST: $17.91    PST: $0.00    HST: $0.00"],
        )
        out = bytes(pdf.output())
        assert out.startswith(b"%PDF")

    def test_multiline_subtitle_grows_header_block(self):
        # The divider rule (and content start y) must move down to fit a
        # 2-line subtitle instead of overlapping it — same y as a 1-line
        # subtitle would clip the second line.
        pytest.importorskip("fpdf")
        pdf_one_line = report_branding.new_branded_pdf("Test Report", "one line")
        pdf_two_lines = report_branding.new_branded_pdf("Test Report", ["line one", "line two"])
        assert pdf_two_lines.y > pdf_one_line.y


class TestRenderPdfTable:
    def test_wraps_long_cell_content_without_error(self):
        # Regression: the original fixed-width single-line pdf.cell()
        # renderer clipped/overlapped text into an unreadable table the
        # moment real data (full UUIDs, microsecond ISO timestamps, long
        # period labels) was used — confirmed against a real downloaded
        # report from the live admin portal. render_pdf_table uses fpdf2's
        # native Table API, which wraps instead of clipping.
        pytest.importorskip("fpdf")
        pdf = report_branding.new_branded_pdf("Driver Insurance-Period Audit", landscape=True)
        fieldnames = ["driver_id", "driver_name", "period", "started_at", "ended_at", "ride_id"]
        rows = [
            {
                "driver_id": "0035af1d-6834-4891-9ac5-7fb93cd7e6bb",
                "driver_name": "Kiran Muddana",
                "period": "3 — Passenger aboard (primary commercial, full)",
                "started_at": "2026-07-28T00:06:13.630970+00:00",
                "ended_at": "2026-07-28T00:06:55.628251+00:00",
                "ride_id": "7b74d9cb-fb55-4dfd-97b1-379cdd27957b",
            }
        ]
        report_branding.render_pdf_table(pdf, fieldnames, rows, col_widths=[2.2, 1.3, 2.5, 1.8, 1.8, 2.2])
        out = bytes(pdf.output())
        assert out.startswith(b"%PDF")

    def test_empty_rows_render_header_only(self):
        pytest.importorskip("fpdf")
        pdf = report_branding.new_branded_pdf("Empty Report")
        report_branding.render_pdf_table(pdf, ["a", "b"], [])
        out = bytes(pdf.output())
        assert out.startswith(b"%PDF")


class TestBrandedExcel:
    def test_new_branded_workbook_and_table(self):
        pytest.importorskip("openpyxl")
        # The real logo asset is checked in (backend/static/branding/
        # spinr_logo.png), so has_logo_asset() is True here — title/subtitle
        # land in column C, leaving A/B for the logo image anchor.
        wb, ws = report_branding.new_branded_workbook("GST/PST Remittance Summary", "July 2026")
        assert ws["C1"].value == "GST/PST Remittance Summary"
        assert ws["C2"].value == "July 2026"

        report_branding.write_branded_table(
            ws, ["month", "gst", "pst"], [{"month": "2026-07", "gst": "123.45", "pst": "148.14"}]
        )
        # Header cells are title-cased ("month" -> "Month") to match the
        # PDF/Word header treatment; row data is untouched.
        assert ws["A6"].value == "Month"
        assert ws["B6"].value == "Gst"
        assert ws["A7"].value == "2026-07"
        assert ws["B7"].value == "123.45"

        buf = io.BytesIO()
        wb.save(buf)
        assert len(buf.getvalue()) > 0

    def test_write_branded_table_sanitizes_formula_leads(self):
        pytest.importorskip("openpyxl")
        _, ws = report_branding.new_branded_workbook("Report")
        report_branding.write_branded_table(ws, ["value"], [{"value": "=SUM(A1)"}])
        assert ws["A7"].value == "'=SUM(A1)"

    def test_write_branded_grouped_table_collapses_children_by_default(self):
        pytest.importorskip("openpyxl")
        _, ws = report_branding.new_branded_workbook("SGI Insurance Billing")
        parent = {"driver_name": "Jane Doe", "phase": "All phases", "phase_km": "14.000"}
        children = [
            {"driver_name": "Jane Doe", "phase": "2 — En route", "phase_km": "1.500"},
            {"driver_name": "Jane Doe", "phase": "3 — Passenger aboard", "phase_km": "12.500"},
        ]
        report_branding.write_branded_grouped_table(ws, ["driver_name", "phase", "phase_km"], [(parent, children)])

        # Parent (row 7, right after the header at row 6) is expanded and
        # not part of the outline group.
        assert ws["B7"].value == "All phases"
        assert ws.row_dimensions[7].outlineLevel == 0
        assert ws.row_dimensions[7].hidden is not True

        # Both children (rows 8-9) are collapsed (hidden) by default at
        # outline level 1 -- expanding them is the admin's action, via
        # Excel's native [+] group control.
        assert ws["B8"].value == "2 — En route"
        assert ws["B9"].value == "3 — Passenger aboard"
        assert ws.row_dimensions[8].outlineLevel == 1
        assert ws.row_dimensions[8].hidden is True
        assert ws.row_dimensions[9].outlineLevel == 1
        assert ws.row_dimensions[9].hidden is True

        # Summary (parent) row sits above its detail rows -- required so
        # the collapse control lands next to the parent, not below the
        # last child.
        assert ws.sheet_properties.outlinePr.summaryBelow is False


class TestBrandedWord:
    def test_new_branded_document_and_table(self):
        pytest.importorskip("docx")
        # With the real logo asset present, paragraph 0 is the logo image
        # (empty text) and the heading is paragraph 1.
        doc = report_branding.new_branded_document("GST/PST Remittance Summary", "July 2026")
        assert doc.paragraphs[0].text == ""
        assert doc.paragraphs[1].text == "GST/PST Remittance Summary"
        assert doc.paragraphs[2].text == "July 2026"
        # The accent rule is a paragraph border, not a table — doc.tables
        # must be empty until the caller adds its own data table.
        assert len(doc.tables) == 0

        report_branding.add_branded_table(
            doc, ["month", "gst", "pst"], [{"month": "2026-07", "gst": "123.45", "pst": "148.14"}]
        )
        table = doc.tables[0]
        # Header cells are title-cased to match the PDF/Excel treatment.
        assert table.rows[0].cells[0].text == "Month"
        assert table.rows[1].cells[0].text == "2026-07"

        buf = io.BytesIO()
        doc.save(buf)
        assert len(buf.getvalue()) > 0
