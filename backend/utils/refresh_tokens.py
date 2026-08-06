"""Refresh-token helpers (audit P0-S3, B-P1-3 reuse detection).

Access tokens stay as short-ish-lived JWTs; refresh tokens are OPAQUE
(unguessable random bytes, sha256-hashed on the way into the DB) and
are the only durable proof a client can present for "I was already
logged in". This split means:

  • A leaked access token is limited by its TTL (see
    core.config.ACCESS_TOKEN_EXPIRE_MINUTES / ADMIN_ACCESS_TOKEN_TTL_HOURS).
  • A leaked refresh token can be revoked at will — we stamp
    revoked_at in refresh_tokens and every subsequent /auth/refresh
    call sees it.
  • An admin can force-logout every active session for a user by
    incrementing users.token_version (handled in dependencies.py).

Rotation policy: every successful /auth/refresh call revokes the old
row and inserts a new one, with replaced_by chaining them. Re-using an
already-rotated refresh token is treated as theft (legitimate clients
always step forward and never present an old value). On detection we
escalate per OAuth2 BCP — see _handle_refresh_token_reuse.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger

try:
    from ..core.config import settings
    from ..db import db
except ImportError:  # pragma: no cover — package-relative fallback
    from core.config import settings
    from db import db

# audiences for which token_version lives on the `users` table; admin
# audiences live on `admin_staff`. Anything else is rejected at the
# rotation step in routes/auth.py — listed here so the cascade in
# _handle_refresh_token_reuse picks the right table.
_USERS_TABLE_AUDIENCES = {"rider", "driver"}
_ADMIN_STAFF_AUDIENCES = {"admin"}

# 48 random bytes → 64 base64url chars. 384 bits of entropy comfortably
# exceeds any practical brute-force budget.
_REFRESH_TOKEN_BYTES = 48

# Grace window for refresh-token ROTATION races. Every successful /auth/refresh
# revokes the token it rotated from (issue_refresh_token(..., replaces=...) sets
# revoked_at + replaced_by). A client that retries after a lost rotation
# response, or fires two near-simultaneous refreshes, then legitimately replays
# that JUST-rotated token. Treating that as theft runs the full cascade —
# token_version bump + revoke-all + WS kick — which logs every device out and
# wedges live driver sockets on "Reconnecting…". Within this window a replay of
# a rotated-forward token is treated as a benign race (clean 401, no cascade); a
# stolen token replayed later, or a token revoked WITHOUT rotation (explicit
# logout / a prior cascade), still escalates.
#
# Sized for mobile reality, not just the sub-second concurrent-refresh race: a
# phone can fire a refresh, lose the rotation response in a dead zone, and only
# retry when the app is next foregrounded minutes later — still holding the
# pre-rotation token. 60s classified that benign retry as theft and logged the
# user out on EVERY device. The security cost is bounded: a genuinely stolen
# token is already revoked, so a replay within the window yields NOTHING to the
# attacker (lookup still returns None) — only the all-device cascade/alert is
# deferred until the window lapses, at which point a persistent replay still
# escalates.
#
# 24h (was 600s) because a driver spends whole shifts in a moving vehicle on
# patchy rural LTE. The realistic failure is: refresh fires, the rotation
# response is lost in a dead zone, the backend has already rotated, and the phone
# still holds the pre-rotation token until it is next foregrounded — which can be
# hours later, at the end of a shift. Ten minutes classified that as theft and
# logged the driver out of every device.
#
# A purely time-based widening would blind the detector for a day, so it is NOT
# purely time-based: see _successor_is_live for the chain-depth condition, and
# _audit_rotation_replay for the audit row that keeps the signal even when the
# cascade is suppressed. Overridable at runtime via the app_settings key
# `refresh_reuse_grace_seconds` so it can be dialled back without a redeploy.
REFRESH_REUSE_GRACE_SECONDS = 86400

# app_settings key that overrides the constant above at runtime.
_GRACE_SETTING_KEY = "refresh_reuse_grace_seconds"


async def _grace_seconds() -> int:
    """Effective grace window: the app_settings override, else the constant.

    Deliberately fail-soft to the constant — a settings-table hiccup must not
    turn every rotation race into a mass logout. get_app_settings() caches
    in-process, so this is not a DB round-trip per call.
    """
    try:
        try:
            from ..settings_loader import get_app_settings
        except ImportError:  # pragma: no cover — package-relative fallback
            from settings_loader import get_app_settings  # type: ignore

        settings_map = await get_app_settings()
        raw = (settings_map or {}).get(_GRACE_SETTING_KEY)
        if raw is None:
            return REFRESH_REUSE_GRACE_SECONDS
        value = int(raw)
        # A non-positive override would disable the grace window entirely and
        # resurrect the mass-logout incident; ignore it rather than obey it.
        if value <= 0:
            logger.warning(
                f"refresh: ignoring non-positive {_GRACE_SETTING_KEY}={raw!r}; using {REFRESH_REUSE_GRACE_SECONDS}s"
            )
            return REFRESH_REUSE_GRACE_SECONDS
        return value
    except Exception as e:
        logger.warning(f"refresh: could not read {_GRACE_SETTING_KEY} ({type(e).__name__}); using default")
        return REFRESH_REUSE_GRACE_SECONDS


def _parse_iso_dt(value) -> Optional[datetime]:
    """Parse a tz-aware UTC datetime from a DB timestamp value, or None."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


