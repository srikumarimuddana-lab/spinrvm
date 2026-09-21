"""The env-credential super admin (``admin-001``) must be revocable.

``admin-001`` is defined by ``ADMIN_EMAIL``/``ADMIN_PASSWORD`` and has no
``admin_staff`` row, so ``_verify_admin_payload`` skipped every authoritative
revocation control it has for staff — ``is_active``, ``token_version``, the idle
timeout — via a bare ``elif user_id != "admin-001"``. What remained was the
per-JTI Redis denylist, which **fails open** by design (see
``test_admin_revocation_failopen.py``, which documents that as intended), and
``/admin/auth/logout-all``, which returned 400 for this account and told the
operator to rotate ``ADMIN_PASSWORD`` — i.e. a redeploy.

Net effect: a leaked super-admin token could not be killed by any in-app
control, and during a Redis outage could not be killed at all. These tests pin
the replacement — a DB-backed ``token_version`` on the settings row (migration
433) with the same semantics as staff.

2026-09-20 review, finding C8.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request as StarletteRequest

import dependencies
from dependencies import JWT_AUD_ADMIN, _verify_admin_payload

pytestmark = pytest.mark.anyio


def _resolve_inner(fn):
    """Unwrap slowapi's @limiter.limit closure so the handler can be called
    directly without tripping rate-limit state. Same helper as
    tests/test_logout_all.py."""
    while True:
        nxt = getattr(fn, "__wrapped__", None)
        if nxt is None:
            for cell in getattr(fn, "__closure__", None) or ():
                val = cell.cell_contents
                if callable(val) and getattr(val, "__code__", None) is not None and val is not fn:
                    return val
            return fn
        fn = nxt


def _make_request() -> StarletteRequest:
    return StarletteRequest(
        {
            "type": "http",
            "method": "POST",
            "path": "/admin/auth/logout-all",
            "query_string": b"",
            "headers": [(b"user-agent", b"TestSuite/1.0")],
            "client": ("127.0.0.1", 1234),
        }
    )


def _admin_payload(token_version: int = 0) -> dict:
    return {
        "user_id": "admin-001",
        "role": "super_admin",
        "email": "ops@spinr.ca",
        "aud": JWT_AUD_ADMIN,
        "jti": "tok-env-1",
        "token_version": token_version,
    }


@pytest.fixture(autouse=True)
def _redis_quiet(monkeypatch):
    """Keep the JTI denylist out of the way — its behaviour is pinned in
    test_admin_revocation_failopen.py and is not what these tests are about."""
    monkeypatch.setattr(dependencies, "redis_get", AsyncMock(return_value=None))


class TestVerifyPath:
    async def test_current_token_is_accepted(self, monkeypatch):
        monkeypatch.setattr(dependencies, "get_env_admin_token_version", AsyncMock(return_value=3))
        user = await _verify_admin_payload(_admin_payload(token_version=3))
        assert user is not None
        assert user["id"] == "admin-001"

    async def test_token_minted_before_a_logout_all_is_rejected(self, monkeypatch):
        """The whole point: operator bumps the version, the outstanding token dies."""
        monkeypatch.setattr(dependencies, "get_env_admin_token_version", AsyncMock(return_value=4))
        with pytest.raises(HTTPException) as exc:
            await _verify_admin_payload(_admin_payload(token_version=3))
        assert exc.value.status_code == 401
        assert exc.value.detail == "ERR_SESSION_REVOKED"

    async def test_unreadable_version_fails_closed_with_503(self, monkeypatch):
        """A DB error must NOT be read as version 0.

        Defaulting to 0 would silently un-revoke every token the operator had
        just killed — the fail-open hole this change removes. 503 (not 401) so
        the client retries instead of discarding a probably-valid token.
        """
        monkeypatch.setattr(
            dependencies,
            "get_env_admin_token_version",
            AsyncMock(side_effect=RuntimeError("settings read failed")),
        )
        with pytest.raises(HTTPException) as exc:
            await _verify_admin_payload(_admin_payload(token_version=0))
        assert exc.value.status_code == 503

    async def test_zero_on_both_sides_still_passes(self, monkeypatch):
        """Forward-compatibility: a token minted before migration 433 carries 0,
        and a settings row without the column reads 0. The check is symmetric on
        0 exactly like the staff path, so deploying code ahead of the migration
        does not lock the super admin out."""
        monkeypatch.setattr(dependencies, "get_env_admin_token_version", AsyncMock(return_value=0))
        user = await _verify_admin_payload(_admin_payload(token_version=0))
        assert user is not None

    async def test_a_claim_ahead_of_stored_is_not_treated_as_stale(self, monkeypatch):
        """_token_version_mismatch is `claim < stored`, so a token minted just
        before a concurrent rollback of the counter is allowed rather than
        locking the operator out mid-incident."""
        monkeypatch.setattr(dependencies, "get_env_admin_token_version", AsyncMock(return_value=1))
        user = await _verify_admin_payload(_admin_payload(token_version=2))
        assert user is not None


class TestStoredVersionHelpers:
    async def test_missing_row_reads_as_zero(self, monkeypatch):
        import utils.env_admin_tokens as mod

        monkeypatch.setattr(mod.db_supabase, "find_one", AsyncMock(return_value=None))
        assert await mod.get_env_admin_token_version() == 0

    async def test_row_without_the_column_reads_as_zero(self, monkeypatch):
        """The window where code is deployed but migration 433 has not run."""
        import utils.env_admin_tokens as mod

        monkeypatch.setattr(mod.db_supabase, "find_one", AsyncMock(return_value={"id": "app_settings"}))
        assert await mod.get_env_admin_token_version() == 0

    async def test_bump_writes_stored_plus_one(self, monkeypatch):
        import utils.env_admin_tokens as mod

        update = AsyncMock()
        monkeypatch.setattr(
            mod.db_supabase,
            "find_one",
            AsyncMock(return_value={"id": "app_settings", "env_admin_token_version": 7}),
        )
        monkeypatch.setattr(mod.db_supabase, "update_one", update)

        assert await mod.bump_env_admin_token_version() == 8
        table, filters, patch_doc = update.await_args.args
        assert table == "settings"
        assert filters == {"id": "app_settings"}
        assert patch_doc == {"env_admin_token_version": 8}

    async def test_read_error_propagates_rather_than_defaulting(self, monkeypatch):
        """Callers decide the failure posture; the helper must not swallow."""
        import utils.env_admin_tokens as mod

        monkeypatch.setattr(mod.db_supabase, "find_one", AsyncMock(side_effect=RuntimeError("db down")))
        with pytest.raises(RuntimeError):
            await mod.get_env_admin_token_version()


class TestLogoutAllRevokesTheEnvAdmin:
    """The plan's stated verify step for C8."""

    async def test_logout_all_bumps_the_version_and_revokes_refresh_tokens(self, monkeypatch):
        import jwt as _jwt

        import routes.admin.auth as admin_auth
        from core.config import settings as _settings

        bump = AsyncMock(return_value=9)
        revoke = AsyncMock(return_value=2)
        monkeypatch.setattr(admin_auth, "bump_env_admin_token_version", bump)
        monkeypatch.setattr(admin_auth, "revoke_all_for_user", revoke)

        token = _jwt.encode(
            {"user_id": "admin-001", "role": "super_admin", "aud": JWT_AUD_ADMIN},
            _settings.JWT_SECRET,
            algorithm=_settings.ALGORITHM,
        )
        inner = _resolve_inner(admin_auth.admin_logout_all)
        result = await inner(_make_request(), authorization=f"Bearer {token}")

        bump.assert_awaited_once()
        revoke.assert_awaited_once_with("admin-001")
        # Same response shape as the staff branch — admin-dashboard's
        # lib/api/auth.ts types this as {success, revoked_refresh_tokens}.
        assert result == {"success": True, "revoked_refresh_tokens": 2}

    async def test_logout_all_no_longer_returns_400_for_the_env_admin(self, monkeypatch):
        """Regression: the previous behaviour was a hard 400 pointing the
        operator at an ADMIN_PASSWORD rotation + redeploy."""
        import jwt as _jwt

        import routes.admin.auth as admin_auth
        from core.config import settings as _settings

        monkeypatch.setattr(admin_auth, "bump_env_admin_token_version", AsyncMock(return_value=1))
        monkeypatch.setattr(admin_auth, "revoke_all_for_user", AsyncMock(return_value=0))

        token = _jwt.encode(
            {"user_id": "admin-001", "role": "super_admin", "aud": JWT_AUD_ADMIN},
            _settings.JWT_SECRET,
            algorithm=_settings.ALGORITHM,
        )
        inner = _resolve_inner(admin_auth.admin_logout_all)
        result = await inner(_make_request(), authorization=f"Bearer {token}")
        assert result["success"] is True


