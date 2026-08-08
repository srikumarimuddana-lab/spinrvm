"""Shared branded layout for Spinr transactional email.

Every new transactional email renders through :func:`render_email`, which
returns **both** an HTML body and a plain-text alternative so
``email_provider.send_transactional_email`` can build a real
``multipart/alternative``. (The existing ride receipt sends HTML only; that is a
separate, flagged retrofit — see docs/notification-channel-coverage.md.)

Three things this module exists to stop happening again:

1. **No logo.** Before this, no Spinr email contained the Spinr mark — the two
   emails with any branding rendered the wordmark as ``<h1>Spinr</h1>`` text.
   The header here uses the real asset, served by ``routes/branding.py``.
2. **Divergent branding.** Each email invented its own shell (or had none). The
   brand tokens below are the single source for email; the footer reuses
   ``report_branding``'s company lines so email and report PDFs cannot drift.
3. **HTML injection.** Admin-authored free text (a suspension or rejection
   reason) reaches the recipient through these templates. Every caller-supplied
   value is escaped here, so no call site has to remember to.

Colour note: this module uses the documented brand red ``#FF3B30``
(.claude/context/brand-spinr.md). The live ride receipt and Spinr Pass invoice
still use ``#ee2b2b`` — deliberately left alone rather than silently restyled
under riders who are already receiving them.
"""

from __future__ import annotations

import html
from typing import Iterable, NamedTuple, Optional, Sequence, Tuple

try:
    from ..core.config import settings as _cfg
    from ..utils.report_branding import COMPANY_CONTACT_LINE, COMPANY_LINE
except ImportError:  # pragma: no cover - direct module imports in tests
    from core.config import settings as _cfg  # type: ignore
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
PAGE_BG = "#F5F5F5"

# Plus Jakarta Sans is the brand face, but webfonts are unreliable in email
# (Outlook desktop ignores @font-face entirely), so it leads a system stack
# rather than being loaded. This degrades by design, not by omission.
FONT_STACK = "'Plus Jakarta Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"

# Rendered width. Matches the existing receipt so the two read as one family
# once the receipt is migrated.
_MAX_WIDTH_PX = 520
# Logo intrinsic size is 768x312 (2x for print); halve it for a crisp retina
# render at 160px wide.
_LOGO_WIDTH_PX = 160


class RenderedEmail(NamedTuple):
    """An email body in both representations required for multipart/alternative."""

    html: str
    text: str


def logo_url() -> str:
    """Absolute URL of the Spinr mark served by ``routes/branding.py``.

    Absolute because an email is read outside any origin — a relative path
    resolves against the mail client, not the API.
    """
    base = (_cfg.PUBLIC_API_BASE_URL or "").rstrip("/")
    return f"{base}/api/v1/branding/spinr-logo.png"


def _esc(value: object) -> str:
    """Escape a caller-supplied value for HTML interpolation.

    ``quote=True`` so values are also safe inside attributes.
    """
    return html.escape(str(value if value is not None else ""), quote=True)


def _header_html(subtitle: Optional[str]) -> str:
    """Brand band with the logo.

    Many clients (Outlook, and Gmail before the sender is trusted) block remote
    images by default. The ``alt`` text is therefore styled to render as a white
    bold wordmark at logo size, so a blocked image degrades to something that
    still reads as Spinr branding rather than a broken-image icon.
    """
    sub = (
        f'<p style="color:rgba(255,255,255,0.85);margin:8px 0 0;font-size:14px;">{_esc(subtitle)}</p>'
        if subtitle
        else ""
    )
    return f"""
        <tr><td style="background:{BRAND_RED};padding:28px 24px;text-align:center;">
          <img src="{logo_url()}" alt="Spinr" width="{_LOGO_WIDTH_PX}"
               style="display:inline-block;border:0;outline:none;text-decoration:none;
                      width:{_LOGO_WIDTH_PX}px;max-width:100%;height:auto;
                      color:#ffffff;font-size:28px;font-weight:800;letter-spacing:-0.5px;"/>
          {sub}
        </td></tr>"""


