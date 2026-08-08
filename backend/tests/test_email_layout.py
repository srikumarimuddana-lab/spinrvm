"""Tests for the shared branded email layout (utils/email_layout.py).

These exist because, before this module, **no test in the repo asserted anything
about email appearance** — the receipt template's colours, header, and footer
could all be silently broken without a single failure. The branding assertions
below are the regression net for that.
"""

import pytest

from utils.email_layout import (
    BRAND_RED,
    BRAND_RED_CONTRAST,
    RenderedEmail,
    logo_url,
    render_email,
    render_text,
)
from utils.report_branding import COMPANY_CONTACT_LINE, COMPANY_LINE

pytestmark = [pytest.mark.unit]


def _render(**kwargs) -> RenderedEmail:
    kwargs.setdefault("heading", "Your account was approved")
    kwargs.setdefault("paragraphs", ["You can now go online and accept rides."])
    return render_email(**kwargs)


# --- Branding -------------------------------------------------------------


def test_logo_url_is_absolute_and_points_at_the_branding_route():
    # Relative paths resolve against the mail client, not the API.
    url = logo_url()
    assert url.startswith("https://")
    assert url.endswith("/api/v1/branding/spinr-logo.png")


def test_html_embeds_the_logo_image():
    html = _render().html
    assert f'src="{logo_url()}"' in html
    assert "<img" in html


def test_logo_has_alt_text_for_blocked_images():
    # Outlook and untrusted-sender Gmail block remote images by default; the
    # alt text is the only branding those recipients see.
    assert 'alt="Spinr"' in _render().html


def test_uses_the_documented_brand_red():
    html = _render(cta=("Open Spinr", "https://spinr.ca")).html
    assert BRAND_RED == "#FF3B30", "brand red must match .claude/context/brand-spinr.md"
    assert BRAND_RED in html
    # Buttons use the AA-contrast variant, not full-strength red.
    assert BRAND_RED_CONTRAST in html


def test_footer_reuses_the_shared_company_lines():
    # Same constants the report PDFs use, so email and PDF cannot drift apart.
    html = _render().html
    assert COMPANY_LINE in html
    assert COMPANY_CONTACT_LINE in html


# --- Plain-text alternative ------------------------------------------------


def test_returns_a_non_empty_text_alternative():
    # send_transactional_email only builds multipart/alternative when both are
    # present; an empty text part silently drops back to html-only.
    rendered = _render()
    assert rendered.text.strip()
    assert "Your account was approved" in rendered.text
    assert "You can now go online and accept rides." in rendered.text


def test_text_alternative_carries_the_footer_and_cta_url():
    text = render_text(
        heading="Documents expiring",
        paragraphs=["Your insurance expires in 3 days."],
        cta=("Upload now", "https://spinr.ca/documents"),
    )
    assert "Upload now: https://spinr.ca/documents" in text
    assert COMPANY_LINE in text


def test_text_alternative_contains_no_markup():
    text = _render(cta=("Open", "https://spinr.ca")).text
    assert "<" not in text and ">" not in text


# --- Escaping (admin free text reaches recipients through here) ------------


def test_escapes_html_in_body_copy():
    # A suspension/rejection reason is admin-authored free text.
    rendered = _render(paragraphs=['<script>alert("x")</script> & more'])
    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html


def test_escapes_quotes_in_cta_url_attribute():
    rendered = _render(cta=("Go", 'https://x.test/"onmouseover="alert(1)'))
    assert '"onmouseover="' not in rendered.html
    assert "&quot;" in rendered.html


def test_escapes_heading_and_greeting():
    rendered = _render(heading="<b>Approved</b>", greeting="Hi <em>Sam</em>,")
    assert "<b>Approved</b>" not in rendered.html
    assert "<em>Sam</em>" not in rendered.html


# --- Structure -------------------------------------------------------------


def test_optional_blocks_are_omitted_cleanly():
    rendered = render_email(heading="Just a heading")
    html = rendered.html
    assert "Just a heading" in html
    # No stray empty paragraph/button markup when nothing was supplied.
    assert "<a href" not in html
    assert html.count("<tr>") >= 2  # header + heading + footer


def test_preheader_defaults_to_the_first_paragraph():
    rendered = _render(paragraphs=["First line wins.", "Second."])
    assert "First line wins." in rendered.html
    # Hidden preview block, not a visible duplicate.
    assert "display:none" in rendered.html


def test_explicit_preheader_overrides_the_default():
    rendered = _render(preheader="Action needed on your documents")
    assert "Action needed on your documents" in rendered.html


def test_subtitle_renders_under_the_logo():
    assert "Ride Receipt" in _render(subtitle="Ride Receipt").html


def test_html_is_a_complete_document():
    html = _render().html
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "</html>" in html
    assert 'meta name="viewport"' in html
