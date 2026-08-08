"""Company identity for email comes from the admin Settings page.

The footer used to be two hardcoded constants, which is wrong for the fields
most likely to change without a deploy: legal name, mailing address, support
address, website. The rule these tests protect is that **an unconfigured
setting reproduces the previously-shipped output byte-for-byte** — wiring this
up must not be able to blank a footer.
"""

from unittest.mock import AsyncMock, patch

import pytest

import utils.company_details as cd
from utils.report_branding import COMPANY_CONTACT_LINE, COMPANY_LINE

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


async def _load(settings=None, fail=False):
    loader = AsyncMock(side_effect=RuntimeError("db down")) if fail else AsyncMock(return_value=settings or {})
    with patch.object(cd, "get_app_settings", loader):
        return await cd.load_company_details()


# --- Fallbacks: empty settings must reproduce today's output ---------------


async def test_empty_settings_reproduce_the_shipped_constants():
    details = await _load({})
    assert details.identity_line == COMPANY_LINE
    assert details.contact_line == COMPANY_CONTACT_LINE
    assert details.name == "Spinr"
    assert details.support_email == "support@spinr.ca"


async def test_settings_load_failure_falls_back_rather_than_raising():
    # An email with a correct-if-stale footer beats an email that never sends.
    details = await _load(fail=True)
    assert details.identity_line == COMPANY_LINE
    assert details.contact_line == COMPANY_CONTACT_LINE


async def test_null_values_do_not_render_the_string_none():
    # The settings loader surfaces a DB NULL as Python None, which would
    # otherwise be str()'d straight into a footer.
    details = await _load({"company_name": None, "company_address": None, "company_email": None})
    assert "None" not in details.identity_line
    assert "None" not in details.contact_line


# --- Configured values win -------------------------------------------------


async def test_name_and_address_come_from_settings():
    details = await _load({"company_name": "Northern Rides Inc.", "company_address": "9 Rose Ave, Regina, SK"})
    assert details.identity_line == "Northern Rides Inc. — 9 Rose Ave, Regina, SK"


async def test_address_parts_are_joined_when_the_later_columns_are_set():
    details = await _load(
        {
            "company_name": "Spinr",
            "company_address": "123 Example St",
            "company_city": "Saskatoon",
            "company_province": "SK",
            "company_postal_code": "S7K 1A1",
        }
    )
    assert details.identity_line == "Spinr — 123 Example St, Saskatoon SK S7K 1A1"


async def test_a_name_with_no_address_keeps_the_shipped_line():
    # Renaming without filling in an address must not produce a footer that
    # silently drops the locality the constant already carried.
    details = await _load({"company_name": "Northern Rides Inc."})
    assert details.identity_line == COMPANY_LINE
    assert details.name == "Northern Rides Inc."


async def test_contact_line_joins_email_website_and_phone():
    details = await _load(
        {
            "company_email": "help@northern.test",
            "company_website": "https://northern.test",
            "company_phone": "+1 306 555 0100",
        }
    )
    assert details.contact_line == "help@northern.test · https://northern.test · +1 306 555 0100"


async def test_support_email_follows_the_configured_company_email():
    # Body copy tells the reader where to write; it must match the footer.
    details = await _load({"company_email": "help@northern.test"})
    assert details.support_email == "help@northern.test"
    assert "help@northern.test" in details.contact_line


async def test_partial_contact_details_omit_the_missing_parts():
    details = await _load({"company_email": "help@northern.test"})
    assert details.contact_line == "help@northern.test"
    assert "·" not in details.contact_line


# --- Logo ------------------------------------------------------------------


async def test_logo_defaults_to_the_bundled_asset():
    details = await _load({})
    assert details.logo_url.endswith("/api/v1/branding/spinr-logo.png")
    assert details.logo_url.startswith("https://")


async def test_configured_logo_url_wins():
    details = await _load({"company_logo_url": "https://cdn.northern.test/logo.png"})
    assert details.logo_url == "https://cdn.northern.test/logo.png"


@pytest.mark.parametrize(
    "bad",
    [
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "/static/logo.png",  # relative: nothing to resolve against in an inbox
        "logo.png",
        "ftp://example.test/logo.png",
    ],
)
async def test_unusable_logo_urls_fall_back_to_the_bundled_asset(bad):
    # An admin is trusted, but this lands in an <img src> in mail sent to
    # riders and drivers — a typo or a paste should degrade, not ship.
    details = await _load({"company_logo_url": bad})
    assert details.logo_url.endswith("/api/v1/branding/spinr-logo.png")


async def test_whitespace_logo_url_falls_back():
    details = await _load({"company_logo_url": "   "})
    assert details.logo_url.endswith("/api/v1/branding/spinr-logo.png")
