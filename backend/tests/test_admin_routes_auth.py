"""
Tests that admin_router routes require authentication.

The router-level `Depends(get_admin_user)` was added in commit c5e95e3
to gate all 110+ admin endpoints. These tests verify that requests
without a valid Authorization header are rejected.
"""

import pytest


class TestAdminRoutesRequireAuth:
    """Verify admin routes reject unauthenticated requests."""

    @pytest.fixture
    def client(self, test_client):
        """Use the TestClient from conftest."""
        return test_client

    def test_admin_settings_requires_auth(self, client):
        """GET /api/admin/settings without token → 401 or 403."""
        response = client.get("/api/admin/settings")
        assert response.status_code in (401, 403)

    def test_admin_drivers_requires_auth(self, client):
        """GET /api/admin/drivers without token → 401 or 403."""
        response = client.get("/api/admin/drivers")
        assert response.status_code in (401, 403)

    def test_admin_staff_requires_auth(self, client):
        """GET /api/admin/staff without token → 401 or 403."""
        response = client.get("/api/admin/staff")
        assert response.status_code in (401, 403)

    def test_admin_users_requires_auth(self, client):
        """GET /api/admin/users without token → 401 or 403."""
        response = client.get("/api/admin/users")
        assert response.status_code in (401, 403)

    def test_admin_auth_login_is_public(self, client):
        """POST /api/admin/auth/login is reachable (returns 401 for bad creds, NOT 403)."""
        response = client.post(
            "/api/admin/auth/login",
            json={"email": "wrong@example.com", "password": "wrong"},
        )
        # Should be 401 "Invalid credentials", NOT 403 "Admin access required"
        assert response.status_code == 401
        assert "Invalid credentials" in response.json().get("detail", "")

    def test_admin_auth_session_is_public(self, client):
        """GET /api/admin/auth/session without token returns authenticated=false, NOT 403."""
        response = client.get("/api/admin/auth/session")
        assert response.status_code == 200
        data = response.json()
        assert data.get("authenticated") is False
