import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

EMAIL = "Manager@Acme.ca"
NORMALIZED_EMAIL = "manager@acme.ca"

_LOCKOUT_NOOP = patch("backend.routes.auth._check_otp_lockout", AsyncMock())
_SEND_CAP_NOOP = patch("backend.routes.auth._enforce_otp_send_cap", AsyncMock())
_RECORD_FAIL_NOOP = patch("backend.routes.auth._record_otp_failure", AsyncMock())
_CLEAR_FAIL_NOOP = patch("backend.routes.auth._clear_otp_failures", AsyncMock())


def _request() -> MagicMock:
    request = MagicMock()
    request.client = MagicMock(host="127.0.0.1")
    request.headers = {"user-agent": "pytest"}
    return request


class TestCompanyEmailOtpSend:
    def test_send_company_email_otp_hashes_code_and_sends_email(self):
        from backend.routes.auth import CompanyEmailOtpSendRequest, send_company_email_otp

        inserted: dict = {}

        async def fake_insert(table: str, row: dict):
            inserted["table"] = table
            inserted["row"] = row
            return row

        with (
            _SEND_CAP_NOOP,
            patch("backend.routes.auth.generate_otp", return_value="1234"),
            patch("backend.routes.auth.db_supabase.delete_many", AsyncMock()),
            patch("backend.routes.auth.db_supabase.insert_one", AsyncMock(side_effect=fake_insert)),
            patch("backend.routes.auth.send_transactional_email", AsyncMock(return_value=True)) as send_email,
        ):
            result = asyncio.run(
                send_company_email_otp(
                    _request(),
                    CompanyEmailOtpSendRequest(email=EMAIL),
                )
            )

        assert result == {"success": True, "message": "Verification code sent"}
        assert inserted["table"] == "corporate_email_otp_records"
        assert inserted["row"]["email"] == NORMALIZED_EMAIL
        assert inserted["row"]["code_hash"] != "1234"
        assert inserted["row"]["code_hash"]
        send_email.assert_awaited_once()
        kwargs = send_email.await_args.kwargs
        assert kwargs["to"] == NORMALIZED_EMAIL
        assert "1234" in kwargs["text"]
        assert kwargs["email_type"] == "corporate_email_otp"