def _body_html(
    greeting: Optional[str],
    heading: Optional[str],
    paragraphs: Sequence[str],
    cta: Optional[Tuple[str, str]],
    footnote: Optional[str],
) -> str:
    parts: list[str] = []

    if greeting:
        parts.append(
            f'<tr><td style="padding:28px 24px 0;">'
            f'<p style="color:{INK};font-size:16px;margin:0;">{_esc(greeting)}</p></td></tr>'
        )

    if heading:
        pad = "16px" if greeting else "28px"
        parts.append(
            f'<tr><td style="padding:{pad} 24px 0;">'
            f'<h1 style="color:{INK};font-size:22px;font-weight:700;margin:0;'
            f'line-height:1.3;letter-spacing:-0.3px;">{_esc(heading)}</h1></td></tr>'
        )

    for para in paragraphs:
        parts.append(
            f'<tr><td style="padding:14px 24px 0;">'
            f'<p style="color:{MUTED};font-size:15px;line-height:1.6;margin:0;">{_esc(para)}</p></td></tr>'
        )

    if cta:
        label, url = cta
        parts.append(
            f'<tr><td style="padding:24px 24px 0;text-align:center;">'
            f'<a href="{_esc(url)}" style="display:inline-block;background:{BRAND_RED_CONTRAST};'
            f"color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;"
            f'padding:13px 28px;border-radius:10px;">{_esc(label)}</a></td></tr>'
        )

    if footnote:
        parts.append(
            f'<tr><td style="padding:20px 24px 0;">'
            f'<p style="color:{MUTED};font-size:12px;line-height:1.5;margin:0;">{_esc(footnote)}</p></td></tr>'
        )

    return "".join(parts)


def _footer_html() -> str:
    """Company identity, reusing the constants the report PDFs already share."""
    return f"""
        <tr><td style="padding:28px 24px 24px;text-align:center;border-top:1px solid {BORDER};">
          <p style="color:{MUTED};font-size:12px;margin:24px 0 0;">{_esc(COMPANY_LINE)}</p>
          <p style="color:{MUTED};font-size:11px;margin:4px 0 0;">{_esc(COMPANY_CONTACT_LINE)}</p>
        </td></tr>"""


def _preheader_html(preheader: Optional[str]) -> str:
    """Inbox preview text. Hidden in the rendered body but read by the client.

    Without it, clients scrape the first visible text — which for a logo-led
    layout is whatever the alt text and subtitle happen to be.
    """
    if not preheader:
        return ""
    return f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all;">{_esc(preheader)}</div>'


def render_email(
    *,
    heading: Optional[str] = None,
    paragraphs: Iterable[str] = (),
    greeting: Optional[str] = None,
    subtitle: Optional[str] = None,
    cta: Optional[Tuple[str, str]] = None,
    footnote: Optional[str] = None,
    preheader: Optional[str] = None,
) -> RenderedEmail:
    """Render one branded email in HTML and plain text.

    Args:
        heading: Bold line under the header, e.g. "Your documents expire soon".
        paragraphs: Body copy, one string per paragraph.
        greeting: Optional salutation, e.g. "Hi Sarah,".
        subtitle: Optional label under the logo, e.g. "Ride Receipt".
        cta: Optional ``(label, url)`` for a single call-to-action button.
        footnote: Optional small print below the body.
        preheader: Optional inbox preview text; falls back to the first paragraph.

    Returns:
        A :class:`RenderedEmail` — pass ``.html`` and ``.text`` straight to
        ``send_transactional_email`` so it builds a multipart/alternative.

    Every value is HTML-escaped; callers pass raw text, never markup.
    """
    paras = [p for p in paragraphs if p]
    preview = preheader or (paras[0] if paras else heading)

    body = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:{PAGE_BG};font-family:{FONT_STACK};">
{_preheader_html(preview)}
    <table width="100%" cellpadding="0" cellspacing="0" role="presentation"
           style="max-width:{_MAX_WIDTH_PX}px;margin:20px auto;background:{SURFACE};
                  border-radius:16px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
{_header_html(subtitle)}
{_body_html(greeting, heading, paras, cta, footnote)}
{_footer_html()}
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
        ),
    )


def render_text(
    *,
    heading: Optional[str] = None,
    paragraphs: Sequence[str] = (),
    greeting: Optional[str] = None,
    cta: Optional[Tuple[str, str]] = None,
    footnote: Optional[str] = None,
) -> str:
    """Plain-text alternative for the same content.

    Not a stripped-HTML afterthought: it is what recipients on text-only
    clients, screen readers, and blocked-image views actually read, and its
    presence materially improves inbox placement.
    """
    lines: list[str] = []
    if greeting:
        lines.append(greeting)
        lines.append("")
    if heading:
        lines.append(heading)
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
    lines.append(COMPANY_LINE)
    lines.append(COMPANY_CONTACT_LINE)
    return "\n".join(lines).strip() + "\n"
