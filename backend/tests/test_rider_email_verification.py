"""Tests for the rider self-serve email verification flow (N14, ACTION_ITEMS.md).

`POST /users/verify-email/request` and `POST /users/verify-email/confirm`
(backend/routes/users.py) reuse the SAME OTP mechanics as the corporate
portal's `verify-email-otp` (backend/routes/auth.py) — see
tests/test_company_email_login.py for the flow this mirrors. Patches target
`backend.routes.users.*` (not `backend.routes.auth.*`) because
`_check_otp_lockout` / `_record_otp_failure` / `_clear_otp_failures` /
`_enforce_otp_send_cap` are imported by name into routes/users.py, which binds
a separate reference in that module's namespace.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

EMAIL = "rider@example.com"

_LOCKOUT_NOOP = patch("backend.routes.users._check_otp_lockout", AsyncMock())
_SEND_CAP_NOOP = patch("backend.routes.users._enforce_otp_send_cap", AsyncMock())
_RECORD_FAIL_NOOP = patch("backend.routes.users._record_otp_failure", AsyncMock())
_CLEAR_FAIL_NOOP = patch("backend.routes.users._clear_otp_failures", AsyncMock())


def _user(**overrides) -> dict:
    base = {
        "id": "rider-1",
        "phone": "+13065550123",
        "email": EMAIL,
        "first_name": "Rae",
        "last_name": "Rider",
        "role": "rider",
        "email_verified": False,
        "is_rider": True,
        "is_driver": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    base.update(overrides)
    return base


def _request() -> MagicMock:
    req = MagicMock()
    req.client = MagicMock(host="127.0.0.1")
    req.headers = {"user-agent": "pytest"}
    return req


class TestRequestEmailVerification:
    def test_hashes_code_and_sends_email(self):
        from backend.routes.users import request_rider_email_verification

        inserted: dict = {}

        async def fake_insert(table: str, row: dict):
            inserted["table"] = table
            inserted["row"] = row
            return row

        with (
            _SEND_CAP_NOOP,
            patch(
                "backend.routes.users.get_app_settings",
                AsyncMock(return_value={"resend_api_key": "re_configured"}),
            ),
            patch("backend.routes.users.generate_otp", return_value="1234"),
            patch("backend.routes.users.db_supabase.delete_many", AsyncMock()),
            patch("backend.routes.users.db_supabase.insert_one", AsyncMock(side_effect=fake_insert)),
            patch("backend.routes.users.send_email_verification_code", AsyncMock(return_value=True)) as send_email,
        ):
            result = asyncio.run(request_rider_email_verification(_request(), _user()))

        assert result == {"success": True, "message": "Verification code sent"}
        assert inserted["table"] == "rider_email_verification_otp"
        assert inserted["row"]["user_id"] == "rider-1"
        assert inserted["row"]["email"] == EMAIL
        assert inserted["row"]["code_hash"] != "1234"
        assert inserted["row"]["code_hash"]
        send_email.assert_awaited_once()
        args = send_email.await_args.args
        assert args[1] == "1234"  # code passed through unhashed to the mailer

    def test_bypasses_unconfigured_provider_in_non_production(self):
        from backend.routes.users import request_rider_email_verification

        with (
            _SEND_CAP_NOOP,
            patch("backend.routes.users.get_app_settings", AsyncMock(return_value={})),
            patch("backend.routes.users.db_supabase.delete_many", AsyncMock()),
            patch("backend.routes.users.db_supabase.insert_one", AsyncMock()),
            patch("backend.routes.users.send_email_verification_code", AsyncMock()) as send_email,
            patch("backend.core.config.settings.ENV", "test"),
        ):
            result = asyncio.run(request_rider_email_verification(_request(), _user()))

        assert result == {"success": True, "message": "Verification code sent"}
        send_email.assert_not_called()

    def test_refuses_bypass_in_production(self):
        from fastapi import HTTPException

        from backend.routes.users import request_rider_email_verification
        from backend.utils.error_handling import SpinrException

        with (
            _SEND_CAP_NOOP,
            patch("backend.routes.users.get_app_settings", AsyncMock(return_value={})),
            patch("backend.core.config.settings.ENV", "production"),
            pytest.raises((SpinrException, HTTPException)) as excinfo,
        ):
            asyncio.run(request_rider_email_verification(_request(), _user()))

        assert getattr(excinfo.value, "status_code", None) == 503

    def test_already_verified_short_circuits(self):
        """No DB write, no send-cap check, no email — a verified rider hitting
        this endpoint again (e.g. a stale UI state) is a harmless no-op."""
        from backend.routes.users import request_rider_email_verification

        with (
            patch("backend.routes.users._enforce_otp_send_cap", AsyncMock()) as send_cap,
            patch("backend.routes.users.db_supabase.insert_one", AsyncMock()) as insert_one,
        ):
            result = asyncio.run(request_rider_email_verification(_request(), _user(email_verified=True)))

        assert result == {
            "success": True,
            "already_verified": True,
            "message": "Your email is already verified",
        }
        send_cap.assert_not_called()
        insert_one.assert_not_called()

    def test_requires_email_on_file(self):
        from fastapi import HTTPException

        from backend.routes.users import request_rider_email_verification
        from backend.utils.error_handling import SpinrException

        with pytest.raises((SpinrException, HTTPException)) as excinfo:
            asyncio.run(request_rider_email_verification(_request(), _user(email=None)))

        assert getattr(excinfo.value, "status_code", None) == 400


class TestConfirmEmailVerification:
    def test_success_flips_flag_and_clears_failures(self):
        from backend.routes.users import RiderEmailVerifyConfirmRequest, confirm_rider_email_verification
        from backend.utils.crypto import hash_otp

        otp = {
            "id": "otp-1",
            "user_id": "rider-1",
            "email": EMAIL,
            "code_hash": hash_otp("1234"),
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            "verified": False,
        }
        clear_failures = AsyncMock()
        update_calls: list = []

        async def fake_update_one(table, filters, patch_body):
            update_calls.append((table, filters, patch_body))
            return {**filters, **patch_body}

        with (
            _LOCKOUT_NOOP,
            _RECORD_FAIL_NOOP,
            patch("backend.routes.users._clear_otp_failures", clear_failures),
            patch("backend.routes.users.db_supabase.get_rows", AsyncMock(return_value=[otp])),
            patch("backend.routes.users.db_supabase.update_one", AsyncMock(side_effect=fake_update_one)),
            patch("backend.routes.users.log_user_action", AsyncMock()),
        ):
            result = asyncio.run(confirm_rider_email_verification(RiderEmailVerifyConfirmRequest(code="1234"), _user()))

        assert result == {"success": True, "message": "Email verified", "email_verified": True}
        clear_failures.assert_awaited_once()
        # Second update_one call flips the users row.
        users_updates = [c for c in update_calls if c[0] == "users"]
        assert len(users_updates) == 1
        _, filters, body = users_updates[0]
        assert filters == {"id": "rider-1"}
        assert body["email_verified"] is True
        assert body["email_verified_at"]

    def test_wrong_code_records_failure_without_flipping(self):
        from backend.routes.users import RiderEmailVerifyConfirmRequest, confirm_rider_email_verification
        from backend.utils.crypto import hash_otp

        otp = {
            "id": "otp-1",
            "user_id": "rider-1",
            "email": EMAIL,
            "code_hash": hash_otp("1234"),
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            "verified": False,
        }
        record_failure = AsyncMock()
        update_one = AsyncMock()

        with (
            _LOCKOUT_NOOP,
            patch("backend.routes.users._record_otp_failure", record_failure),
            patch("backend.routes.users.db_supabase.get_rows", AsyncMock(return_value=[otp])),
            patch("backend.routes.users.db_supabase.update_one", update_one),
            pytest.raises(Exception) as excinfo,
        ):
            asyncio.run(confirm_rider_email_verification(RiderEmailVerifyConfirmRequest(code="9999"), _user()))

        assert getattr(excinfo.value, "status_code", None) == 400
        record_failure.assert_awaited_once()
        update_one.assert_not_called()

    def test_expired_code_rejected(self):
        from backend.routes.users import RiderEmailVerifyConfirmRequest, confirm_rider_email_verification
        from backend.utils.crypto import hash_otp

        otp = {
            "id": "otp-1",
            "user_id": "rider-1",
            "email": EMAIL,
            "code_hash": hash_otp("1234"),
            "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            "verified": False,
        }

        with (
            _LOCKOUT_NOOP,
            patch("backend.routes.users.db_supabase.get_rows", AsyncMock(return_value=[otp])),
            patch("backend.routes.users.db_supabase.update_one", AsyncMock()) as update_one,
            pytest.raises(Exception) as excinfo,
        ):
            asyncio.run(confirm_rider_email_verification(RiderEmailVerifyConfirmRequest(code="1234"), _user()))

        assert getattr(excinfo.value, "status_code", None) == 400
        update_one.assert_not_called()

    def test_locked_out_returns_429(self):
        from fastapi import HTTPException

        from backend.routes.users import RiderEmailVerifyConfirmRequest, confirm_rider_email_verification

        lockout_exc = HTTPException(status_code=429, detail="Too many failed attempts")
        with (
            patch("backend.routes.users._check_otp_lockout", AsyncMock(side_effect=lockout_exc)),
            patch("backend.routes.users.db_supabase.get_rows") as get_rows,
            pytest.raises(HTTPException) as excinfo,
        ):
            asyncio.run(confirm_rider_email_verification(RiderEmailVerifyConfirmRequest(code="1234"), _user()))

        assert excinfo.value.status_code == 429
        get_rows.assert_not_called()

    def test_email_changed_mid_flow_does_not_verify_new_address(self):
        """Code was minted for the OLD email on the account; the row now has
        a different one. Must refuse rather than silently verifying the new
        address using a code that was never sent to it."""
        from backend.routes.users import RiderEmailVerifyConfirmRequest, confirm_rider_email_verification
        from backend.utils.crypto import hash_otp

        otp = {
            "id": "otp-1",
            "user_id": "rider-1",
            "email": "old@example.com",
            "code_hash": hash_otp("1234"),
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            "verified": False,
        }

        with (
            _LOCKOUT_NOOP,
            _RECORD_FAIL_NOOP,
            patch("backend.routes.users.db_supabase.get_rows", AsyncMock(return_value=[otp])),
            patch("backend.routes.users.db_supabase.update_one", AsyncMock()) as update_one,
            pytest.raises(Exception) as excinfo,
        ):
            asyncio.run(
                confirm_rider_email_verification(
                    RiderEmailVerifyConfirmRequest(code="1234"), _user(email="new@example.com")
                )
            )

        assert getattr(excinfo.value, "status_code", None) == 409
        update_one.assert_not_called()


# ── Rate limit enforcement (rider_email_verify_request_limit: 3/hour) ──────


def _build_users_app() -> FastAPI:
    """Minimal FastAPI app with only the users router and the real slowapi
    limiter wired, mirroring tests/test_promo_rate_limit.py's approach."""
    from dependencies import get_current_user
    from routes.users import api_router as users_router
    from utils.rate_limiter import default_limiter, rate_limit_exceeded_handler

    app = FastAPI()
    app.state.limiter = default_limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(users_router)

    app.dependency_overrides[get_current_user] = lambda: {
        "id": "rider-1",
        "phone": "+13065550123",
        "email": EMAIL,
        "role": "rider",
        "email_verified": False,
    }
    return app


