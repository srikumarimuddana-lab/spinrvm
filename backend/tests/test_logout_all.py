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
        fb_revoke = MagicMock()

        with (
            patch("backend.routes.auth.db.update_one", update_one),
            patch("backend.routes.auth.revoke_all_for_user", revoke_all),
            patch("backend.socket_manager.manager.kick_user", kick_user),
            patch("backend.routes.auth._revoke_firebase_refresh_tokens", fb_revoke),
        ):
            inner = _resolve_inner(logout_all)
            request = MagicMock()
            current_user = {"id": "user-rider-1", "token_version": 7}
            result = await inner(request, MagicMock(), current_user=current_user)

        # token_version bumped from 7 -> 8 against the right row, and the
        # Firebase revocation watermark stamped (dynamic timestamp string).
        args, _ = update_one.call_args
        assert args[0] == "users"
        assert args[1] == {"id": "user-rider-1"}
        set_payload = args[2]["$set"]
        assert set_payload["token_version"] == 8
        assert isinstance(set_payload["sessions_invalid_before"], str) and set_payload["sessions_invalid_before"]
        # refresh tokens revoked for the same user
        revoke_all.assert_awaited_once_with("user-rider-1")
        # Firebase refresh tokens revoked too (forces Firebase re-sign-in).
        fb_revoke.assert_called_once_with("user-rider-1")
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
            result = await inner(request, MagicMock(), current_user=current_user)

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
            result = await inner(request, MagicMock(), current_user=current_user)

        args, _ = update_one.call_args
        assert args[0] == "users"
        assert args[1] == {"id": "user-rider-2"}
        set_payload = args[2]["$set"]
        assert set_payload["token_version"] == 1
        assert isinstance(set_payload["sessions_invalid_before"], str) and set_payload["sessions_invalid_before"]
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
                await inner(request, MagicMock(), current_user=current_user)

        assert exc.value.status_code == 500
        assert "Could not invalidate sessions" in exc.value.detail
        # We must NOT have proceeded to revoke_all_for_user — that step
        # is meaningless if access tokens are still alive.
        revoke_all.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_takes_idle_driver_offline(self):
        """Sign-out-all used to PUT /drivers/{id}/status from the client
        AFTER token_version was bumped. That second call 401'd. Fold the
        go-offline into this request so the client has one round trip.
        Idle driver (no assigned ride) → is_online/is_available false +
        Period 0 + presence clear."""
        from backend.routes.auth import logout_all

        update_one = AsyncMock(return_value={})
        get_rows = AsyncMock(
            side_effect=[
                [{"id": "drv-1", "user_id": "user-drv-1", "is_online": True}],
                [],  # no obligated ride
            ]
        )
        revoke_all = AsyncMock(return_value=1)
        period = AsyncMock()
        clear_presence = AsyncMock()

        with (
            patch("backend.routes.auth.db.update_one", update_one),
            patch("backend.routes.auth.db.get_rows", get_rows),
            patch("backend.routes.auth.revoke_all_for_user", revoke_all),
            patch("backend.routes.auth.record_period_transition", period),
            patch("backend.routes.auth.clear_presence", clear_presence),
            patch("backend.socket_manager.manager.kick_user", AsyncMock(return_value=0)),
            patch("backend.routes.auth._revoke_firebase_refresh_tokens", MagicMock()),
        ):
            inner = _resolve_inner(logout_all)
            result = await inner(MagicMock(), MagicMock(), current_user={"id": "user-drv-1", "token_version": 0})

        assert result == {"success": True, "revoked_refresh_tokens": 1}
        driver_writes = [c for c in update_one.await_args_list if c.args[0] == "drivers"]
        assert len(driver_writes) == 1
        driver_payload = driver_writes[0].args[2]
        assert driver_payload["is_online"] is False
        assert driver_payload["is_available"] is False
        period.assert_awaited_once_with("drv-1", 0)
        clear_presence.assert_awaited_once_with("drv-1")

    @pytest.mark.asyncio
    async def test_skips_go_offline_when_driver_is_on_an_obligated_ride(self):
        """Period 2/3 must not close to Period 0 while a ride is still
        assigned — same rule as PUT /drivers/{id}/status 409. Sessions
        still die; insurance stays on the ride."""
        from backend.routes.auth import logout_all

        update_one = AsyncMock(return_value={})
        get_rows = AsyncMock(
            side_effect=[
                [{"id": "drv-2", "user_id": "user-drv-2", "is_online": True}],
                [{"id": "ride-1", "status": "in_progress"}],
            ]
        )
        period = AsyncMock()
        clear_presence = AsyncMock()

        with (
            patch("backend.routes.auth.db.update_one", update_one),
            patch("backend.routes.auth.db.get_rows", get_rows),
            patch("backend.routes.auth.revoke_all_for_user", AsyncMock(return_value=0)),
            patch("backend.routes.auth.record_period_transition", period),
            patch("backend.routes.auth.clear_presence", clear_presence),
            patch("backend.socket_manager.manager.kick_user", AsyncMock(return_value=0)),
            patch("backend.routes.auth._revoke_firebase_refresh_tokens", MagicMock()),
        ):
            inner = _resolve_inner(logout_all)
            result = await inner(MagicMock(), MagicMock(), current_user={"id": "user-drv-2", "token_version": 3})

        assert result["success"] is True
        assert all(c.args[0] != "drivers" for c in update_one.await_args_list)
        period.assert_not_awaited()
        clear_presence.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_driver_offline_failure_does_not_fail_logout_all(self):
        """Token kill is the contract. A driver-row write failure must
        not roll it back or 500 the button."""
        from backend.routes.auth import logout_all

        update_one = AsyncMock(side_effect=[{"id": "user-drv-3"}, RuntimeError("drivers 503")])
        get_rows = AsyncMock(
            side_effect=[
                [{"id": "drv-3", "user_id": "user-drv-3", "is_online": True}],
                [],
            ]
        )

        with (
            patch("backend.routes.auth.db.update_one", update_one),
            patch("backend.routes.auth.db.get_rows", get_rows),
            patch("backend.routes.auth.revoke_all_for_user", AsyncMock(return_value=1)),
            patch("backend.socket_manager.manager.kick_user", AsyncMock(return_value=0)),
            patch("backend.routes.auth._revoke_firebase_refresh_tokens", MagicMock()),
        ):
            inner = _resolve_inner(logout_all)
            result = await inner(MagicMock(), MagicMock(), current_user={"id": "user-drv-3", "token_version": 1})

        assert result == {"success": True, "revoked_refresh_tokens": 1}


