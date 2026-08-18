"""Company identity for transactional email, sourced from admin settings.

The footer on a Spinr email used to be two hardcoded constants. That is fine
for a report PDF filed once, but wrong for email: the legal name, mailing
address, support address and website are exactly the things that change without
a code deploy — a move, a rebrand, a new support alias — and an email carrying a
stale address is a compliance problem, not a cosmetic one.

These now come from the ``settings`` row the admin dashboard already edits
("Company Info (shown in apps)" on the Settings page), the same source
``/api/company-info`` serves to the rider and driver apps.

**Every field falls back to the previously-hardcoded value.** An unconfigured
setting reproduces today's output byte-for-byte, so wiring this up cannot
silently blank a footer.

Scope: transactional email only. ``report_branding.py`` keeps feeding the PDF,
Excel and Word report headers from its own constants — those documents have
already been handed to SGI and airport authorities, and changing them under an
admin's keystroke is a different decision (see the 2026-08-08 change log).
"""

from __future__ import annotations

import logging
from typing import NamedTuple, Optional, Tuple
from urllib.parse import urlparse

try:
    from ..core.config import settings as _cfg
    from ..settings_loader import get_app_settings
    from ..utils.address_format import address_lines as _address_lines
    from ..utils.address_format import coalesce_setting as _coalesce
    from ..utils.address_format import postal_address as _postal_address
    from ..utils.report_branding import COMPANY_CONTACT_LINE, COMPANY_LINE
except ImportError:  # pragma: no cover - direct module imports in tests
    from core.config import settings as _cfg  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.address_format import address_lines as _address_lines  # type: ignore
    from utils.address_format import coalesce_setting as _coalesce  # type: ignore
    from utils.address_format import postal_address as _postal_address  # type: ignore
    from utils.report_branding import COMPANY_CONTACT_LINE, COMPANY_LINE  # type: ignore

logger = logging.getLogger(__name__)

# Last-resort support address, matching the one baked into COMPANY_CONTACT_LINE.
_DEFAULT_SUPPORT_EMAIL = "support@spinr.ca"


class CompanyDetails(NamedTuple):
    """Everything an email footer needs, already resolved and fallback-applied."""

    name: str
    #: Legal name + mailing address, e.g. "Spinr Technologies Inc. — 123 Main St, Saskatoon, SK".
    identity_line: str
    #: Mailing address on its own, for layouts that print the name separately
    #: (the invoice PDF's header block). Empty when none is configured.
    address: str
    #: Support address, website and phone, joined for display.
    contact_line: str
    #: Support address on its own, for body copy that tells the reader where to write.
    support_email: str
    #: Absolute URL of the logo to render in the header.
    logo_url: str
    #: Product/brand name for email BODY copy ("Open the {app_name} driver
    #: app", "your {app_name} wallet"), independent of the legal entity
    #: ``name`` above. Falls back to "Spinr" — see ``company_app_name`` in
    #: ``schemas.AppSettings`` and ACTION_ITEMS.md N17. Defaulted (rather than
    #: required) so existing keyword-only ``CompanyDetails(...)`` test
    #: fixtures built before this field existed keep constructing.
    app_name: str = "Spinr"
    #: The mailing address split into display lines — street, then locality.
    #: The email footer prints one per line (the conventional receipt shape)
    #: rather than the comma-joined ``address`` above, which reads as a
    #: run-on at footer type sizes. Defaulted for the same reason as
    #: ``app_name``: fixtures built before it existed keep constructing.
    address_lines: Tuple[str, ...] = ()

    @property
    def name_sentence(self) -> str:
        """The company name, safe to end a sentence with.

        A configured legal name usually already ends in a period ("… Inc."),
        and copy that appends its own produces "Spinr Technologies Inc..".
        Every template that ends a sentence with the name must use this rather
        than remembering the rule — it is not the kind of thing anyone notices
        in review, and it is now in front of customers on every email.
        """
        return self.name.rstrip(".") + "."


