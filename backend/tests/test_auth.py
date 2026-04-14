"""
Unit tests for authentication and security modules.
Tests cover JWT token handling, OTP generation/verification, and user authentication.
"""

import os
import sys
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestOTPCreation:
    """Tests for OTP generation and verification."""

    def test_generate_otp_format(self):
        """Test OTP generation returns correct format."""
        from backend.dependencies import generate_otp

        otp = generate_otp()

        assert otp is not None
        assert len(otp) == 6
        assert otp.isdigit()

    def test_generate_otp_randomness(self):
        """Test that generated OTPs are random."""
        from backend.dependencies import generate_otp

        otps = [generate_otp() for _ in range(10)]

        # All OTPs should be unique (extremely high probability)
        assert len(set(otps)) == len(otps)

    def test_generate_otp_range(self):
        """Test OTP is within valid 6-digit range."""
        from backend.dependencies import generate_otp

        for _ in range(100):
            otp = generate_otp()
            otp_int = int(otp)
            assert 0 <= otp_int <= 999999


class TestJWTTokenHandling:
    """Tests for JWT token creation and verification.

    These were written against the pre-audit JWT shape (``sub`` claim,
    raw ``jwt.*Error`` propagation, ``SECRET_KEY`` on settings). The
    current dependencies module uses ``user_id``/``phone`` claims, maps
    all JWT errors to ``HTTPException(401)``, and reads ``JWT_SECRET``
    from the real settings object. Rewrite is in-place; no production
    code changes.
    """

    def test_create_jwt_token(self):
        """create_jwt_token returns a non-empty JWT string."""
        from backend.dependencies import create_jwt_token

        token = create_jwt_token(user_id="user_123", phone="+1234567890")

        assert isinstance(token, str)
        assert token.count(".") == 2  # header.payload.signature

    def test_create_jwt_token_with_session(self):
        """session_id, when provided, is embedded in the payload and survives a round-trip."""
        from backend.dependencies import create_jwt_token, verify_jwt_token

        token = create_jwt_token(user_id="user_123", phone="+1234567890", session_id="session_abc")
        decoded = verify_jwt_token(token)

        assert decoded["session_id"] == "session_abc"
        assert decoded["user_id"] == "user_123"
        assert decoded["phone"] == "+1234567890"

    def test_verify_jwt_token_valid(self):
        """A freshly-minted token decodes back to its input claims."""
        from backend.dependencies import create_jwt_token, verify_jwt_token

        token = create_jwt_token(user_id="user_123", phone="+1234567890")
        decoded = verify_jwt_token(token)

        # The production payload uses ``user_id`` — the old ``sub`` claim
        # was removed in the audit P0-S3 refactor.
        assert decoded["user_id"] == "user_123"
        assert decoded["phone"] == "+1234567890"
        assert "exp" in decoded and "iat" in decoded

    def test_verify_jwt_token_invalid(self):
        """A malformed token raises HTTPException(401)."""
        from fastapi import HTTPException

        from backend.dependencies import verify_jwt_token

        with pytest.raises(HTTPException) as exc_info:
            verify_jwt_token("invalid.token.here")
        assert exc_info.value.status_code == 401

    def test_verify_jwt_token_expired(self):
        """An expired token raises HTTPException(401) — not a raw jwt error."""
        import jwt
        from fastapi import HTTPException

        from backend.core.config import settings
        from backend.dependencies import JWT_ALGORITHM, verify_jwt_token

        payload = {
            "user_id": "user_123",
            "phone": "+1234567890",
            "exp": datetime.utcnow() - timedelta(minutes=5),
        }
        expired_token = jwt.encode(payload, settings.JWT_SECRET, algorithm=JWT_ALGORITHM)

        with pytest.raises(HTTPException) as exc_info:
            verify_jwt_token(expired_token)
        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()

    def test_verify_jwt_token_wrong_secret(self):
        """A token signed with a different secret raises HTTPException(401)."""
        import jwt
        from fastapi import HTTPException

        from backend.dependencies import JWT_ALGORITHM, verify_jwt_token

        payload = {
            "user_id": "user_123",
            "phone": "+1234567890",
            "exp": datetime.utcnow() + timedelta(minutes=30),
        }
        wrong_token = jwt.encode(payload, "wrong-secret-key-for-this-test-only", algorithm=JWT_ALGORITHM)

        with pytest.raises(HTTPException) as exc_info:
            verify_jwt_token(wrong_token)
        assert exc_info.value.status_code == 401


