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
import hmac
import json
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger

try:
    from ..core.config import settings
    from ..db import db
    from ..utils.env_admin_tokens import ENV_ADMIN_USER_ID, bump_env_admin_token_version
    from ..utils.error_handling import DatabaseError, DuplicateRecordError, db_error_text, pg_error_code
    from ..utils.log_context import get_request_id
except ImportError:  # pragma: no cover — package-relative fallback
    from core.config import settings
    from db import db
    from utils.env_admin_tokens import ENV_ADMIN_USER_ID, bump_env_admin_token_version
    from utils.error_handling import DatabaseError, DuplicateRecordError, db_error_text, pg_error_code
    from utils.log_context import get_request_id

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

_NON_THEFT_REVOCATION_REASONS = frozenset(
    {
        "user_logout",
        "logout_all",
        "admin_logout",
        "admin_logout_all",
        "admin_action",
        "account_deletion",
        "session_superseded",
    }
)


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


def _is_missing_revocation_reason_column(exc: Exception) -> bool:
    """Match only PostgREST/Postgres missing-column errors for our new field."""
    code = pg_error_code(exc)
    error_text = db_error_text(exc)
    return code in {"PGRST204", "42703"} and "revocation_reason" in error_text


def _is_benign_rotation_replay(row: dict) -> bool:
    """True when a revoked-token replay is a known rotation race or sign-out.

    Two windows, chosen by HOW the token died:
      • ``replaced_by`` set — rotated forward by a successful refresh. A replay
        within ``REFRESH_REUSE_GRACE_SECONDS`` is a retry after a lost rotation
        response or two near-simultaneous refreshes.
      • ``replaced_by`` empty — only an allowlisted explicit sign-out reason
        proves that this specific token was administratively revoked. The
        token stays dead, but replay cannot kill sessions issued afterward.
        Missing/unknown reasons (including legacy rows) retain theft handling.
    Rotated-token grace remains bounded; explicit sign-out classification has
    no time window because it relies on the persisted reason, not token age.
    """
    revoked_at = _parse_iso_dt(row.get("revoked_at"))
    if not revoked_at:
        return False
    age = (datetime.now(timezone.utc) - revoked_at).total_seconds()
    if row.get("replaced_by"):
        return 0 <= age <= REFRESH_REUSE_GRACE_SECONDS
    return row.get("revocation_reason") in _NON_THEFT_REVOCATION_REASONS


def _hash_refresh_token(raw: str) -> str:
    """sha256 hex of the raw refresh token.

    Never store the plaintext — a DB dump must not yield usable tokens.
    Sha256 is fine here (not bcrypt) because the raw value already has
    384 bits of entropy; we only need a 1-to-1 lookup.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _generate_raw_token() -> str:
    return secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)


# Shape of a server-minted raw token (48 bytes -> 64 base64url chars). A
# client-proposed successor (X8) must match it exactly or it is ignored.
PROPOSED_REFRESH_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{64}$")


def is_valid_proposed_refresh_token(value: Optional[str]) -> bool:
    return isinstance(value, str) and bool(PROPOSED_REFRESH_TOKEN_RE.fullmatch(value))


def _is_unique_violation(exc: Exception) -> bool:
    return isinstance(exc, DuplicateRecordError) or pg_error_code(exc) == "23505"


async def issue_refresh_token(
    user_id: str,
    *,
    audience: str = "rider",
    user_agent: Optional[str] = None,
    ip: Optional[str] = None,
    replaces: Optional[str] = None,
    token_version: Optional[int] = None,
    raw: Optional[str] = None,
    session_id: Optional[str] = None,
) -> tuple[str, str, datetime]:
    """Mint a new refresh token row for ``user_id``.

    Returns ``(raw_token, row_id, expires_at)``. The raw token is what
    we hand back to the client — it is NOT stored anywhere on our side
    after this function returns; only the sha256 hash lands in the DB.

    ``replaces`` is the id of the refresh token being rotated (if any);
    we set replaced_by on that row in a separate step so the chain is
    queryable during incident response.

    ``raw`` (X8, default-off successor commitment) is a client-proposed
    successor. It must match ``PROPOSED_REFRESH_TOKEN_RE``. On a UNIQUE
    conflict the insert is retried once with a server-generated token, so a
    proposal can never collide into, or probe, another row. Token material
    is never logged.

    ``session_id`` (login_supersede_driver_app_only_enabled) is the login
    session this chain belongs to; rotation carries it forward. Omitted, the
    row is written exactly as before.
    """
    if raw is not None and not is_valid_proposed_refresh_token(raw):
        raise ValueError("proposed refresh token has an invalid shape")
    raw = raw or _generate_raw_token()
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
    # The login/parent credential owns this value. Re-reading the user here
    # would upgrade an old refresh racing a driver login into its new generation.
    if token_version is not None:
        row["token_version"] = int(token_version)
    if session_id:
        row["session_id"] = str(session_id)

    try:
        result = await db.insert_one("refresh_tokens", row)
    except Exception as exc:
        if not _is_unique_violation(exc):
            raise
        logger.warning("refresh: token_hash conflict on issue; retrying with a server-generated token")
        raw = _generate_raw_token()
        row["token_hash"] = _hash_refresh_token(raw)
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

    A DB failure raises ``DatabaseError`` (503, code 9005) rather than
    returning None (X7/C10): a false 401 would sign a valid session out
    during a transient outage. Not-found/revoked/expired still return None.
    """
    if not raw:
        return None

    token_hash = _hash_refresh_token(raw)
    try:
        row = await db.find_one("refresh_tokens", {"token_hash": token_hash})
    except Exception as exc:
        logger.opt(exception=True).error("refresh_tokens lookup failed")
        raise DatabaseError(message="Could not verify session; please try again") from exc
    if not row:
        return None

    if row.get("audience") in _USERS_TABLE_AUDIENCES:
        try:
            user = await db.find_one("users", {"id": row.get("user_id")})
        except Exception as exc:
            logger.opt(exception=True).error("refresh generation lookup failed")
            raise DatabaseError(message="Could not verify session; please try again") from exc
        # NULL is an unbound legacy credential, never proof of a newer login.
        # Check BEFORE replay detection: an old rotated token cannot invalidate
        # the replacement phone merely by being presented again.
        if not user or not await refresh_token_generation_matches(row, user):
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
                "refresh: benign {} replay — returning 401 without cascade (row_id={} user_id={} audience={})",
                "rotation within grace window" if row.get("replaced_by") else "explicit revocation",
                row.get("id"),
                row.get("user_id"),
                row.get("audience"),
            )
            if not row.get("replaced_by"):
                # An explicitly signed-out token cannot be exchanged or harm a
                # later session, but preserve a forensic record of its replay.
                await _record_post_revoke_race(row)
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


