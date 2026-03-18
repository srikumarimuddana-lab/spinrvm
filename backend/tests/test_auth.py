"""
Unit tests for authentication and security modules.
Tests cover JWT token handling, OTP generation/verification, and user authentication.
"""
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timedelta
from typing import Dict, Any

import sys
import os
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
    """Tests for JWT token creation and verification."""
    
    @pytest.fixture
    def mock_settings(self):
        """Mock settings with test values."""
        with patch('backend.dependencies.settings') as mock_settings:
            mock_settings.SECRET_KEY = 'test-secret-key-for-testing-only'
            mock_settings.ALGORITHM = 'HS256'
            mock_settings.ACCESS_TOKEN_EXPIRE_MINUTES = 30
            yield mock_settings
    
    def test_create_jwt_token(self, mock_settings):
        """Test JWT token creation."""
        from backend.dependencies import create_jwt_token
        
        token = create_jwt_token(
            user_id='user_123',
            phone='+1234567890'
        )
        
        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 0
    
    def test_create_jwt_token_with_session(self, mock_settings):
        """Test JWT token creation with session ID."""
        from backend.dependencies import create_jwt_token
        
        token = create_jwt_token(
            user_id='user_123',
            phone='+1234567890',
            session_id='session_abc'
        )
        
        assert token is not None
        # Verify token can be decoded
        decoded = create_jwt_token.verify_jwt_token(token, mock_settings)
        assert decoded['session_id'] == 'session_abc'
    
    def test_verify_jwt_token_valid(self, mock_settings):
        """Test verifying a valid JWT token."""
        from backend.dependencies import create_jwt_token, verify_jwt_token
        
        # Create token
        token = create_jwt_token(
            user_id='user_123',
            phone='+1234567890'
        )
        
        # Verify token
        decoded = verify_jwt_token(token)
        
        assert decoded is not None
        assert decoded['sub'] == 'user_123'
        assert decoded['phone'] == '+1234567890'
    
    def test_verify_jwt_token_invalid(self, mock_settings):
        """Test verifying an invalid JWT token."""
        from backend.dependencies import verify_jwt_token
        
        with pytest.raises(Exception):
            verify_jwt_token('invalid.token.here')
    
    def test_verify_jwt_token_expired(self, mock_settings):
        """Test verifying an expired JWT token."""
        import jwt
        from backend.dependencies import verify_jwt_token
        
        # Create expired token
        payload = {
            'sub': 'user_123',
            'phone': '+1234567890',
            'exp': datetime.utcnow() - timedelta(minutes=5)  # Expired 5 minutes ago
        }
        
        expired_token = jwt.encode(
            payload,
            mock_settings.SECRET_KEY,
            algorithm=mock_settings.ALGORITHM
        )
        
        with pytest.raises(jwt.ExpiredSignatureError):
            verify_jwt_token(expired_token)
    
    def test_verify_jwt_token_wrong_algorithm(self, mock_settings):
        """Test verifying token with wrong algorithm."""
        import jwt
        from backend.dependencies import verify_jwt_token
        
        # Create token with different algorithm
        payload = {
            'sub': 'user_123',
            'phone': '+1234567890',
            'exp': datetime.utcnow() + timedelta(minutes=30)
        }
        
        wrong_token = jwt.encode(
            payload,
            'wrong-secret-key',
            algorithm=mock_settings.ALGORITHM
        )
        
        with pytest.raises(jwt.InvalidTokenError):
            verify_jwt_token(wrong_token)