# ─────────────────────────────────────────────────────────────────────────────
# /admin/auth/logout-all
# ─────────────────────────────────────────────────────────────────────────────


def _admin_jwt(user_id: str = "staff-001") -> str:
    """Mint a JWT the admin handler will accept. Uses the conftest
    JWT_SECRET fixture (test-secret-key-for-ci-only-32chars!!)."""
    from backend.core.config import settings
    from backend.dependencies import JWT_AUD_ADMIN

    return jwt.encode(
        {"user_id": user_id, "role": "support", "aud": JWT_AUD_ADMIN},
        settings.JWT_SECRET,
        algorithm=settings.ALGORITHM,
    )


class TestFirebaseSessionRevocation:
    """Pin the Firebase revocation contract. Firebase ID tokens carry no
    token_version claim, so /auth/logout-all enforces revocation via the
    users.sessions_invalid_before watermark compared against the token's
    auth_time. (Previously _token_version_mismatch({}, user) was used, which
    never revoked version-0 users and permanently locked out anyone who had
    bumped token_version.)"""

    def test_no_watermark_means_not_revoked(self):
        from backend.dependencies import _firebase_session_revoked

        assert _firebase_session_revoked({"auth_time": 1000}, None) is False
        assert _firebase_session_revoked({"auth_time": 1000}, "") is False

    def test_token_before_watermark_is_revoked(self):
        from datetime import datetime, timezone

        from backend.dependencies import _firebase_session_revoked

        watermark = datetime(2026, 6, 1, tzinfo=timezone.utc)
        before = int(datetime(2026, 5, 31, tzinfo=timezone.utc).timestamp())
        assert _firebase_session_revoked({"auth_time": before}, watermark.isoformat()) is True

    def test_token_after_watermark_is_allowed(self):
        from datetime import datetime, timezone

        from backend.dependencies import _firebase_session_revoked

        watermark = datetime(2026, 6, 1, tzinfo=timezone.utc)
        after = int(datetime(2026, 6, 2, tzinfo=timezone.utc).timestamp())
        assert _firebase_session_revoked({"auth_time": after}, watermark.isoformat()) is False

    def test_refreshed_token_keeps_old_auth_time_so_still_revoked(self):
        """The crux: refreshing a Firebase ID token does NOT change auth_time
        (the sign-in moment), only iat. A token refreshed AFTER logout-all must
        still be rejected because auth_time predates the watermark."""
        from datetime import datetime, timezone

        from backend.dependencies import _firebase_session_revoked

        watermark = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
        payload = {
            "auth_time": watermark - 3600,  # signed in an hour before logout-all
            "iat": watermark + 3600,  # but token refreshed an hour after
        }
        assert _firebase_session_revoked(payload, watermark) is True

    def test_same_second_token_is_revoked(self):
        """A Firebase token signed in during the same whole second as the
        logout-all watermark must be revoked. _to_epoch truncates the watermark
        to whole seconds while auth_time is already whole seconds, so the
        comparison must be `<=`, not `<`."""
        from backend.dependencies import _firebase_session_revoked

        # Watermark 1000.7s truncates to 1000; a token whose auth_time is 1000
        # (signed in earlier in that same second) must still be rejected.
        assert _firebase_session_revoked({"auth_time": 1000}, "1970-01-01T00:16:40.700000+00:00") is True

    def test_missing_auth_time_with_watermark_fails_closed(self):
        from backend.dependencies import _firebase_session_revoked

        assert _firebase_session_revoked({}, 1_700_000_000) is True

    def test_firebase_revoke_seam_swallows_errors(self):
        """The Firebase revoke is best-effort hardening; a Firebase SDK error
        (e.g. user not found for OTP/JWT users) must never propagate and fail
        logout-all — the watermark is the authoritative enforcement."""
        from backend.routes.auth import _revoke_firebase_refresh_tokens

        with patch("firebase_admin.auth.revoke_refresh_tokens", side_effect=RuntimeError("boom")):
            # Must not raise.
            _revoke_firebase_refresh_tokens("user-rider-1")

    def test_to_epoch_handles_iso_datetime_and_number(self):
        from datetime import datetime, timezone

        from backend.dependencies import _to_epoch

        dt = datetime(2026, 6, 1, tzinfo=timezone.utc)
        epoch = int(dt.timestamp())
        assert _to_epoch(dt.isoformat()) == epoch
        assert _to_epoch(dt.isoformat().replace("+00:00", "Z")) == epoch
        assert _to_epoch(dt) == epoch
        assert _to_epoch(epoch) == epoch
        assert _to_epoch(None) is None
        assert _to_epoch("") is None
        assert _to_epoch("not-a-date") is None


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
    async def test_revokes_admin_001_super_admin(self):
        """admin-001 IS revocable now — it must not be special-cased away.

        This test previously asserted the opposite: a 400 telling the operator
        to rotate ADMIN_PASSWORD and redeploy, on the reasoning that with no
        admin_staff row there was no token_version to bump. That made the env
        super admin the one account no in-app control could force-log-out, and
        during a Redis outage the JTI denylist (which fails open by design)
        could not kill it either. C8 in PR #5602 gave it a DB-backed
        token_version on the settings row, so this path now bumps it exactly as
        the staff branch does and every outstanding token dies on its next
        request. The old assertion is kept in the git history, not here.
        """
        from backend.routes.admin.auth import admin_logout_all

        token = _admin_jwt("admin-001")
        inner = _resolve_inner(admin_logout_all)
        request = MagicMock()

        bump = AsyncMock(return_value=7)
        with (
            patch("backend.routes.admin.auth.bump_env_admin_token_version", bump),
            patch("backend.routes.admin.auth.revoke_all_for_user", AsyncMock(return_value=3)),
        ):
            result = await inner(request, authorization=f"Bearer {token}")

        # The counter is bumped -- that is what actually revokes the tokens.
        bump.assert_awaited_once()
        assert result["success"] is True
        assert result["revoked_refresh_tokens"] == 3

    @pytest.mark.asyncio
    async def test_admin_001_logout_all_surfaces_a_failed_bump(self):
        """If the counter cannot be written, logout-all must NOT report success.

        The worst outcome this path can produce is answering 200 for a
        revocation that never reached the database: the operator believes a
        leaked super-admin token is dead while it stays live until expiry.
        bump_env_admin_token_version raises rather than returning quietly when
        the write matches no row, and that must propagate.
        """
        from backend.routes.admin.auth import admin_logout_all

        token = _admin_jwt("admin-001")
        inner = _resolve_inner(admin_logout_all)
        request = MagicMock()

        with patch(
            "backend.routes.admin.auth.bump_env_admin_token_version",
            AsyncMock(side_effect=RuntimeError("counter not written")),
        ):
            with pytest.raises(RuntimeError):
                await inner(request, authorization=f"Bearer {token}")

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
            "staff-real",
            client_types=["admin"],
            reason="logout_all",
        )
        assert result == {"success": True, "revoked_refresh_tokens": 2}
