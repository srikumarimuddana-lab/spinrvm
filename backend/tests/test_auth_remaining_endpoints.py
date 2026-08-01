"""routes/auth.py — remaining untested endpoints closing the coverage gap
past verify_otp (see docs/change-log/2026-07-29-a1b-verify-otp-coverage.md
and test_verify_otp_login_flow.py for the established pattern).

Covers what test_company_email_login.py, test_logout_all.py,
test_p1_token_refresh.py, and test_p1_auth_hardening.py do NOT already pin:
  - plain POST /auth/logout (cookie + body token revoke, redis session
    delete, cookie clearing, best-effort audit failure)
  - firebase_auth_login happy paths (new user, existing user) and the
    pending_deletion PIPEDA handoff — the hardening suite only covers
    DB-failure and revocation branches, never a successful mint
  - reactivate_account (PIPEDA self-serve reactivation): happy path,
    idempotent re-call on an already-active account, expired/invalid
    token, purged-account 410, and DB-write failure surfacing 503
  - send_company_email_otp / verify_company_email_otp DB and email
    failure branches not covered by test_company_email_login.py
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _resolve_inner(fn):
    """slowapi's @limiter.limit wraps the coroutine without setting
    __wrapped__. Walk __closure__ cells to pull out the original so we can
    call the handler without tripping rate-limit state. (Same helper as
    test_verify_otp_login_flow.py / test_logout_all.py.)"""
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


def _request(cookies: dict | None = None) -> MagicMock:
    request = MagicMock()
    request.client = MagicMock(host="127.0.0.1")
    request.headers = {"user-agent": "pytest"}
    request.cookies = cookies or {}
    return request


# ─────────────────────────────────────────────────────────────────────────────
# POST /auth/logout
# ─────────────────────────────────────────────────────────────────────────────


class TestLogout:
    @pytest.mark.asyncio
    async def test_revokes_cookie_refresh_token_and_clears_session(self):
        from backend.routes.auth import logout

        revoke = AsyncMock()
        redis_del = AsyncMock()
        audit = AsyncMock()
        with (
            patch("backend.routes.auth.revoke_refresh_token", revoke),
            patch("backend.routes.auth.redis_delete", redis_del),
            patch("backend.routes.auth._audit_log_user", audit),
            patch("backend.routes.auth.clear_csrf_cookie"),
        ):
            inner = _resolve_inner(logout)
            request = _request(cookies={"refresh_token": "cookie-raw-token"})
            current_user = {"id": "u1"}
            result = await inner(request, MagicMock(), body=None, current_user=current_user)

        revoke.assert_awaited_once_with("cookie-raw-token")
        redis_del.assert_awaited_once_with("session:u1")
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_falls_back_to_body_token_when_no_cookie(self):
        from backend.routes.auth import LogoutRequest, logout

        revoke = AsyncMock()
        with (
            patch("backend.routes.auth.revoke_refresh_token", revoke),
            patch("backend.routes.auth.redis_delete", AsyncMock()),
            patch("backend.routes.auth._audit_log_user", AsyncMock()),
            patch("backend.routes.auth.clear_csrf_cookie"),
        ):
            inner = _resolve_inner(logout)
            request = _request()  # no cookie
            body = LogoutRequest(refresh_token="body-raw-token")
            result = await inner(request, MagicMock(), body=body, current_user={"id": "u2"})

        revoke.assert_awaited_once_with("body-raw-token")
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_no_refresh_token_still_clears_session_and_cookies(self):
        """No cookie, no body token — still a valid logout: clears the
        server-side session and cookies without erroring."""
        from backend.routes.auth import logout

        revoke = AsyncMock()
        redis_del = AsyncMock()
        clear_cookie = MagicMock()
        with (
            patch("backend.routes.auth.revoke_refresh_token", revoke),
            patch("backend.routes.auth.redis_delete", redis_del),
            patch("backend.routes.auth._audit_log_user", AsyncMock()),
            patch("backend.routes.auth.clear_csrf_cookie", clear_cookie),
        ):
            inner = _resolve_inner(logout)
            request = _request()
            result = await inner(request, MagicMock(), body=None, current_user={"id": "u3"})

        revoke.assert_not_awaited()
        redis_del.assert_awaited_once_with("session:u3")
        clear_cookie.assert_called_once()
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_audit_log_failure_does_not_fail_logout(self):
        """Best-effort audit write — a Sentry/audit hiccup must not block
        a user from successfully signing out."""
        from backend.routes.auth import logout

        with (
            patch("backend.routes.auth.revoke_refresh_token", AsyncMock()),
            patch("backend.routes.auth.redis_delete", AsyncMock()),
            patch("backend.routes.auth._audit_log_user", AsyncMock(side_effect=RuntimeError("audit down"))),
            patch("backend.routes.auth.clear_csrf_cookie"),
        ):
            inner = _resolve_inner(logout)
            request = _request(cookies={"refresh_token": "raw"})
            result = await inner(request, MagicMock(), body=None, current_user={"id": "u4"})

        assert result == {"success": True}


# ─────────────────────────────────────────────────────────────────────────────
# POST /auth/firebase — firebase_auth_login happy paths + pending_deletion
# ─────────────────────────────────────────────────────────────────────────────


class TestFirebaseAuthLoginHappyPaths:
    @pytest.mark.asyncio
    async def test_new_user_created_and_logged_in(self):
        from backend.routes.auth import FirebaseAuthRequest, firebase_auth_login

        payload = {"uid": "fb-new-1", "phone_number": "+13065550001", "aud": "driver-app"}
        fb_stub = MagicMock()
        fb_stub.verify_id_token.return_value = payload

        with (
            patch.dict("sys.modules", {"firebase_admin.auth": fb_stub}),
            patch("backend.routes.auth.settings.FIREBASE_DRIVER_APP_ID", "driver-app"),
            patch("backend.routes.auth.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
            patch("backend.routes.auth.db_supabase.get_user_by_phone", AsyncMock(return_value=None)),
            patch("backend.routes.auth.db_supabase.create_user", AsyncMock(return_value=True)),
            patch(
                "backend.routes.auth.issue_refresh_token",
                AsyncMock(return_value=("raw-refresh", "hash", datetime.now(timezone.utc) + timedelta(days=30))),
            ),
            patch("backend.routes.auth._audit_log_user", AsyncMock()),
        ):
            import sys

            sys.modules["firebase_admin"].auth = fb_stub
            request = _request()
            response = MagicMock()
            result = await firebase_auth_login(request, response, FirebaseAuthRequest(firebase_token="tok"))

        assert result.is_new_user is True
        assert result.token
        assert result.refresh_token == "raw-refresh"

    @pytest.mark.asyncio
    async def test_existing_user_logged_in(self):
        from backend.routes.auth import FirebaseAuthRequest, firebase_auth_login

        payload = {"uid": "fb-existing-1", "phone_number": "+13065550002", "aud": "driver-app"}
        existing = {
            "id": "fb-existing-1",
            "phone": "+13065550002",
            "role": "driver",
            "is_rider": False,
            "is_driver": True,
            "token_version": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        fb_stub = MagicMock()
        fb_stub.verify_id_token.return_value = payload

        with (
            patch.dict("sys.modules", {"firebase_admin.auth": fb_stub}),
            patch("backend.routes.auth.settings.FIREBASE_DRIVER_APP_ID", "driver-app"),
            patch("backend.routes.auth.db_supabase.get_user_by_id", AsyncMock(return_value=dict(existing))),
            patch("backend.routes.auth.db_supabase.update_one", AsyncMock(return_value=True)),
            patch(
                "backend.routes.auth.issue_refresh_token",
                AsyncMock(return_value=("raw-refresh-2", "hash", datetime.now(timezone.utc) + timedelta(days=30))),
            ),
            patch("backend.routes.auth._audit_log_user", AsyncMock()),
        ):
            import sys

            sys.modules["firebase_admin"].auth = fb_stub
            request = _request()
            response = MagicMock()
            result = await firebase_auth_login(request, response, FirebaseAuthRequest(firebase_token="tok"))

        assert result.is_new_user is False
        assert result.token

    @pytest.mark.asyncio
    async def test_pending_deletion_returns_reactivation_handoff(self):
        from backend.routes.auth import FirebaseAuthRequest, firebase_auth_login

        payload = {"uid": "fb-pending-1", "phone_number": "+13065550003", "aud": "driver-app"}
        existing = {
            "id": "fb-pending-1",
            "phone": "+13065550003",
            "status": "pending_deletion",
            "deletion_scheduled_at": "2026-08-15T00:00:00Z",
        }
        fb_stub = MagicMock()
        fb_stub.verify_id_token.return_value = payload

        with (
            patch.dict("sys.modules", {"firebase_admin.auth": fb_stub}),
            patch("backend.routes.auth.settings.FIREBASE_DRIVER_APP_ID", "driver-app"),
            patch("backend.routes.auth.db_supabase.get_user_by_id", AsyncMock(return_value=dict(existing))),
        ):
            import sys

            sys.modules["firebase_admin"].auth = fb_stub
            request = _request()
            response = MagicMock()
            result = await firebase_auth_login(request, response, FirebaseAuthRequest(firebase_token="tok"))

        body = json.loads(result.body)
        assert body["requires_reactivation"] is True
        assert body["reactivation_token"]

    @pytest.mark.asyncio
    async def test_deleted_account_raises_410(self):
        from backend.routes.auth import FirebaseAuthRequest, firebase_auth_login

        payload = {"uid": "fb-deleted-1", "phone_number": "+13065550004", "aud": "driver-app"}
        existing = {"id": "fb-deleted-1", "phone": "+13065550004", "status": "deleted"}
        fb_stub = MagicMock()
        fb_stub.verify_id_token.return_value = payload

        with (
            patch.dict("sys.modules", {"firebase_admin.auth": fb_stub}),
            patch("backend.routes.auth.settings.FIREBASE_DRIVER_APP_ID", "driver-app"),
            patch("backend.routes.auth.db_supabase.get_user_by_id", AsyncMock(return_value=dict(existing))),
        ):
            import sys

            sys.modules["firebase_admin"].auth = fb_stub
            with pytest.raises(HTTPException) as exc:
                await firebase_auth_login(_request(), MagicMock(), FirebaseAuthRequest(firebase_token="tok"))

        assert exc.value.status_code == 410
        assert exc.value.detail == "ERR_ACCOUNT_DELETED"

    @pytest.mark.asyncio
    async def test_invalid_firebase_token_raises_401(self):
        from backend.routes.auth import FirebaseAuthRequest, firebase_auth_login
        from backend.utils.error_handling import SpinrException

        fb_stub = MagicMock()
        fb_stub.verify_id_token.side_effect = Exception("bad token")

        with patch.dict("sys.modules", {"firebase_admin.auth": fb_stub}):
            import sys

            sys.modules["firebase_admin"].auth = fb_stub
            with pytest.raises(SpinrException) as exc:
                await firebase_auth_login(_request(), MagicMock(), FirebaseAuthRequest(firebase_token="bad"))

        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_driver_audience_config_raises_503(self):
        from backend.routes.auth import FirebaseAuthRequest, firebase_auth_login
        from backend.utils.error_handling import SpinrException

        payload = {"uid": "fb-x", "phone_number": "+13065550005", "aud": "driver-app"}
        fb_stub = MagicMock()
        fb_stub.verify_id_token.return_value = payload

        with (
            patch.dict("sys.modules", {"firebase_admin.auth": fb_stub}),
            patch("backend.routes.auth.settings.FIREBASE_DRIVER_APP_ID", ""),
        ):
            import sys

            sys.modules["firebase_admin"].auth = fb_stub
            with pytest.raises(SpinrException) as exc:
                await firebase_auth_login(_request(), MagicMock(), FirebaseAuthRequest(firebase_token="tok"))

        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_wrong_audience_token_raises_401(self):
        """A token minted for a different Firebase app must not be accepted —
        audience binding guard (B-P1-1/DV-10)."""
        from backend.routes.auth import FirebaseAuthRequest, firebase_auth_login
        from backend.utils.error_handling import SpinrException

        payload = {"uid": "fb-y", "phone_number": "+13065550006", "aud": "some-other-app"}
        fb_stub = MagicMock()
        fb_stub.verify_id_token.return_value = payload

        with (
            patch.dict("sys.modules", {"firebase_admin.auth": fb_stub}),
            patch("backend.routes.auth.settings.FIREBASE_DRIVER_APP_ID", "driver-app"),
        ):
            import sys

            sys.modules["firebase_admin"].auth = fb_stub
            with pytest.raises(SpinrException) as exc:
                await firebase_auth_login(_request(), MagicMock(), FirebaseAuthRequest(firebase_token="tok"))

        assert exc.value.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# POST /auth/reactivate — PIPEDA self-serve reactivation
# ─────────────────────────────────────────────────────────────────────────────


class TestReactivateAccount:
    @pytest.mark.asyncio
    async def test_pending_deletion_account_reactivated_and_logged_in(self):
        from backend.routes.auth import ReactivateRequest, reactivate_account

        user = {
            "id": "u-react-1",
            "phone": "+13065551111",
            "status": "pending_deletion",
            "token_version": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        update_mock = AsyncMock(return_value=True)
        with (
            patch("backend.routes.auth.verify_reactivation_token", return_value="u-react-1"),
            patch("backend.routes.auth.db_supabase.get_user_by_id", AsyncMock(return_value=dict(user))),
            patch("backend.routes.auth.db_supabase.update_one", update_mock),
            patch("backend.routes.auth.redis_set", AsyncMock()),
            patch(
                "backend.routes.auth.issue_refresh_token",
                AsyncMock(return_value=("raw-refresh", "hash", datetime.now(timezone.utc) + timedelta(days=30))),
            ),
            patch("backend.routes.auth._audit_log_user", AsyncMock()),
        ):
            inner = _resolve_inner(reactivate_account)
            body = ReactivateRequest(reactivation_token="valid-token")
            result = await inner(_request(), MagicMock(), body)

        assert result.token
        assert result.refresh_token == "raw-refresh"
        # First update_one call restores status; called with users table + user id
        calls = [c.args for c in update_mock.await_args_list]
        assert any(c[0] == "users" and c[1] == {"id": "u-react-1"} and c[2].get("status") == "active" for c in calls)

    @pytest.mark.asyncio
    async def test_already_active_account_is_idempotent(self):
        """A repeat reactivate call on an already-active account must skip
        the restore write and just re-issue a session."""
        from backend.routes.auth import ReactivateRequest, reactivate_account

        user = {
            "id": "u-react-2",
            "phone": "+13065552222",
            "status": "active",
            "token_version": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        update_mock = AsyncMock(return_value=True)
        with (
            patch("backend.routes.auth.verify_reactivation_token", return_value="u-react-2"),
            patch("backend.routes.auth.db_supabase.get_user_by_id", AsyncMock(return_value=dict(user))),
            patch("backend.routes.auth.db_supabase.update_one", update_mock),
            patch("backend.routes.auth.redis_set", AsyncMock()),
            patch(
                "backend.routes.auth.issue_refresh_token",
                AsyncMock(return_value=("raw-refresh", "hash", datetime.now(timezone.utc) + timedelta(days=30))),
            ),
        ):
            inner = _resolve_inner(reactivate_account)
            body = ReactivateRequest(reactivation_token="valid-token")
            result = await inner(_request(), MagicMock(), body)

        assert result.token
        # Only the session_id update happened — never a status="active" write.
        for c in update_mock.await_args_list:
            assert c.args[2].get("status") != "active"

    @pytest.mark.asyncio
    async def test_purged_account_raises_410(self):
        from backend.routes.auth import ReactivateRequest, reactivate_account

        user = {"id": "u-react-3", "phone": "+13065553333", "status": "deleted"}
        with (
            patch("backend.routes.auth.verify_reactivation_token", return_value="u-react-3"),
            patch("backend.routes.auth.db_supabase.get_user_by_id", AsyncMock(return_value=dict(user))),
        ):
            inner = _resolve_inner(reactivate_account)
            body = ReactivateRequest(reactivation_token="valid-token")
            with pytest.raises(HTTPException) as exc:
                await inner(_request(), MagicMock(), body)

        assert exc.value.status_code == 410

    @pytest.mark.asyncio
    async def test_missing_user_raises_410(self):
        from backend.routes.auth import ReactivateRequest, reactivate_account

        with (
            patch("backend.routes.auth.verify_reactivation_token", return_value="u-ghost"),
            patch("backend.routes.auth.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
        ):
            inner = _resolve_inner(reactivate_account)
            body = ReactivateRequest(reactivation_token="valid-token")
            with pytest.raises(HTTPException) as exc:
                await inner(_request(), MagicMock(), body)

        assert exc.value.status_code == 410

    @pytest.mark.asyncio
    async def test_invalid_token_propagates_401(self):
        """verify_reactivation_token itself raises 401 for expired/forged
        tokens — reactivate_account must let it propagate unchanged."""
        from backend.routes.auth import ReactivateRequest, reactivate_account

        with patch(
            "backend.routes.auth.verify_reactivation_token",
            side_effect=HTTPException(status_code=401, detail="ERR_REACTIVATION_EXPIRED"),
        ):
            inner = _resolve_inner(reactivate_account)
            body = ReactivateRequest(reactivation_token="expired-token")
            with pytest.raises(HTTPException) as exc:
                await inner(_request(), MagicMock(), body)

        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_restore_write_failure_raises_503(self):
        """A DB failure while flipping status back to active must surface
        loudly (503), never silently pretend the account was restored."""
        from backend.routes.auth import ReactivateRequest, reactivate_account
        from backend.utils.error_handling import SpinrException

        user = {"id": "u-react-4", "phone": "+13065554444", "status": "pending_deletion", "token_version": 0}
        with (
            patch("backend.routes.auth.verify_reactivation_token", return_value="u-react-4"),
            patch("backend.routes.auth.db_supabase.get_user_by_id", AsyncMock(return_value=dict(user))),
            patch(
                "backend.routes.auth.db_supabase.update_one",
                AsyncMock(side_effect=RuntimeError("db down")),
            ),
        ):
            inner = _resolve_inner(reactivate_account)
            body = ReactivateRequest(reactivation_token="valid-token")
            with pytest.raises(SpinrException) as exc:
                await inner(_request(), MagicMock(), body)

        assert exc.value.status_code == 503


# ─────────────────────────────────────────────────────────────────────────────
# send_company_email_otp / verify_company_email_otp — remaining error branches
# ─────────────────────────────────────────────────────────────────────────────


class TestCompanyEmailOtpErrorBranches:
    @pytest.mark.asyncio
    async def test_send_db_persist_failure_raises_503(self):
        from backend.routes.auth import CompanyEmailOtpSendRequest, send_company_email_otp
        from backend.utils.error_handling import SpinrException

        with (
            patch("backend.routes.auth._enforce_otp_send_cap", AsyncMock()),
            patch("backend.routes.auth.generate_otp", return_value="1234"),
            patch("backend.routes.auth.db_supabase.delete_many", AsyncMock()),
            patch("backend.routes.auth.db_supabase.insert_one", AsyncMock(side_effect=RuntimeError("db down"))),
        ):
            with pytest.raises(SpinrException) as exc:
                await send_company_email_otp(_request(), CompanyEmailOtpSendRequest(email="a@b.com"))

        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_send_email_failure_cleans_up_row_and_raises_502(self):
        from backend.routes.auth import CompanyEmailOtpSendRequest, send_company_email_otp

        delete_many = AsyncMock()
        with (
            patch("backend.routes.auth._enforce_otp_send_cap", AsyncMock()),
            patch("backend.routes.auth.generate_otp", return_value="1234"),
            patch("backend.routes.auth.db_supabase.delete_many", delete_many),
            patch("backend.routes.auth.db_supabase.insert_one", AsyncMock(return_value=True)),
            patch("backend.routes.auth.send_transactional_email", AsyncMock(return_value=False)),
        ):
            with pytest.raises(HTTPException) as exc:
                await send_company_email_otp(_request(), CompanyEmailOtpSendRequest(email="a@b.com"))

        assert exc.value.status_code == 502
        # cleanup delete_many is called twice: once to clear prior codes,
        # once to remove the just-inserted-but-undelivered row.
        assert delete_many.await_count == 2

    @pytest.mark.asyncio
    async def test_verify_otp_lookup_db_failure_raises_503(self):
        from backend.routes.auth import CompanyEmailOtpVerifyRequest, verify_company_email_otp
        from backend.utils.error_handling import SpinrException

        with (
            patch("backend.routes.auth._check_otp_lockout", AsyncMock()),
            patch("backend.routes.auth.db_supabase.get_rows", AsyncMock(side_effect=RuntimeError("db down"))),
        ):
            with pytest.raises(SpinrException) as exc:
                await verify_company_email_otp(
                    _request(), MagicMock(), CompanyEmailOtpVerifyRequest(email="a@b.com", code="1234")
                )

        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_verify_expired_otp_raises_400(self):
        from backend.routes.auth import CompanyEmailOtpVerifyRequest, verify_company_email_otp
        from backend.utils.crypto import hash_otp
        from backend.utils.error_handling import SpinrException

        expired_otp = {
            "id": "otp-x",
            "email": "a@b.com",
            "code_hash": hash_otp("1234"),
            "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            "verified": False,
        }
        with (
            patch("backend.routes.auth._check_otp_lockout", AsyncMock()),
            patch("backend.routes.auth.db_supabase.get_rows", AsyncMock(return_value=[expired_otp])),
        ):
            with pytest.raises(SpinrException) as exc:
                await verify_company_email_otp(
                    _request(), MagicMock(), CompanyEmailOtpVerifyRequest(email="a@b.com", code="1234")
                )

        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_verify_no_otp_record_raises_400_invalid(self):
        from backend.routes.auth import CompanyEmailOtpVerifyRequest, verify_company_email_otp
        from backend.utils.error_handling import SpinrException

        record_failure = AsyncMock()
        with (
            patch("backend.routes.auth._check_otp_lockout", AsyncMock()),
            patch("backend.routes.auth._record_otp_failure", record_failure),
            patch("backend.routes.auth.db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            with pytest.raises(SpinrException) as exc:
                await verify_company_email_otp(
                    _request(), MagicMock(), CompanyEmailOtpVerifyRequest(email="a@b.com", code="1234")
                )

        assert exc.value.status_code == 400
        record_failure.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_verify_new_user_created_for_unrecognized_email(self):
        """No existing user row for this corporate email → the session
        helper creates a fresh rider account (is_new_user=True)."""
        from backend.routes.auth import CompanyEmailOtpVerifyRequest, verify_company_email_otp
        from backend.utils.crypto import hash_otp

        otp = {
            "id": "otp-new",
            "email": "brandnew@corp.com",
            "code_hash": hash_otp("1234"),
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            "verified": False,
        }

        async def fake_get_rows(table, filters=None, **kwargs):
            if table == "corporate_email_otp_records":
                return [otp]
            return []  # no user, no invites

        with (
            patch("backend.routes.auth._check_otp_lockout", AsyncMock()),
            patch("backend.routes.auth._record_otp_failure", AsyncMock()),
            patch("backend.routes.auth._clear_otp_failures", AsyncMock()),
            patch("backend.routes.auth.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.auth.db_supabase.update_one", AsyncMock()),
            patch("backend.routes.auth.db_supabase.create_user", AsyncMock(return_value={"id": "new-corp-user"})),
            patch("backend.routes.auth.redis_set", AsyncMock()),
            patch("backend.routes.auth.create_jwt_token", return_value="tok"),
            patch(
                "backend.routes.auth.issue_refresh_token",
                AsyncMock(return_value=("rt", "rh", datetime.now(timezone.utc) + timedelta(days=30))),
            ),
            patch("backend.routes.auth.generate_csrf_token", return_value="csrf"),
            patch("backend.routes.auth.set_csrf_cookie"),
        ):
            result = await verify_company_email_otp(
                _request(), MagicMock(), CompanyEmailOtpVerifyRequest(email="brandnew@corp.com", code="1234")
            )


# ─────────────────────────────────────────────────────────────────────────────
# GET /auth/me — self-heal / stats / onboarding-status failure branches.
# CLAUDE.md: DB/derivation failures on these three sub-fetches must be
# caught, logged with logger.error, and NOT block the response (the profile
# fetch itself must still succeed) — but they must never be silently
# swallowed without a loud log. These three except blocks were the last
# confirmed real gap in routes/auth.py's coverage (B-P1-5).
# ─────────────────────────────────────────────────────────────────────────────
class TestGetMeFailureBranches:
    def _base_user(self):
        return {
            "id": "user-me-1",
            "phone": "+13065550099",
            "first_name": "Pat",
            "last_name": "Driver",
            "email": "pat@example.com",
            "role": "rider",
            "is_rider": True,
            "is_driver": False,
            "profile_complete": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    @pytest.mark.asyncio
    async def test_profile_complete_self_heal_db_write_failure_still_returns_profile(self):
        """update_one raising must be caught + logged (not raised to the
        client) but current_user['profile_complete'] is still flipped to
        True in-memory for this response — the DB write failure only
        affects persistence for the *next* login."""
        from backend.routes.auth import get_me

        user = self._base_user()
        with (
            patch("backend.routes.auth.db_supabase.update_one", AsyncMock(side_effect=Exception("db down"))),
            patch("backend.routes.auth.db_supabase.count_documents", AsyncMock(return_value=3)),
            patch.dict(
                "sys.modules",
                {
                    "onboarding_status": MagicMock(
                        derive_driver_onboarding_status=AsyncMock(return_value=(None, None, None))
                    )
                },
            ),
        ):
            result = await get_me(current_user=user)

        assert result.profile_complete is True
        assert user["total_rides"] == 3

    @pytest.mark.asyncio
    async def test_ride_count_fetch_failure_is_caught_and_logged(self):
        """count_documents raising must not block the /me response; the
        rider simply won't get total_rides populated this call."""
        from backend.routes.auth import get_me

        user = self._base_user()
        user["profile_complete"] = True  # skip self-heal branch for isolation
        with (
            patch("backend.routes.auth.db_supabase.update_one", AsyncMock()),
            patch(
                "backend.routes.auth.db_supabase.count_documents",
                AsyncMock(side_effect=Exception("rides table unavailable")),
            ),
            patch.dict(
                "sys.modules",
                {
                    "onboarding_status": MagicMock(
                        derive_driver_onboarding_status=AsyncMock(return_value=(None, None, None))
                    )
                },
            ),
        ):
            result = await get_me(current_user=user)

        assert "total_rides" not in user or user.get("total_rides") is None or result is not None
        assert result.id == "user-me-1"

    @pytest.mark.asyncio
    async def test_driver_onboarding_status_derivation_failure_is_caught_and_logged(self):
        """derive_driver_onboarding_status raising must not block the /me
        response — driver_onboarding_status simply stays unset."""
        from backend.routes.auth import get_me

        user = self._base_user()
        user["profile_complete"] = True
        user["is_driver"] = True

        async def _boom(_user):
            raise RuntimeError("onboarding derivation blew up")

        with (
            patch("backend.routes.auth.db_supabase.update_one", AsyncMock()),
            patch("backend.routes.auth.db_supabase.count_documents", AsyncMock(return_value=0)),
            patch.dict(
                "sys.modules",
                {"onboarding_status": MagicMock(derive_driver_onboarding_status=_boom)},
            ),
        ):
            result = await get_me(current_user=user)

        assert result.id == "user-me-1"
        assert "driver_onboarding_status" not in user or user.get("driver_onboarding_status") is None
