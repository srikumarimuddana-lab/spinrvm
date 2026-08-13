"""Shared branded layout for Spinr transactional email.

Every transactional email renders through :func:`render_email`, which returns
**both** an HTML body and a plain-text alternative so
``email_provider.send_transactional_email`` can build a real
``multipart/alternative``.

Three things this module exists to stop happening again:

1. **No logo.** Before this, no Spinr email contained the Spinr mark — the two
   emails with any branding rendered the wordmark as ``<h1>Spinr</h1>`` text.
   The header here uses the real asset, served by ``routes/branding.py``.
2. **Divergent branding.** Each email invented its own shell (or had none). The
   brand tokens below are the single source for email.
3. **HTML injection.** Admin-authored free text (a suspension or rejection
   reason) reaches the recipient through these templates. Every caller-supplied
   value is escaped here, so no call site has to remember to.

Layout structure
----------------
Modelled on the receipt layout Uber uses, because it solves a problem the first
version of this module got wrong:

- **A light header band, not a brand-coloured one.** ``spinr_logo.png`` is a
  charcoal wordmark whose "o" is a *red* spiral. Rendered on a red band the
  spiral disappeared into the background and the charcoal went muddy — the mark
  is drawn for a light ground. Brand presence comes from a rule above the band
  and from the accent colour on buttons, not from drowning the logo.
- **The headline lives in the header**, at display size, immediately under the
  logo — so the email states what it is before any body copy.
- **A dark footer** carrying the legal name and the mailing address on separate
  lines. That is both the conventional receipt shape and what the admin
  Settings page actually stores.

The logo is deliberately **not** repeated in the footer: the asset is dark-on-
transparent and would be invisible there, and inventing a light variant is a
design decision for a person, not a default to ship quietly.
"""

from __future__ import annotations

import html
import re
from typing import Iterable, NamedTuple, Optional, Sequence, Tuple

try:
    from ..utils.company_details import CompanyDetails, load_company_details
    from ..utils.report_branding import COMPANY_CONTACT_LINE, COMPANY_LINE
except ImportError:  # pragma: no cover - direct module imports in tests
    from utils.company_details import CompanyDetails, load_company_details  # type: ignore
    from utils.report_branding import COMPANY_CONTACT_LINE, COMPANY_LINE  # type: ignore

# --- Brand tokens -----------------------------------------------------------
# Source: .claude/context/brand-spinr.md, which in turn mirrors
# shared/theme/index.ts. Defined once here, never re-stated per template.
BRAND_RED = "#FF3B30"
# primaryDark — for text/buttons that need WCAG AA contrast on white. The
# full-strength brand red does not pass AA as body text.
BRAND_RED_CONTRAST = "#D32F2F"
INK = "#1A1A1A"
MUTED = "#6B7280"
BORDER = "#E5E7EB"
SURFACE = "#FFFFFF"
PAGE_BG = "#F0F0F0"

#: Header band. Light by necessity, not by taste — see the module docstring:
#: the logo's red spiral is invisible on a red ground.
HEADER_BG = "#F2F2F2"
#: Small print in the header (the date block).
HEADER_META = "#6B6B6B"

#: Footer band. Dark, so the email closes on something solid.
FOOTER_BG = "#101010"
FOOTER_NAME = "#FFFFFF"
FOOTER_TEXT = "#A6A6A6"

# Plus Jakarta Sans is the brand face, but webfonts are unreliable in email
# (Outlook desktop ignores @font-face entirely), so it leads a system stack
# rather than being loaded. This degrades by design, not by omission.
FONT_STACK = "'Plus Jakarta Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"

# Rendered width. 600px is the widely-supported email standard; Uber's own is
# 700, which is wider than several clients handle gracefully.
_MAX_WIDTH_PX = 600
# Logo intrinsic size is 768x312; 132px wide keeps it crisp at 2x.
_LOGO_WIDTH_PX = 132
# Horizontal padding, shrunk on narrow screens by the media query below.
_PAD_X = 40


class RenderedEmail(NamedTuple):
    """An email body in both representations required for multipart/alternative."""

    html: str
    text: str


def _esc(value: object) -> str:
    """Escape a caller-supplied value for HTML interpolation.

    ``quote=True`` so values are also safe inside attributes.
    """
    return html.escape(str(value if value is not None else ""), quote=True)


