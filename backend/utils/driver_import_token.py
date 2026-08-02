"""Signed driver-import validation tokens.

`POST /admin/drivers/import/commit` used to accept a CSV and commit it
directly — no server-side proof that `/admin/drivers/import/validate` had
ever been called for that exact file, and no rate limit. An admin session
(or a buggy retry loop against either endpoint) could bulk-create driver +
user rows without ever seeing the dry-run report, and nothing capped how
often it could happen. Corporate + admin portal review, gap #45.

`validate_driver_import` mints a short-lived token bound to
(batch, sha256 of the raw CSV bytes, admin id); `commit_driver_import`
requires it and re-derives the same three values from its own request,
so committing without a matching, unexpired, same-admin validate call for
byte-identical CSV content fails closed. This also subsumes the existing
"CSV changed since validate" check — a changed file hashes differently,
so the token simply won't verify.

Mirrors `utils/offer_card_token.py` exactly — pure stdlib HMAC-SHA256,
`base64url(payload).base64url(sig)`. No JWT dep.
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


# Long enough for an admin to read the dry-run report (warnings/errors,
# possibly hundreds of rows) before clicking commit; short enough that a
# stale token can't be replayed hours later against a since-changed roster.
DEFAULT_TTL_SECONDS = 1800  # 30 minutes


class DriverImportTokenError(ValueError):
    """Raised when a token is malformed, signature-invalid, expired, or
    bound to a different batch/CSV/admin than the current request."""


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _sign(payload_bytes: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()


def sign_driver_import_token(
    *,
    batch: str,
    csv_sha256: str,
    admin_id: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: Optional[float] = None,
) -> str:
    """Return a signed token authorising a commit of this exact CSV/batch/admin."""
    issued = int(now if now is not None else time.time())
    payload: Dict[str, Any] = {
        "v": 1,
        "b": batch,
        "h": csv_sha256,
        "a": admin_id,
        "iat": issued,
        "exp": issued + ttl_seconds,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = _sign(payload_bytes, settings.JWT_SECRET)
    return f"{_b64url_encode(payload_bytes)}.{_b64url_encode(sig)}"


def verify_driver_import_token(
    token: str,
    *,
    batch: str,
    csv_sha256: str,
    admin_id: str,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Return the decoded payload iff the token is valid, unexpired, and
    bound to this exact (batch, csv_sha256, admin_id).

    Raises DriverImportTokenError on any failure — the endpoint returns a
    flat 400 without leaking which specific check failed.
    """
    if not token or token.count(".") != 1:
        raise DriverImportTokenError("malformed token")

    payload_b64, sig_b64 = token.split(".", 1)

    try:
        payload_bytes = _b64url_decode(payload_b64)
        provided_sig = _b64url_decode(sig_b64)
    except (ValueError, TypeError) as e:
        raise DriverImportTokenError(f"base64 decode failed: {e}") from e

    expected_sig = _sign(payload_bytes, settings.JWT_SECRET)
    if not hmac.compare_digest(provided_sig, expected_sig):
        raise DriverImportTokenError("signature mismatch")

    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError as e:
        raise DriverImportTokenError(f"payload is not JSON: {e}") from e

    if payload.get("v") != 1:
        raise DriverImportTokenError(f"unsupported token version: {payload.get('v')}")

    current = int(now if now is not None else time.time())
    if current >= payload.get("exp", 0):
        raise DriverImportTokenError("token expired — validate the CSV again")

    if payload.get("b") != batch:
        raise DriverImportTokenError("token batch mismatch")
    if payload.get("h") != csv_sha256:
        raise DriverImportTokenError("token CSV mismatch — the file changed since validation")
    if payload.get("a") != admin_id:
        raise DriverImportTokenError("token admin mismatch")

    return payload
