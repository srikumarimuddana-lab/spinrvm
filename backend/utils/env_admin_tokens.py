"""Revocation state for the env-credential super admin (``admin-001``).

``admin-001`` is the super admin defined by ``ADMIN_EMAIL`` / ``ADMIN_PASSWORD``
in the environment rather than by a row in ``admin_staff``. That absence is the
problem this module solves: every authoritative revocation control the admin
pipeline has — ``is_active``, ``token_version``, the idle timeout — is read from
an ``admin_staff`` row, so ``_verify_admin_payload`` skipped all of them for
this one account (``elif user_id != "admin-001"``). The only thing standing
between a leaked super-admin token and the dashboard was the per-JTI Redis
denylist, which deliberately **fails open** on a Redis error, and
``/admin/auth/logout-all`` refused to act on the account at all — the documented
recovery was "rotate ADMIN_PASSWORD in the environment", i.e. a deploy.

So this gives ``admin-001`` the one control it was missing, in the DB, using the
same semantics as staff: a monotonic ``token_version``. Mint stamps the current
value into the token; every request compares the claim against the stored value;
``/admin/auth/logout-all`` bumps it, and every token minted before the bump dies
on its next request.

Why the DB and not a Redis allowlist (which is how break-glass does it):
``utils/redis_client.py`` falls back to an in-process dict when ``REDIS_URL`` is
unset, so a fail-closed Redis allowlist would make a token minted on one replica
unverifiable on another, and a Redis blip would lock the super admin out of the
live dashboard during an incident — the exact outcome the JTI denylist's
fail-open comment says was being avoided. Break-glass accepts that trade because
it is a rare emergency token; ``admin-001`` is a routine login. The DB is
already a hard dependency of every admin request, so keying on it adds no new
failure mode.

The counter lives on the ``settings`` singleton row per CLAUDE.md's
"Settings in DB" convention. It is deliberately **not** on
``SettingsUpdateRequest``: this is auth state an operator revokes through
``/admin/auth/logout-all``, not a value to hand-edit in the settings screen.
"""

try:
    from .. import db_supabase
    from ..utils.error_handling import DatabaseError
except ImportError:  # pragma: no cover - dual import
    import db_supabase  # type: ignore
    from utils.error_handling import DatabaseError  # type: ignore

# The user_id carried by env-credential super-admin tokens. There is no
# admin_staff row with this id.
ENV_ADMIN_USER_ID = "admin-001"

_SETTINGS_ROW_ID = "app_settings"
_VERSION_COLUMN = "env_admin_token_version"


async def get_env_admin_token_version() -> int:
    """Current revocation generation for ``admin-001``.

    Read uncached and straight from the ``settings`` row: this is an auth
    decision, and the 60-second settings cache would leave a revoked
    super-admin token live for up to a minute after an operator pressed
    logout-all — which is the moment they least want lag.

    **Selects only the two columns it needs, never ``*``.** The ``settings``
    singleton carries ~130 columns including `stripe_secret_key`,
    `twilio_auth_token`, `apns_p8_key` and four `ai_api_key_*` values. A
    ``SELECT *`` here would drag the entire production secret bundle into the
    local frame of an auth function on every super-admin request — not logged
    today, but one future traceback or Sentry breadcrumb away from being so.

    Raises ``DatabaseError`` (503) rather than ever returning a fallback. A
    missing row, a missing column, or a read failure must NOT read as "version
    0": with ``_token_version_mismatch`` being ``claim < stored``, a stored 0
    passes every token ever minted, which would silently un-revoke everything
    an operator just killed — the exact fail-open behaviour this module exists
    to remove. **Because a missing column is an error here, migration 434 must
    be applied before this code is deployed** (see the migration header).
    """
    rows = await db_supabase.get_rows(
        "settings",
        {"id": _SETTINGS_ROW_ID},
        limit=1,
        columns=f"id,{_VERSION_COLUMN}",
    )
    if not rows:
        raise DatabaseError(
            "env-admin revocation counter is unreadable",
            details={"reason": "settings_row_missing", "row_id": _SETTINGS_ROW_ID},
        )
    return int(rows[0].get(_VERSION_COLUMN) or 0)


async def bump_env_admin_token_version() -> int:
    """Invalidate every outstanding ``admin-001`` access token. Returns the new
    version.

    Two operators pressing logout-all at the same instant can both read N and
    both write N+1, so the counter can advance by one instead of two. That is
    harmless here: the property that matters is that the stored value ends up
    strictly greater than the version stamped into any already-minted token,
    and a single increment achieves it. It is a revocation generation, not a
    count of revocations.

    Raises ``DatabaseError`` if the read fails or the write matched no row.
    """
    new_version = await get_env_admin_token_version() + 1
    updated = await db_supabase.update_one("settings", {"id": _SETTINGS_ROW_ID}, {_VERSION_COLUMN: new_version})
    # update_one returns None when the filter matched NO row, and does not
    # raise (repositories/_base.py). Discarding that return would let
    # /admin/auth/logout-all answer 200 with a new version for a revocation
    # that never hit the database — the operator would believe a leaked
    # super-admin token was dead while it stayed live until its own expiry.
    # That is the single worst outcome this module can produce, so the write
    # is verified rather than assumed.
    if not isinstance(updated, dict):
        raise DatabaseError(
            "env-admin revocation counter was not written",
            details={"reason": "settings_row_not_updated", "row_id": _SETTINGS_ROW_ID},
        )
    return new_version


__all__ = [
    "DatabaseError",
    "ENV_ADMIN_USER_ID",
    "bump_env_admin_token_version",
    "get_env_admin_token_version",
]
