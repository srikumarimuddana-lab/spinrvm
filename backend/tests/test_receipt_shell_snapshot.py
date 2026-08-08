"""Pins the ride receipt's shell so the branding retrofit can prove what it changed.

Written **before** the retrofit, deliberately. The receipt is a live-tested,
tax-bearing document: the fare rows and the separate GST/PST line items are a
regulatory requirement, and the whole risk of restyling it is that the shell
change quietly disturbs the content.

So this file asserts two different things:

  * the *content* invariants that must survive the retrofit untouched — these
    are the regression net, and they should never need editing;
  * the *shell* as it stands today, so the diff is visible when it moves and
    the flag's off-position can be proven byte-identical.

`test_receipt_line_items.py` already covers the fare maths in depth; this is
about the wrapper around it.
"""

import pytest

from utils.company_details import CompanyDetails
from utils.email_receipt import generate_receipt_html, generate_receipt_text

pytestmark = [pytest.mark.unit]

_RIDE = {
    "id": "abcdef1234",
    "ride_code": "SPN-4417",
    "pickup_address": "220 3rd Ave S, Saskatoon",
    "dropoff_address": "Diefenbaker Airport, Saskatoon",
    "total_fare": "24.00",
    "tax_amount": "2.64",
    "grand_total": "26.64",
    "tax_breakdown": {"GST": {"rate": 5.0, "amount": 1.20}, "PST": {"rate": 6.0, "amount": 1.44}},
    "ride_completed_at": "2026-08-08T14:30:00+00:00",
}
_RIDER = {"id": "u1", "first_name": "Sarah", "last_name": "Johnson", "email": "sarah@example.com"}
_DRIVER = {"first_name": "Alex", "last_name": "Chen", "driver_code": "SPN-D-0042", "driver_vehicle": "Toyota Corolla"}


def _html(**kwargs):
    return generate_receipt_html(_RIDE, _RIDER, _DRIVER, **kwargs)


# --- Content invariants: must survive the retrofit unchanged ---------------


def test_gst_and_pst_stay_separate_line_items():
    # CLAUDE.md: rider receipts must show GST and PST as separate lines. This
    # is the one assertion in this file that is a legal requirement, not a
    # styling preference.
    html = _html()
    assert "GST (5%)" in html
    assert "PST (6%)" in html


def test_grand_total_is_shown():
    assert "26.64" in _html()


def test_ride_reference_is_shown():
    # The rider quotes this to support; losing it would make a receipt useless
    # for the one thing people email us about.
    assert "SPN-4417" in _html()


def test_pickup_and_dropoff_are_shown():
    html = _html()
    assert "220 3rd Ave S, Saskatoon" in html
    assert "Diefenbaker Airport, Saskatoon" in html


def test_driver_is_identified_without_personal_contact():
    # PIPEDA: the driver's code and vehicle, never their phone or email.
    html = _html()
    assert "Alex" in html
    assert "SPN-D-0042" in html
    assert "Toyota Corolla" in html


def test_rider_is_greeted_by_name():
    assert "Sarah" in _html()


def test_ride_date_is_shown():
    assert "August 08, 2026" in _html()


def test_is_a_complete_html_document():
    html = _html()
    assert "<!DOCTYPE html>" in html
    assert "</html>" in html
    assert 'meta name="viewport"' in html


# --- Shell as it stands today ----------------------------------------------
#
# These are the assertions the retrofit is expected to change. They exist so
# the change is deliberate and reviewable rather than incidental, and so the
# flag's off-position can be proven to still produce this exact output.


def test_current_shell_uses_the_legacy_brand_red():
    assert "#ee2b2b" in _html()


def test_current_shell_renders_the_wordmark_as_text_not_an_image():
    html = _html()
    assert ">Spinr</h1>" in html


def test_current_shell_has_no_logo_image():
    html = _html(include_route_snapshot=False)
    # The only <img> the receipt can carry today is the route snapshot, and
    # that is suppressed here.
    assert "<img" not in html


def test_current_shell_footer_is_hardcoded():
    html = _html()
    assert "Spinr Technologies Inc." in html
    assert "support@spinr.ca" in html


# --- Branded shell (branded_receipt_enabled = true) ------------------------

_COMPANY = CompanyDetails(
    name="Spinr Technologies Inc.",
    address="220 3rd Ave S, Saskatoon SK S7K 1M1",
    identity_line="Spinr Technologies Inc. — 220 3rd Ave S, Saskatoon SK S7K 1M1",
    contact_line="help@spinr.ca · https://spinr.ca · +1 306 555 0100",
    support_email="help@spinr.ca",
    logo_url="https://api-spinr.spinr.ca/api/v1/branding/spinr-logo.png",
)


