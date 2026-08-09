"""Tests for the shared branded email layout (utils/email_layout.py).

These exist because, before this module, **no test in the repo asserted anything
about email appearance** — the receipt template's colours, header, and footer
could all be silently broken without a single failure. The branding assertions
below are the regression net for that.

The layout is async now: the logo and the footer's company identity come from
the admin Settings page rather than from constants, so most of these drive it
with a stubbed `CompanyDetails` and a few exercise the real loader's fallbacks.
"""

from unittest.mock import AsyncMock, patch

import pytest

import utils.email_layout as layout
from utils.company_details import CompanyDetails
from utils.email_layout import BRAND_RED, BRAND_RED_CONTRAST, RenderedEmail, render_email, render_text
from utils.report_branding import COMPANY_CONTACT_LINE, COMPANY_LINE

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

_COMPANY = CompanyDetails(
    name="Spinr",
    address="123 Example St, Saskatoon, SK",
    identity_line="Spinr Technologies Inc. — 123 Example St, Saskatoon, SK",
    contact_line="support@spinr.ca · https://spinr.ca",
    support_email="support@spinr.ca",
    logo_url="https://api-spinr.spinr.ca/api/v1/branding/spinr-logo.png",
)


async def _render(**kwargs) -> RenderedEmail:
    kwargs.setdefault("heading", "Your account was approved")
    kwargs.setdefault("paragraphs", ["You can now go online and accept rides."])
    kwargs.setdefault("company", _COMPANY)
    return await render_email(**kwargs)


# --- Branding --------------------------------------------------------------


async def test_html_embeds_the_configured_logo():
    html = (await _render()).html
    assert f'src="{_COMPANY.logo_url}"' in html
    assert "<img" in html


async def test_logo_alt_text_carries_the_company_name():
    # Outlook and untrusted-sender Gmail block remote images by default, so the
    # alt text is the only branding those recipients see — and it must follow a
    # rename rather than leaving "Spinr" behind where nobody looks.
    html = (await _render(company=_COMPANY._replace(name="Northern Rides Inc."))).html
    assert 'alt="Northern Rides Inc."' in html


async def test_uses_the_documented_brand_red():
    html = (await _render(cta=("Open Spinr", "https://spinr.ca"))).html
    assert BRAND_RED == "#FF3B30", "brand red must match .claude/context/brand-spinr.md"
    assert BRAND_RED in html
    # Buttons use the AA-contrast variant, not full-strength red.
    assert BRAND_RED_CONTRAST in html


async def test_footer_uses_the_configured_company_identity():
    html = (await _render()).html
    assert _COMPANY.identity_line in html
    assert _COMPANY.contact_line in html


async def test_footer_follows_a_settings_change():
    renamed = _COMPANY._replace(
        identity_line="Northern Rides Inc. — 9 Rose Ave, Regina, SK",
        contact_line="help@northern.test",
    )
    html = (await _render(company=renamed)).html
    assert "Northern Rides Inc. — 9 Rose Ave, Regina, SK" in html
    assert "help@northern.test" in html
    assert COMPANY_LINE not in html


async def test_loads_company_details_when_the_caller_passes_none():
    loader = AsyncMock(return_value=_COMPANY)
    with patch.object(layout, "load_company_details", loader):
        html = (await render_email(heading="x", company=None)).html
    loader.assert_awaited_once()
    assert _COMPANY.identity_line in html


async def test_preloaded_company_skips_the_settings_read():
    loader = AsyncMock(return_value=_COMPANY)
    with patch.object(layout, "load_company_details", loader):
        await render_email(heading="x", company=_COMPANY)
    loader.assert_not_awaited()


# --- Plain-text alternative ------------------------------------------------


async def test_returns_a_non_empty_text_alternative():
    # send_transactional_email only builds multipart/alternative when both are
    # present; an empty text part silently drops back to html-only.
    rendered = await _render()
    assert rendered.text.strip()
    assert "Your account was approved" in rendered.text
    assert "You can now go online and accept rides." in rendered.text


async def test_text_alternative_carries_the_configured_footer():
    assert _COMPANY.identity_line in (await _render()).text


async def test_text_alternative_carries_the_cta_url():
    text = render_text(
        heading="Documents expiring",
        paragraphs=["Your insurance expires in 3 days."],
        cta=("Upload now", "https://spinr.ca/documents"),
        company=_COMPANY,
    )
    assert "Upload now: https://spinr.ca/documents" in text


def test_render_text_without_company_falls_back_to_the_static_constants():
    # It stays synchronous, so it cannot reach settings; the shipped constants
    # are the honest fallback rather than a blank footer.
    text = render_text(heading="x")
    assert COMPANY_LINE in text
    assert COMPANY_CONTACT_LINE in text


async def test_text_alternative_contains_no_markup():
    text = (await _render(cta=("Open", "https://spinr.ca"))).text
    assert "<" not in text and ">" not in text


# --- Escaping (admin free text reaches recipients through here) ------------


async def test_escapes_html_in_body_copy():
    # A suspension/rejection reason is admin-authored free text.
    rendered = await _render(paragraphs=['<script>alert("x")</script> & more'])
    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html


async def test_escapes_quotes_in_cta_url_attribute():
    rendered = await _render(cta=("Go", 'https://x.test/"onmouseover="alert(1)'))
    assert '"onmouseover="' not in rendered.html
    assert "&quot;" in rendered.html


