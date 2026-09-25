"""Unit tests for utils.receipt_pdf.generate_receipt_pdf."""

from __future__ import annotations

from decimal import Decimal

try:
    from utils.receipt_pdf import _fare_lines, generate_receipt_pdf
except ImportError:
    from backend.utils.receipt_pdf import _fare_lines, generate_receipt_pdf  # type: ignore[no-redef]

_RIDE = {
    "id": "ride-abc12345",
    "ride_code": "SPIN1234",
    "base_fare": "5.00",
    "distance_fare": "3.00",
    "time_fare": "2.00",
    "booking_fee": "1.00",
    "total_fare": "11.00",
    "grand_total": "12.21",  # 11.00 + GST 0.55 + PST 0.66
    "tax_breakdown": {"GST": {"rate": 5, "amount": "0.55"}, "PST": {"rate": 6, "amount": "0.66"}},
    "distance_km": "10.5",
    "duration_minutes": 15,
    "pickup_address": "123 Main St",
    "dropoff_address": "456 Elm St",
    "ride_completed_at": "2026-06-15T17:30:00Z",
}
_RIDER = {"id": "rider-1", "email": "r@example.com", "first_name": "Al", "last_name": "R"}
_DRIVER = {"first_name": "Dee", "last_name": "Driver", "driver_vehicle": "Toyota Prius"}


def test_pdf_is_valid_bytes():
    pdf = generate_receipt_pdf(_RIDE, _RIDER, _DRIVER, Decimal("2.00"))
    assert isinstance(pdf, (bytes, bytearray))
    assert bytes(pdf).startswith(b"%PDF")
    assert len(pdf) > 800  # a real document, not an empty stub


def test_grand_total_includes_tax_and_tip():
    rows, grand = _fare_lines(_RIDE, Decimal("2.00"))
    # persisted grand_total 12.21 + tip 2.00
    assert grand == Decimal("14.21")
    labels = [r[0] for r in rows]
    assert "GST (5%)" in labels  # tax shown as separate line items
    assert "PST (6%)" in labels
    assert "Tip" in labels


def test_tax_fallback_from_stored_tax_amount():
    ride = {**_RIDE, "tax_breakdown": {}, "tax_amount": "1.21"}  # no itemised tax
    rows, grand = _fare_lines(ride, Decimal("0"))
    # MONEY-002: the stored tax_amount (not a grand_total gap) as a single Tax line
    assert ("Tax", "$1.21") in rows
    assert grand == Decimal("12.21")


def test_no_driver_phone_or_plate_in_pdf():
    # PIPEDA: even if a driver dict carries a phone, it must not be rendered.
    pdf = bytes(generate_receipt_pdf(_RIDE, _RIDER, {**_DRIVER, "phone": "+13065551212"}))
    assert b"3065551212" not in pdf


def _amt(s: str) -> Decimal:
    return Decimal(s.replace("$", ""))


def test_minimum_fare_adjustment_row_reconciles_to_subtotal():
    # base+dist+time+booking = 5.90, floored up to 8.00 at booking.
    ride = {
        **_RIDE,
        "base_fare": "3.50",
        "distance_fare": "0.15",
        "time_fare": "0.25",
        "booking_fee": "2.00",
        "total_fare": "8.00",
        "grand_total": "8.00",
        "tax_breakdown": {},
    }
    rows, _grand = _fare_lines(ride, Decimal("0"))
    labels = [r[0] for r in rows]
    assert "Minimum fare adjustment" in labels
    assert ("Minimum fare adjustment", "$2.10") in rows  # 8.00 − 5.90
    # The rendered fare rows above the rule must sum to the printed Subtotal.
    subtotal_idx = labels.index("Subtotal")
    fare_sum = sum((_amt(a) for _l, a in rows[:subtotal_idx] if a), Decimal("0"))
    assert fare_sum == _amt(dict(rows)["Subtotal"])


def test_airport_surcharge_row_in_pdf():
    ride = {**_RIDE, "airport_fee": "4.00", "total_fare": "15.00", "grand_total": "15.00", "tax_breakdown": {}}
    rows, _grand = _fare_lines(ride, Decimal("0"))
    assert ("Airport surcharge", "$4.00") in rows
    # Not clamped once airport is counted → no adjustment row.
    assert "Minimum fare adjustment" not in [r[0] for r in rows]


def test_no_minimum_fare_row_when_not_clamped_pdf():
    # Default _RIDE: total_fare 11.00 == component sum → no adjustment row.
    rows, _grand = _fare_lines(_RIDE, Decimal("0"))
    assert "Minimum fare adjustment" not in [r[0] for r in rows]