class TestRequestEndpointRateLimit:
    """Scoped fixture (not module-level) — enabling the real limiter globally
    would also gate the direct-call unit tests above, which pass a MagicMock
    Request that only the disabled-limiter no-op path tolerates."""

    @pytest.fixture(autouse=True)
    def _enable_real_limiter(self):
        from utils.rate_limiter import default_limiter

        default_limiter.enabled = True
        inner = getattr(default_limiter, "_limiter", None)
        storage = getattr(inner, "storage", None) if inner is not None else None
        if storage is not None and callable(getattr(storage, "reset", None)):
            result = storage.reset()
            if result is not None:
                asyncio.run(result)
        yield
        default_limiter.enabled = False

    @staticmethod
    def _client() -> TestClient:
        app = _build_users_app()
        return TestClient(app, raise_server_exceptions=False)

    def test_under_limit_all_succeed(self):
        client = self._client()
        with (
            patch("routes.users.get_app_settings", AsyncMock(return_value={})),
            patch("routes.users.db_supabase.delete_many", AsyncMock()),
            patch("routes.users.db_supabase.insert_one", AsyncMock()),
            patch("routes.users._enforce_otp_send_cap", AsyncMock()),
            patch("core.config.settings.ENV", "test"),
        ):
            for i in range(3):
                r = client.post("/users/verify-email/request")
                assert r.status_code != 429, f"Request {i + 1}/3 got 429 too early. Body: {r.text}"

    def test_fourth_request_in_hour_is_429(self):
        client = self._client()
        with (
            patch("routes.users.get_app_settings", AsyncMock(return_value={})),
            patch("routes.users.db_supabase.delete_many", AsyncMock()),
            patch("routes.users.db_supabase.insert_one", AsyncMock()),
            patch("routes.users._enforce_otp_send_cap", AsyncMock()),
            patch("core.config.settings.ENV", "test"),
        ):
            for _ in range(3):
                client.post("/users/verify-email/request")
            r = client.post("/users/verify-email/request")

        assert r.status_code == 429, f"Expected 429 on 4th request, got {r.status_code}. Body: {r.text}"
        body = r.json()
        assert body.get("error") == "rate_limit_exceeded"
        assert "Retry-After" in r.headers
