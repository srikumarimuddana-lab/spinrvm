"""Short-lived session-revocation tombstones.

Why a tombstone and not an absence check
----------------------------------------
``/auth/logout`` revokes the *refresh* token, but the access token it was
presented with stays valid until its ``exp`` (15 min). That window is what let a
signed-out driver app keep POSTing GPS to ``/drivers/location-batch``: the
headless background task held a cached copy of a still-valid access token.

The obvious fix — reject a token whose ``session:{user_id}`` Redis key is gone —
is wrong. Absence is ambiguous: the key may never have been written, may have
aged out on its own TTL, or Redis may have restarted (``utils/redis_client``
silently degrades to an in-process dict, so any restart loses all keys). Treating
absence as revocation would 401 every legitimate driver after a Redis bounce.

So we invert it and record *positive evidence* of a logout instead. A tombstone
is written only when a user explicitly signs out, and only the presence of that
tombstone rejects a request. Every ambiguous state — no tombstone, no session_id
claim, Redis unreachable — resolves to "allow". Fail-open is deliberate: this is
defense-in-depth behind the client-side teardown, and a false rejection would
drop GPS points that settle a driver's billed distance and back the SGI
per-insurance-period audit trail.

Not enforced in ``get_current_user``
------------------------------------
Deliberately. That function runs on every authenticated request, and adding a
Redis round-trip there risks the auth-refresh (<200 ms) and dispatch (<2 s) P95
SLAs. Callers opt in at the specific ingest points where a zombie writer
actually causes harm.

Granularity and its known limit
-------------------------------
Tombstones are keyed on the JWT's ``session_id`` claim, which ``/auth/refresh``
preserves (it re-reads ``users.current_session_id``), so one tombstone covers a
login session across token rotations. ``users.current_session_id`` is a single
column, though, so a second device that logs in overwrites it and a later
refresh can land both devices on the same ``session_id``. Callers therefore
tombstone only when the logging-out token's ``session_id`` still matches
``users.current_session_id`` — see ``should_tombstone``. Residual risk is bounded
to ``ACCESS_TOKEN_EXPIRE_MINUTES`` and only on an explicit user-initiated logout.
"""

import logging
from typing import Optional

try:
    from ..core.config import settings
    from .redis_client import redis_get, redis_set
except ImportError:  # python -m backend.server vs top-level
    from core.config import settings  # type: ignore
    from utils.redis_client import redis_get, redis_set  # type: ignore

logger = logging.getLogger(__name__)

# Distinct from the "session:" prefix (which stores the *live* session id per
# user). Registered in redis_client.KNOWN_KEY_PREFIXES so the admin Redis
# dashboard accounts for these keys instead of silently under-reporting.
_TOMBSTONE_PREFIX = "revoked_session:"

_TOMBSTONE_VALUE = "1"


def _key(session_id: str) -> str:
    return f"{_TOMBSTONE_PREFIX}{session_id}"


def _tombstone_ttl_seconds() -> int:
    """TTL == access-token lifetime.

    A tombstone only needs to outlive the longest-lived access token that could
    still be carrying the revoked ``session_id``. Past that, the token is
    rejected by its own ``exp`` and the tombstone is dead weight. +60s of slack
    absorbs clock skew between the API replica that minted the token and the one
    checking it.
    """
    return int(settings.ACCESS_TOKEN_EXPIRE_MINUTES) * 60 + 60


def should_tombstone(payload_session_id: Optional[str], current_session_id: Optional[str]) -> bool:
    """Whether a logout should tombstone the presented session.

    False when the token carries no ``session_id`` (nothing to key on), or when
    it no longer matches ``users.current_session_id`` — meaning another device
    has since logged in and owns the current session. Tombstoning a stale
    session_id in that case would revoke the *other* device's traffic, so we
    let the stale token die on its own ``exp`` instead.
    """
    if not payload_session_id:
        return False
    return payload_session_id == current_session_id


async def revoke_session(session_id: str) -> bool:
    """Record that ``session_id`` was signed out. Returns whether it was stored.

    Never raises: a logout must succeed for the user even if Redis is down. A
    failure here degrades to the pre-existing behaviour (access token usable
    until ``exp``), so it is a warning, not an error — the session *is* revoked
    where it durably matters (the refresh-token row).
    """
    if not session_id:
        return False
    try:
        await redis_set(_key(session_id), _TOMBSTONE_VALUE, ttl=_tombstone_ttl_seconds())
        return True
    except Exception:
        logger.warning(
            "session-revocation tombstone write failed; access token remains valid until exp",
            exc_info=True,
        )
        return False


async def is_session_revoked(session_id: Optional[str]) -> bool:
    """Whether this session has been explicitly signed out.

    Fail-open on every ambiguous input (no session id, Redis error) — see the
    module docstring. A Firebase-authenticated session carries no ``session_id``
    claim and so is never rejected here; ``logout-all`` /
    ``sessions_invalid_before`` remains its revocation path.
    """
    if not session_id:
        return False
    try:
        return await redis_get(_key(session_id)) is not None
    except Exception:
        logger.warning(
            "session-revocation tombstone read failed; allowing request (fail-open)",
            exc_info=True,
        )
        return False