# ── Promo/discount line item (C102) ───────────────────────────────────────
#
# ACTION_ITEMS.md C102: the PDF receipt never disclosed a discount line,
# unlike the JSON receipt's _build_fare_breakdown (routes/rides/_shared.py) —
# a line-item-transparency gap, not a money-correctness bug, since the
# persisted grand_total already reconciled to what was actually charged.


def test_promo_discount_line_is_pure_disclosure_when_grand_total_persisted():
    ride = {**_RIDE, "discount_amount": "4.00", "promo_code": "SAVE10"}
    rows, grand = _fare_lines(ride, Decimal("0"))
    assert ("Promo (SAVE10)", "-$4.00") in rows
    assert grand == Decimal("12.21")  # persisted grand_total, unchanged


def test_promo_discount_without_code_uses_generic_label_pdf():
    ride = {**_RIDE, "discount_amount": "1.50", "promo_code": None}
    rows, _grand = _fare_lines(ride, Decimal("0"))
    assert ("Promo discount", "-$1.50") in rows


def test_no_promo_row_when_discount_is_zero_pdf():
    rows, _grand = _fare_lines(_RIDE, Decimal("0"))
    assert not any(label.startswith("Promo") for label, _amt in rows)


def test_promo_discount_capped_at_ride_fare_not_raw_amount_pdf():
    # ride fare portion = base 5.00 + distance 3.00 + time 2.00 = 10.00;
    # a $20 promo must render capped at $10.00, never the raw $20.
    ride = {**_RIDE, "discount_amount": "20.00", "promo_code": "HUGE"}
    rows, _grand = _fare_lines(ride, Decimal("0"))
    assert ("Promo (HUGE)", "-$10.00") in rows


def test_fallback_total_without_persisted_grand_total_subtracts_discount_pdf():
    """When grand_total isn't persisted, the reconstructed total must still
    subtract the discount or the visible rows no longer sum to the printed
    total now that the discount is a real line item."""
    ride = {**_RIDE, "grand_total": None, "discount_amount": "2.50", "promo_code": None}
    rows, grand = _fare_lines(ride, Decimal("0"))
    assert ("Promo discount", "-$2.50") in rows
    # subtotal 11.00 + tax (0.55+0.66=1.21) - discount 2.50
    assert grand == Decimal("9.71")


# ── Surge as a real dollar line item (ranked #26 / audit N14) ─────────────
#
# Before this fix the PDF receipt never disclosed surge at all (no footnote,
# no line item). Now it must show a real Decimal dollar amount, matching the
# in-app fare breakdown's calculation (routes/rides/_shared.py::_build_fare_breakdown):
# surge_delta = surged(distance+time) - unsurged(distance+time), $0 when the
# minimum-fare floor already absorbed it.


def test_surge_renders_real_dollar_line_item_not_just_footnote():
    # base=3.50, distance=6.30 (surged), time=2.50 (surged), booking=1.00.
    # Components sum to 13.30 == total_fare → not min-fare clamped.
    # surge=1.5x → surged_dt=8.80, unsurged_dt=round(8.80/1.5)=5.87,
    # surge_delta=round(8.80-5.87)=2.93.
    ride = {
        **_RIDE,
        "base_fare": "3.50",
        "distance_fare": "6.30",
        "time_fare": "2.50",
        "booking_fee": "1.00",
        "surge_multiplier": "1.5",
        "total_fare": "13.30",
        "grand_total": "13.30",
        "tax_breakdown": {},
    }
    rows, _grand = _fare_lines(ride, Decimal("0"))
    labels = [r[0] for r in rows]
    assert "Surge (1.50×)" in labels
    assert ("Surge (1.50×)", "$2.93") in rows
    # Footnote stays alongside as supplementary context, not a replacement.
    notes = [a for lbl, a in rows if lbl == "__note__"]
    assert any("1.50" in n and "surge" in n.lower() for n in notes)

    # Reconciliation: rendered fare rows (excluding the footnote note and the
    # divider) still sum to the printed Subtotal — no double counting from
    # inserting the Surge line.
    subtotal_idx = labels.index("Subtotal")
    fare_sum = sum(
        (_amt(a) for lbl, a in rows[:subtotal_idx] if a and lbl != "__note__"),
        Decimal("0"),
    )
    assert fare_sum == _amt(dict((lbl, a) for lbl, a in rows if lbl != "__note__")["Subtotal"])

    # The PDF itself must actually generate (real amount renders in latin-1).
    pdf = generate_receipt_pdf(ride, _RIDER, _DRIVER, Decimal("0"))
    assert bytes(pdf).startswith(b"%PDF")


def test_no_surge_line_when_multiplier_is_one_pdf():
    rows, _grand = _fare_lines(_RIDE, Decimal("0"))  # default surge_multiplier absent → 1
    labels = [r[0] for r in rows]
    assert not [lbl for lbl in labels if lbl.startswith("Surge")]
    assert "__note__" not in labels