async def _successor_is_live(replaced_by: str) -> bool:
    """True when the row this token was rotated into is still un-revoked.

    This is the chain-DEPTH condition, and it is what makes a 24h grace window
    safe rather than a 24h blind spot.

    A lost-rotation-response race leaves the client exactly ONE step behind: it
    holds T1, the server rotated T1→T2, and T2 is the live token. Replaying T1 is
    benign.

    If T2 is *also* revoked the chain has moved two or more steps — someone
    completed a further rotation while this client was still holding T1. For a
    single honest client that cannot happen: ``refreshTokens`` always re-reads the
    freshest persisted token before retrying, and the driver app's background task
    no longer refreshes at all (see driver-app/utils/backgroundLocation.ts
    ``getBackgroundAuthToken`` — "The background task NEVER calls /auth/refresh
    itself"). Two holders of one family is the theft signal, so escalate.

    ⚠ This gate's correctness DEPENDS on there being exactly one refresh actor per
    session. If a second concurrent refresher is ever reintroduced, a depth-2
    replay becomes a legitimate outcome again and this condition turns into a
    false-positive source — revisit it together with that change.

    Fail-soft: an unreadable or missing successor returns True (benign). We would
    rather miss one escalation than mass-logout a driver because of a DB hiccup;
    the audit row is still written either way.
    """
    try:
        successor = await db.find_one("refresh_tokens", {"id": replaced_by})
    except Exception as e:
        logger.warning(f"refresh: successor lookup failed for {replaced_by} ({type(e).__name__}); treating as benign")
        return True
    if not successor:
        # Chain is broken (row purged by retention, or the chaining update failed
        # — issue_refresh_token treats that as best-effort). Not evidence of theft.
        return True
    return not successor.get("revoked_at")


async def _is_benign_rotation_replay(row: dict) -> bool:
    """True when a revoked-token replay is a normal rotation race, not theft.

    Requires ALL of:
      • ``replaced_by`` is set — the token was rotated forward by a successful
        refresh. A token revoked WITHOUT a replacement was killed by an explicit
        logout or a prior cascade, and replaying that is always a real signal.
      • It was revoked within the effective grace window (see _grace_seconds) —
        a stolen token replayed well after the window still escalates.
      • The successor is still live (see _successor_is_live) — the client is
        exactly one rotation behind, not two or more.
    """
    replaced_by = row.get("replaced_by")
    if not replaced_by:
        return False
    revoked_at = _parse_iso_dt(row.get("revoked_at"))
    if not revoked_at:
        return False
    age = (datetime.now(timezone.utc) - revoked_at).total_seconds()
    if not (0 <= age <= await _grace_seconds()):
        return False
    return await _successor_is_live(str(replaced_by))