def _bundled_logo_url() -> str:
    """The Spinr mark served by ``routes/branding.py``.

    Absolute because an email is read outside any origin — a relative path
    resolves against the mail client, not the API.
    """
    base = (_cfg.PUBLIC_API_BASE_URL or "").rstrip("/")
    return f"{base}/api/v1/branding/spinr-logo.png"


def _safe_logo_url(raw: str) -> Optional[str]:
    """Accept an admin-supplied logo URL only if it is absolute http(s).

    An admin is trusted, but this value lands in an ``<img src>`` in mail sent
    to riders and drivers, so a typo'd or pasted ``javascript:`` / ``data:``
    URL should degrade to the bundled asset rather than ship. A relative path
    is rejected for the same reason the default is absolute: there is no origin
    to resolve it against in an inbox.
    """
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        logger.warning("company_logo_url is not an absolute http(s) URL — falling back to the bundled asset")
        return None
    return raw


#: Punctuation the assembled lines use that fpdf2's core fonts cannot encode.
#: The em dash separating name from address is the one that actually bites; a
#: middot is already latin-1 and passes through unchanged.
_PDF_SAFE_SUBSTITUTIONS = {
    "—": "-",  # em dash
    "–": "-",  # en dash
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "…": "...",
}


def to_latin1(value: str) -> str:
    """Fold a settings-driven line to something fpdf2's core fonts can render.

    The PDF receipt and invoice use Helvetica, an fpdf2 core font, which is
    latin-1 only — a raw em dash raises on output. An admin can also paste
    anything at all into a settings field, so unencodable characters are
    dropped rather than allowed to fail the whole PDF: a receipt with one odd
    character missing beats no attachment.
    """
    for bad, good in _PDF_SAFE_SUBSTITUTIONS.items():
        value = value.replace(bad, good)
    return value.encode("latin-1", errors="ignore").decode("latin-1")


async def load_company_details() -> CompanyDetails:
    """Resolve company identity from admin settings, with static fallbacks.

    Never raises: a settings-load failure returns the previously-hardcoded
    values, so an email still goes out with a correct-if-stale footer rather
    than not going out at all.

    ``get_app_settings`` caches for 60 s in-process, so this is effectively
    free per send and an admin edit propagates within a minute.
    """
    try:
        settings = await get_app_settings()
    except Exception as exc:
        logger.warning("company details: settings load failed, using static fallbacks: %s", exc)
        settings = {}

    name = _coalesce(settings, "company_name") or "Spinr"
    app_name = _coalesce(settings, "company_app_name") or "Spinr"
    address = _postal_address(settings)
    # Only claim an assembled identity line when an address is actually
    # configured; otherwise keep the shipped constant, which already carries
    # the legal name and locality.
    identity_line = f"{name} — {address}" if address else COMPANY_LINE

    # Build the contact line from CONFIGURED values only. Seeding it with the
    # default support address would make the list non-empty even when nothing
    # is set, and the fallback below would never fire — quietly dropping the
    # website the shipped constant carries. The default belongs to
    # `support_email`, which body copy needs unconditionally, not to the line.
    configured_email = _coalesce(settings, "company_email")
    support_email = configured_email or _DEFAULT_SUPPORT_EMAIL
    contact_parts = [
        p
        for p in (
            configured_email,
            _coalesce(settings, "company_website"),
            _coalesce(settings, "company_phone"),
        )
        if p
    ]
    contact_line = " · ".join(contact_parts) if contact_parts else COMPANY_CONTACT_LINE

    return CompanyDetails(
        name=name,
        app_name=app_name,
        identity_line=identity_line,
        address=address,
        contact_line=contact_line,
        support_email=support_email,
        logo_url=_safe_logo_url(_coalesce(settings, "company_logo_url")) or _bundled_logo_url(),
        address_lines=_address_lines(settings),
    )