def _esc_multiline(value: object) -> str:
    """Escape a paragraph, keeping the line breaks the caller wrote.

    HTML collapses a newline to a space, so an address block or a list of the
    files in a data export would render as one run-on line — while the plain-
    text alternative kept its shape, making the two versions disagree.

    ``<br>`` rather than ``white-space:pre-line`` because Outlook's Word
    rendering engine handles the property inconsistently, and a list that
    silently un-wraps in one major client is the failure this avoids.

    Runs of spaces are preserved for the same reason. Several bodies routed
    through here are column-aligned with padding — the safety-team incident
    alert ("Ride ID:   …"), the driver statement's indented totals — and they
    used to be read as plain text, where the alignment held. HTML collapses a
    run of spaces to one, so without this, adding an HTML part would have made
    those emails *worse* than sending none.

    Escaping happens first, so neither the ``<br>`` nor the entity can be
    forged by caller text.
    """
    escaped = _esc(value).replace("\n", "<br>")
    return re.sub(r"  +", lambda m: "&nbsp;" * len(m.group()), escaped)


def header_html(
    company: CompanyDetails,
    subtitle: Optional[str] = None,
    *,
    heading: Optional[str] = None,
    intro: Optional[str] = None,
    meta_lines: Sequence[str] = (),
) -> str:
    """Brand rule, logo, optional date block, and the headline.

    Args:
        company: Resolved identity — supplies the logo URL and the name.
        subtitle: Small label above the headline, e.g. "Ride Receipt".
            Positional for the two call sites that already pass it this way.
        heading: The headline, at display size.
        intro: One line under the headline.
        meta_lines: Right-aligned small print, e.g. ("Aug 6, 2026", "8:31 pm").

    Many clients (Outlook, and Gmail before the sender is trusted) block remote
    images by default, so the ``alt`` text is styled to render as a bold
    wordmark at logo size. It carries the *configured* company name, so a
    rebrand does not leave "Spinr" behind in the one place nobody thinks to
    look.
    """
    meta = "".join(
        f'<div style="color:{HEADER_META};font-size:12px;line-height:18px;">{_esc(line)}</div>'
        for line in meta_lines
        if line
    )

    blocks = [
        f"""
        <tr><td style="background:{HEADER_BG};padding:28px {_PAD_X}px 0;" class="px">
          <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
            <tr>
              <td align="left" style="vertical-align:middle;">
                <img src="{_esc(company.logo_url)}" alt="{_esc(company.name)}" width="{_LOGO_WIDTH_PX}"
                     style="display:block;border:0;outline:none;text-decoration:none;
                            width:{_LOGO_WIDTH_PX}px;max-width:100%;height:auto;
                            color:{INK};font-size:24px;font-weight:800;letter-spacing:-0.5px;"/>
              </td>
              <td align="right" style="vertical-align:top;text-align:right;">{meta}</td>
            </tr>
          </table>
        </td></tr>"""
    ]

    if subtitle:
        blocks.append(
            f'<tr><td style="background:{HEADER_BG};padding:24px {_PAD_X}px 0;" class="px">'
            f'<div style="color:{HEADER_META};font-size:12px;font-weight:600;'
            f'letter-spacing:0.08em;text-transform:uppercase;">{_esc(subtitle)}</div></td></tr>'
        )

    if heading:
        pad_top = "8px" if subtitle else "28px"
        blocks.append(
            f'<tr><td style="background:{HEADER_BG};padding:{pad_top} {_PAD_X}px 0;" class="px">'
            f'<h1 class="h1" style="color:{INK};font-size:30px;line-height:38px;font-weight:700;'
            f'margin:0;letter-spacing:-0.5px;word-wrap:break-word;">{_esc(heading)}</h1></td></tr>'
        )

    if intro:
        blocks.append(
            f'<tr><td style="background:{HEADER_BG};padding:12px {_PAD_X}px 0;" class="px">'
            f'<div style="color:{MUTED};font-size:16px;line-height:26px;">{_esc(intro)}</div></td></tr>'
        )

    # Closes the band. Without it the last block's background stops at its own
    # padding and the band looks clipped.
    blocks.append(f'<tr><td style="background:{HEADER_BG};height:32px;font-size:0;line-height:0;">&nbsp;</td></tr>')
    return "".join(blocks)


def _brand_rule_html() -> str:
    """The one piece of full-strength brand red in the chrome.

    Sits above the header band. The logo cannot supply the brand colour on its
    own here — it is mostly charcoal — and a coloured band is what broke the
    mark in the first place, so the rule carries it instead.
    """
    return f'<tr><td style="background:{BRAND_RED};height:4px;font-size:0;line-height:0;">&nbsp;</td></tr>'