async def classify_committed_replay(parent_raw: str, proposed_raw: str) -> tuple[str, Optional[dict]]:
    """Classify a replayed parent against its committed successor (X8).

    Returns ``("recover", successor)``, ``("dead", None)`` or
    ``("no_match", None)``:

    * ``no_match``: the parent is unknown, not revoked, has no
      ``replaced_by``, or the successor's hash is not sha256(proposed)
      (constant-time compare). The caller continues on the normal path.
    * ``recover``: the successor was committed to ``proposed_raw``, belongs
      to the same user, has a rider/driver audience, is neither revoked nor
      expired, carries the parent's ``token_version`` and matches the user's
      current generation. The caller re-serves the same successor.
    * ``dead``: the proposal matched but any other check failed.

    Holding both the parent and the proposed successor already means holding
    a valid credential, so ``recover`` grants nothing new. DB errors raise
    ``DatabaseError`` (503), never a false 401.
    """
    if not parent_raw or not is_valid_proposed_refresh_token(proposed_raw):
        return "no_match", None
    try:
        parent = await db.find_one("refresh_tokens", {"token_hash": _hash_refresh_token(parent_raw)})
        if not parent or not parent.get("revoked_at") or not parent.get("replaced_by"):
            return "no_match", None
        successor = await db.find_one("refresh_tokens", {"id": parent["replaced_by"]})
    except Exception as exc:
        logger.opt(exception=True).error("refresh: committed replay lookup failed")
        raise DatabaseError(message="Could not verify session; please try again") from exc
    if not successor or not hmac.compare_digest(
        str(successor.get("token_hash") or ""), _hash_refresh_token(proposed_raw)
    ):
        return "no_match", None

    if (
        str(successor.get("user_id") or "") != str(parent.get("user_id") or "")
        or not successor.get("user_id")
        or successor.get("audience") not in _USERS_TABLE_AUDIENCES
        or successor.get("revoked_at")
        or successor.get("token_version") != parent.get("token_version")
    ):
        return "dead", None
    expires_at = _parse_iso_dt(successor.get("expires_at"))
    if not expires_at or datetime.now(timezone.utc) >= expires_at:
        return "dead", None
    try:
        user = await db.find_one("users", {"id": successor["user_id"]})
    except Exception as exc:
        logger.opt(exception=True).error("refresh: committed replay user lookup failed")
        raise DatabaseError(message="Could not verify session; please try again") from exc
    if not user or not await refresh_token_generation_matches(successor, user):
        return "dead", None
    return "recover", successor


