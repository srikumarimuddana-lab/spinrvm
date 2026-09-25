"""MONEY-002 / ROADMAP X8 — receipt tax fallback when ``tax_breakdown`` is missing.

Rider receipts must show GST and PST as separate line items (CLAUDE.md,
Saskatchewan section). Both renderers (PDF ``_fare_lines`` and email
``_build_fare_rows``) read the persisted ``tax_breakdown``. When it is
missing they fall back to ONE line showing the STORED ``tax_amount``:

* no GST/PST split is derived — no per-ride rate is stored when the
  breakdown is missing, so a split would be a guess;
* the label stays plain "Tax" (not "Tax (GST/PST)", which claims a known mix
  of both taxes — same decision as corporate_statement_pdf.py, #4259);
* the fallback is flagged loudly (error log + Sentry), since it falls short
  of the separate-line-items rule and no current write path produces it.

The PDF previously reconstructed tax as ``grand_total - total_fare +
discount``, which also swept in area fees (``total_fare`` excludes them) and
fabricated a "Tax" line on rides with area fees and no tax at all.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

try:
    from utils import receipt_pdf as receipt_pdf_mod
    from utils.email_receipt import _build_fare_rows
    from utils.receipt_pdf import _fare_lines, generate_receipt_pdf
except ImportError:
    from backend.utils import receipt_pdf as receipt_pdf_mod  # type: ignore[no-redef]
    from backend.utils.email_receipt import _build_fare_rows  # type: ignore[no-redef]
    from backend.utils.receipt_pdf import _fare_lines, generate_receipt_pdf  # type: ignore[no-redef]

_FLAG_MSG = "receipt tax fallback"
_RIDER = {"id": "rider-1", "first_name": "Al", "last_name": "R"}


def _ride(**overrides) -> dict:
    # total_fare 11.00 = 5.00 + 3.00 + 2.00 + 1.00; GST 5% = 0.55, PST 6% = 0.66.
    base = {
        "id": "ride-tax-0001",
        "ride_code": "SPIN0001",
        "status": "completed",
        "base_fare": "5.00",
        "distance_fare": "3.00",
        "time_fare": "2.00",
        "booking_fee": "1.00",
        "surge_multiplier": "1.0",
        "total_fare": "11.00",
        "area_fees_total": "0",
        "area_fees_breakdown": [],
        "tax_amount": "1.21",
        "tax_breakdown": {"GST": {"rate": 5.0, "amount": 0.55}, "PST": {"rate": 6.0, "amount": 0.66}},
        "grand_total": "12.21",
        "distance_km": "10.5",
        "duration_minutes": 15,
        "ride_completed_at": "2026-06-15T17:30:00Z",
    }
    base.update(overrides)
    return base


def _amt(s: str) -> Decimal:
    return Decimal(s.replace("-", "").replace("$", ""))


def _pdf_tax_rows(rows):
    return [(lbl, a) for lbl, a in rows if lbl == "Tax" or lbl.startswith(("GST", "PST", "HST"))]


@pytest.fixture
def sentry_calls(monkeypatch):
    import sentry_sdk

    calls = []
    monkeypatch.setattr(sentry_sdk, "capture_message", lambda msg, **kw: calls.append((msg, kw)))
    return calls


def _flag_records(caplog):
    return [r for r in caplog.records if r.levelname == "ERROR" and _FLAG_MSG in r.getMessage()]


# ── Breakdown present: output unchanged, no telemetry ───────────────────────


def test_pdf_with_breakdown_shows_separate_gst_pst_and_does_not_flag(caplog, sentry_calls):
    with caplog.at_level("ERROR"):
        rows, grand = _fare_lines(_ride(), Decimal("0"))
    assert _pdf_tax_rows(rows) == [("GST (5%)", "$0.55"), ("PST (6%)", "$0.66")]
    assert grand == Decimal("12.21")
    assert not _flag_records(caplog)
    assert sentry_calls == []


def test_email_with_breakdown_shows_separate_gst_pst_and_does_not_flag(caplog, sentry_calls):
    with caplog.at_level("ERROR"):
        html, total = _build_fare_rows(_ride(), Decimal("0"))
    assert "GST (5%)" in html and "$0.55" in html
    assert "PST (6%)" in html and "$0.66" in html
    assert ">Tax<" not in html
    assert total == Decimal("12.21")
    assert not _flag_records(caplog)
    assert sentry_calls == []


def test_pdf_odd_cent_breakdown_lines_sum_exactly_to_stored_tax():
    # taxable 10.55: GST 0.5275 -> 0.53, PST 0.633 -> 0.63; stored total 1.16.
    ride = _ride(
        total_fare="10.55",
        base_fare="4.55",
        tax_amount="1.16",
        tax_breakdown={"GST": {"rate": 5.0, "amount": 0.53}, "PST": {"rate": 6.0, "amount": 0.63}},
        grand_total="11.71",
    )
    rows, grand = _fare_lines(ride, Decimal("0"))
    tax_rows = _pdf_tax_rows(rows)
    assert [lbl for lbl, _ in tax_rows] == ["GST (5%)", "PST (6%)"]
    assert sum((_amt(a) for _, a in tax_rows), Decimal("0")) == Decimal("1.16")
    assert grand == Decimal("11.71")


# ── PST-exempt (GST-only) breakdown — current SK configuration ─────────────


def test_pdf_gst_only_breakdown_renders_no_pst_and_no_combined_line(caplog, sentry_calls):
    ride = _ride(tax_amount="0.55", tax_breakdown={"GST": {"rate": 5.0, "amount": 0.55}}, grand_total="11.55")
    with caplog.at_level("ERROR"):
        rows, grand = _fare_lines(ride, Decimal("0"))
    assert _pdf_tax_rows(rows) == [("GST (5%)", "$0.55")]
    assert grand == Decimal("11.55")
    assert not _flag_records(caplog)
    assert sentry_calls == []


def test_email_gst_only_breakdown_renders_no_pst_and_no_combined_line(caplog, sentry_calls):
    ride = _ride(tax_amount="0.55", tax_breakdown={"GST": {"rate": 5.0, "amount": 0.55}}, grand_total="11.55")
    with caplog.at_level("ERROR"):
        html, total = _build_fare_rows(ride, Decimal("0"))
    assert "GST (5%)" in html
    assert "PST" not in html
    assert ">Tax<" not in html
    assert total == Decimal("11.55")
    assert not _flag_records(caplog)


# ── Breakdown missing, nonzero stored tax: one "Tax" line + loud flag ──────


@pytest.mark.parametrize("missing", [{}, None])
@pytest.mark.parametrize("tax", ["1.21", "1.37"])  # 1.37: odd-cent total
def test_pdf_missing_breakdown_shows_stored_tax_exactly_and_flags(missing, tax, caplog, sentry_calls):
    grand = str(Decimal("11.00") + Decimal(tax))
    ride = _ride(tax_breakdown=missing, tax_amount=tax, grand_total=grand)
    with caplog.at_level("ERROR"):
        rows, total = _fare_lines(ride, Decimal("0"))
    tax_rows = _pdf_tax_rows(rows)
    assert tax_rows == [("Tax", f"${tax}")]  # single line, never a guessed split
    assert sum((_amt(a) for _, a in tax_rows), Decimal("0")) == Decimal(tax)
    assert total == Decimal(grand)
    recs = _flag_records(caplog)
    assert len(recs) == 1
    assert "ride-tax-0001" in recs[0].getMessage() and "receipt_pdf" in recs[0].getMessage()
    assert len(sentry_calls) == 1
    msg, kw = sentry_calls[0]
    assert msg == "receipt_gst_pst_breakdown_missing"
    assert kw["tags"]["domain"] == "payments"
    assert kw["tags"]["ride_id"] == "ride-tax-0001"


@pytest.mark.parametrize("missing", [{}, None])
@pytest.mark.parametrize("tax", ["1.21", "1.37"])
def test_email_missing_breakdown_shows_stored_tax_exactly_and_flags(missing, tax, caplog, sentry_calls):
    grand = str(Decimal("11.00") + Decimal(tax))
    ride = _ride(tax_breakdown=missing, tax_amount=tax, grand_total=grand)
    with caplog.at_level("ERROR"):
        html, total = _build_fare_rows(ride, Decimal("0"))
    assert ">Tax<" in html
    assert f"${tax}" in html
    assert "GST" not in html and "PST" not in html
    assert total == Decimal(grand)
    recs = _flag_records(caplog)
    assert len(recs) == 1 and "email_receipt" in recs[0].getMessage()
    assert [m for m, _ in sentry_calls] == ["receipt_gst_pst_breakdown_missing"]


def test_pdf_missing_breakdown_no_persisted_grand_reconciles_from_stored_tax():
    ride = _ride(tax_breakdown={}, tax_amount="1.37", grand_total=None)
    rows, grand = _fare_lines(ride, Decimal("2.00"))
    assert ("Tax", "$1.37") in rows
    assert grand == Decimal("14.37")  # 11.00 + 1.37 + tip 2.00


def test_pdf_missing_breakdown_with_discount_keeps_tax_and_promo_separate():
    # grand_total = 11.00 + 1.21 - 2.00 = 10.21
    ride = _ride(tax_breakdown={}, discount_amount="2.00", grand_total="10.21")
    rows, grand = _fare_lines(ride, Decimal("0"))
    assert ("Tax", "$1.21") in rows
    assert ("Promo discount", "-$2.00") in rows
    assert grand == Decimal("10.21")


# ── Regression: area fees were fabricated into a "Tax" line (PDF) ──────────


def test_pdf_area_fees_without_tax_no_longer_fabricate_a_tax_line(caplog, sentry_calls):
    """Before: gap = grand_total(14.00) - total_fare(11.00) = 3.00 rendered as
    "Tax $3.00" — the area fee disclosed twice, once mislabelled as tax."""
    ride = _ride(
        tax_breakdown={},
        tax_amount="0",
        area_fees_breakdown=[{"name": "Airport pickup fee", "type": "airport", "calculated_value": 3.00}],
        area_fees_total="3.00",
        grand_total="14.00",
    )
    with caplog.at_level("ERROR"):
        rows, grand = _fare_lines(ride, Decimal("0"))
    assert ("Airport pickup fee", "$3.00") in rows
    assert _pdf_tax_rows(rows) == []
    assert grand == Decimal("14.00")
    assert not _flag_records(caplog)
    assert sentry_calls == []


def test_pdf_area_fees_with_missing_breakdown_shows_only_the_stored_tax():
    ride = _ride(
        tax_breakdown={},
        tax_amount="0.70",
        area_fees_breakdown=[{"name": "Airport pickup fee", "type": "airport", "calculated_value": 3.00}],
        area_fees_total="3.00",
        grand_total="14.70",
    )
    rows, grand = _fare_lines(ride, Decimal("0"))
    assert _pdf_tax_rows(rows) == [("Tax", "$0.70")]  # was "$3.70" (fees + tax)
    assert grand == Decimal("14.70")


# ── Zero tax, no breakdown: no tax line, no noise ──────────────────────────


def test_zero_tax_without_breakdown_renders_no_tax_line_and_does_not_flag(caplog, sentry_calls):
    ride = _ride(tax_breakdown={}, tax_amount="0", grand_total="11.00")
    with caplog.at_level("ERROR"):
        rows, _ = _fare_lines(ride, Decimal("0"))
        html, _ = _build_fare_rows(ride, Decimal("0"))
    assert _pdf_tax_rows(rows) == []
    assert ">Tax<" not in html
    assert not _flag_records(caplog)
    assert sentry_calls == []


# ── Telemetry must never break a receipt ────────────────────────────────────


def test_sentry_failure_does_not_break_either_renderer(monkeypatch):
    import sentry_sdk

    def _boom(*_a, **_kw):
        raise RuntimeError("sentry down")

    monkeypatch.setattr(sentry_sdk, "capture_message", _boom)
    ride = _ride(tax_breakdown={}, tax_amount="1.37", grand_total="12.37")
    rows, _ = _fare_lines(ride, Decimal("0"))
    html, _ = _build_fare_rows(ride, Decimal("0"))
    assert ("Tax", "$1.37") in rows
    assert "$1.37" in html
    pdf = generate_receipt_pdf(ride, _RIDER, None, Decimal("0"))
    assert bytes(pdf).startswith(b"%PDF")


def test_flag_logs_no_pii():
    """Only the ride id and amount reach the log line (PIPEDA)."""
    ride = _ride(pickup_address="1 Example Rd", rider_phone="+10000000000")
    import logging

    records = []

    class _H(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    h = _H()
    receipt_pdf_mod.logger.addHandler(h)
    try:
        receipt_pdf_mod.flag_missing_tax_breakdown(ride, Decimal("1.37"), "receipt_pdf")
    finally:
        receipt_pdf_mod.logger.removeHandler(h)
    assert records and all("Example Rd" not in m and "0000000000" not in m for m in records)