def _body_html(
    greeting: Optional[str],
    paragraphs: Sequence[str],
    cta: Optional[Tuple[str, str]],
    footnote: Optional[str],
) -> str:
    parts: list[str] = []
    first_pad = "32px"

    if greeting:
        parts.append(
            f'<tr><td style="padding:{first_pad} {_PAD_X}px 0;" class="px">'
            f'<p style="color:{INK};font-size:16px;line-height:24px;margin:0;">{_esc(greeting)}</p></td></tr>'
        )
        first_pad = "16px"

    for para in paragraphs:
        parts.append(
            f'<tr><td style="padding:{first_pad} {_PAD_X}px 0;" class="px">'
            f'<p style="color:{MUTED};font-size:15px;line-height:24px;margin:0;">{_esc_multiline(para)}</p></td></tr>'
        )
        first_pad = "14px"

    if cta:
        label, url = cta
        parts.append(
            f'<tr><td style="padding:28px {_PAD_X}px 0;" class="px">'
            f'<a href="{_esc(url)}" style="display:inline-block;background:{BRAND_RED_CONTRAST};'
            f"color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;"
            f'padding:13px 28px;border-radius:8px;">{_esc(label)}</a></td></tr>'
        )

    if footnote:
        parts.append(
            f'<tr><td style="padding:24px {_PAD_X}px 0;" class="px">'
            f'<p style="color:{MUTED};font-size:12px;line-height:18px;margin:0;">{_esc(footnote)}</p></td></tr>'
        )

    parts.append('<tr><td style="height:32px;font-size:0;line-height:0;">&nbsp;</td></tr>')
    return "".join(parts)


def footer_html(company: CompanyDetails) -> str:
    """Legal name and mailing address, from the admin Settings page.

    The name sits on its own line with the address beneath it, one line per
    part — the conventional receipt shape, and the one the Settings page's
    fields already describe. The single comma-joined ``identity_line`` reads as
    a run-on at this type size, so it is kept for the plain-text alternative
    and the PDF header instead.
    """
    lines = company.address_lines or ((company.address,) if company.address else ())
    address = "".join(
        f'<div style="color:{FOOTER_TEXT};font-size:13px;line-height:20px;">{_esc(line)}</div>' for line in lines
    )
    contact = (
        f'<div style="color:{FOOTER_TEXT};font-size:13px;line-height:20px;padding-top:12px;">'
        f"{_esc(company.contact_line)}</div>"
        if company.contact_line
        else ""
    )
    return f"""
        <tr><td style="background:{FOOTER_BG};padding:28px {_PAD_X}px;" class="px">
          <div style="color:{FOOTER_NAME};font-size:14px;line-height:22px;font-weight:600;">{_esc(company.name)}</div>
          {address}
          {contact}
        </td></tr>"""


def _preheader_html(preheader: Optional[str]) -> str:
    """Inbox preview text. Hidden in the rendered body but read by the client.

    Without it, clients scrape the first visible text — which for a logo-led
    layout is whatever the alt text and subtitle happen to be.
    """
    if not preheader:
        return ""
    return f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all;">{_esc(preheader)}</div>'


#: Responsive and dark-mode overrides.
#:
#: The header band is pinned light in dark mode on purpose. The logo is
#: charcoal-on-transparent, so a dark band would erase it — the same failure as
#: the red band, arrived at from the other direction. Fixing it properly needs
#: a light-on-dark variant of the asset, which is a design decision for a
#: person to make, not something to ship silently.
_HEAD_STYLE = """
    <style type="text/css">
      body { margin:0; padding:0; -webkit-text-size-adjust:100%; -ms-text-size-adjust:100%; }
      table, td { border-collapse:collapse; mso-table-lspace:0; mso-table-rspace:0; }
      @media only screen and (max-width:620px) {
        .px { padding-left:24px !important; padding-right:24px !important; }
        .h1 { font-size:24px !important; line-height:31px !important; }
      }
      @media (prefers-color-scheme: dark) {
        .page { background:#0E0E0E !important; }
        .card { background:#1C1C1C !important; }
        .card p { color:#B4B4B4 !important; }
      }
    </style>"""


