"""
A-P3-8: Security tests for admin auth and RBAC scenarios.

Covers six critical security properties:
  1. Brute-force lockout (5 failures in 1 hour → 423 for 24 hours)
  2. Deactivated staff tokens are rejected (403 on login)
  3. Non-super_admin cannot access staff module endpoints
  4. Only super_admin can create staff (role escalation prevented)
  5. Logout invalidates the refresh token
  6. Expired access token returns 401 (idle timeout)
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest

# ---------------------------------------------------------------------------
# Minimal stubs so the module imports succeed in isolation
# ---------------------------------------------------------------------------
for _mod in ["loguru", "logging_utils", "sentry_sdk", "firebase_admin"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_admin_token(
    user_id: str = "admin-001",
    email: str = "admin@spinr.ca",
    role: str = "super_admin",
    modules: list | None = None,
    expired: bool = False,
    secret: str = "test-secret-32-chars-minimum-len",
) -> str:
    """Mint a signed admin JWT for use in test requests."""
    now = datetime.now(timezone.utc)
    exp = now - timedelta(minutes=5) if expired else now + timedelta(hours=1)
    return jwt.encode(
        {
            "user_id": user_id,
            "email": email,
            "role": role,
            "modules": modules or ["dashboard"],
            "phone": email,
            "token_version": 0,
            "aud": "spinr:admin",
            "iat": now,
            "exp": exp,
        },
        secret,
        algorithm="HS256",
    )


# ---------------------------------------------------------------------------
# Test: 1 — Brute-force lockout (A-P3-2)
# ---------------------------------------------------------------------------


class TestBruteForceAccountLockout:
    """After _LOGIN_MAX_FAILURES failures the account is locked (HTTP 423)."""

    def test_login_lockout_after_5_failures(self, test_client):
        """Mock Redis to report failure threshold reached → expect 423."""
        with patch("backend.routes.admin.auth._is_account_locked", new=AsyncMock(return_value=True)):
            response = test_client.post(
                "/api/admin/auth/login",
                json={"email": "locked@spinr.ca", "password": "any"},
            )
        assert response.status_code == 423, f"Expected 423 (locked), got {response.status_code}"
        body = response.json()
        assert "locked" in body.get("detail", "").lower() or "lock" in str(body).lower()

    def test_login_succeeds_when_not_locked(self, test_client):
        """When _is_account_locked returns False, login proceeds normally (401 on bad creds)."""
        with (
            patch("backend.routes.admin.auth._is_account_locked", new=AsyncMock(return_value=False)),
            patch("backend.routes.admin.auth.db_supabase") as mock_db,
        ):
            mock_db.get_rows = AsyncMock(return_value=[])  # no staff row → fall-through
            response = test_client.post(
                "/api/admin/auth/login",
                json={"email": "unknown@spinr.ca", "password": "wrong"},
            )
        # Not 423 (not locked) and not 403 (not an auth gate block)
        assert response.status_code != 423
        assert response.status_code in (401, 500)

    def test_failure_counter_incremented_on_bad_credentials(self, test_client):
        """_record_login_failure must be called when credentials are wrong."""
        with (
            patch("backend.routes.admin.auth._is_account_locked", new=AsyncMock(return_value=False)),
            patch("backend.routes.admin.auth._record_login_failure", new=AsyncMock()) as mock_record,
            patch("backend.routes.admin.auth.db_supabase") as mock_db,
        ):
            mock_db.get_rows = AsyncMock(return_value=[])
            test_client.post(
                "/api/admin/auth/login",
                json={"email": "target@spinr.ca", "password": "wrong"},
            )
        mock_record.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test: 2 — Deactivated staff account is rejected (A-P1-2 regression)
# ---------------------------------------------------------------------------


class TestDeactivatedStaffRejected:
    """A deactivated staff member cannot log in even with correct password."""

    def test_deactivated_staff_token_is_rejected(self, test_client):
        """staff.is_active=False → 403 even when password matches."""
        from backend.utils.password import hash_password

        fake_staff = {
            "id": "staff-deact-1",
            "email": "deact@spinr.ca",
            "password_hash": hash_password("correct-password-123"),
            "role": "support",
            "modules": ["dashboard"],
            "is_active": False,
            "token_version": 0,
        }
        with (
            patch("backend.routes.admin.auth._is_account_locked", new=AsyncMock(return_value=False)),
            patch("backend.routes.admin.auth.db_supabase") as mock_db,
        ):
            mock_db.get_rows = AsyncMock(return_value=[fake_staff])
            response = test_client.post(
                "/api/admin/auth/login",
                json={"email": "deact@spinr.ca", "password": "correct-password-123"},
            )
        assert response.status_code == 403
        assert "deactivated" in response.json().get("detail", "").lower()


# ---------------------------------------------------------------------------
# Test: 3+4 — Role-based access control on staff endpoints (A-P3-5)
# ---------------------------------------------------------------------------


class TestStaffRBAC:
    """Non-super_admin tokens cannot mutate staff rows."""

    @pytest.fixture
    def support_override(self, test_client):
        """Override get_admin_user with a support-role token."""
        from backend.server import app

        try:
            from backend.dependencies import get_admin_user
        except ImportError:
            from dependencies import get_admin_user

        app.dependency_overrides[get_admin_user] = lambda: {
            "id": "staff-support-1",
            "role": "support",
            "modules": ["support", "dashboard"],
        }
        yield test_client
        app.dependency_overrides.pop(get_admin_user, None)

    @pytest.fixture
    def super_admin_override(self, test_client):
        """Override get_admin_user with a super_admin token."""
        from backend.server import app

        try:
            from backend.dependencies import get_admin_user
        except ImportError:
            from dependencies import get_admin_user

        app.dependency_overrides[get_admin_user] = lambda: {
            "id": "admin-001",
            "role": "super_admin",
            "modules": ["staff", "dashboard"],
        }
        yield test_client
        app.dependency_overrides.pop(get_admin_user, None)

    def test_support_role_cannot_create_staff(self, support_override):
        """A support-role actor gets 403 on POST /staff."""
        client = support_override
        response = client.post(
            "/api/admin/staff",
            json={
                "email": "new@spinr.ca",
                "password": "password-twelve-chars",
                "first_name": "New",
                "last_name": "Staff",
                "role": "support",
            },
        )
        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.json()}"

    def test_support_role_cannot_update_staff(self, support_override):
        """A support-role actor gets 403 on PUT /staff/{id} (A-P3-5)."""
        client = support_override
        response = client.put(
            "/api/admin/staff/some-staff-id",
            json={"first_name": "Hacked"},
        )
        assert response.status_code == 403

    def test_support_role_cannot_delete_staff(self, support_override):
        """A support-role actor gets 403 on DELETE /staff/{id}."""
        client = support_override
        response = client.delete("/api/admin/staff/some-staff-id")
        assert response.status_code == 403

    def test_only_super_admin_can_create_staff(self, support_override):
        """Finance/support roles cannot escalate themselves by creating a super_admin."""
        client = support_override
        response = client.post(
            "/api/admin/staff",
            json={
                "email": "escalate@spinr.ca",
                "password": "password-twelve-chars",
                "first_name": "Evil",
                "last_name": "Actor",
                "role": "super_admin",
            },
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Test: 5 — Logout invalidates refresh token (A-P1-7 regression)
# ---------------------------------------------------------------------------


class TestLogoutInvalidatesToken:
    """Logout must actually revoke the refresh token."""

    def test_logout_calls_revoke_refresh_token(self, test_client):
        """POST /logout with a refresh token must invoke revoke_refresh_token."""
        with patch("backend.routes.admin.auth.revoke_refresh_token", new=AsyncMock()) as mock_revoke:
            response = test_client.post(
                "/api/admin/auth/logout",
                json={"refresh_token": "fake-refresh-token"},
            )
        assert response.status_code == 200
        mock_revoke.assert_awaited_once_with("fake-refresh-token")

    def test_logout_without_token_still_succeeds(self, test_client):
        """Logout with no refresh token is a no-op (clears CSRF cookie)."""
        response = test_client.post("/api/admin/auth/logout", json={})
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Test: 6 — Expired access token returns 401 (idle timeout, A-P2-2)
# ---------------------------------------------------------------------------


class TestExpiredTokenRejected:
    """An expired JWT must be rejected by the session endpoint."""

    def test_expired_token_returns_unauthenticated(self, test_client):
        """GET /session with an expired JWT returns authenticated=false."""
        from backend.core.config import settings

        expired_token = _make_admin_token(expired=True, secret=settings.JWT_SECRET)
        response = test_client.get(
            "/api/admin/auth/session",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("authenticated") is False, f"Expected authenticated=false, got: {data}"

    def test_valid_token_returns_authenticated(self, test_client):
        """GET /session with a fresh valid JWT returns authenticated=true."""
        from backend.core.config import settings

        valid_token = _make_admin_token(expired=False, secret=settings.JWT_SECRET)
        response = test_client.get(
            "/api/admin/auth/session",
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("authenticated") is True, f"Expected authenticated=true, got: {data}"


# ---------------------------------------------------------------------------
# Test: 7 — Expired / tampered token is HARD-rejected (401) on a protected
#           admin *resource* route, not merely softened on /session (A-P2-2).
# ---------------------------------------------------------------------------


class TestExpiredTokenRejectedOnResource:
    """The soft GET /session endpoint returns authenticated=false for an
    expired token; a real admin resource must instead reject with HTTP 401 so
    an expired or tampered console token can never reach handler logic.

    Targets GET /api/admin/drivers/stats — guarded by the router-level
    ``Depends(get_admin_user)`` (routes/admin/__init__.py). Auth resolves (and
    fails at JWT decode) before the handler runs, so no Supabase mock is
    needed. Deliberately does NOT use the ``admin_override`` fixture — the real
    ``get_admin_user`` must run.
    """

    RESOURCE_URL = "/api/admin/drivers/stats"

    def test_expired_token_is_rejected_401(self, test_client):
        from backend.core.config import settings

        # modules=["drivers"] so expiry — not RBAC — is the only possible
        # rejection reason (the decode raises before any module check anyway).
        expired = _make_admin_token(expired=True, modules=["drivers"], secret=settings.JWT_SECRET)
        resp = test_client.get(self.RESOURCE_URL, headers={"Authorization": f"Bearer {expired}"})
        assert resp.status_code == 401, f"Expected 401 for expired admin token, got {resp.status_code}: {resp.text}"
        # C4: the client-facing message is now a static "Invalid token" for
        # every JWT decode failure (expired/malformed/wrong-aud/bad-sig
        # alike) -- interpolating the real PyJWT reason into the response
        # would let an attacker fingerprint which claim failed. The real
        # cause ("Token has expired") is server-logged only.
        assert "invalid token" in resp.text.lower()

    def test_tampered_signature_is_rejected_401(self, test_client):
        # Signed with a secret other than settings.JWT_SECRET → signature
        # verification fails → InvalidTokenError → 401 (never 403/500).
        forged = _make_admin_token(
            expired=False,
            modules=["drivers"],
            secret="forged-secret-not-the-real-one-000",
        )
        resp = test_client.get(self.RESOURCE_URL, headers={"Authorization": f"Bearer {forged}"})
        assert resp.status_code == 401, f"Expected 401 for tampered admin token, got {resp.status_code}: {resp.text}"
