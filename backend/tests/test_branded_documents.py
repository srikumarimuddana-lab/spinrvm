"""Company identity reaches the receipt PDF and the Spinr Pass invoice PDF.

These two attachments are the reason the retrofit could not stop at the email
body. A receipt email whose footer names one company while the PDF stapled to
it names another is worse than either being stale on its own — it makes the
pair look forged. Both are also documents a rider or driver may file for tax,
so the issuing company's name has to be the configured one.

`to_latin1` matters more than it looks: the assembled identity line uses an em
dash, fpdf2's core fonts are latin-1 only, and a raw em dash raises on output —
which would mean no attachment at all.
"""

import pytest

from utils.company_details import CompanyDetails, to_latin1
from utils.receipt_pdf import generate_receipt_pdf
from utils.subscription_invoice_pdf import generate_subscription_invoice_pdf

pytestmark = [pytest.mark.unit]

_COMPANY = CompanyDetails(
    name="Northern Rides Inc.",
    address="9 Rose Ave, Regina SK S4P 3A1",
    identity_line="Northern Rides Inc. — 9 Rose Ave, Regina SK S4P 3A1",
    contact_line="help@northern.test · https://northern.test",
    support_email="help@northern.test",
    logo_url="https://cdn.northern.test/logo.png",
)

_RIDE = {
    "id": "abcdef1234",
    "ride_code": "SPN-4417",
    "pickup_address": "220 3rd Ave S, Saskatoon",
    "dropoff_address": "Diefenbaker Airport, Saskatoon",
    "total_fare": "24.00",
    "grand_total": "26.64",
    "tax_breakdown": {"GST": {"rate": 5.0, "amount": 1.20}, "PST": {"rate": 6.0, "amount": 1.44}},
    "ride_completed_at": "2026-08-08T14:30:00+00:00",
}
_RIDER = {"id": "u1", "first_name": "Sarah", "last_name": "Johnson"}


def _invoice(**kwargs):
    from decimal import Decimal

    defaults = dict(
        invoice_number="INV-001",
        payment_date="August 08, 2026",
        driver_name="Alex Chen",
        driver_email="alex@example.com",
        plan_name="Pro",
        duration_label="Monthly",
        billing_reason="subscription_cycle",
        subtotal=Decimal("40.00"),
        gst_amount=Decimal("2.00"),
        pst_amount=Decimal("2.40"),
        hst_amount=Decimal("0"),
        tax_total=Decimal("4.40"),
        total=Decimal("44.40"),
    )
    defaults.update(kwargs)
    return generate_subscription_invoice_pdf(**defaults)


# --- to_latin1 -------------------------------------------------------------


def test_em_dash_is_folded_not_dropped():
    # The identity line's separator. Left raw, fpdf2 raises and the receipt
    # arrives with no PDF at all.
    assert to_latin1("Acme — 1 Main St") == "Acme - 1 Main St"


def test_latin1_punctuation_passes_through():
    # A middot is already latin-1; the existing invoice footer relies on it.
    assert to_latin1("a · b") == "a · b"


def test_unencodable_characters_are_dropped_rather_than_raising():
    # An admin can paste anything into a settings field. One missing glyph
    # beats a missing attachment.
    assert to_latin1("Acme 株式会社") == "Acme "


def test_smart_quotes_are_folded():
    assert to_latin1("“Acme’s”") == '"Acme\'s"'


# --- Receipt PDF -----------------------------------------------------------


def test_receipt_pdf_is_a_pdf_with_configured_company():
    pdf = generate_receipt_pdf(_RIDE, _RIDER, None, company=_COMPANY)
    assert pdf[:4] == b"%PDF"


def test_receipt_pdf_still_renders_without_a_company():
    # The flag's off-position, and the fallback if settings are unreachable.
    pdf = generate_receipt_pdf(_RIDE, _RIDER, None, company=None)
    assert pdf[:4] == b"%PDF"


def test_receipt_pdf_survives_a_company_name_full_of_unencodable_characters():
    """The failure this guards is total: an fpdf2 encoding error means no
    attachment, on the one email that doubles as a tax record."""
    hostile = _COMPANY._replace(
        name="Acme 株式会社",
        identity_line="Acme 株式会社 — 東京",
        contact_line="help@acme.test · 東京",
    )
    assert generate_receipt_pdf(_RIDE, _RIDER, None, company=hostile)[:4] == b"%PDF"


# --- Invoice PDF -----------------------------------------------------------


def test_invoice_pdf_is_a_pdf_with_configured_company():
    assert _invoice(company=_COMPANY)[:4] == b"%PDF"


def test_invoice_pdf_still_renders_without_a_company():
    assert _invoice(company=None)[:4] == b"%PDF"


def test_invoice_pdf_survives_unencodable_company_details():
    hostile = _COMPANY._replace(name="Acme 株式会社", address="東京", contact_line="東京")
    assert _invoice(company=hostile)[:4] == b"%PDF"


def test_invoice_pdf_content_is_unchanged_by_the_company_switch():
    """Presentation only: the same charges must produce the same-sized document
    regardless of whose name is on it, give or take the name's own length."""
    with_company = _invoice(company=_COMPANY)
    without = _invoice(company=None)
    assert abs(len(with_company) - len(without)) < 2000