async def _audit_rotation_replay(row: dict) -> None:
    """Record a benign-classified rotation replay.

    The cascade is suppressed on this path, but the SIGNAL must not be. Before
    this, the benign branch emitted only a logger.warning — no audit row, no
    Sentry tag — so widening the window from 10 minutes to 24 hours would have
    created a day-long hole in the forensic record. Security ops can now see
    "this account had rotation replays" even when no account was ever locked,
    which is the standing visibility that replaces the escalation we deferred.

    Best-effort: never block or fail the auth path.
    """
    try:
        await db.insert_one(
            "audit_logs",
            {
                "id": str(uuid.uuid4()),
                "action": "refresh_token_rotation_replay",
                "entity_type": "user",
                "entity_id": row.get("user_id") or "unknown",
                "actor_id": "system:refresh_reuse_detector",
                "details": json.dumps(
                    {
                        "replayed_row_id": row.get("id"),
                        "audience": row.get("audience"),
                        "replayed_user_agent": row.get("user_agent"),
                        "replayed_ip": row.get("ip"),
                        "original_revoked_at": row.get("revoked_at"),
                        "replaced_by": row.get("replaced_by"),
                        "classified": "benign_rotation_replay",
                        "cascade_suppressed": True,
                        "detected_at": datetime.now(timezone.utc).isoformat(),
                    }
                ),
            },
        )
    except Exception as e:
        logger.error(f"refresh: rotation-replay audit insert failed (user={row.get('user_id')}): {e}")


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

    result = await db.insert_one("refresh_tokens", row)
    row_id = (result or {}).get("id") or row.get("id") or ""

    if replaces:
        # Mark the previous row as replaced. Best-effort — if the
        # update fails we still return the new token; the worst case
        # is an un-chained row that the audit script will flag.
        try:
            await db.update_one(
                "refresh_tokens",
                {"id": replaces},
                {"$set": {"replaced_by": row_id, "revoked_at": now.isoformat()}},
            )
        except Exception as e:  # pragma: no cover
            logger.opt(exception=True).error(f"Could not chain refresh token {replaces} → {row_id}: {e}")

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
        row = await db.find_one("refresh_tokens", {"token_hash": token_hash})
    except Exception as e:
        logger.error(f"refresh_tokens lookup failed: {e}")
        return None
    if not row:
        return None

    # Replay attack guard: a revoked refresh token presented by a real
    # client is a strong signal of theft. Legitimate clients always step
    # forward to the latest token they were issued and never return to
    # an old value. We escalate per OAuth2 BCP §4.14.2: cascade-revoke
    # every session for the user, bump token_version (kills in-flight
    # access tokens), and write a high-signal audit_logs row.
    if row.get("revoked_at"):
        # Replaying a revoked token is the OAuth2 BCP §4.14.2 theft signal — but
        # normal rotation also revokes the prior token, so a client retry (lost
        # rotation response) or two near-simultaneous refreshes legitimately
        # replay a just-rotated token. Escalating that mass-revokes every
        # session (token_version bump) and wedges live WebSockets. Suppress the
        # cascade for a benign rotation race inside the grace window; a real
        # theft replay (outside the window, or a non-rotated revocation) still
        # cascades. Either way the client gets a generic 401 (no oracle).
        if await _is_benign_rotation_replay(row):
            logger.warning(
                "refresh: benign rotation replay within grace window — "
                "returning 401 without cascade "
                f"(row_id={row.get('id')} user_id={row.get('user_id')} "
                f"audience={row.get('audience')})"
            )
            # Suppress the cascade, NOT the signal.
            await _audit_rotation_replay(row)
            return None
        await _handle_refresh_token_reuse(row)
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


