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
# user out on EVERY device. 10 min covers the "lost response, app resumed
# shortly after" case. The security cost is bounded: a genuinely stolen token
# is already revoked, so a replay within the window yields NOTHING to the
# attacker (lookup still returns None) — only the all-device cascade/alert is
# deferred until the window lapses, at which point a persistent replay still
# escalates.
REFRESH_REUSE_GRACE_SECONDS = 600

# Grace window for a token revoked WITHOUT rotation (explicit logout, logout-all,
# a prior cascade) and replayed moments later. Observed 2026-09-12 on the admin
# dashboard: logout() fires the cookie-clearing BFF call without awaiting it and
# navigates to /login, whose bootstrap refreshes with the cookie still present —
# the just-revoked token was replayed 1.3 s after its own logout, classified as
# theft, and the cascade logged the founder out of every admin session while
# they were approving driver documents. The credential is dead either way; a
# cascade here can only kill the user's OTHER live sessions. Kept short — this
# is a same-client overlap window, not the mobile "lost rotation response"
# case the 10-min rotation grace above exists for. A replay past it still
# escalates.
REFRESH_REVOKE_RACE_GRACE_SECONDS = 60


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


def _is_benign_rotation_replay(row: dict) -> bool:
    """True when a revoked-token replay is a client race, not theft.

    Two windows, chosen by HOW the token died:
      • ``replaced_by`` set — rotated forward by a successful refresh. A replay
        within ``REFRESH_REUSE_GRACE_SECONDS`` is a retry after a lost rotation
        response or two near-simultaneous refreshes.
      • ``replaced_by`` empty — killed by an explicit logout, logout-all or a
        prior cascade. A replay within ``REFRESH_REVOKE_RACE_GRACE_SECONDS`` is
        the same client's logout/refresh overlap (see the constant).
    A stolen token replayed after its window still escalates.
    """
    revoked_at = _parse_iso_dt(row.get("revoked_at"))
    if not revoked_at:
        return False
    age = (datetime.now(timezone.utc) - revoked_at).total_seconds()
    window = REFRESH_REUSE_GRACE_SECONDS if row.get("replaced_by") else REFRESH_REVOKE_RACE_GRACE_SECONDS
    return 0 <= age <= window


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


async def is_new_device(user_id: str, audience: str, user_agent: Optional[str]) -> bool:
    """True when ``user_agent`` has never been seen before for this user+audience.

    ``refresh_tokens.user_agent`` (captured at every ``issue_refresh_token`` call,
    see above) is the only device signal we persist today — ``ip`` is too
    unstable on mobile networks to use as a fingerprint. Call this BEFORE
    ``issue_refresh_token`` for the current login; otherwise the row this
    login is about to create would satisfy its own "have we seen this
    before" check.

    A blank/missing user_agent can't be fingerprinted at all, so it's
    treated as "not new" (never alerts) rather than risking a false
    positive on every login from a client that omits the header.

    Fails quiet on a DB error — returns False (no alert). A missed device
    alert is far cheaper than adding a hard dependency to the login path;
    login must never block or slow down on this check.
    """
    ua = (user_agent or "").strip()
    if not ua:
        return False
    try:
        existing = await db.find_one(
            "refresh_tokens",
            {"user_id": user_id, "audience": audience, "user_agent": ua},
        )
    except Exception as e:
        logger.error(f"is_new_device check failed for user {user_id}: {e}")
        return False
    return existing is None


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
        if _is_benign_rotation_replay(row):
            logger.warning(
                "refresh: benign {} replay within grace window — "
                "returning 401 without cascade "
                "(row_id={} user_id={} audience={})",
                "rotation" if row.get("replaced_by") else "post-revoke",
                row.get("id"),
                row.get("user_id"),
                row.get("audience"),
            )
            return None
        # One cascade per dead row. The cascade answers the first replay by
        # revoking everything issued BEFORE detection; a second replay of the
        # same row can only come from a holder of that already-dead credential,
        # and re-cascading revokes sessions minted AFTER the first response —
        # sessions that holder never had. Observed 2026-09-09/10: one stale
        # install replayed a token revoked 2026-08-26 three times, and each
        # replay logged the same driver out of their live device mid-shift
        # (Sentry CRIMSON-SMOKE-7445-B/C). The replay is still logged and
        # alerted every time; only the destructive step is not repeated.
        if await _reuse_already_handled(row):
            logger.warning(
                "refresh: replay of an already-cascaded revoked token — "
                "returning 401 without a second cascade "
                f"(row_id={row.get('id')} user_id={row.get('user_id')} "
                f"audience={row.get('audience')} original_revoked_at={row.get('revoked_at')})"
            )
            _capture_reuse_event(row, repeated=True)
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


REUSE_AUDIT_ACTION = "refresh_token_reuse_detected"


