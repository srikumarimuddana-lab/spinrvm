"""B-P1-13 — pin the contract for /auth/logout-all and /admin/auth/logout-all.

These two endpoints are the user-facing kill-switch promised in
docs/runbooks/auth-tokens.md (the "Sign out of all devices" /
"Sign out everywhere" buttons we just wired into the rider account
screen, driver profile, and admin sidebar).

Contract pinned here — break either and the runbook recovery path
is silently broken too:

  /auth/logout-all (rider/driver):
    * Bumps users.token_version (kills in-flight access tokens via the
      dependencies.py middleware re-read on next request).
    * Calls revoke_all_for_user() (revokes every non-revoked refresh
      token row).
    * Returns {"success": True, "revoked_refresh_tokens": <int>}.
    * On token_version bump failure -> HTTP 500 (refuses to half-do
      the job and leave the operator believing they're signed out).

  /admin/auth/logout-all:
    * 401 when Authorization header missing.
    * 400 when caller is admin-001 (env-var super admin has no DB
      row -> no token_version to bump; the runbook tells operators
      to rotate ADMIN_PASSWORD instead).
    * 404 when the staff row referenced by the JWT no longer exists.
    * Happy path: bumps admin_staff.token_version + revokes refresh
      tokens + same response shape as the rider/driver endpoint.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi import HTTPException


def _resolve_inner(fn):
    """Unwrap slowapi's @limiter.limit closure so we can call the
    handler directly without tripping rate-limit state. Same helper
    used in test_auth_send_otp.py."""
    while True:
        nxt = getattr(fn, "__wrapped__", None)
        if nxt is None:
            closure = getattr(fn, "__closure__", None) or ()
            for cell in closure:
                val = cell.cell_contents
                if callable(val) and getattr(val, "__code__", None) is not None:
                    if val is fn:
                        continue
                    return val
            return fn
        fn = nxt


# ─────────────────────────────────────────────────────────────────────────────
# /auth/logout-all (rider/driver)
# ─────────────────────────────────────────────────────────────────────────────


class TestLogoutAllRiderDriver:
    """Pin /auth/logout-all behaviour. Handler lives at
    backend/routes/auth.py::logout_all and depends on get_current_user
    (which we bypass by passing current_user= directly to the unwrapped
    coroutine)."""

    @pytest.mark.asyncio
    async def test_bumps_token_version_and_revokes_refresh_tokens(self):
        from backend.routes.auth import logout_all

        update_one = AsyncMock(return_value={"id": "user-rider-1"})
        revoke_all = AsyncMock(return_value=3)
        kick_user = AsyncMock(return_value=0)

        with (
            patch("backend.routes.auth.db.update_one", update_one),
            patch("backend.routes.auth.revoke_all_for_user", revoke_all),
            patch("backend.socket_manager.manager.kick_user", kick_user),
        ):
            inner = _resolve_inner(logout_all)
            request = MagicMock()
            current_user = {"id": "user-rider-1", "token_version": 7}
            result = await inner(request, current_user=current_user)

        # token_version bumped from 7 -> 8 against the right row
        update_one.assert_awaited_once_with(
            "users",
            {"id": "user-rider-1"},
            {"$set": {"token_version": 8}},
        )
        # refresh tokens revoked for the same user
        revoke_all.assert_awaited_once_with("user-rider-1")
        # B-P1-11: WS sockets kicked for the same user, scoped to
        # rider+driver (not admin). Pin the scope — admin sockets
        # belong to a different identity space.
        kick_user.assert_awaited_once_with(
            "user-rider-1",
            client_types=["rider", "driver"],
            reason="logout_all",
        )
        # response shape the clients (authStore.logoutAll) parse
        assert result == {"success": True, "revoked_refresh_tokens": 3}

    @pytest.mark.asyncio
    async def test_ws_kick_failure_does_not_fail_the_response(self):
        """B-P1-11: kick is best-effort. The token_version bump + refresh
        revoke are the durable contract; the WS kick is a UX
        accelerator. A kick failure (Redis hiccup, manager bug) must
        NOT roll back the logout — the heartbeat re-validation closes
        the socket within 30s anyway."""
        from backend.routes.auth import logout_all

        update_one = AsyncMock(return_value={"id": "user-rider-x"})
        revoke_all = AsyncMock(return_value=2)
        kick_user = AsyncMock(side_effect=RuntimeError("manager exploded"))

        with (
            patch("backend.routes.auth.db.update_one", update_one),
            patch("backend.routes.auth.revoke_all_for_user", revoke_all),
            patch("backend.socket_manager.manager.kick_user", kick_user),
        ):
            inner = _resolve_inner(logout_all)
            request = MagicMock()
            current_user = {"id": "user-rider-x", "token_version": 0}
            result = await inner(request, current_user=current_user)

        # The durable contract still landed.
        update_one.assert_awaited_once()
        revoke_all.assert_awaited_once_with("user-rider-x")
        # And we still respond success — the operator's "Sign out
        # everywhere" button must not show an error when token_version
        # was successfully bumped.
        assert result == {"success": True, "revoked_refresh_tokens": 2}

    @pytest.mark.asyncio
    async def test_treats_missing_token_version_as_zero(self):
        """First-ever logout-all on a user row that predates the
        token_version column (NULL/missing) must still bump cleanly to 1
        rather than crash on int(None)."""
        from backend.routes.auth import logout_all

        update_one = AsyncMock(return_value={})
        revoke_all = AsyncMock(return_value=0)

        with (
            patch("backend.routes.auth.db.update_one", update_one),
            patch("backend.routes.auth.revoke_all_for_user", revoke_all),
        ):
            inner = _resolve_inner(logout_all)
            request = MagicMock()
            # token_version key entirely absent — handler uses .get(...) or 0
            current_user = {"id": "user-rider-2"}
            result = await inner(request, current_user=current_user)

        update_one.assert_awaited_once_with(
            "users",
            {"id": "user-rider-2"},
            {"$set": {"token_version": 1}},
        )
        assert result == {"success": True, "revoked_refresh_tokens": 0}

    @pytest.mark.asyncio
    async def test_raises_500_when_token_version_bump_fails(self):
        """If we cannot bump token_version, refuse to claim success.
        Per CLAUDE.md "do not silently swallow errors" — telling the
        operator they are signed out everywhere when access tokens are
        still live is the worst possible outcome."""
        from backend.routes.auth import logout_all

        update_one = AsyncMock(side_effect=RuntimeError("supabase 503"))
        revoke_all = AsyncMock()

        with (
            patch("backend.routes.auth.db.update_one", update_one),
            patch("backend.routes.auth.revoke_all_for_user", revoke_all),
        ):
            inner = _resolve_inner(logout_all)
            request = MagicMock()
            current_user = {"id": "user-rider-3", "token_version": 1}
            with pytest.raises(HTTPException) as exc:
                await inner(request, current_user=current_user)

        assert exc.value.status_code == 500
        assert "Could not invalidate sessions" in exc.value.detail
        # We must NOT have proceeded to revoke_all_for_user — that step
        # is meaningless if access tokens are still alive.
        revoke_all.assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────────────
# /admin/auth/logout-all
# ─────────────────────────────────────────────────────────────────────────────


def _admin_jwt(user_id: str = "staff-001") -> str:
    """Mint a JWT the admin handler will accept. Uses the conftest
    JWT_SECRET fixture (test-secret-key-for-ci-only-32chars!!)."""
    from backend.core.config import settings

    return jwt.encode(
        {"user_id": user_id, "role": "support"},
        settings.JWT_SECRET,
        algorithm=settings.ALGORITHM,
    )


class TestAdminLogoutAll:
    """Pin /admin/auth/logout-all behaviour. Handler lives at
    backend/routes/admin/auth.py::admin_logout_all. Unlike the rider
    path it parses Authorization itself (no Depends(get_current_user))
    so we exercise the full header → JWT-decode → DB-lookup pipeline."""

    @pytest.mark.asyncio
    async def test_rejects_missing_authorization(self):
        from backend.routes.admin.auth import admin_logout_all

        inner = _resolve_inner(admin_logout_all)
        request = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await inner(request, authorization=None)

        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_rejects_non_bearer_scheme(self):
        from backend.routes.admin.auth import admin_logout_all

        inner = _resolve_inner(admin_logout_all)
        request = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await inner(request, authorization="Basic deadbeef")

        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_refuses_admin_001_super_admin(self):
        """admin-001 is the env-var super admin — no DB row exists, so
        bumping token_version is meaningless. Runbook tells operators
        to rotate ADMIN_PASSWORD; the handler must redirect them
        there explicitly rather than silently no-op."""
        from backend.routes.admin.auth import admin_logout_all

        token = _admin_jwt("admin-001")
        inner = _resolve_inner(admin_logout_all)
        request = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await inner(request, authorization=f"Bearer {token}")

        assert exc.value.status_code == 400
        # Must explicitly mention the rotation path so the operator
        # doesn't have to grep the runbook for the recovery step.
        assert "ADMIN_PASSWORD" in exc.value.detail

    @pytest.mark.asyncio
    async def test_404_when_staff_not_found(self):
        from backend.routes.admin.auth import admin_logout_all

        token = _admin_jwt("staff-ghost")
        find_one = AsyncMock(return_value=None)
        inner = _resolve_inner(admin_logout_all)
        request = MagicMock()

        with patch("backend.routes.admin.auth.db.find_one", find_one):
            with pytest.raises(HTTPException) as exc:
                await inner(request, authorization=f"Bearer {token}")

        assert exc.value.status_code == 404
        find_one.assert_awaited_once_with("admin_staff", {"id": "staff-ghost"})

    @pytest.mark.asyncio
    async def test_bumps_admin_staff_and_revokes(self):
        from backend.routes.admin.auth import admin_logout_all

        token = _admin_jwt("staff-real")
        find_one = AsyncMock(return_value={"id": "staff-real", "token_version": 4})
        update_one = AsyncMock(return_value={"id": "staff-real"})
        revoke_all = AsyncMock(return_value=2)
        kick_user = AsyncMock(return_value=0)

        with (
            patch("backend.routes.admin.auth.db.find_one", find_one),
            patch("backend.routes.admin.auth.db.update_one", update_one),
            patch("backend.routes.admin.auth.revoke_all_for_user", revoke_all),
            patch("backend.socket_manager.manager.kick_user", kick_user),
        ):
            inner = _resolve_inner(admin_logout_all)
            request = MagicMock()
            result = await inner(request, authorization=f"Bearer {token}")

        update_one.assert_awaited_once_with(
            "admin_staff",
            {"id": "staff-real"},
            {"$set": {"token_version": 5}},
        )
        revoke_all.assert_awaited_once_with("staff-real")
        # B-P1-11: admin sockets only — never kick rider/driver sockets
        # for an admin force-logout (different identity space).
        kick_user.assert_awaited_once_with(
            "staff-real", client_types=["admin"], reason="logout_all",
        )
        assert result == {"success": True, "revoked_refresh_tokens": 2}