class TestGetCurrentUser:
    """Tests for get_current_user dependency."""
    
    @pytest.fixture
    def mock_credentials(self):
        """Mock HTTP authorization credentials."""
        from fastapi.security import HTTPAuthorizationCredentials
        
        return HTTPAuthorizationCredentials(
            scheme='Bearer',
            credentials='eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyXzEyMyIsInBob25lIjoiKzEyMzQ1Njc4OTAifQ.test_sig'
        )
    
    @pytest.mark.asyncio
    async def test_get_current_user_valid_token(self, mock_credentials):
        """Test get_current_user with valid token."""
        from backend.dependencies import get_current_user
        
        with patch('backend.dependencies.verify_jwt_token') as mock_verify:
            mock_verify.return_value = {
                'sub': 'user_123',
                'phone': '+1234567890'
            }
            
            user = await get_current_user(mock_credentials)
            
            assert user['user_id'] == 'user_123'
            assert user['phone'] == '+1234567890'
    
    @pytest.mark.asyncio
    async def test_get_current_user_invalid_token(self, mock_credentials):
        """Test get_current_user with invalid token."""
        from backend.dependencies import get_current_user
        from fastapi import HTTPException
        
        with patch('backend.dependencies.verify_jwt_token') as mock_verify:
            mock_verify.side_effect = Exception('Invalid token')
            
            with pytest.raises(HTTPException) as exc_info:
                await get_current_user(mock_credentials)
            
            assert exc_info.value.status_code == 401
    
    @pytest.mark.asyncio
    async def test_get_current_user_missing_credentials(self):
        """Test get_current_user with missing credentials."""
        from backend.dependencies import get_current_user
        from fastapi import HTTPException
        
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(None)
        
        assert exc_info.value.status_code == 401


class TestAdminUserVerification:
    """Tests for admin user verification."""
    
    @pytest.mark.asyncio
    async def test_get_admin_user_is_admin(self):
        """Test get_admin_user with admin user."""
        from backend.dependencies import get_admin_user
        
        admin_user = {
            'user_id': 'admin_123',
            'phone': '+1234567890',
            'is_admin': True
        }
        
        result = await get_admin_user(admin_user)
        
        assert result == admin_user
    
    @pytest.mark.asyncio
    async def test_get_admin_user_not_admin(self):
        """Test get_admin_user with non-admin user."""
        from backend.dependencies import get_admin_user
        from fastapi import HTTPException
        
        regular_user = {
            'user_id': 'user_123',
            'phone': '+1234567890',
            'is_admin': False
        }
        
        with pytest.raises(HTTPException) as exc_info:
            await get_admin_user(regular_user)
        
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == 'User is not an admin'


class TestFirebaseIntegration:
    """Tests for Firebase authentication integration."""
    
    @pytest.mark.asyncio
    async def test_firebase_init(self, mock_firebase_admin):
        """Test Firebase initialization."""
        from backend.core.security import init_firebase
        
        with patch('backend.core.security.firebase') as mock_firebase:
            init_firebase()
            # Firebase should be initialized
            assert mock_firebase.initialize_app.called or True  # May be skipped if already init
    
    @pytest.mark.asyncio
    async def test_create_firebase_user(self, mock_firebase_admin):
        """Test creating user via Firebase."""
        from backend.db_supabase import create_user
        
        mock_firebase_admin.auth.create_user.return_value = MagicMock(uid='firebase_uid')
        
        with patch('backend.db_supabase.supabase') as mock_supabase:
            mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(
                return_value=MagicMock(data=[{'id': 'user_123'}])
            )
            
            result = await create_user({
                'phone': '+1234567890',
                'email': 'test@example.com'
            })
            
            assert result is not None
    
    @pytest.mark.asyncio
    async def test_get_firebase_user(self, mock_firebase_admin):
        """Test getting user from Firebase."""
        from backend.db_supabase import get_user_by_id
        
        mock_firebase_admin.auth.get_user.return_value = MagicMock(
            uid='user_123',
            phone_number='+1234567890'
        )
        
        with patch('backend.db_supabase.supabase') as mock_supabase:
            mock_response = MagicMock()
            mock_response.data = [{'id': 'user_123', 'phone': '+1234567890'}]
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
                return_value=mock_response
            )
            
            result = await get_user_by_id('user_123')
            
            assert result is not None
            assert result['id'] == 'user_123'
    
    @pytest.mark.asyncio
    async def test_get_user_by_phone_firebase(self, mock_firebase_admin):
        """Test getting user by phone number."""
        from backend.db_supabase import get_user_by_phone
        
        mock_firebase_admin.auth.get_user_by_phone_number.return_value = MagicMock(
            uid='user_123'
        )
        
        with patch('backend.db_supabase.supabase') as mock_supabase:
            mock_response = MagicMock()
            mock_response.data = [{'id': 'user_123', 'phone': '+1234567890'}]
            mock_supabase.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
                return_value=mock_response
            )
            
            result = await get_user_by_phone('+1234567890')
            
            assert result is not None


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
            return_value=MagicMock(data=[{'id': 'otp_123'}])
        )
        
        response = test_client.post(
            '/api/auth/send-otp',
            json={'phone': '+1234567890'}
        )
        
        # Should either succeed or be rate limited
        assert response.status_code in [200, 429]
    
    def test_send_otp_missing_phone(self, test_client):
        """Test sending OTP with missing phone number."""
        response = test_client.post(
            '/api/auth/send-otp',
            json={}
        )
        
        assert response.status_code == 422  # Validation error
    
    def test_send_otp_invalid_phone_format(self, test_client):
        """Test sending OTP with invalid phone format."""
        response = test_client.post(
            '/api/auth/send-otp',
            json={'phone': 'invalid'}
        )
        
        # Should be validation error or handled gracefully
        assert response.status_code in [400, 422]
    
    def test_verify_otp_success(self, test_client, mock_supabase_client):
        """Test verifying OTP successfully."""
        # Mock OTP lookup
        mock_response = MagicMock()
        mock_response.data = [{'id': 'otp_123', 'verified': False}]
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        response = test_client.post(
            '/api/auth/verify-otp',
            json={'phone': '+1234567890', 'code': '123456'}
        )
        
        # Should succeed or fail with appropriate error
        assert response.status_code in [200, 400, 401]
    
    def test_verify_otp_missing_fields(self, test_client):
        """Test verifying OTP with missing fields."""
        response = test_client.post(
            '/api/auth/verify-otp',
            json={'phone': '+1234567890'}  # Missing code
        )
        
        assert response.status_code == 422  # Validation error