class TestEveryMintPathStampsTheCurrentVersion:
    """Login is not the only place an admin-001 access token is created.

    The refresh endpoint hardcoded ``token_version = 0`` for this account. That
    was harmless while nothing ever bumped the counter, but the moment an
    operator uses logout-all once, a refresh would mint a token that
    _verify_admin_payload rejects on sight — turning "refreshes for 30 days"
    into "re-login every hour" with no visible cause. Both mint paths must read
    the same stored value.
    """

    def test_no_mint_path_hardcodes_a_zero_version_for_the_env_admin(self):
        import inspect
        import re

        import routes.admin.auth as admin_auth

        src = inspect.getsource(admin_auth)
        # Strip comments so prose about the old behaviour does not trip this.
        code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
        assert not re.search(r"^\s*token_version\s*=\s*0\s*$", code, re.MULTILINE), (
            "a mint path assigns token_version = 0 literally; for admin-001 that "
            "produces a token already invalidated by any prior logout-all. Read "
            "get_env_admin_token_version() instead."
        )
        assert not re.search(r"token_version\s*=\s*0\s*,", code), (
            "a _mint_admin_access_token call passes token_version=0 literally — see above"
        )


class TestTheCounterIsNotDisclosedToStaff:
    """`GET /admin/settings` returns every column of the settings row, masking
    only the string-typed credentials in `_CREDENTIAL_FIELDS`. The revocation
    counter is an int, so without an explicit strip it would round-trip to any
    authenticated staff account — telling them how many times the super admin
    has been force-logged-out, and what version a token would have to claim.
    Useless without JWT_SECRET, but there is no reason to publish it.

    Raised by spinr-security-auditor against this change's diff.
    """

    def test_settings_response_omits_the_env_admin_counter(self):
        from routes.admin.settings import _mask_credentials

        masked = _mask_credentials(
            {
                "id": "app_settings",
                "env_admin_token_version": 7,
                "surge_enabled": True,
            }
        )
        assert "env_admin_token_version" not in masked
        # The strip must not swallow ordinary settings alongside it.
        assert masked["surge_enabled"] is True

    def test_credential_masking_still_works(self):
        """Regression guard on the same helper — the new `continue` sits above
        the masking branch, so a mistake there would silently unmask secrets."""
        from routes.admin.settings import _mask_credentials

        masked = _mask_credentials({"stripe_secret_key": "sk_test_abcdefghijklmnop"})
        assert masked["stripe_secret_key"] == "sk_test_*****"