async def render_email(
    *,
    heading: Optional[str] = None,
    paragraphs: Iterable[str] = (),
    greeting: Optional[str] = None,
    subtitle: Optional[str] = None,
    cta: Optional[Tuple[str, str]] = None,
    footnote: Optional[str] = None,
    preheader: Optional[str] = None,
    intro: Optional[str] = None,
    meta_lines: Sequence[str] = (),
    company: Optional[CompanyDetails] = None,
) -> RenderedEmail:
    """Render one branded email in HTML and plain text.

    Async because the logo and the footer's company identity come from the
    admin Settings page, not from constants. Loading them here rather than
    asking each call site to pass them is deliberate: a caller that forgot
    would silently ship a stale footer, and there is no compiler or reviewer
    that catches a missing keyword argument with a sensible default.

    Args:
        heading: The headline. Rendered in the header band at display size, so
            the email says what it is before any body copy.
        paragraphs: Body copy, one string per paragraph.
        greeting: Optional salutation, e.g. "Hi Sarah,".
        subtitle: Optional small label above the headline, e.g. "Ride Receipt".
        cta: Optional ``(label, url)`` for a single call-to-action button.
        footnote: Optional small print below the body.
        preheader: Optional inbox preview text; falls back to the first paragraph.
        intro: Optional line under the headline, still inside the header band.
        meta_lines: Optional right-aligned small print in the header, e.g. a
            date and time.
        company: Pre-loaded identity, when the caller already resolved it (e.g.
            to interpolate the support address into its copy). Skips a second
            settings read; ``get_app_settings`` caches for 60 s either way.

    Returns:
        A :class:`RenderedEmail` — pass ``.html`` and ``.text`` straight to
        ``send_transactional_email`` so it builds a multipart/alternative.

    Every value is HTML-escaped; callers pass raw text, never markup.
    """
    details = company if company is not None else await load_company_details()
    paras = [p for p in paragraphs if p]
    preview = preheader or intro or (paras[0] if paras else heading)

    body = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark">
{_HEAD_STYLE}
</head>
<body class="page" style="margin:0;padding:0;background:{PAGE_BG};font-family:{FONT_STACK};">
{_preheader_html(preview)}
    <table class="page" width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background:{PAGE_BG};">
      <tr><td align="center" style="padding:24px 0;">
        <table class="card" width="100%" cellpadding="0" cellspacing="0" role="presentation"
               style="max-width:{_MAX_WIDTH_PX}px;background:{SURFACE};border-radius:14px;overflow:hidden;">
{_brand_rule_html()}
{header_html(details, subtitle, heading=heading, intro=intro, meta_lines=meta_lines)}
{_body_html(greeting, paras, cta, footnote)}
{footer_html(details)}
        </table>
      </td></tr>
    </table>
</body>
</html>
"""

    return RenderedEmail(
        html=body,
        text=render_text(
            heading=heading,
            paragraphs=paras,
            greeting=greeting,
            cta=cta,
            footnote=footnote,
            intro=intro,
            company=details,
        ),
    )


async def render_from_text(
    *,
    heading: str,
    body: str,
    company: Optional[CompanyDetails] = None,
) -> RenderedEmail:
    """Wrap an already-written plain-text body in the shared branded shell.

    The bridge for senders whose copy was authored as plain text — corporate
    KYB decisions, ops alerts, admin broadcasts, driver statements, tax and
    DSAR exports. Rewriting each one's copy into structured paragraphs would be
    a bigger, riskier change than they deserve; this gives them the logo and
    the configured company details without touching a word of what they say.

    Paragraphs split on blank lines, matching how these bodies are written. A
    single newline inside a paragraph is kept as a line break in both
    representations (see :func:`_esc_multiline`), so an address block or a
    short list holds its shape.
    """
    paragraphs = [p.strip() for p in (body or "").split("\n\n") if p.strip()]
    return await render_email(heading=heading, paragraphs=paragraphs, company=company)


def render_text(
    *,
    heading: Optional[str] = None,
    paragraphs: Sequence[str] = (),
    greeting: Optional[str] = None,
    cta: Optional[Tuple[str, str]] = None,
    footnote: Optional[str] = None,
    intro: Optional[str] = None,
    company: Optional[CompanyDetails] = None,
) -> str:
    """Plain-text alternative for the same content.

    Not a stripped-HTML afterthought: it is what recipients on text-only
    clients, screen readers, and blocked-image views actually read, and its
    presence materially improves inbox placement.

    Stays synchronous: ``render_email`` passes the identity it already loaded.
    Called directly without ``company``, it falls back to the static constants
    rather than reaching for settings from a sync context.
    """
    lines: list[str] = []
    if greeting:
        lines.append(greeting)
        lines.append("")
    if heading:
        lines.append(heading)
        lines.append("")
    if intro:
        lines.append(intro)
        lines.append("")
    for para in paragraphs:
        lines.append(para)
        lines.append("")
    if cta:
        label, url = cta
        lines.append(f"{label}: {url}")
        lines.append("")
    if footnote:
        lines.append(footnote)
        lines.append("")
    lines.append("--")
    if company is not None:
        lines.append(company.identity_line)
        lines.append(company.contact_line)
    else:
        lines.append(COMPANY_LINE)
        lines.append(COMPANY_CONTACT_LINE)
    return "\n".join(lines).strip() + "\n"