def test_surge_delta_is_zero_when_minimum_fare_absorbs_it():
    # Tiny surged ride, but floored up to a high minimum — surge added $0 on
    # top of the floor, so the line still appears (disclosure) but at $0.00,
    # matching the in-app fare breakdown's behaviour on a min-fare ride.
    ride = {
        **_RIDE,
        "base_fare": "1.00",
        "distance_fare": "1.00",
        "time_fare": "0.40",
        "booking_fee": "0.00",
        "surge_multiplier": "2.0",
        "total_fare": "20.00",
        "grand_total": "20.00",
        "tax_breakdown": {},
    }
    rows, _grand = _fare_lines(ride, Decimal("0"))
    assert ("Surge (2.00×)", "$0.00") in rows
    assert ("Minimum fare adjustment", "$17.60") in rows  # 20.00 - 2.40
    labels = [r[0] for r in rows]
    subtotal_idx = labels.index("Subtotal")
    fare_sum = sum(
        (_amt(a) for lbl, a in rows[:subtotal_idx] if a and lbl != "__note__"),
        Decimal("0"),
    )
    assert fare_sum == Decimal("20.00")


# ── Latin-1 folding (production incident 2026-09-12) ─────────────────────────
# fpdf2's Helvetica is a core font and latin-1 only. Any unencodable character
# reaching it raises FPDFUnicodeEncodingException and fails the ENTIRE
# attachment, so no rider gets a receipt at all. Both completed rides on
# 2026-09-12 (18:37:19 and 19:30:52) failed exactly this way on a lone em dash
# used as the empty-address placeholder.


def test_missing_addresses_do_not_raise_on_the_em_dash_placeholder():
    """The original crash: an absent address fell back to a bare "—"."""
    ride = {**_RIDE, "pickup_address": "", "dropoff_address": ""}
    pdf = generate_receipt_pdf(ride, _RIDER, _DRIVER, Decimal("0"))
    assert bytes(pdf).startswith(b"%PDF")


def test_non_latin1_address_is_folded_rather_than_failing_the_receipt():
    ride = {
        **_RIDE,
        # Curly quote, en dash and an accent — all real geocoder output.
        "pickup_address": "123 O\u2019Brien St \u2013 Rear Entrance",
        "dropoff_address": "456 Rue Beaus\u00e9jour",
    }
    pdf = generate_receipt_pdf(ride, _RIDER, _DRIVER, Decimal("0"))
    assert bytes(pdf).startswith(b"%PDF")


def test_accented_driver_name_still_produces_a_receipt():
    driver = {"first_name": "Jos\u00e9", "last_name": "Mu\u00f1oz", "driver_vehicle": "Toyota Prius"}
    pdf = generate_receipt_pdf(_RIDE, _RIDER, driver, Decimal("0"))
    assert bytes(pdf).startswith(b"%PDF")


def test_ride_without_a_ride_code_does_not_use_an_unencodable_placeholder():
    ride = {**_RIDE}
    ride.pop("ride_code")
    ride["id"] = ""
    pdf = generate_receipt_pdf(ride, _RIDER, _DRIVER, Decimal("0"))
    assert bytes(pdf).startswith(b"%PDF")


# ── Tax-gap fallback + discount interaction (follow-up, found resolving a
# merge conflict against another session's C102 fix) ─────────────────────
#
# The other session's fix (see the "Promo/discount line item (C102)" block
# above) deliberately left one residual documented as out of scope in
# ACTION_ITEMS.md: the "no tax_breakdown persisted" gap fallback below
# inferred tax as (persisted_grand - subtotal), which is wrong once a
# discount is also present, since grand_total = subtotal + tax - discount.
# Fixed as part of resolving the merge; this is the one test that isn't
# already covered by the block above.


def test_tax_gap_fallback_correctly_separates_tax_from_discount():
    """Regression for a latent bug this fix also closes: on a legacy ride with
    no tax_breakdown, the old gap formula (persisted_grand - subtotal) treated
    the WHOLE gap as tax — silently wrong (or a dropped Tax line entirely,
    since a negative gap failed the `> 0.005` guard) whenever a discount was
    also present, since grand_total = subtotal + tax - discount."""
    ride = {
        **_RIDE,
        "tax_breakdown": {},
        "tax_amount": "1.21",
        "discount_amount": "2.00",
        # True tax is 1.21 (as in _RIDE's GST+PST); grand_total reflects
        # subtotal(11.00) + tax(1.21) - discount(2.00) = 10.21.
        "grand_total": "10.21",
    }
    rows, grand = _fare_lines(ride, Decimal("0"))
    assert ("Tax", "$1.21") in rows
    assert ("Promo discount", "-$2.00") in rows
    assert grand == Decimal("10.21")