def _branded(**kwargs):
    kwargs.setdefault("include_route_snapshot", False)
    return generate_receipt_html(_RIDE, _RIDER, _DRIVER, company=_COMPANY, **kwargs)


def test_branded_shell_shows_the_configured_company_name_and_address():
    # The whole point of the retrofit: a receipt carries the company details an
    # admin actually configured, not a constant compiled in months ago.
    html = _branded()
    assert "220 3rd Ave S, Saskatoon SK S7K 1M1" in html
    assert "help@spinr.ca" in html


def test_branded_shell_drops_the_hardcoded_footer():
    assert "Spinr Technologies Inc. · Saskatoon, SK" not in _branded()
    assert "www.spinr.ca" not in _branded()


def test_branded_shell_renders_the_real_logo():
    html = _branded()
    assert f'src="{_COMPANY.logo_url}"' in html
    assert ">Spinr</h1>" not in html, "the text wordmark should be gone"


def test_branded_shell_uses_the_documented_brand_red():
    html = _branded()
    assert "#FF3B30" in html
    assert "#ee2b2b" not in html


def test_branded_greeting_follows_the_configured_name():
    html = generate_receipt_html(
        _RIDE, _RIDER, _DRIVER, include_route_snapshot=False, company=_COMPANY._replace(name="Northern Rides Inc.")
    )
    assert "Thanks for riding with Northern Rides Inc." in html


# --- Content is identical either way ---------------------------------------
#
# The flag governs the wrapper. A receipt is a tax-bearing document, so its
# disclosed charges must not depend on a presentation switch.


@pytest.mark.parametrize("company", [None, _COMPANY])
def test_disclosed_charges_are_the_same_under_both_shells(company):
    html = generate_receipt_html(_RIDE, _RIDER, _DRIVER, include_route_snapshot=False, company=company)
    assert "GST (5%)" in html
    assert "PST (6%)" in html
    assert "26.64" in html
    assert "SPN-4417" in html


def test_only_the_shell_differs_between_the_two():
    """Strip both shells and the remaining body must be character-identical."""
    legacy = generate_receipt_html(_RIDE, _RIDER, _DRIVER, include_route_snapshot=False, company=None)
    branded = generate_receipt_html(_RIDE, _RIDER, _DRIVER, include_route_snapshot=False, company=_COMPANY)

    def _body(html: str) -> str:
        # Everything between the header block and the footer block.
        start = html.index("<!-- Greeting -->")
        end = html.index("<!-- Footer -->")
        return html[start:end]

    legacy_body, branded_body = _body(legacy), _body(branded)
    # Two known, intentional shell bleeds into the body: the accent colour on
    # the total and the route pin, and the company name in the greeting.
    normalised = branded_body.replace("#FF3B30", "#ee2b2b").replace(
        "Thanks for riding with Spinr Technologies Inc.", "Thanks for riding with Spinr"
    )
    assert normalised == legacy_body


# --- Plain-text alternative ------------------------------------------------


def test_text_receipt_carries_the_separate_tax_lines():
    # A text-only client must not get a receipt missing its tax breakdown —
    # that is the part of the document with a regulatory requirement behind it.
    text = generate_receipt_text(_RIDE, _RIDER, _DRIVER, company=_COMPANY)
    assert "GST (5%)" in text
    assert "PST (6%)" in text


def test_text_receipt_carries_total_reference_and_route():
    text = generate_receipt_text(_RIDE, _RIDER, _DRIVER, company=_COMPANY)
    assert "26.64" in text
    assert "SPN-4417" in text
    assert "220 3rd Ave S, Saskatoon" in text


def test_text_receipt_lists_the_same_charges_as_the_html():
    text = generate_receipt_text(_RIDE, _RIDER, _DRIVER, company=_COMPANY)
    html = _branded()
    for label in ("GST (5%)", "PST (6%)"):
        assert label in text and label in html


def test_text_receipt_contains_no_markup():
    text = generate_receipt_text(_RIDE, _RIDER, _DRIVER, company=_COMPANY)
    assert "<" not in text and ">" not in text


def test_text_receipt_falls_back_to_the_legacy_footer_without_a_company():
    text = generate_receipt_text(_RIDE, _RIDER, _DRIVER, company=None)
    assert "Spinr Technologies Inc. · Saskatoon, SK" in text