async def refresh_token_generation_matches(row: dict, user: dict) -> bool:
    """Check a refresh row against the current generation, including rollout rows.

    Older API replicas omit ``token_version``. While single-driver-session is
    dark, those rows remain usable unless they predate a logout-all watermark.
    Once the flag is enabled, an unbound row cannot prove it belongs to the
    current generation and is rejected. Explicitly revoked rows are still
    rejected by ``lookup_refresh_token`` after this check.
    """
    current = int(user.get("token_version") or 0)
    stored = row.get("token_version")
    if stored is not None:
        return int(stored) == current
    if current == 0:
        return True
    try:
        app_settings = await db.find_one("settings", {"id": "app_settings"})
    except Exception as exc:
        logger.opt(exception=True).error("refresh: could not read driver-session rollout flag")
        raise DatabaseError(message="Could not verify session; please try again") from exc
    if (app_settings or {}).get("driver_single_session_enabled"):
        return False

    # Dark deployment compatibility must not undo logout-all. Only accept an
    # unbound credential created after the user's authoritative kill watermark.
    watermark = _parse_iso_dt(user.get("sessions_invalid_before"))
    issued_at = _parse_iso_dt(row.get("issued_at"))
    return not watermark or bool(issued_at and issued_at >= watermark)


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


async def _record_post_revoke_race(row: dict) -> None:
    """Forensic record + alert for a replay of an explicitly signed-out token.

    The cascade is withheld (the credential is dead; only the user's other
    sessions could be hurt), but the event is not silent: the revoke may have
    been an admin force-logout on a suspected account, and the audit trail
    must show that the dead token was presented again. Written with
    ``cascade_ok: False`` so ``_reuse_already_handled`` never treats it as a
    completed cascade. Unknown/legacy revocations never call this helper.
    Best-effort; never raises into the auth path.
    """
    _capture_reuse_event(row, repeated=False, benign_race=True)
    try:
        await db.insert_one(
            "audit_logs",
            {
                "id": str(uuid.uuid4()),
                "action": REUSE_AUDIT_ACTION,
                "entity_type": "user",
                "entity_id": row.get("user_id") or "unknown",
                "actor_id": "system:refresh_reuse_detector",
                # CRIMSON-SMOKE-7445-9: top-level column (migration 279), same one
                # `_capture_reuse_event`'s Sentry tag now carries — lets
                # correlate_incident.py join this row to its Sentry event and
                # request-scoped log lines instead of a manual user_id match.
                "request_id": get_request_id() or None,
                "details": json.dumps(
                    {
                        "replayed_row_id": row.get("id") or "",
                        "audience": row.get("audience") or "",
                        "replayed_user_agent": row.get("user_agent"),
                        "replayed_ip": row.get("ip"),
                        "original_revoked_at": row.get("revoked_at"),
                        "replaced_by": None,
                        "benign": "explicit_revocation_replay",
                        "cascade_ok": False,
                        "detected_at": datetime.now(timezone.utc).isoformat(),
                    }
                ),
            },
        )
    except Exception as e:
        logger.error(f"post-revoke-race: audit_logs insert failed (user={row.get('user_id')}): {e}")