async def _reuse_already_handled(row: dict) -> bool:
    """True when a cascade has already answered this revoked row.

    Either the row was the replayed token that triggered a cascade, or it is
    one of the rows that cascade revoked (``cascade_revoked_row_ids``). The
    second case matters as much as the first: every session a cascade kills
    still holds its cookie/keychain token and replays it when its access
    token expires up to an hour later. Observed 2026-09-12 (admin): a session
    killed at 03:19:57 replayed at 04:05:17, and because that row id was not
    the one on record, the replay re-cascaded and killed the fresh session
    the founder had logged into at 03:21 — each dead session became a
    landmine for the next live one. A holder of a cascade-revoked credential
    never had the sessions minted after that cascade, so revoking them again
    protects nothing.

    Reads the audit_logs row the cascade writes (Step 4 below) rather than a
    new column: the record is already the 7-year forensic trail for the
    event, and a user has a handful of these at most. Any read failure
    answers False, which keeps the conservative path (cascade) — never the
    other way round.
    """
    user_id = row.get("user_id") or ""
    row_id = row.get("id") or ""
    if not user_id or not row_id:
        return False
    try:
        # Newest first: a user's reuse records accrue for 7 years, and the
        # row being checked is the one most recently written. Without an
        # explicit order a >100-row history could push it past the limit and
        # re-run the cascade this check exists to suppress.
        rows = await db.get_rows(
            "audit_logs",
            {"action": REUSE_AUDIT_ACTION, "entity_id": user_id},
            order="created_at",
            desc=True,
            limit=100,
        )
    except Exception as e:
        logger.opt(exception=True).error(
            f"reuse-cascade: audit lookup failed, defaulting to cascade (user={user_id}): {e}"
        )
        return False
    for audit in rows or []:
        details = audit.get("details")
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except ValueError:
                continue
        # Only a FULLY successful cascade counts. Steps 2 and 3 are each
        # best-effort; if either failed (a Supabase blip during revoke-all)
        # the audit row still lands, but the response is incomplete — an
        # attacker's rotated-forward token could still be live — and the next
        # replay must run the cascade again. Rows written before this flag
        # existed have no `cascade_ok` and are treated as not handled, which
        # errs toward one extra cascade, never toward a suppressed one.
        if not isinstance(details, dict) or details.get("cascade_ok") is not True:
            continue
        if details.get("replayed_row_id") == row_id:
            return True
        # Rows written before the cascade recorded its victims carry no list;
        # a victim of one of those cascades still re-cascades once.
        revoked_ids = details.get("cascade_revoked_row_ids")
        if isinstance(revoked_ids, list) and row_id in revoked_ids:
            return True
    return False


def _capture_reuse_event(row: dict, *, repeated: bool) -> None:
    """Tagged Sentry event for the alert rule (tag:spinr_alert). No-op when
    SENTRY_DSN is unset. Best-effort; never blocks the caller. A repeated
    replay of an already-cascaded row is a warning with its own tag so the
    on-call rule can keep paging on first detection without paging on every
    later replay from the same stale install."""
    user_id = row.get("user_id") or ""
    audience = row.get("audience") or ""
    row_id = row.get("id") or ""
    try:
        import sentry_sdk  # type: ignore

        sentry_sdk.capture_message(
            "REFRESH TOKEN REPLAY (already cascaded)" if repeated else "REFRESH TOKEN REUSE DETECTED",
            level="warning" if repeated else "error",
            tags={
                "spinr_alert": "refresh_token_replay_repeat" if repeated else "refresh_token_reuse",
                "audience": audience or "unknown",
                "domain": "auth",
                "surface": "backend",
            },
            contexts={
                "refresh_token_reuse": {
                    "row_id": row_id,
                    "user_id": user_id,
                    "audience": audience,
                    "original_revoked_at": str(row.get("revoked_at")),
                    "replaced_by": str(row.get("replaced_by")),
                    "repeated": repeated,
                },
            },
        )
    except Exception as e:
        # Sentry not configured / import failed / network. For a FIRST
        # detection the call site's logger.error still reaches Sentry through
        # the loguru bridge (ERROR level). A repeat is logged at WARNING, which
        # the bridge does not forward — so if this capture fails, a repeat
        # replay is in the logs and the audit trail but not alerted. Accepted:
        # the cascade for that row has already run, and the credential is dead.
        logger.debug(f"refresh-token-reuse Sentry capture skipped: {e}")


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
    _capture_reuse_event(row, repeated=False)

    # Step 2: token_version bump. Pick the right table by audience.
    new_version: Optional[int] = None
    bump_ok = False
    revoke_ok = False
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
        # A bump that is not applicable (admin-001's env-var creds, an
        # unknown audience) is complete by design, not a failure.
        bump_ok = True
    except Exception as e:
        logger.error(f"reuse-cascade: token_version bump failed (table={target_table} user={user_id}): {e}")

    # Step 3: refresh-token cascade. The ids go into the audit row so a later
    # replay from one of the sessions killed here is recognised as already
    # answered (see _reuse_already_handled) instead of cascading again.
    revoked_ids: list[str] = []
    try:
        revoked_ids = await revoke_all_for_user_ids(user_id) if user_id else []
        revoke_ok = True
    except Exception as e:
        logger.error(f"reuse-cascade: revoke_all_for_user failed (user={user_id}): {e}")
    revoked_count = len(revoked_ids)

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
            "cascade_revoked_row_ids": revoked_ids,
            # Read back by _reuse_already_handled: a repeat replay of this row
            # skips the cascade only when both destructive steps succeeded.
            "cascade_ok": bump_ok and revoke_ok,
            "detected_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.insert_one(
            "audit_logs",
            {
                "id": str(uuid.uuid4()),
                "action": REUSE_AUDIT_ACTION,
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
    """
    return len(await revoke_all_for_user_ids(user_id))


async def revoke_all_for_user_ids(user_id: str) -> list[str]:
    """Revoke every non-revoked refresh token for a user; return the row ids.

    The reuse cascade records these ids on its audit row so a later replay
    from one of the sessions it killed does not cascade again.

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
        return []
    revoked: list[str] = []
    for row in rows or []:
        if row.get("revoked_at"):
            continue
        try:
            await db.update_one(
                "refresh_tokens",
                {"id": row["id"]},
                {"$set": {"revoked_at": now_iso}},
            )
            revoked.append(str(row["id"]))
        except Exception as e:  # pragma: no cover
            logger.opt(exception=True).error(f"revoke_all_for_user: could not revoke {row.get('id')}: {e}")
    return revoked