class TestCompanyEmailOtpVerify:
    def test_verify_company_email_otp_issues_tokens_and_accepts_invite(self):
        from backend.routes.auth import CompanyEmailOtpVerifyRequest, verify_company_email_otp
        from backend.utils.crypto import hash_otp

        user = {
            "id": "11111111-1111-1111-1111-111111111111",
            "phone": "+13065550123",
            "email": NORMALIZED_EMAIL,
            "first_name": "Mina",
            "last_name": "Manager",
            "role": "rider",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "profile_complete": True,
            "is_rider": True,
            "is_driver": False,
            "token_version": 0,
        }
        otp = {
            "id": "otp-1",
            "email": NORMALIZED_EMAIL,
            "code_hash": hash_otp("1234"),
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            "verified": False,
        }
        invite = {
            "id": "22222222-2222-2222-2222-222222222222",
            "company_id": "33333333-3333-3333-3333-333333333333",
            "invited_email": NORMALIZED_EMAIL,
            "status": "invited",
        }

        async def fake_get_rows(table: str, filters=None, **kwargs):
            if table == "corporate_email_otp_records":
                return [otp]
            if table == "users":
                return [user]
            if table == "corporate_members":
                return [invite]
            return []

        response = MagicMock()
        with (
            _LOCKOUT_NOOP,
            _RECORD_FAIL_NOOP,
            _CLEAR_FAIL_NOOP,
            patch("backend.routes.auth.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.auth.db_supabase.update_one", AsyncMock()) as update_one,
            patch(
                "backend.routes.auth.db_supabase.accept_member_invite",
                AsyncMock(return_value={**invite, "status": "active"}),
            ) as accept,
            patch("backend.routes.auth.redis_set", AsyncMock()),
            patch("backend.routes.auth.create_jwt_token", return_value="access-token"),
            patch(
                "backend.routes.auth.issue_refresh_token",
                AsyncMock(
                    return_value=(
                        "refresh-token",
                        "refresh-hash",
                        datetime.now(timezone.utc) + timedelta(days=30),
                    )
                ),
            ),
            patch("backend.routes.auth.generate_csrf_token", return_value="csrf-token"),
            patch("backend.routes.auth.set_csrf_cookie"),
        ):
            result = asyncio.run(
                verify_company_email_otp(
                    _request(),
                    response,
                    CompanyEmailOtpVerifyRequest(email=EMAIL, code="1234"),
                )
            )

        assert result.token == "access-token"
        assert result.refresh_token == "refresh-token"
        assert result.csrf_token == "csrf-token"
        assert result.user.email == NORMALIZED_EMAIL
        accept.assert_awaited_once_with(member_id=invite["id"], user_id=user["id"])
        update_one.assert_any_await(
            "corporate_email_otp_records",
            {"id": otp["id"]},
            {"verified": True},
        )

    def test_verify_company_email_otp_rejects_invalid_code_without_session(self):
        from backend.routes.auth import CompanyEmailOtpVerifyRequest, verify_company_email_otp
        from backend.utils.crypto import hash_otp

        otp = {
            "id": "otp-1",
            "email": NORMALIZED_EMAIL,
            "code_hash": hash_otp("1234"),
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            "verified": False,
        }

        with (
            _LOCKOUT_NOOP,
            _RECORD_FAIL_NOOP,
            patch("backend.routes.auth.db_supabase.get_rows", AsyncMock(return_value=[otp])),
            patch("backend.routes.auth.create_jwt_token") as create_token,
            pytest.raises(Exception) as excinfo,
        ):
            asyncio.run(
                verify_company_email_otp(
                    _request(),
                    MagicMock(),
                    CompanyEmailOtpVerifyRequest(email=EMAIL, code="9999"),
                )
            )

        create_token.assert_not_called()
        assert getattr(excinfo.value, "status_code", None) == 400