def _capture_reuse_event(row: dict, *, repeated: bool, benign_race: bool = False) -> None:
    """Tagged Sentry event for the alert rule (tag:spinr_alert). No-op when
    SENTRY_DSN is unset. Best-effort; never blocks the caller. A repeated
    replay of an already-cascaded row is a warning with its own tag so the
    on-call rule can keep paging on first detection without paging on every
    later replay from the same stale install; a post-revoke race (no cascade)
    is a warning with a third tag."""
    user_id = row.get("user_id") or ""
    audience = row.get("audience") or ""
    row_id = row.get("id") or ""
    if benign_race:
        message, level, alert = (
            "REFRESH TOKEN REPLAY (post-revoke race, no cascade)",
            "warning",
            "refresh_token_post_revoke_replay",
        )
    elif repeated:
        message, level, alert = "REFRESH TOKEN REPLAY (already cascaded)", "warning", "refresh_token_replay_repeat"
    else:
        message, level, alert = "REFRESH TOKEN REUSE DETECTED", "error", "refresh_token_reuse"
    try:
        import sentry_sdk  # type: ignore

        tags = {
            "spinr_alert": alert,
            "audience": audience or "unknown",
            "domain": "auth",
            "surface": "backend",
        }
        # CRIMSON-SMOKE-7445-9: a top-level tag (not buried in `contexts`) so
        # this event can be joined by
        # scripts/incident-analysis/correlate_incident.py's request_id-keyed
        # correlation, and by ad-hoc Sentry search. Omitted (not a literal
        # "unknown" string) when replay is detected outside a request
        # (shouldn't happen for this code path, but must never crash on a
        # missing id) — a fabricated placeholder value would be truthy and
        # get bucketed into one artificial merged cluster by the correlator,
        # which groups on `if req_id:`, while no audit_logs row is ever
        # keyed "unknown" to join it against.
        request_id = get_request_id()
        if request_id:
            tags["request_id"] = request_id

        sentry_sdk.capture_message(
            message,
            level=level,
            tags=tags,
            contexts={
                "refresh_token_reuse": {
                    "row_id": row_id,
                    "user_id": user_id,
                    "audience": audience,
                    "original_revoked_at": str(row.get("revoked_at")),
                    "replaced_by": str(row.get("replaced_by")),
                    "repeated": repeated,
                    "benign_race": benign_race,
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
        if user_id == ENV_ADMIN_USER_ID:
            # admin-001 is the env-var-creds super admin — it has no admin_staff
            # row, but migration 434 gave it its own revocable token_version on
            # the settings row (utils/env_admin_tokens.py), the same mechanism
            # /admin/auth/logout-all already bumps for it. Mirror that here so a
            # detected reuse of a stolen admin-001 refresh token actually
            # invalidates its live access tokens, not just the refresh chain —
            # previously this fell through the branches below and did nothing,
            # a gap that predates 434 and was never closed when it landed.
            new_version = await bump_env_admin_token_version()
        else:
            if audience in _USERS_TABLE_AUDIENCES:
                target_table = "users"
            elif audience in _ADMIN_STAFF_AUDIENCES and user_id:
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
        # A bump that is not applicable (an unknown audience) is complete by
        # design, not a failure.
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
                # CRIMSON-SMOKE-7445-9: see _record_post_revoke_race's comment above.
                "request_id": get_request_id() or None,
                "details": json.dumps(details_payload),
            },
        )
    except Exception as e:
        logger.error(f"reuse-cascade: audit_logs insert failed (user={user_id}): {e}")


async def refresh_token_session_id(raw: str, *, user_id: Optional[str] = None) -> Optional[str]:
    """The session id stored on the refresh-token row for ``raw``, if any.

    Holding the raw refresh token proves the caller owns that chain, so logout
    can tombstone the chain's own session without touching other devices.
    With ``user_id``, a row belonging to anyone else answers None. Lookup
    failures answer None (logout falls back to its current-session rule).
    """
    if not raw:
        return None
    try:
        row = await db.find_one("refresh_tokens", {"token_hash": _hash_refresh_token(raw)})
    except Exception:
        logger.opt(exception=True).error("refresh_token_session_id lookup failed")
        return None
    if not row or (user_id is not None and str(row.get("user_id") or "") != str(user_id)):
        return None
    session_id = row.get("session_id")
    return str(session_id) if session_id else None


async def revoke_refresh_token(raw: str, *, reason: Optional[str] = None) -> bool:
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
    revoked_at = datetime.now(timezone.utc).isoformat()
    update = {"revoked_at": revoked_at}
    if reason:
        update["revocation_reason"] = reason
    try:
        await db.update_one(
            "refresh_tokens",
            {"id": row["id"]},
            {"$set": update},
        )
        return True
    except Exception as e:
        if reason and _is_missing_revocation_reason_column(e):
            logger.opt(exception=True).error(
                "refresh_tokens.revocation_reason unavailable; retrying logout revocation without reason"
            )
            try:
                await db.update_one("refresh_tokens", {"id": row["id"]}, {"$set": {"revoked_at": revoked_at}})
                return True
            except Exception as retry_error:
                logger.opt(exception=True).error(f"revoke_refresh_token fallback update failed: {retry_error}")
                return False
        logger.opt(exception=True).error(f"revoke_refresh_token update failed: {e}")
        return False


async def revoke_all_for_user(user_id: str, *, reason: Optional[str] = None) -> int:
    """Revoke every non-revoked refresh token for a user. Returns count.

    This is what /auth/logout-all and the admin "force logout" action
    call. token_version bump does the access-token side; this does the
    refresh-token side. Both are necessary.
    """
    return len(await revoke_all_for_user_ids(user_id, reason=reason))


async def revoke_all_for_user_ids(user_id: str, *, reason: Optional[str] = None) -> list[str]:
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
        update = {"revoked_at": now_iso}
        if reason:
            update["revocation_reason"] = reason
        try:
            await db.update_one(
                "refresh_tokens",
                {"id": row["id"]},
                {"$set": update},
            )
            revoked.append(str(row["id"]))
        except Exception as e:
            if reason and _is_missing_revocation_reason_column(e):
                logger.opt(exception=True).error(
                    "refresh_tokens.revocation_reason unavailable; retrying bulk revocation without reason"
                )
                try:
                    await db.update_one("refresh_tokens", {"id": row["id"]}, {"$set": {"revoked_at": now_iso}})
                    revoked.append(str(row["id"]))
                    continue
                except Exception as retry_error:
                    logger.opt(exception=True).error(
                        f"revoke_all_for_user fallback update failed for {row.get('id')}: {retry_error}"
                    )
                    continue
            logger.opt(exception=True).error(f"revoke_all_for_user: could not revoke {row.get('id')}: {e}")
    return revoked
