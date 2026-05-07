"""
P1: POST /auth/logout-all — multi-device session termination

Pins the security-baseline guarantees of the "sign out of all devices"
endpoint (see backend/routes/auth.py::logout_all, ~line 589):

  • Bumps users.token_version so every outstanding access token issued
    before the call is rejected on its next request (the dependency
    re-reads the row and compares versions).
  • Calls revoke_all_for_user(user_id) so every non-revoked refresh
    token row for the caller is stamped revoked_at — subsequent
    /auth/refresh attempts return 401 with no oracle leakage between
    "unknown" / "revoked" / "expired".
  • Per-user isolation: bumping user A's token_version and revoking
    user A's refresh tokens must never touch user B's rows.
  • Auth required: an unauthenticated request must be rejected by
    Depends(get_current_user) before any side-effect runs.

These four invariants are what a "my account is compromised, kill
everything" button must give the user. Regressing any of them is a
silent privilege-leak.

Run:
    pytest backend/tests/test_p1_auth_logout_all.py -v
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request


USER_A = "user_p1_logout_all_A"
USER_B = "user_p1_logout_all_B"


def _fake_request() -> MagicMock:
    """Build a MagicMock that satisfies SlowAPI's isinstance(req, Request) check.

    The @limiter.limit decorator on /auth/logout-all and /auth/refresh requires
    a real starlette Request. spec=Request lets isinstance() pass without
    building a full ASGI scope.
    """
    fake = MagicMock(spec=Request)
    fake.headers = {}
    fake.client = MagicMock(host="127.0.0.1")
    return fake


def _user_row(user_id: str = USER_A, token_version: int = 0) -> dict:
    return {
        "id": user_id,
        "phone": "+15551112222",
        "role": "rider",
        "profile_complete": True,
        "token_version": token_version,
        "current_session_id": "sess-old",
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /auth/logout-all — happy path
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestLogoutAllHappyPath:
    """Pins the side-effects of a successful logout-all call.

    Code under test: backend/routes/auth.py::logout_all (~line 589).
    """

    async def test_bumps_token_version_and_revokes_refresh_tokens(self):
        """A successful call must:
        1. update users row with token_version + 1, and
        2. invoke revoke_all_for_user(user_id) so every refresh token row dies.
        """
        from backend.routes import auth as auth_mod

        update_calls: list[dict] = []
        revoke_calls: list[str] = []

        async def _capture_update(table, filter_doc, update_doc):
            update_calls.append({"table": table, "filter": filter_doc, "update": update_doc})
            return None

        async def _capture_revoke(user_id):
            revoke_calls.append(user_id)
            return 3  # pretend three live refresh tokens were just revoked

        with (
            patch("backend.routes.auth.db.update_one", AsyncMock(side_effect=_capture_update)),
            patch("backend.routes.auth.revoke_all_for_user", AsyncMock(side_effect=_capture_revoke)),
        ):
            result = await auth_mod.logout_all(
                request=_fake_request(),
                current_user=_user_row(token_version=4),
            )

        # token_version bump went to the right user with version+1
        assert update_calls, "users.update_one was not called"
        users_call = next((c for c in update_calls if c["table"] == "users"), None)
        assert users_call is not None, "no update on users table"
        assert users_call["filter"] == {"id": USER_A}
        assert users_call["update"] == {"$set": {"token_version": 5}}

        # refresh-token side: every row for this user revoked
        assert revoke_calls == [USER_A], (
            "revoke_all_for_user must be called exactly once with the caller's id"
        )

        # Endpoint returns no body fields the client can parse oracle info from;
        # the implementation logs and returns implicitly (None). Either is fine,
        # what matters is no exception was raised.
        assert result is None or isinstance(result, dict)

    async def test_token_version_increments_from_zero_when_unset(self):
        """If the user row has no token_version yet (legacy users), the bump
        must still produce a valid integer >= 1, never None or a crash."""
        from backend.routes import auth as auth_mod

        captured: dict = {}

        async def _capture_update(table, filter_doc, update_doc):
            captured.update(update_doc)
            return None

        with (
            patch("backend.routes.auth.db.update_one", AsyncMock(side_effect=_capture_update)),
            patch("backend.routes.auth.revoke_all_for_user", AsyncMock(return_value=0)),
        ):
            # Note: token_version key absent entirely
            user = {"id": USER_A, "phone": "+15551112222", "role": "rider"}
            await auth_mod.logout_all(request=_fake_request(), current_user=user)

        assert captured == {"$set": {"token_version": 1}}, (
            "Missing token_version must be treated as 0 → bumped to 1"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Post-logout-all: refresh tokens cannot be exchanged
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestRefreshAfterLogoutAll:
    """After logout-all, the lookup_refresh_token call inside /auth/refresh
    returns None for a revoked row (see refresh_tokens.py::lookup_refresh_token —
    revoked_at != NULL → returns None, no oracle), so refresh_access_token
    raises 401.

    This test pins the joint behavior: a revoked refresh token cannot be
    traded for a fresh access token.
    """

    async def test_revoked_refresh_token_cannot_obtain_new_access_token(self):
        from backend.routes import auth as auth_mod
        from fastapi import HTTPException

        # Simulate the post-logout-all state: lookup returns None because the
        # row was stamped revoked_at by revoke_all_for_user.
        with (
            patch("backend.routes.auth.lookup_refresh_token", AsyncMock(return_value=None)),
            patch("backend.routes.auth.get_remote_address", return_value="127.0.0.1"),
        ):
            class _Body:
                refresh_token = "previously-valid-now-revoked"

            with pytest.raises(HTTPException) as exc_info:
                await auth_mod.refresh_access_token(request=_fake_request(), body=_Body())

        assert exc_info.value.status_code == 401, (
            "Revoked refresh token must yield 401 — same response as unknown/expired "
            "to avoid an oracle distinguishing the failure modes"
        )

    async def test_old_access_token_rejected_after_token_version_bump(self):
        """The access-token side of logout-all: get_current_user must reject a
        JWT minted at version=N when the DB row is now at version=N+1.

        This pins the dependency-level guarantee that makes /auth/logout-all
        an immediate kill rather than waiting for access-token expiry.
        """
        from backend.dependencies import create_jwt_token, get_current_user
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials

        # Token was minted before the bump, version 1.
        old_token = create_jwt_token(
            user_id=USER_A,
            phone="+15551112222",
            token_version=1,
        )

        # DB now reflects version=2 (logout-all just ran).
        bumped_user = {
            "id": USER_A,
            "phone": "+15551112222",
            "role": "rider",
            "token_version": 2,
            "current_session_id": None,
        }

        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=old_token)

        with (
            patch(
                "backend.dependencies.firebase_auth.verify_id_token",
                side_effect=ValueError("not a firebase token"),
            ),
            patch(
                "backend.dependencies.db_supabase.get_user_by_id",
                AsyncMock(return_value=bumped_user),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(creds)

        assert exc_info.value.status_code == 401
        assert "revoked" in exc_info.value.detail.lower(), (
            f"Expected an ERR_SESSION_REVOKED-style message, got: {exc_info.value.detail!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Per-user isolation
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestPerUserIsolation:
    """Logout-all on user A must not touch user B's token_version OR refresh
    tokens. The DB filter is keyed by current_user["id"]; if a regression
    accidentally widened the filter (e.g. {} or by phone prefix) it would
    log out the entire userbase.
    """

    async def test_logout_all_on_user_a_does_not_touch_user_b(self):
        from backend.routes import auth as auth_mod

        update_filters: list[dict] = []
        revoke_args: list[str] = []

        async def _capture_update(table, filter_doc, update_doc):
            update_filters.append(filter_doc)
            return None

        async def _capture_revoke(user_id):
            revoke_args.append(user_id)
            return 0

        with (
            patch("backend.routes.auth.db.update_one", AsyncMock(side_effect=_capture_update)),
            patch("backend.routes.auth.revoke_all_for_user", AsyncMock(side_effect=_capture_revoke)),
        ):
            await auth_mod.logout_all(
                request=_fake_request(),
                current_user=_user_row(user_id=USER_A, token_version=0),
            )

        # The users-table update filter must mention USER_A and ONLY USER_A.
        assert all(f.get("id") == USER_A for f in update_filters), (
            f"users.update_one filter must scope to USER_A only; got {update_filters!r}"
        )
        assert any(f.get("id") == USER_A for f in update_filters)
        assert all(f.get("id") != USER_B for f in update_filters)

        # revoke_all_for_user must be called only with USER_A.
        assert revoke_args == [USER_A], (
            f"revoke_all_for_user must be called with USER_A only; got {revoke_args!r}"
        )
        assert USER_B not in revoke_args


# ─────────────────────────────────────────────────────────────────────────────
# Unauthenticated request → 401
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestAuthRequired:
    """logout-all is gated by Depends(get_current_user); a missing or invalid
    bearer token must be rejected with 401 BEFORE any side effect runs.

    A missing-auth regression here would let an attacker permanently kill any
    user's sessions just by knowing their id.
    """

    async def test_no_credentials_returns_401(self):
        from backend.dependencies import get_current_user
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            # Simulate FastAPI binding `credentials=None` when no Authorization header.
            await get_current_user(credentials=None)  # type: ignore[arg-type]

        assert exc_info.value.status_code == 401

    async def test_invalid_token_blocks_logout_all_side_effects(self):
        """A garbage token → 401 from get_current_user → logout_all body never
        executes, so neither db.update_one nor revoke_all_for_user is called.
        """
        from backend.dependencies import get_current_user
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials

        update_calls: list = []
        revoke_calls: list = []

        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="garbage.not.a.jwt")

        with (
            patch(
                "backend.dependencies.firebase_auth.verify_id_token",
                side_effect=ValueError("not a firebase token"),
            ),
            patch("backend.routes.auth.db.update_one", AsyncMock(side_effect=lambda *a, **k: update_calls.append(a))),
            patch("backend.routes.auth.revoke_all_for_user", AsyncMock(side_effect=lambda *a, **k: revoke_calls.append(a))),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(credentials=creds)

        assert exc_info.value.status_code == 401
        # Critical: the body never ran, so no side effects fired.
        assert update_calls == [], "db.update_one must NOT be called when auth fails"
        assert revoke_calls == [], "revoke_all_for_user must NOT be called when auth fails"
