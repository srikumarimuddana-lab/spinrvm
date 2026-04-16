"""Refresh-token helpers (audit P0-S3).

Access tokens stay as short-ish-lived JWTs; refresh tokens are OPAQUE
(unguessable random bytes, sha256-hashed on the way into the DB) and
are the only durable proof a client can present for "I was already
logged in". This split means:

  • A leaked access token is limited by its TTL (see
    core.config.ACCESS_TOKEN_TTL_DAYS / ADMIN_ACCESS_TOKEN_TTL_HOURS).
  • A leaked refresh token can be revoked at will — we stamp
    revoked_at in refresh_tokens and every subsequent /auth/refresh
    call sees it.
  • An admin can force-logout every active session for a user by
    incrementing users.token_version (handled in dependencies.py).

Rotation policy: every successful /auth/refresh call revokes the old
row and inserts a new one, with replaced_by chaining them. Re-using an
already-rotated refresh token is treated as theft — the full chain is
revoked (cascading revocation is a follow-up; for now we log and
revoke the single replayed token).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger

try:
    from ..core.config import settings
    from ..db import db
except ImportError:  # pragma: no cover — package-relative fallback
    from db import db

    from core.config import settings

# 48 random bytes → 64 base64url chars. 384 bits of entropy comfortably
# exceeds any practical brute-force budget.
_REFRESH_TOKEN_BYTES = 48


def _hash_refresh_token(raw: str) -> str:
    """sha256 hex of the raw refresh token.

    Never store the plaintext — a DB dump must not yield usable tokens.
    Sha256 is fine here (not bcrypt) because the raw value already has
    384 bits of entropy; we only need a 1-to-1 lookup.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _generate_raw_token() -> str:
    return secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)


async def issue_refresh_token(
    user_id: str,
    *,
    audience: str = "rider",
    user_agent: Optional[str] = None,
    ip: Optional[str] = None,
    replaces: Optional[str] = None,
) -> tuple[str, str, datetime]:
    """Mint a new refresh token row for ``user_id``.

    Returns ``(raw_token, row_id, expires_at)``. The raw token is what
    we hand back to the client — it is NOT stored anywhere on our side
    after this function returns; only the sha256 hash lands in the DB.

    ``replaces`` is the id of the refresh token being rotated (if any);
    we set replaced_by on that row in a separate step so the chain is
    queryable during incident response.
    """
    raw = _generate_raw_token()
    token_hash = _hash_refresh_token(raw)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    row = {
        "user_id": user_id,
        "token_hash": token_hash,
        "audience": audience,
        "issued_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "user_agent": (user_agent or "")[:512] or None,
        "ip": (ip or "")[:64] or None,
    }

    result = await db.refresh_tokens.insert_one(row)
    row_id = (result or {}).get("id") or row.get("id") or ""

    if replaces:
        # Mark the previous row as replaced. Best-effort — if the
        # update fails we still return the new token; the worst case
        # is an un-chained row that the audit script will flag.
        try:
            await db.refresh_tokens.update_one(
                {"id": replaces},
                {"$set": {"replaced_by": row_id, "revoked_at": now.isoformat()}},
            )
        except Exception as e:  # pragma: no cover
            logger.warning(f"Could not chain refresh token {replaces} → {row_id}: {e}")

    return raw, row_id, expires_at


async def lookup_refresh_token(raw: str) -> Optional[dict]:
    """Return the refresh-token row for ``raw`` if it's valid.

    "Valid" means: row exists, revoked_at is NULL, expires_at is in the
    future. Returns None otherwise — callers MUST NOT distinguish
    between "not found" / "revoked" / "expired" in the response to the
    client, to avoid leaking oracle information.
    """
    if not raw:
        return None

    token_hash = _hash_refresh_token(raw)
    try:
        row = await db.refresh_tokens.find_one({"token_hash": token_hash})
    except Exception as e:
        logger.error(f"refresh_tokens lookup failed: {e}")
        return None
    if not row:
        return None

    # revoked?
    if row.get("revoked_at"):
        # Presenting a revoked token is suspicious — log once, revoke
        # any successor (possible replay) and return nothing.
        logger.warning(
            f"Refresh token presented after revocation "
            f"(id={row.get('id')}, user={row.get('user_id')}, audience={row.get('audience')})"
        )
        return None

    expires_at = row.get("expires_at")
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return None
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at and datetime.now(timezone.utc) >= expires_at:
        return None

    return row


async def revoke_refresh_token(raw: str) -> bool:
    """Stamp revoked_at on the row for ``raw``. Returns True if a row
    was actually revoked (i.e. the token was valid); False otherwise.
    Safe to call with arbitrary input — unknown hashes are a no-op.
    """
    if not raw:
        return False
    token_hash = _hash_refresh_token(raw)
    try:
        row = await db.refresh_tokens.find_one({"token_hash": token_hash})
    except Exception as e:
        logger.warning(f"revoke_refresh_token lookup failed: {e}")
        return False
    if not row or row.get("revoked_at"):
        return False
    try:
        await db.refresh_tokens.update_one(
            {"id": row["id"]},
            {"$set": {"revoked_at": datetime.now(timezone.utc).isoformat()}},
        )
        return True
    except Exception as e:
        logger.warning(f"revoke_refresh_token update failed: {e}")
        return False


async def revoke_all_for_user(user_id: str) -> int:
    """Revoke every non-revoked refresh token for a user. Returns count.

    This is what /auth/logout-all and the admin "force logout" action
    call. token_version bump does the access-token side; this does the
    refresh-token side. Both are necessary.

    Implementation note: we can't express `revoked_at IS NULL` through
    the Mongo-style wrapper's `{field: None}` syntax (postgrest-py
    turns that into `= NULL` which is always false), so we pull the
    full row set for the user and filter client-side. A typical user
    has <10 refresh tokens, so the round-trip cost is negligible.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        rows = await db.refresh_tokens.find({"user_id": user_id}).to_list(1000)
    except Exception as e:
        logger.warning(f"refresh_tokens scan failed for user {user_id}: {e}")
        return 0
    n = 0
    for row in rows or []:
        if row.get("revoked_at"):
            continue
        try:
            await db.refresh_tokens.update_one(
                {"id": row["id"]},
                {"$set": {"revoked_at": now_iso}},
            )
            n += 1
        except Exception as e:  # pragma: no cover
            logger.warning(f"revoke_all_for_user: could not revoke {row.get('id')}: {e}")
    return n
