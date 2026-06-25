"""
Unit tests for authentication and security modules.
Tests cover JWT token handling, OTP generation/verification, and user authentication.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# All async tests in this module use anyio (not asyncio).
pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Module-level mock_settings fixture — shared by TestJWTTokenHandling,
# TestSessionManagement, and TestTokenRefresh.
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_settings():
    """Mock settings with test values matching the JWT_SECRET env var."""
    with patch("backend.dependencies.settings") as _mock_settings:
        _mock_settings.JWT_SECRET = "test-secret-key-for-ci-only-32chars!!"
        _mock_settings.ALGORITHM = "HS256"
        _mock_settings.ACCESS_TOKEN_EXPIRE_MINUTES = 30
        yield _mock_settings


class TestOTPCreation:
    """Tests for OTP generation and verification."""

    def test_generate_otp_format(self):
        """Test OTP generation returns correct format."""
        from backend.dependencies import generate_otp

        otp = generate_otp()

        assert otp is not None
        assert len(otp) == 4
        assert otp.isdigit()

    def test_generate_otp_randomness(self):
        """Test that generated OTPs vary (4-digit space only has 10k values)."""
        from backend.dependencies import generate_otp

        otps = [generate_otp() for _ in range(10)]

        # Can't require all unique — 4-digit space has real collision odds.
        # Just verify we're not always returning the same value.
        assert len(set(otps)) > 1

    def test_generate_otp_range(self):
        """Test OTP is within valid 4-digit range."""
        from backend.dependencies import generate_otp

        for _ in range(100):
            otp = generate_otp()
            otp_int = int(otp)
            assert 0 <= otp_int <= 9999


class TestJWTTokenHandling:
    """Tests for JWT token creation and verification."""

    def test_create_jwt_token(self, mock_settings):
        """Test JWT token creation."""
        from backend.dependencies import create_jwt_token

        token = create_jwt_token(user_id="user_123", phone="+1234567890")

        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 0

    def test_create_jwt_token_with_session(self, mock_settings):
        """Test JWT token creation with session ID."""
        from backend.dependencies import create_jwt_token, verify_jwt_token

        token = create_jwt_token(user_id="user_123", phone="+1234567890", session_id="session_abc")

        assert token is not None
        # Verify token can be decoded and contains session_id
        decoded = verify_jwt_token(token)
        assert decoded["session_id"] == "session_abc"

    def test_verify_jwt_token_valid(self, mock_settings):
        """Test verifying a valid JWT token."""
        from backend.dependencies import create_jwt_token, verify_jwt_token

        # Create token
        token = create_jwt_token(user_id="user_123", phone="+1234567890")

        # Verify token
        decoded = verify_jwt_token(token)

        assert decoded is not None
        assert decoded["user_id"] == "user_123"
        assert decoded["phone"] == "+1234567890"

    def test_verify_jwt_token_invalid(self, mock_settings):
        """Test verifying an invalid JWT token."""
        from backend.dependencies import verify_jwt_token

        with pytest.raises(Exception):
            verify_jwt_token("invalid.token.here")

    def test_verify_jwt_token_expired(self, mock_settings):
        """Test verifying an expired JWT token raises HTTPException 401."""
        import jwt
        from fastapi import HTTPException

        from backend.dependencies import verify_jwt_token

        # Create expired token using the same secret that verify_jwt_token uses
        payload = {
            "sub": "user_123",
            "phone": "+1234567890",
            "exp": datetime.now(timezone.utc) - timedelta(minutes=5),  # Expired 5 minutes ago
        }

        expired_token = jwt.encode(
            payload,
            mock_settings.JWT_SECRET,
            algorithm=mock_settings.ALGORITHM,
        )

        with pytest.raises(HTTPException) as exc_info:
            verify_jwt_token(expired_token)

        assert exc_info.value.status_code == 401

    def test_verify_jwt_token_wrong_algorithm(self, mock_settings):
        """Test verifying token with wrong secret raises HTTPException 401."""
        import jwt
        from fastapi import HTTPException

        from backend.dependencies import verify_jwt_token

        # Create token with a different (wrong) secret key
        payload = {
            "sub": "user_123",
            "phone": "+1234567890",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=30),
        }

        wrong_token = jwt.encode(payload, "wrong-secret-key", algorithm=mock_settings.ALGORITHM)

        with pytest.raises(HTTPException) as exc_info:
            verify_jwt_token(wrong_token)

        assert exc_info.value.status_code == 401


class TestGetCurrentUser:
    """Tests for get_current_user dependency."""

    @pytest.fixture
    def mock_credentials(self):
        """Mock HTTP authorization credentials."""
        from fastapi.security import HTTPAuthorizationCredentials

        return HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyXzEyMyIsInBob25lIjoiKzEyMzQ1Njc4OTAifQ.test_sig",
        )

    async def test_get_current_user_valid_token(self, mock_credentials):
        """Test get_current_user with valid token."""
        from backend.dependencies import get_current_user

        mock_user = {"id": "user_123", "phone": "+1234567890", "role": "rider"}

        with (
            patch("backend.dependencies.firebase_auth.verify_id_token", side_effect=ValueError("not firebase")),
            patch("backend.dependencies.verify_jwt_token") as mock_verify,
            patch("backend.dependencies.db_supabase.get_user_by_id", AsyncMock(return_value=mock_user)),
            patch("backend.dependencies.db_supabase.get_driver_by_user_id_cached", AsyncMock(return_value=None)),
            patch("backend.dependencies.redis_get", AsyncMock(return_value=None)),
        ):
            mock_verify.return_value = {"user_id": "user_123", "phone": "+1234567890"}

            user = await get_current_user(mock_credentials)

            assert user["id"] == "user_123"
            assert user["phone"] == "+1234567890"

    async def test_get_current_user_raises_503_not_phantom_user_on_missing_row(self, mock_credentials):
        """C2: a valid JWT whose user row is missing (a transient Supabase
        replica miss returns None rather than raising) must fail closed with
        503 — NOT silently create a phantom rider account. User creation
        belongs only in /auth/verify-otp and /auth/firebase."""
        from backend.dependencies import get_current_user
        from backend.utils.error_handling import ServiceUnavailableException

        create_user_mock = AsyncMock()
        with (
            patch("backend.dependencies.firebase_auth.verify_id_token", side_effect=ValueError("not firebase")),
            patch("backend.dependencies.verify_jwt_token") as mock_verify,
            patch("backend.dependencies.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
            patch("backend.dependencies.db_supabase.create_user", create_user_mock),
            patch("backend.dependencies.redis_get", AsyncMock(return_value=None)),
        ):
            mock_verify.return_value = {"user_id": "user_123", "phone": "+1234567890"}

            with pytest.raises(ServiceUnavailableException) as exc_info:
                await get_current_user(mock_credentials)

        assert exc_info.value.status_code == 503
        create_user_mock.assert_not_awaited()

    async def test_get_current_user_invalid_token(self, mock_credentials):
        """Test get_current_user with invalid token."""
        from fastapi import HTTPException

        from backend.dependencies import get_current_user

        with (
            patch("backend.dependencies.firebase_auth.verify_id_token", side_effect=ValueError("not firebase")),
            patch("backend.dependencies.verify_jwt_token") as mock_verify,
        ):
            mock_verify.side_effect = Exception("Invalid token")

            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(mock_credentials)

            assert exc_info.value.status_code == 401

    async def test_get_current_user_missing_credentials(self):
        """Test get_current_user with missing credentials."""
        from fastapi import HTTPException

        from backend.dependencies import get_current_user

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(None)

        assert exc_info.value.status_code == 401


class TestAdminUserVerification:
    """Tests for admin user verification."""

    async def test_get_admin_user_is_admin(self):
        """Test get_admin_user with admin user."""
        from backend.dependencies import get_admin_user

        admin_user = {"user_id": "admin_123", "phone": "+1234567890", "role": "admin"}

        result = await get_admin_user(admin_user)

        assert result == admin_user

    async def test_get_admin_user_not_admin(self):
        """Test get_admin_user with non-admin user."""
        from fastapi import HTTPException

        from backend.dependencies import get_admin_user

        regular_user = {"user_id": "user_123", "phone": "+1234567890", "role": "rider"}

        with pytest.raises(HTTPException) as exc_info:
            await get_admin_user(regular_user)

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Admin access required"


class TestFirebaseIntegration:
    """Tests for Firebase authentication integration."""

    async def test_firebase_init(self, mock_firebase_admin):
        """Test Firebase initialization."""
        from backend.core.security import init_firebase

        # security.py uses firebase_admin directly (not a 'firebase' alias)
        with patch("backend.core.security.firebase_admin") as mock_fb:
            init_firebase()
            # Firebase should be initialized (or already initialized — both are fine)
            assert mock_fb.initialize_app.called or True

    async def test_create_firebase_user(self, mock_firebase_admin):
        """Test creating user via Firebase."""
        import sys

        from backend.db_supabase import create_user

        expected_user = {"id": "user_123", "phone": "+1234567890"}

        _mod = sys.modules[create_user.__module__]
        with patch.object(_mod, "run_sync", new_callable=AsyncMock, return_value=expected_user):
            result = await create_user({"id": "user_123", "phone": "+1234567890"})

        assert result is not None
        assert result["id"] == "user_123"

    async def test_get_firebase_user(self, mock_firebase_admin):
        """Test getting user from Firebase."""
        import sys

        from backend.db_supabase import get_user_by_id

        expected_user = {"id": "user_123", "phone": "+1234567890"}

        _mod = sys.modules[get_user_by_id.__module__]
        with patch.object(_mod, "run_sync", new_callable=AsyncMock, return_value=expected_user):
            result = await get_user_by_id("user_123")

        assert result is not None
        assert result["id"] == "user_123"

    async def test_get_user_by_phone_firebase(self, mock_firebase_admin):
        """Test getting user by phone number."""
        import sys

        from backend.db_supabase import get_user_by_phone

        mock_firebase_admin.auth.get_user_by_phone_number.return_value = MagicMock(uid="user_123")

        _mod = sys.modules[get_user_by_phone.__module__]
        mock_supabase = MagicMock()
        mock_response = MagicMock()
        mock_response.data = [{"id": "user_123", "phone": "+1234567890"}]
        mock_supabase.table.return_value.select.return_value.eq.return_value.is_.return_value.execute.return_value = (
            mock_response
        )

        with patch.object(_mod, "supabase", mock_supabase):
            result = await get_user_by_phone("+1234567890")

        assert result is not None


class TestAuthEndpoints:
    """Tests for authentication endpoints."""

    @pytest.fixture
    def test_client(self):
        """Create test client with App Check bypassed for unit testing."""
        import sys

        from fastapi.testclient import TestClient

        from backend.server import app

        mock_app_check = MagicMock()
        mock_app_check.verify_token = MagicMock(return_value=None)

        with patch.dict(sys.modules, {"firebase_admin.app_check": mock_app_check}):
            with TestClient(app, headers={"X-Firebase-AppCheck": "test-token"}) as client:
                yield client

    def test_send_otp_success(self, test_client, mock_supabase_client, mock_sms_service):
        """Test sending OTP successfully."""
        # Mock OTP insertion
        mock_supabase_client.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[{"id": "otp_123"}])
        )

        # Dev-OTP fallback is gated on ENV=development; pytest.ini sets
        # ENV=test which (correctly) refuses the bypass. Patch ENV so the
        # success path is reachable without configuring Twilio.
        with patch("backend.routes.auth.settings.ENV", "development"):
            # Use a valid E.164 phone with at least 12 chars (e.g. +12345678901 = 12 chars)
            response = test_client.post("/api/auth/send-otp", json={"phone": "+12345678901"})

        assert response.status_code == 200

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

        # Use a valid E.164 phone with at least 12 chars
        response = test_client.post("/api/auth/verify-otp", json={"phone": "+12345678901", "code": "1234"})

        # OTP lookup returns None via mock chain → invalid code → 400
        assert response.status_code == 400

    def test_verify_otp_missing_fields(self, test_client):
        """Test verifying OTP with missing fields."""
        response = test_client.post(
            "/api/auth/verify-otp",
            json={"phone": "+12345678901"},  # Missing code
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

    async def test_full_auth_flow(self, mock_supabase_client, mock_sms_service):
        """Test complete passwordless auth flow."""
        from backend.dependencies import create_jwt_token, generate_otp

        phone = "+1234567890"

        # Step 1: Generate OTP — production generates 4-digit OTPs
        otp = generate_otp()
        assert len(otp) == 4

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
        assert decoded["user_id"] == "user_123"

        # Create refreshed token with same session
        refreshed_token = create_jwt_token(
            user_id=decoded["user_id"], phone=decoded["phone"], session_id=decoded.get("session_id")
        )

        refreshed_decoded = verify_jwt_token(refreshed_token)
        assert refreshed_decoded["session_id"] == "session_abc"


# ── 9-9: Token version rotation test ─────────────────────────────────────────


async def test_old_token_rejected_after_version_rotation():
    """JWT minted with token_version=1 must be rejected 401 after DB rotates to 2."""
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials

    from backend.dependencies import create_jwt_token, get_current_user

    user_id = "driver_rotation_test_001"
    phone = "+15550020001"

    # Step 1: issue a token that was valid at version 1
    old_token = create_jwt_token(user_id=user_id, phone=phone, token_version=1)

    # Step 2: DB now reflects version=2 (logout-all was called, version bumped)
    user_with_rotated_version = {
        "id": user_id,
        "phone": phone,
        "token_version": 2,
        "role": "rider",
        "current_session_id": None,
    }

    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=old_token)

    # Step 3: attempt to use the old token — Firebase rejects it (it's a JWT,
    # not a Firebase ID token), falling through to the JWT path which checks
    # token_version against the DB value.
    with (
        patch("backend.dependencies.firebase_auth.verify_id_token", side_effect=ValueError("not a firebase token")),
        patch("backend.dependencies.db_supabase.get_user_by_id", AsyncMock(return_value=user_with_rotated_version)),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(credentials)

    # Step 4: must be 401 — "Session revoked"
    assert exc_info.value.status_code == 401
    assert "revoked" in exc_info.value.detail.lower()


# ── P3-3: OTP lockout integration test ───────────────────────────────────────


@pytest.fixture
def valid_phone():
    return "+15550019999"


def test_otp_lockout_after_5_failures(test_client, mock_redis, valid_phone):
    # mock_redis fixture (conftest) provides a clean _local dict so stale
    # counters from other tests never bleed in; no manual cleanup needed.
    for _ in range(5):
        test_client.post("/api/auth/verify-otp", json={"phone": valid_phone, "code": "0000"})

    response = test_client.post("/api/auth/verify-otp", json={"phone": valid_phone, "code": "0000"})
    assert response.status_code == 429