async def test_escapes_heading_and_greeting():
    rendered = await _render(heading="<b>Approved</b>", greeting="Hi <em>Sam</em>,")
    assert "<b>Approved</b>" not in rendered.html
    assert "<em>Sam</em>" not in rendered.html


async def test_escapes_the_company_name_and_logo_url():
    # These come from an admin-editable settings field, so they are no more
    # trusted as markup than any other caller-supplied value.
    evil = _COMPANY._replace(name='" onerror="alert(1)', logo_url='https://x.test/"onload="alert(1)')
    html = (await _render(company=evil)).html
    assert ' onerror="alert(1)"' not in html
    assert ' onload="alert(1)"' not in html
    assert "&quot;" in html


# --- Structure -------------------------------------------------------------


async def test_optional_blocks_are_omitted_cleanly():
    rendered = await render_email(heading="Just a heading", company=_COMPANY)
    html = rendered.html
    assert "Just a heading" in html
    # No stray empty paragraph/button markup when nothing was supplied.
    assert "<a href" not in html
    assert html.count("<tr>") >= 2  # header + heading + footer


async def test_preheader_defaults_to_the_first_paragraph():
    rendered = await _render(paragraphs=["First line wins.", "Second."])
    assert "First line wins." in rendered.html
    # Hidden preview block, not a visible duplicate.
    assert "display:none" in rendered.html


async def test_explicit_preheader_overrides_the_default():
    rendered = await _render(preheader="Action needed on your documents")
    assert "Action needed on your documents" in rendered.html


async def test_subtitle_renders_under_the_logo():
    assert "Ride Receipt" in (await _render(subtitle="Ride Receipt")).html


async def test_html_is_a_complete_document():
    html = (await _render()).html
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "</html>" in html
    assert 'meta name="viewport"' in html


# --- render_from_text ------------------------------------------------------
# The bridge for senders whose copy was authored as plain text (KYB decisions,
# ops alerts, admin broadcasts, statements, tax/DSAR exports). Its whole value
# is giving them the logo and the configured footer *without* editing a word of
# what they say — so these pin both halves of that.


async def test_render_from_text_splits_paragraphs_on_blank_lines():
    rendered = await layout.render_from_text(
        heading="Your statement is ready",
        body="First paragraph.\n\nSecond paragraph.",
        company=_COMPANY,
    )
    assert "First paragraph." in rendered.html
    assert "Second paragraph." in rendered.html
    # Two <p> blocks, not one run-together block.
    assert rendered.html.count("First paragraph.</p>") == 1
    assert rendered.html.count("Second paragraph.</p>") == 1


async def test_render_from_text_keeps_single_newlines_inside_a_paragraph():
    # An address block or short list must not be exploded into separate blocks.
    rendered = await layout.render_from_text(
        heading="Invite",
        body="123 Example St\nSaskatoon, SK",
        company=_COMPANY,
    )
    # Both lines land in one block, contiguously — not split across two <p>s.
    assert "123 Example St\nSaskatoon, SK" in rendered.html


async def test_render_from_text_carries_the_logo_and_footer():
    rendered = await layout.render_from_text(heading="Hi", body="Body copy.", company=_COMPANY)
    assert _COMPANY.logo_url in rendered.html
    assert _COMPANY.identity_line in rendered.html
    assert _COMPANY.identity_line in rendered.text


async def test_render_from_text_escapes_the_body():
    rendered = await layout.render_from_text(
        heading="Broadcast",
        body="<script>alert(1)</script>",
        company=_COMPANY,
    )
    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html


async def test_render_from_text_tolerates_an_empty_body():
    # A sender that computed an empty body should still produce a branded
    # shell rather than raising inside a best-effort notification path.
    rendered = await layout.render_from_text(heading="Notice", body="", company=_COMPANY)
    assert "Notice" in rendered.html
    assert _COMPANY.identity_line in rendered.html


async def test_line_breaks_inside_a_paragraph_survive_into_html():
    # The DSAR export email lists the files in the archive one per line. HTML
    # collapses a newline to a space, so without this the plain-text version
    # would keep its shape and the HTML would render one run-on line.
    rendered = await _render(paragraphs=["The archive contains:\n• rides.csv\n• payouts.csv"])
    assert "The archive contains:<br>• rides.csv<br>• payouts.csv" in rendered.html
    assert "• rides.csv\n• payouts.csv" in rendered.text


async def test_a_br_cannot_be_forged_by_caller_text():
    # Escaping runs before newlines become <br>, so caller text saying "<br>"
    # stays visible text rather than becoming markup.
    rendered = await _render(paragraphs=["literal <br> tag"])
    assert "literal &lt;br&gt; tag" in rendered.html


async def test_column_alignment_survives_into_html():
    # The safety-team incident alert and the driver statement pad with spaces
    # to line up their columns. They were read as plain text before an HTML
    # part existed; HTML collapses a run of spaces, so without preserving them
    # adding HTML would have made those emails worse than sending none.
    rendered = await _render(paragraphs=["Ride ID:   abc123\nCreated:   now"])
    assert "Ride ID:&nbsp;&nbsp;&nbsp;abc123" in rendered.html
    assert "Ride ID:   abc123" in rendered.text


async def test_a_single_space_is_left_alone():
    # Only runs are preserved — ordinary prose must still wrap normally.
    rendered = await _render(paragraphs=["one two three"])
    assert "one two three" in rendered.html
    assert "&nbsp;" not in rendered.html
