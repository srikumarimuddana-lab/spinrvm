"""
Signed offer-card token helpers.

The driver-app ride-offer notification renders a branded fare banner
(``BigPicture`` on Android). Notifee fetches that image over a plain HTTP
GET from the OS — **without** the app's JWT or Firebase App Check header.
So the banner endpoint cannot rely on normal auth; instead the dispatch
push carries a short-lived signed URL that the endpoint verifies.

The token binds the image to exactly one ``ride_id`` + ``driver_id`` and
expires at (or just after) the offer's own expiry, so:

  - another rider/driver can't fetch this offer's banner (driver binding),
  - the URL can't be replayed after the offer is gone (short TTL),
  - the banner contains only minimised PII (rider first name + area), so
    even within the TTL the exposure is bounded (see routes/offer_card.py).

Mirrors ``utils/estimate_token.py`` exactly — pure stdlib HMAC-SHA256,
``base64url(payload).base64url(sig)``. No JWT dep.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional

try:
    from ..core.config import settings
except ImportError:
    from core.config import settings


# Offers live ~15-30s; give the banner URL a small buffer beyond that so a
# driver expanding the notification a few seconds late still loads the image,
# but not so long that the URL is useful once the offer is gone.
DEFAULT_TTL_SECONDS = 90


class OfferCardTokenError(ValueError):
    """Raised when a token is malformed, signature-invalid, or expired."""


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _sign(payload_bytes: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()


def sign_offer_card_token(
    *,
    ride_id: str,
    driver_id: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: Optional[float] = None,
) -> str:
    """Return a signed token authorising the offer-card image for one offer."""
    issued = int(now if now is not None else time.time())
    payload: Dict[str, Any] = {
        "v": 1,
        "rd": ride_id,
        "dr": driver_id,
        "iat": issued,
        "exp": issued + ttl_seconds,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = _sign(payload_bytes, settings.JWT_SECRET)
    return f"{_b64url_encode(payload_bytes)}.{_b64url_encode(sig)}"


def verify_offer_card_token(
    token: str,
    *,
    ride_id: str,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Return the decoded payload iff the token is valid, unexpired, and bound
    to this ``ride_id``.

    Raises ``OfferCardTokenError`` on any failure so the endpoint can return a
    flat 403 without leaking which check failed.
    """
    if not token or token.count(".") != 1:
        raise OfferCardTokenError("malformed token")

    payload_b64, sig_b64 = token.split(".", 1)

    try:
        payload_bytes = _b64url_decode(payload_b64)
        provided_sig = _b64url_decode(sig_b64)
    except (ValueError, TypeError) as e:
        raise OfferCardTokenError(f"base64 decode failed: {e}") from e

    expected_sig = _sign(payload_bytes, settings.JWT_SECRET)
    if not hmac.compare_digest(provided_sig, expected_sig):
        raise OfferCardTokenError("signature mismatch")

    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError as e:
        raise OfferCardTokenError(f"payload is not JSON: {e}") from e

    if payload.get("v") != 1:
        raise OfferCardTokenError(f"unsupported token version: {payload.get('v')}")

    current = int(now if now is not None else time.time())
    if current >= payload.get("exp", 0):
        raise OfferCardTokenError("token expired")

    if payload.get("rd") != ride_id:
        raise OfferCardTokenError("token ride mismatch")

    return payload