async def _handle_refresh_token_reuse(row: dict) -> None:
    """Escalate when a revoked refresh token is replayed (B-P1-3).

    Steps (each best-effort, none crash the caller):
      1. logger.error with full context — security ops needs this loud.
      2. Bump token_version on users (rider/driver) or admin_staff
         (admin), invalidating every in-flight access token on next
         request (dependencies.py re-reads the row).
      3. Revoke every refresh token for the user (cascade). Conservative
         — we don't know which device is the attacker, so blow them all
         away and force re-auth on every device.
      4. Insert an audit_logs row tagged action='refresh_token_reuse_detected'
         so admin dashboard + the 7y forensic record both surface it.

    Caller (lookup_refresh_token) returns None to the auth route either
    way, so the client sees a generic 401. No oracle leakage.
    """
    user_id = row.get("user_id") or ""
    audience = row.get("audience") or ""
    row_id = row.get("id") or ""

    logger.error(
        f"REFRESH TOKEN REUSE DETECTED — possible theft. "
        f"row_id={row_id} user_id={user_id} audience={audience} "
        f"original_revoked_at={row.get('revoked_at')} replaced_by={row.get('replaced_by')}"
    )
    # Explicit tagged Sentry event so the alert rule can match on
    # tag:spinr_alert=refresh_token_reuse (PagerDuty → on-call).
    # No-op when SENTRY_DSN is unset. Best-effort; never blocks the cascade.
    try:
        import sentry_sdk  # type: ignore

        sentry_sdk.capture_message(
            "REFRESH TOKEN REUSE DETECTED",
            level="error",
            tags={
                "spinr_alert": "refresh_token_reuse",
                "audience": audience or "unknown",
                "domain": "auth",
            },
            contexts={
                "refresh_token_reuse": {
                    "row_id": row_id,
                    "user_id": user_id,
                    "audience": audience,
                    "original_revoked_at": str(row.get("revoked_at")),
                    "replaced_by": str(row.get("replaced_by")),
                },
            },
        )
    except Exception as e:
        # Sentry not configured / import failed / network — the logger.error
        # above already carries the same signal via the loguru→Sentry bridge.
        logger.debug(f"refresh-token-reuse Sentry capture skipped: {e}")

    # Step 2: token_version bump. Pick the right table by audience.
    new_version: Optional[int] = None
    target_table: Optional[str] = None
    try:
        if audience in _USERS_TABLE_AUDIENCES:
            target_table = "users"
        elif audience in _ADMIN_STAFF_AUDIENCES and user_id and user_id != "admin-001":
            # admin-001 is the env-var-creds super admin — has no row to bump.
            target_table = "admin_staff"
        if target_table and user_id:
            current = await db.find_one(target_table, {"id": user_id})
            new_version = int((current or {}).get("token_version") or 0) + 1
            bump = {"token_version": new_version}
            if target_table == "users":
                # Firebase ID tokens carry no token_version claim, so the Firebase
                # auth paths (HTTP + WS) enforce revocation via the
                # sessions_invalid_before watermark. Stamp it here so a
                # refresh-token-reuse compromise also kills Firebase sessions,
                # not just the JWT ones.
                bump["sessions_invalid_before"] = datetime.now(timezone.utc).isoformat()
            await db.update_one(
                target_table,
                {"id": user_id},
                {"$set": bump},
            )
    except Exception as e:
        logger.error(f"reuse-cascade: token_version bump failed (table={target_table} user={user_id}): {e}")

    # Step 3: refresh-token cascade.
    revoked_count = 0
    try:
        revoked_count = await revoke_all_for_user(user_id) if user_id else 0
    except Exception as e:
        logger.error(f"reuse-cascade: revoke_all_for_user failed (user={user_id}): {e}")

    # Step 3.5 (B-P1-11): kick live WebSocket sockets for the user.
    # Without this, an attacker holding the access token paired with
    # the replayed refresh token keeps their WS open until the heartbeat
    # tick (≤30s) — long enough to receive ride state for the victim.
    # Best-effort: a kick failure does not skip the audit_logs insert,
    # and the heartbeat re-validation closes the socket on next tick
    # regardless.
    if user_id:
        try:
            try:
                from ..socket_manager import manager as ws_manager
            except ImportError:  # pragma: no cover — package-relative fallback
                from socket_manager import manager as ws_manager  # type: ignore
            if audience in _USERS_TABLE_AUDIENCES:
                await ws_manager.kick_user(
                    user_id,
                    client_types=["rider", "driver"],
                    reason="refresh_token_reuse",
                )
            elif audience in _ADMIN_STAFF_AUDIENCES:
                await ws_manager.kick_user(
                    user_id,
                    client_types=["admin"],
                    reason="refresh_token_reuse",
                )
        except Exception as e:
            logger.error(f"reuse-cascade: WS kick failed (user={user_id} audience={audience}): {e}")

    # Step 4: audit_logs row. Production schema (migration 57):
    # id TEXT PK / action / entity_type / entity_id / actor_id / details TEXT.
    try:
        details_payload = {
            "replayed_row_id": row_id,
            "audience": audience,
            "replayed_user_agent": row.get("user_agent"),
            "replayed_ip": row.get("ip"),
            "original_revoked_at": row.get("revoked_at"),
            "replaced_by": row.get("replaced_by"),
            "cascade_token_version": new_version,
            "cascade_refresh_revoked": revoked_count,
            "detected_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.insert_one(
            "audit_logs",
            {
                "id": str(uuid.uuid4()),
                "action": "refresh_token_reuse_detected",
                "entity_type": "user",
                "entity_id": user_id or "unknown",
                "actor_id": "system:refresh_reuse_detector",
                "details": json.dumps(details_payload),
            },
        )
    except Exception as e:
        logger.error(f"reuse-cascade: audit_logs insert failed (user={user_id}): {e}")


async def revoke_refresh_token(raw: str) -> bool:
    """Stamp revoked_at on the row for ``raw``. Returns True if a row
    was actually revoked (i.e. the token was valid); False otherwise.
    Safe to call with arbitrary input — unknown hashes are a no-op.
    """
    if not raw:
        return False
    token_hash = _hash_refresh_token(raw)
    try:
        row = await db.find_one("refresh_tokens", {"token_hash": token_hash})
    except Exception as e:
        logger.opt(exception=True).error(f"revoke_refresh_token lookup failed: {e}")
        return False
    if not row or row.get("revoked_at"):
        return False
    try:
        await db.update_one(
            "refresh_tokens",
            {"id": row["id"]},
            {"$set": {"revoked_at": datetime.now(timezone.utc).isoformat()}},
        )
        return True
    except Exception as e:
        logger.opt(exception=True).error(f"revoke_refresh_token update failed: {e}")
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
        rows = await db.get_rows("refresh_tokens", {"user_id": user_id}, limit=1000)
    except Exception as e:
        logger.opt(exception=True).error(f"refresh_tokens scan failed for user {user_id}: {e}")
        return 0
    n = 0
    for row in rows or []:
        if row.get("revoked_at"):
            continue
        try:
            await db.update_one(
                "refresh_tokens",
                {"id": row["id"]},
                {"$set": {"revoked_at": now_iso}},
            )
            n += 1
        except Exception as e:  # pragma: no cover
            logger.opt(exception=True).error(f"revoke_all_for_user: could not revoke {row.get('id')}: {e}")
    return n