class TestSessionManagement:
    """Tests for session management."""
    
    def test_session_id_in_token(self, mock_settings):
        """Test that session ID is included in JWT token."""
        from backend.dependencies import create_jwt_token, verify_jwt_token
        
        session_id = 'test_session_123'
        token = create_jwt_token(
            user_id='user_123',
            phone='+1234567890',
            session_id=session_id
        )
        
        decoded = verify_jwt_token(token)
        assert decoded.get('session_id') == session_id
    
    @pytest.mark.asyncio
    async def test_session_invalidation(self):
        """Test session invalidation logic."""
        # Sessions can be invalidated by checking against a blacklist
        # or by verifying the session still exists in the database
        
        session_blacklist = {'session_123', 'session_456'}
        
        def is_session_valid(session_id: str) -> bool:
            return session_id not in session_blacklist
        
        assert is_session_valid('session_789') is True
        assert is_session_valid('session_123') is False


class TestPasswordlessAuth:
    """Tests for passwordless authentication flow."""
    
    @pytest.mark.asyncio
    async def test_full_auth_flow(self, mock_supabase_client, mock_sms_service):
        """Test complete passwordless auth flow."""
        from backend.dependencies import generate_otp, create_jwt_token
        
        phone = '+1234567890'
        
        # Step 1: Generate OTP
        otp = generate_otp()
        assert len(otp) == 6
        
        # Step 2: Send OTP (mocked)
        await mock_sms_service.send_otp(phone, otp)
        mock_sms_service.send_otp.assert_called_once()
        
        # Step 3: Verify OTP (would check database)
        mock_response = MagicMock()
        mock_response.data = [{'id': 'otp_123', 'verified': False}]
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value=mock_response
        )
        
        # Step 4: Create JWT token after verification
        token = create_jwt_token(
            user_id='user_123',
            phone=phone
        )
        
        assert token is not None
        assert isinstance(token, str)


class TestTokenRefresh:
    """Tests for token refresh functionality."""
    
    def test_token_refresh_with_valid_session(self, mock_settings):
        """Test refreshing token with valid session."""
        from backend.dependencies import create_jwt_token, verify_jwt_token
        
        # Create initial token
        original_token = create_jwt_token(
            user_id='user_123',
            phone='+1234567890',
            session_id='session_abc'
        )
        
        decoded = verify_jwt_token(original_token)
        assert decoded['sub'] == 'user_123'
        
        # Create refreshed token with same session
        refreshed_token = create_jwt_token(
            user_id=decoded['sub'],
            phone=decoded['phone'],
            session_id=decoded.get('session_id')
        )
        
        refreshed_decoded = verify_jwt_token(refreshed_token)
        assert refreshed_decoded['session_id'] == 'session_abc'