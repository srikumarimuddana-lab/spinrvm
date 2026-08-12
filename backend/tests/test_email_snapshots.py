"""Whole-document snapshot regression tests for Spinr's transactional emails.

Closes ACTION_ITEMS.md's N12 ("no visual/snapshot regression tooling for
email"). Existing tests (``test_email_layout.py``, ``test_receipt_shell_snapshot.py``)
each assert specific substrings — the brand colour is present, the GST line is
present, the logo URL is present — but none of them would catch a change that
breaks the surrounding markup in a way none of those specific assertions
happens to touch (an unclosed ``<tr>``, a dropped ``role="presentation"``, a
style attribute silently removed by a refactor). These tests pin the entire
rendered HTML and plain-text output of the two real, currently-shipping email
templates against a committed golden file, so any drift shows up as a failing
diff instead of quietly reaching an inbox.

This still does **not** verify actual rendering in Gmail/Outlook/Apple Mail —
no such tooling exists in this repo (the standing gap N12 itself names) and
building a real per-client renderer is out of scope here. What this closes is
the byte-level regression net for the layer we generate and control.

See ``tests/_html_snapshot.py`` for the assertion helper and how to
regenerate a snapshot after a deliberate change.
"""

import pytest

from backend.tests._html_snapshot import assert_snapshot
from utils.company_details import CompanyDetails
from utils.email_layout import render_email, render_from_text
from utils.email_receipt import generate_receipt_html, generate_receipt_text

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

_COMPANY = CompanyDetails(
    name="Spinr Technologies Inc.",
    address="220 3rd Ave S, Saskatoon SK S7K 1M1",
    identity_line="Spinr Technologies Inc. — 220 3rd Ave S, Saskatoon SK S7K 1M1",
    contact_line="help@spinr.ca · https://spinr.ca · +1 306 555 0100",
    support_email="help@spinr.ca",
    logo_url="https://api-spinr.spinr.ca/api/v1/branding/spinr-logo.png",
)

# Fixed ride/rider/driver fixtures — identical shape to
# test_receipt_shell_snapshot.py's, kept in sync deliberately (a divergent
# fixture here would pin a scenario that file doesn't also exercise, making a
# real regression harder to correlate between the two).
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


# --- Shared branded layout (utils/email_layout.py) --------------------------
# Two variants: the minimal shape (heading + one paragraph, everything else
# omitted) and the full shape (every optional slot filled) — between them they
# exercise every branch _body_html/render_email has, so a change to any slot's
# markup shows up in at least one snapshot.


async def test_minimal_layout_snapshot():
    rendered = await render_email(
        heading="Your account was approved",
        paragraphs=["You can now go online and accept rides."],
        company=_COMPANY,
    )
    assert_snapshot("layout_minimal_html", rendered.html)
    assert_snapshot("layout_minimal_text", rendered.text)


async def test_full_layout_snapshot():
    rendered = await render_email(
        greeting="Hi Sarah,",
        heading="Your documents expire soon",
        subtitle="Document Reminder",
        paragraphs=[
            "Your driver's licence expires in 5 days.",
            "Upload a renewed copy to keep receiving ride offers.",
        ],
        cta=("Upload now", "https://spinr.ca/documents"),
        footnote="This is an automated reminder. Reply to support@spinr.ca with questions.",
        preheader="Action needed on your driver documents",
        company=_COMPANY,
    )
    assert_snapshot("layout_full_html", rendered.html)
    assert_snapshot("layout_full_text", rendered.text)


async def test_render_from_text_bridge_snapshot():
    # The bridge used by KYB decisions, ops alerts, admin broadcasts,
    # statements, and tax/DSAR exports — a distinct code path from
    # render_email's structured-paragraphs API, worth its own pin.
    rendered = await render_from_text(
        heading="Your monthly statement is ready",
        body=(
            "Here is your driver earnings statement for July 2026.\n\n"
            "Total earnings: $1,240.50\nTotal rides: 62\n\n"
            "Download the attached PDF for the full breakdown."
        ),
        company=_COMPANY,
    )
    assert_snapshot("render_from_text_html", rendered.html)
    assert_snapshot("render_from_text_text", rendered.text)


# --- Ride receipt (utils/email_receipt.py) -----------------------------------
# Both shells: the legacy pre-retrofit HTML (still live for accounts where
# branded_receipt_enabled is false) and the branded shell. Route-snapshot
# image fetching is disabled so both renders stay fully deterministic and
# offline, matching test_receipt_shell_snapshot.py's own convention.


def test_legacy_receipt_shell_snapshot():
    html = generate_receipt_html(_RIDE, _RIDER, _DRIVER, include_route_snapshot=False, company=None)
    assert_snapshot("receipt_legacy_html", html)


def test_branded_receipt_shell_snapshot():
    html = generate_receipt_html(_RIDE, _RIDER, _DRIVER, include_route_snapshot=False, company=_COMPANY)
    assert_snapshot("receipt_branded_html", html)


def test_receipt_text_snapshot():
    text = generate_receipt_text(_RIDE, _RIDER, _DRIVER, company=_COMPANY)
    assert_snapshot("receipt_text", text)