class TestCompanyEmailOtpLockout:
    """P1 fix: verify email-OTP endpoints wire into the brute-force lockout."""

    def test_verify_locked_out_email_returns_429(self):
        from fastapi import HTTPException

        from backend.routes.auth import CompanyEmailOtpVerifyRequest, verify_company_email_otp

        lockout_exc = HTTPException(status_code=429, detail="Too many failed attempts")
        with (
            patch("backend.routes.auth._check_otp_lockout", AsyncMock(side_effect=lockout_exc)),
            patch("backend.routes.auth.db_supabase.get_rows") as get_rows,
            pytest.raises(HTTPException) as excinfo,
        ):
            asyncio.run(
                verify_company_email_otp(
                    _request(),
                    MagicMock(),
                    CompanyEmailOtpVerifyRequest(email=EMAIL, code="1234"),
                )
            )

        assert excinfo.value.status_code == 429
        get_rows.assert_not_called()

    def test_verify_wrong_code_records_failure(self):
        from backend.routes.auth import CompanyEmailOtpVerifyRequest, verify_company_email_otp
        from backend.utils.crypto import hash_otp

        otp = {
            "id": "otp-1",
            "email": NORMALIZED_EMAIL,
            "code_hash": hash_otp("1234"),
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            "verified": False,
        }
        record_failure = AsyncMock()

        with (
            _LOCKOUT_NOOP,
            patch("backend.routes.auth._record_otp_failure", record_failure),
            patch("backend.routes.auth.db_supabase.get_rows", AsyncMock(return_value=[otp])),
            pytest.raises(Exception),
        ):
            asyncio.run(
                verify_company_email_otp(
                    _request(),
                    MagicMock(),
                    CompanyEmailOtpVerifyRequest(email=EMAIL, code="9999"),
                )
            )

        record_failure.assert_awaited_once()

    def test_verify_success_clears_failures(self):
        from backend.routes.auth import CompanyEmailOtpVerifyRequest, verify_company_email_otp
        from backend.utils.crypto import hash_otp

        user = {
            "id": "u-1",
            "phone": "+13065550123",
            "email": NORMALIZED_EMAIL,
            "first_name": "Mina",
            "last_name": "Manager",
            "role": "rider",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "profile_complete": True,
            "is_rider": True,
            "is_driver": False,
            "token_version": 0,
        }
        otp = {
            "id": "otp-1",
            "email": NORMALIZED_EMAIL,
            "code_hash": hash_otp("1234"),
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            "verified": False,
        }
        clear_failures = AsyncMock()

        async def fake_get_rows(table, filters=None, **kwargs):
            if table == "corporate_email_otp_records":
                return [otp]
            if table == "users":
                return [user]
            return []

        with (
            _LOCKOUT_NOOP,
            _RECORD_FAIL_NOOP,
            patch("backend.routes.auth._clear_otp_failures", clear_failures),
            patch("backend.routes.auth.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            patch("backend.routes.auth.db_supabase.update_one", AsyncMock()),
            patch("backend.routes.auth.redis_set", AsyncMock()),
            patch("backend.routes.auth.create_jwt_token", return_value="tok"),
            patch(
                "backend.routes.auth.issue_refresh_token",
                AsyncMock(return_value=("rt", "rh", datetime.now(timezone.utc) + timedelta(days=30))),
            ),
            patch("backend.routes.auth.generate_csrf_token", return_value="csrf"),
            patch("backend.routes.auth.set_csrf_cookie"),
        ):
            asyncio.run(
                verify_company_email_otp(
                    _request(),
                    MagicMock(),
                    CompanyEmailOtpVerifyRequest(email=EMAIL, code="1234"),
                )
            )

        clear_failures.assert_awaited_once()

    def test_send_does_not_check_lockout(self):
        """Parity with the phone send_otp path: a locked-out destination may
        still REQUEST a code. The lockout is enforced at verify, so gating send
        buys no security — it would only strip the recovery path from a user
        whose address someone else mistyped into the lockout."""
        from backend.routes.auth import CompanyEmailOtpSendRequest, send_company_email_otp

        check_lockout = AsyncMock()
        with (
            patch("backend.routes.auth._check_otp_lockout", check_lockout),
            _SEND_CAP_NOOP,
            patch("backend.routes.auth.generate_otp", return_value="1234"),
            patch("backend.routes.auth.db_supabase.delete_many", AsyncMock()),
            patch("backend.routes.auth.db_supabase.insert_one", AsyncMock()),
            patch("backend.routes.auth.send_transactional_email", AsyncMock(return_value=True)),
        ):
            result = asyncio.run(
                send_company_email_otp(
                    _request(),
                    CompanyEmailOtpSendRequest(email=EMAIL),
                )
            )

        assert result == {"success": True, "message": "Verification code sent"}
        check_lockout.assert_not_called()

    def test_send_enforces_send_cap(self):
        from fastapi import HTTPException

        from backend.routes.auth import CompanyEmailOtpSendRequest, send_company_email_otp

        cap_exc = HTTPException(status_code=429, detail="Too many code requests")
        with (
            patch("backend.routes.auth._enforce_otp_send_cap", AsyncMock(side_effect=cap_exc)),
            patch("backend.routes.auth.generate_otp") as gen_otp,
            pytest.raises(HTTPException) as excinfo,
        ):
            asyncio.run(
                send_company_email_otp(
                    _request(),
                    CompanyEmailOtpSendRequest(email=EMAIL),
                )
            )

        assert excinfo.value.status_code == 429
        gen_otp.assert_not_called()
