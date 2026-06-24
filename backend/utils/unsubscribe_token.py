"""
Signed one-click unsubscribe token helpers.

A marketing email's unsubscribe link and ``List-Unsubscribe`` header must work
WITHOUT the recipient being logged in — they click it from their mail client,
which carries no JWT. So the link carries a signed token that the unsubscribe
endpoint verifies, binding the action to exactly one ``user_id`` + ``channel``.

CASL requires the unsubscribe mechanism to remain functional for at least 60
days after the message is sent; in practice we never want a stale link to stop
working, so — unlike the short-lived offer-card token — this token does NOT
expire. Its only job is to stop a stranger from unsubscribing someone else by
guessing a user_id: the HMAC signature (keyed on JWT_SECRET) makes the token
unforgeable, so possession of a valid link is the authorisation.

Pure stdlib HMAC-SHA256, ``base64url(payload).base64url(sig)`` — mirrors
utils/offer_card_token.py. No JWT dep.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any, Dict

try:
    from ..core.config import settings
except ImportError:
    from core.config import settings  # type: ignore

# Channels a recipient can unsubscribe from via a signed link. Push has no
# email/SMS-style unsubscribe link (it's toggled in-app), so only these two.
_VALID_CHANNELS = ("email", "sms")


class UnsubscribeTokenError(ValueError):
    """Raised when a token is malformed or signature-invalid."""


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _sign(payload_bytes: bytes) -> bytes:
    return hmac.new(settings.JWT_SECRET.encode("utf-8"), payload_bytes, hashlib.sha256).digest()


def sign_unsubscribe_token(*, user_id: str, channel: str) -> str:
    """Return a signed, non-expiring token authorising unsubscribe for one
    user + channel."""
    if channel not in _VALID_CHANNELS:
        raise UnsubscribeTokenError(f"unsupported channel: {channel}")
    payload: Dict[str, Any] = {"v": 1, "u": user_id, "c": channel}
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = _sign(payload_bytes)
    return f"{_b64url_encode(payload_bytes)}.{_b64url_encode(sig)}"


def verify_unsubscribe_token(token: str) -> Dict[str, Any]:
    """Return ``{"user_id", "channel"}`` iff the token is well-formed and the
    signature matches. Raises ``UnsubscribeTokenError`` otherwise so the
    endpoint can return a flat 400 without leaking which check failed."""
    if not token or token.count(".") != 1:
        raise UnsubscribeTokenError("malformed token")

    payload_b64, sig_b64 = token.split(".", 1)
    try:
        payload_bytes = _b64url_decode(payload_b64)
        provided_sig = _b64url_decode(sig_b64)
    except (ValueError, TypeError) as e:
        raise UnsubscribeTokenError(f"base64 decode failed: {e}") from e

    expected_sig = _sign(payload_bytes)
    if not hmac.compare_digest(provided_sig, expected_sig):
        raise UnsubscribeTokenError("signature mismatch")

    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError as e:
        raise UnsubscribeTokenError(f"payload is not JSON: {e}") from e

    if payload.get("v") != 1:
        raise UnsubscribeTokenError(f"unsupported token version: {payload.get('v')}")
    channel = payload.get("c")
    if channel not in _VALID_CHANNELS:
        raise UnsubscribeTokenError(f"unsupported channel: {channel}")
    user_id = payload.get("u")
    if not user_id:
        raise UnsubscribeTokenError("missing user_id")

    return {"user_id": user_id, "channel": channel}
