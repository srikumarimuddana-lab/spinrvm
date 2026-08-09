"""Public endpoint serving the Spinr logo for transactional-email headers.

Email clients cannot read a file off disk, and ``utils/email_provider._build_mime``
builds only ``multipart/alternative`` / ``multipart/mixed`` — it has no
``multipart/related`` support, so a CID-embedded inline image is not available.
Branded emails therefore reference the logo by URL, and that URL is this route.

Why a single-file route rather than ``app.mount("/static", StaticFiles(...))``:
``backend/static/`` also holds ``sgi_forms/D00032_*.pdf`` and ``D00033_*.pdf`` —
the SGI regulator form templates. A directory mount would publish those too, and
would serve them from a sub-application that bypasses part of the middleware
stack. Exposing exactly one file keeps the blast radius at one file.

Like ``routes/offer_card.py`` this is intentionally unauthenticated at the
middleware layer (it is in ``_APP_CHECK_EXEMPT_PREFIXES``): the fetch comes from
a mail client, which cannot attach a JWT or a Firebase App Check header. Unlike
the offer card there is no signed token, because there is nothing to authorise —
the asset is a public brand mark with no PII and no per-user dimension. It is
the same image already shipped inside every Spinr-branded report PDF.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import Response

try:
    from ..utils.rate_limiter import default_limiter
    from ..utils.report_branding import LOGO_PATH, has_logo_asset
except ImportError:
    from utils.rate_limiter import default_limiter
    from utils.report_branding import LOGO_PATH, has_logo_asset

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/branding", tags=["branding"])

# The asset is immutable for the life of a deploy (see
# static/branding/README.md — it is replaced by re-running the resize, never
# hand-edited), so it is read once and held in memory. 95 KB.
_logo_bytes: Optional[bytes] = None

# One year, immutable: mail clients and the image proxies in front of them
# (Gmail's, notably) cache aggressively, and re-fetching a logo that never
# changes on every receipt open is pure waste.
_CACHE_CONTROL = "public, max-age=31536000, immutable"


def _load_logo() -> Optional[bytes]:
    """Read and memoise the logo. Returns None when the asset is missing.

    Mirrors ``report_branding.has_logo_asset()``'s contract: a missing asset
    degrades (PDF headers render without an image; email falls back to the
    styled alt text) rather than raising.
    """
    global _logo_bytes
    if _logo_bytes is not None:
        return _logo_bytes
    if not has_logo_asset():
        logger.error("branding: %s is missing — emails will render without a logo", LOGO_PATH.name)
        return None
    try:
        _logo_bytes = LOGO_PATH.read_bytes()
    except OSError as exc:
        logger.error("branding: failed to read %s: %s", LOGO_PATH.name, exc)
        return None
    return _logo_bytes


@router.get("/spinr-logo.png")
@default_limiter.limit("120/minute")
async def spinr_logo_png(request: Request) -> Response:  # noqa: ARG001 — required by SlowAPI's limiter
    """Serve the Spinr bullseye + wordmark used in branded email headers."""
    return _build_logo_response()


def _build_logo_response() -> Response:
    """Testable without the SlowAPI/request plumbing the decorated route carries."""
    png = _load_logo()
    if png is None:
        return Response(status_code=404)
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": _CACHE_CONTROL},
    )