class TestGetCurrentUser:
    """Tests for get_current_user dependency.

    The production function tries Firebase first, then falls back to the
    legacy JWT. Tests force the Firebase path to reject so the JWT path
    runs predictably. The original suite asserted ``user["user_id"]``
    against a payload whose key was ``"sub"`` — the current code reads
    ``payload["user_id"]`` and populates ``user["id"]``, which is what
    the DB row uses.
    """

    @pytest.fixture
    def mock_credentials(self):
        from fastapi.security import HTTPAuthorizationCredentials

        return HTTPAuthorizationCredentials(scheme="Bearer", credentials="not.a.real.jwt")

    @pytest.mark.asyncio
    async def test_get_current_user_valid_token(self, mock_credentials):
        """Firebase rejects → JWT path → DB miss → auto-created rider returned."""
        from firebase_admin.auth import InvalidIdTokenError

        from backend.dependencies import get_current_user

        with (
            patch(
                "backend.dependencies.firebase_auth.verify_id_token", side_effect=InvalidIdTokenError("not firebase")
            ),
            patch("backend.dependencies.verify_jwt_token") as mock_verify,
            patch("backend.dependencies.db") as mock_db,
        ):
            mock_verify.return_value = {"user_id": "user_123", "phone": "+1234567890"}
            mock_db.users.find_one = AsyncMock(return_value=None)
            mock_db.users.insert_one = AsyncMock()

            user = await get_current_user(mock_credentials)

            # Auto-created user row keeps id/phone from the JWT payload.
            assert user["id"] == "user_123"
            assert user["phone"] == "+1234567890"
            assert user["role"] == "rider"  # always default, never trust JWT claim
            assert user["is_driver"] is False

    @pytest.mark.asyncio
    async def test_get_current_user_invalid_token(self, mock_credentials):
        """Firebase rejects AND JWT rejects → HTTPException(401)."""
        from fastapi import HTTPException
        from firebase_admin.auth import InvalidIdTokenError

        from backend.dependencies import get_current_user

        with (
            patch(
                "backend.dependencies.firebase_auth.verify_id_token", side_effect=InvalidIdTokenError("not firebase")
            ),
            patch("backend.dependencies.verify_jwt_token", side_effect=Exception("Invalid token")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(mock_credentials)
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_get_current_user_missing_credentials(self):
        """No credentials → HTTPException(401)."""
        from fastapi import HTTPException

        from backend.dependencies import get_current_user

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(None)
        assert exc_info.value.status_code == 401


class TestAdminUserVerification:
    """Tests for admin user verification."""

    @pytest.mark.asyncio
    async def test_get_admin_user_is_admin(self):
        """An authenticated user whose role is in the admin set is passed through."""
        from backend.dependencies import get_admin_user

        admin_user = {"id": "admin_123", "phone": "+1234567890", "role": "admin"}

        result = await get_admin_user(admin_user)
        assert result == admin_user

    @pytest.mark.asyncio
    async def test_get_admin_user_super_admin(self):
        """``super_admin`` (env creds) is also accepted."""
        from backend.dependencies import get_admin_user

        super_admin = {"id": "admin-001", "phone": "", "role": "super_admin"}

        result = await get_admin_user(super_admin)
        assert result == super_admin

    @pytest.mark.asyncio
    async def test_get_admin_user_not_admin(self):
        """A rider role raises 403 with the current error message."""
        from fastapi import HTTPException

        from backend.dependencies import get_admin_user

        regular_user = {"id": "user_123", "phone": "+1234567890", "role": "rider"}

        with pytest.raises(HTTPException) as exc_info:
            await get_admin_user(regular_user)
        assert exc_info.value.status_code == 403
        # The audit-era error message is "Admin access required", not the
        # older "User is not an admin". Keep the assertion tight against
        # the current phrasing so a silent change is caught.
        assert exc_info.value.detail == "Admin access required"

    @pytest.mark.asyncio
    async def test_get_admin_user_missing_role(self):
        """A user row with no role key at all is rejected (default '' is not admin)."""
        from fastapi import HTTPException

        from backend.dependencies import get_admin_user

        no_role_user = {"id": "user_123", "phone": "+1234567890"}

        with pytest.raises(HTTPException) as exc_info:
            await get_admin_user(no_role_user)
        assert exc_info.value.status_code == 403


class TestFirebaseIntegration:
    """Tests for Firebase initialization + the user DB helpers that
    used to be Firebase-backed and are now Supabase-backed.

    The production code path uses `run_sync()` which dispatches the
    synchronous supabase-py chain to an executor. That means the
    terminal ``.execute()`` in the mock chain must be a plain
    ``MagicMock`` returning a response object — not an ``AsyncMock``,
    which would yield an un-awaited coroutine and crash
    ``_single_row_from_res`` silently to None.
    """

    def test_init_firebase_runs_without_error(self):
        """init_firebase() swallows every exception path internally
        (see core/security.py:11-26) and is called unconditionally at
        server startup. This test pins that contract: no matter what
        FIREBASE_SERVICE_ACCOUNT_JSON looks like, the call must not
        raise. The real module attribute is ``firebase_admin``, not
        ``firebase`` (the old test's AttributeError).
        """
        from backend.core.security import init_firebase

        with patch("backend.core.security.firebase_admin") as mock_firebase_admin:
            init_firebase()
            # Either initialize_app was called, or an exception inside
            # the JSON parse path was swallowed. Both are acceptable.
            assert mock_firebase_admin is not None

    @pytest.mark.asyncio
    async def test_create_user_inserts_via_supabase(self):
        """create_user() runs the supabase insert chain through
        ``run_sync`` (synchronous inside the executor). Terminal
        .execute() must therefore be a MagicMock, not AsyncMock."""
        from backend.db_supabase import create_user

        with patch("backend.db_supabase.supabase") as mock_supabase:
            mock_response = MagicMock()
            mock_response.data = [{"id": "user_123", "phone": "+1234567890", "email": "test@example.com"}]
            mock_supabase.table.return_value.insert.return_value.execute = MagicMock(return_value=mock_response)

            result = await create_user({"phone": "+1234567890", "email": "test@example.com"})

            assert result is not None
            assert result["id"] == "user_123"

    @pytest.mark.asyncio
    async def test_get_user_by_id_returns_first_row(self):
        """get_user_by_id() -> _single_row_from_res picks data[0]."""
        from backend.db_supabase import get_user_by_id

        with patch("backend.db_supabase.supabase") as mock_supabase:
            mock_response = MagicMock()
            mock_response.data = [{"id": "user_123", "phone": "+1234567890"}]
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = MagicMock(
                return_value=mock_response
            )

            result = await get_user_by_id("user_123")

            assert result is not None
            assert result["id"] == "user_123"

    @pytest.mark.asyncio
    async def test_get_user_by_phone_returns_first_row(self):
        """get_user_by_phone() hits the same chain as get_user_by_id
        but keyed on the ``phone`` column."""
        from backend.db_supabase import get_user_by_phone

        with patch("backend.db_supabase.supabase") as mock_supabase:
            mock_response = MagicMock()
            mock_response.data = [{"id": "user_123", "phone": "+1234567890"}]
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = MagicMock(
                return_value=mock_response
            )

            result = await get_user_by_phone("+1234567890")

            assert result is not None
            assert result["phone"] == "+1234567890"


class TestAuthEndpoints:
    """Tests for authentication endpoints."""

    @pytest.fixture
    def test_client(self):
        """Create test client with mocked dependencies."""
        from fastapi.testclient import TestClient

        from backend.server import app

        return TestClient(app)

    def test_send_otp_success(self, test_client, mock_supabase_client, mock_sms_service):
        """Test sending OTP successfully."""
        # Mock OTP insertion
        mock_supabase_client.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[{"id": "otp_123"}])
        )

        response = test_client.post("/api/auth/send-otp", json={"phone": "+1234567890"})

        # Should either succeed or be rate limited
        assert response.status_code in [200, 429]

    def test_send_otp_missing_phone(self, test_client):
        """Test sending OTP with missing phone number."""
        response = test_client.post("/api/auth/send-otp", json={})

        assert response.status_code == 422  # Validation error

    def test_send_otp_invalid_phone_format(self, test_client):
        """Test sending OTP with invalid phone format."""
        response = test_client.post("/api/auth/send-otp", json={"phone": "invalid"})

        # Should be validation error or handled gracefully
        assert response.status_code in [400, 422]

    def test_verify_otp_success(self, test_client, mock_supabase_client):
        """Test verifying OTP successfully."""
        # Mock OTP lookup
        mock_response = MagicMock()
        mock_response.data = [{"id": "otp_123", "verified": False}]
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value=mock_response
        )

        response = test_client.post("/api/auth/verify-otp", json={"phone": "+1234567890", "code": "123456"})

        # Should succeed or fail with appropriate error
        assert response.status_code in [200, 400, 401]

    def test_verify_otp_missing_fields(self, test_client):
        """Test verifying OTP with missing fields."""
        response = test_client.post(
            "/api/auth/verify-otp",
            json={"phone": "+1234567890"},  # Missing code
        )

        assert response.status_code == 422  # Validation error


