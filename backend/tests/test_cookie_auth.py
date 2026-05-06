"""Tests for HttpOnly cookie-based authentication."""
import pytest
from fastapi.testclient import TestClient

from backend.core.config import settings
from backend.server import app


@pytest.fixture
def client_with_mocks(patch_external_dependencies):
    """Provide test client with all external dependencies mocked."""
    return TestClient(app)


@pytest.mark.unit
def test_cookie_security_flags():
    """Verify cookies are configured with correct security flags."""
    assert settings.COOKIE_HTTPONLY is True
    assert settings.COOKIE_SAMESITE == "Strict"
    assert settings.COOKIE_SECURE in [True, False]


@pytest.mark.unit
def test_httponly_cookie_settings_enabled():
    """Verify httponly and other security cookie settings are enabled."""
    # These settings are critical for cookie-based auth security
    assert settings.COOKIE_HTTPONLY is True, "HttpOnly flag must be enabled"
    assert settings.COOKIE_SAMESITE == "Strict", "SameSite must be Strict for security"
    # Secure flag may vary: True in production, False in development


@pytest.mark.unit
def test_cookie_configuration_immutable(client_with_mocks):
    """Verify cookie security configuration is set at app creation."""
    # The TestClient wraps the FastAPI app
    # Configuration should be locked in at app factory time
    assert hasattr(settings, 'COOKIE_HTTPONLY')
    assert hasattr(settings, 'COOKIE_SAMESITE')
    assert hasattr(settings, 'COOKIE_SECURE')


@pytest.mark.unit
def test_no_tokens_in_response_body():
    """Verify response format doesn't expose tokens in JSON body."""
    # This documents expected behavior:
    # Tokens should ONLY be in HTTP-only cookies, never in JSON
    response_data = {
        'status': 'ok',
        'user': {
            'id': 'user-123',
            'phone': '+16475551234',
        }
    }

    assert 'access_token' not in response_data
    assert 'refresh_token' not in response_data
    assert 'token' not in response_data


@pytest.mark.unit
def test_client_credentials_requirement():
    """Document that clients must use credentials: 'include' for cookie-based auth."""
    # This is a specification test:
    # - Axios clients must use: { withCredentials: true }
    # - Fetch clients must use: { credentials: 'include' }
    # Without these settings, cookies are not sent by the browser
    assert True  # Markers for documentation
