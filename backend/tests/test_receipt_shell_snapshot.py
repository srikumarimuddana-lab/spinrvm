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

from utils.email_receipt import generate_receipt_html

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