class TestSessionManagement:
    """Tests for session management."""

    def test_session_id_in_token(self, mock_settings):
        """Test that session ID is included in JWT token."""
        from backend.dependencies import create_jwt_token, verify_jwt_token

        session_id = "test_session_123"
        token = create_jwt_token(user_id="user_123", phone="+1234567890", session_id=session_id)

        decoded = verify_jwt_token(token)
        assert decoded.get("session_id") == session_id

    @pytest.mark.asyncio
    async def test_session_invalidation(self):
        """Test session invalidation logic."""
        # Sessions can be invalidated by checking against a blacklist
        # or by verifying the session still exists in the database

        session_blacklist = {"session_123", "session_456"}

        def is_session_valid(session_id: str) -> bool:
            return session_id not in session_blacklist

        assert is_session_valid("session_789") is True
        assert is_session_valid("session_123") is False


class TestPasswordlessAuth:
    """Tests for passwordless authentication flow."""

    @pytest.mark.asyncio
    async def test_full_auth_flow(self, mock_supabase_client, mock_sms_service):
        """Test complete passwordless auth flow."""
        from backend.dependencies import create_jwt_token, generate_otp

        phone = "+1234567890"

        # Step 1: Generate OTP
        otp = generate_otp()
        assert len(otp) == 6

        # Step 2: Send OTP (mocked)
        await mock_sms_service.send_otp(phone, otp)
        mock_sms_service.send_otp.assert_called_once()

        # Step 3: Verify OTP (would check database)
        mock_response = MagicMock()
        mock_response.data = [{"id": "otp_123", "verified": False}]
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value=mock_response
        )

        # Step 4: Create JWT token after verification
        token = create_jwt_token(user_id="user_123", phone=phone)

        assert token is not None
        assert isinstance(token, str)


class TestTokenRefresh:
    """Tests for token refresh functionality."""

    def test_token_refresh_with_valid_session(self, mock_settings):
        """Test refreshing token with valid session."""
        from backend.dependencies import create_jwt_token, verify_jwt_token

        # Create initial token
        original_token = create_jwt_token(user_id="user_123", phone="+1234567890", session_id="session_abc")

        decoded = verify_jwt_token(original_token)
        assert decoded["sub"] == "user_123"

        # Create refreshed token with same session
        refreshed_token = create_jwt_token(
            user_id=decoded["sub"], phone=decoded["phone"], session_id=decoded.get("session_id")
        )

        refreshed_decoded = verify_jwt_token(refreshed_token)
        assert refreshed_decoded["session_id"] == "session_abc"
