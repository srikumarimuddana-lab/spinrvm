"""Smoke tests for utils/corporate_statement_pdf.py.

Mirrors test_driver_statement_pdf.py's pattern exactly (%PDF prefix +
pypdf text-extraction, since fpdf2 compresses content streams and raw-byte
assertions would silently never match).
"""

from __future__ import annotations

import io

from utils.corporate_statement_pdf import generate_corporate_statement_pdf


def _pdf_text(pdf_bytes: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    return " ".join(page.extract_text() or "" for page in reader.pages)


_COMPANY = {"id": "c1", "name": "Acme Co", "legal_name": "Acme Co Ltd"}

_LINE_ITEM = {
    "ride_id": "ride-abc123",
    "member_id": "member-1",
    "source_type": "allowance",
    "allowance_debit_amount": "18.50",
    "master_fallback_amount": "0.00",
    "tax_amount": "0.93",
    "tax_breakdown": {"GST": {"rate": 5, "amount": "0.93"}},
    "created_at": "2026-07-15T12:00:00Z",
}

_FIXTURE = {
    "month": "2026-07",
    "from": "2026-07-01T00:00:00",
    "to": "2026-08-01T00:00:00",
    "line_items": [_LINE_ITEM],
    "summary": {
        "ride_count": 1,
        "allowance_total": "18.50",
        "master_total": "0.00",
        "total": "18.50",
        "tax_total": "0.93",
        "tax_by_type": {"GST": "0.93"},
        "by_member": [
            {
                "member_id": "member-1",
                "ride_count": 1,
                "allowance_total": "18.50",
                "master_total": "0.00",
                "total": "18.50",
            }
        ],
    },
}


def test_generates_pdf_bytes():
    pdf = generate_corporate_statement_pdf(_COMPANY, _FIXTURE)
    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1500


def test_handles_empty_statement_without_raising():
    pdf = generate_corporate_statement_pdf({}, {})
    assert pdf.startswith(b"%PDF")


def test_states_record_not_a_bill():
    """The document must not imply it triggers a charge — funds were
    already debited as each ride settled."""
    text = _pdf_text(generate_corporate_statement_pdf(_COMPANY, _FIXTURE))
    assert "not a bill" in text


def test_tax_breakdown_line_present():
    text = _pdf_text(generate_corporate_statement_pdf(_COMPANY, _FIXTURE))
    assert "GST" in text
    assert "0.93" in text


def test_line_item_row_cap_is_disclosed_not_silent():
    from utils.corporate_statement_pdf import _MAX_LINE_ITEM_ROWS

    many_items = [dict(_LINE_ITEM, ride_id=f"ride-{i}") for i in range(_MAX_LINE_ITEM_ROWS + 7)]
    fixture = {**_FIXTURE, "line_items": many_items}
    text = _pdf_text(generate_corporate_statement_pdf(_COMPANY, fixture))
    assert "7 more rides not shown" in text
    assert f"showing the first {_MAX_LINE_ITEM_ROWS} of {_MAX_LINE_ITEM_ROWS + 7}" in text


def test_member_row_cap_is_disclosed_not_silent():
    from utils.corporate_statement_pdf import _MAX_MEMBER_ROWS

    many_members = [
        {
            "member_id": f"member-{i}",
            "ride_count": 1,
            "allowance_total": "1.00",
            "master_total": "0.00",
            "total": "1.00",
        }
        for i in range(_MAX_MEMBER_ROWS + 3)
    ]
    fixture = {**_FIXTURE, "summary": {**_FIXTURE["summary"], "by_member": many_members}}
    text = _pdf_text(generate_corporate_statement_pdf(_COMPANY, fixture))
    assert "3 more members not shown" in text


def test_no_overflow_note_when_everything_fits():
    text = _pdf_text(generate_corporate_statement_pdf(_COMPANY, _FIXTURE))
    assert "not shown" not in text


def test_company_name_with_special_characters_does_not_raise():
    """pdf_safe must be applied to user-controlled company name text —
    fpdf2's core fonts crash on non-Latin-1 characters (em dash, curly
    quotes) otherwise."""
    company = {**_COMPANY, "legal_name": "Acme — Co's “Rides” Inc."}
    pdf = generate_corporate_statement_pdf(company, _FIXTURE)
    assert pdf.startswith(b"%PDF")


def test_no_line_items_renders_placeholder():
    fixture = {**_FIXTURE, "line_items": [], "summary": {**_FIXTURE["summary"], "by_member": []}}
    text = _pdf_text(generate_corporate_statement_pdf(_COMPANY, fixture))
    assert "No rides in this period" in text
    assert "No member activity in this period" in text
