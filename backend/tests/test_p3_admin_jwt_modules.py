"""
P3-6: Admin JWT module-gating integration tests.

Admin JWTs carry a `modules` claim that controls which dashboard
sections a staff member can access. These tests verify the full
claim lifecycle:

  1. _mint_admin_access_token embeds the correct claims
     (role, modules, email, exp, token_version).
  2. verify_jwt_token / get_current_user correctly extracts the modules
     claim and bypasses the DB lookup for admin tokens.
  3. get_admin_user accepts all valid admin roles and rejects others.
  4. _token_version_mismatch detects stale tokens correctly.
  5. The /api/admin/auth/session endpoint reflects the modules claim.
  6. Expired tokens are rejected with HTTP 401.

Run:
    pytest backend/tests/test_p3_admin_jwt_modules.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest

# ── helpers ───────────────────────────────────────────────────────────


def _mint(
    user_id: str = "admin_001",
    email: str = "admin@spinr.ca",
    role: str = "admin",
    modules: list | None = None,
    token_version: int = 0,
    ttl_hours: float = 12,
) -> str:
    """Mint a real (signed) admin JWT using the same code path as production."""
    from backend.routes.admin.auth import _mint_admin_access_token

    token, _ = _mint_admin_access_token(
        user_id=user_id,
        email=email,
        role=role,
        modules=modules if modules is not None else ["dashboard", "users"],
        token_version=token_version,
    )
    return token


def _decode(token: str) -> dict:
    from backend.core.config import settings

    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.ALGORITHM], options={"verify_aud": False})


# ── JWT claim structure ───────────────────────────────────────────────


class TestMintAdminAccessToken:
    def test_token_contains_required_claims(self):
        token = _mint(role="admin", modules=["dashboard", "drivers"])
        payload = _decode(token)

        assert payload["role"] == "admin"
        assert payload["email"] == "admin@spinr.ca"
        assert payload["modules"] == ["dashboard", "drivers"]
        assert "exp" in payload
        assert "iat" in payload
        assert payload["token_version"] == 0

    def test_token_expires_after_ttl(self):
        """The exp claim must be roughly ADMIN_ACCESS_TOKEN_TTL_HOURS in the future."""
        from backend.core.config import settings

        token = _mint()
        payload = _decode(token)

        ttl = settings.ADMIN_ACCESS_TOKEN_TTL_HOURS
        now = datetime.now(timezone.utc).timestamp()
        exp = payload["exp"]
        # Within 90%–110% of the configured TTL window
        assert exp > now + ttl * 3600 * 0.9
        assert exp < now + ttl * 3600 * 1.1

    def test_token_carries_specific_modules(self):
        modules = ["dashboard", "support", "disputes"]
        token = _mint(modules=modules)
        payload = _decode(token)
        assert payload["modules"] == modules

    def test_token_version_embedded(self):
        token = _mint(token_version=7)
        payload = _decode(token)
        assert payload["token_version"] == 7

    def test_empty_modules_list_is_valid(self):
        """A token with no module access is still structurally valid."""
        token = _mint(modules=[])
        payload = _decode(token)
        assert payload["modules"] == []

    def test_super_admin_can_have_all_modules(self):
        all_modules = [
            "dashboard",
            "users",
            "drivers",
            "rides",
            "earnings",
            "promotions",
            "surge",
            "service_areas",
            "vehicle_types",
            "pricing",
            "support",
            "disputes",
            "notifications",
            "settings",
            "corporate_accounts",
            "documents",
            "heatmap",
            "staff",
        ]
        token = _mint(role="super_admin", modules=all_modules)
        payload = _decode(token)
        assert set(payload["modules"]) == set(all_modules)


# ── get_current_user with admin JWT ──────────────────────────────────


class TestGetCurrentUserAdminJWT:
    """Admin tokens bypass the DB lookup and return claims directly."""

    @pytest.mark.anyio
    async def test_admin_jwt_returns_modules_without_db_lookup(self):
        """admin-001 (env-seeded super admin) has no DB row — claims are trusted directly."""
        from fastapi.security import HTTPAuthorizationCredentials

        from dependencies import get_current_user

        modules = ["dashboard", "promotions"]
        # admin-001 is the one user_id that bypasses the admin_staff DB lookup
        token = _mint(role="admin", modules=modules, user_id="admin-001")

        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        user = await get_current_user(creds)

        assert user["id"] == "admin-001"
        assert user["role"] == "admin"
        assert user["modules"] == modules

    @pytest.mark.anyio
    async def test_operations_role_passes_through(self):
        from fastapi.security import HTTPAuthorizationCredentials

        from dependencies import get_current_user

        # Use admin-001 to bypass the admin_staff DB lookup — this test
        # verifies JWT claim parsing for operations role, not DB validation.
        token = _mint(role="operations", modules=["rides", "drivers"], user_id="admin-001")
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        user = await get_current_user(creds)

        assert user["role"] == "operations"
        assert "rides" in user["modules"]

    @pytest.mark.anyio
    async def test_rider_jwt_does_not_carry_modules_claim(self):
        """A regular rider JWT never has a `modules` key — get_current_user
        should not treat it as an admin token."""
        from unittest.mock import AsyncMock, patch

        from fastapi.security import HTTPAuthorizationCredentials

        import backend.db_supabase as dbs
        from backend.core.config import settings
        from backend.dependencies import get_current_user

        now = datetime.now(timezone.utc)
        rider_token = jwt.encode(
            {
                "user_id": "rider_xyz",
                "phone": "+13069999999",
                "iat": now,
                "exp": now + timedelta(minutes=15),
                "token_version": 0,
            },
            settings.JWT_SECRET,
            algorithm=settings.ALGORITHM,
        )
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=rider_token)

        rider_row = {
            "id": "rider_xyz",
            "phone": "+13069999999",
            "role": "rider",
            "token_version": 0,
            "current_session_id": None,
        }
        with patch.object(dbs, "get_user_by_id", AsyncMock(return_value=rider_row)):
            with patch.object(dbs, "get_driver_by_user_id_cached", AsyncMock(return_value=None)):
                user = await get_current_user(creds)

        assert user["role"] == "rider"
        assert "modules" not in user or user.get("modules") is None


# ── get_admin_user role check ─────────────────────────────────────────


class TestGetAdminUserRoleGating:
    @pytest.mark.anyio
    @pytest.mark.parametrize("role", ["admin", "super_admin", "operations", "support", "finance", "custom"])
    async def test_valid_admin_roles_pass_with_marker(self, role):
        """A verified admin (marker set by _verify_admin_payload) passes."""
        from dependencies import get_admin_user

        user = {"id": "u1", "role": role, "modules": ["dashboard"], "_admin_verified": True}
        result = await get_admin_user(user)
        assert result is user

    @pytest.mark.anyio
    @pytest.mark.parametrize("role", ["admin", "super_admin", "operations", "support", "finance", "custom"])
    async def test_admin_roles_without_marker_raise_403(self, role):
        """REGRESSION: an admin role string with no verified-admin marker (an
        ordinary rider/driver token whose users.role was set) must NOT pass."""
        from fastapi import HTTPException

        from dependencies import get_admin_user

        user = {"id": "u1", "role": role, "modules": ["dashboard"]}  # no _admin_verified
        with pytest.raises(HTTPException) as exc_info:
            await get_admin_user(user)
        assert exc_info.value.status_code == 403

    @pytest.mark.anyio
    @pytest.mark.parametrize("role", ["rider", "driver", "guest", "", "superadmin", "ADMIN"])
    async def test_invalid_roles_raise_403(self, role):
        from fastapi import HTTPException

        from dependencies import get_admin_user

        user = {"id": "u1", "role": role}
        with pytest.raises(HTTPException) as exc_info:
            await get_admin_user(user)
        assert exc_info.value.status_code == 403


# ── token_version revocation ──────────────────────────────────────────


class TestTokenVersionRevocation:
    def test_matching_version_is_not_stale(self):
        from dependencies import _token_version_mismatch

        payload = {"token_version": 3}
        user_row = {"token_version": 3}
        assert _token_version_mismatch(payload, user_row) is False

    def test_older_token_version_is_stale(self):
        from dependencies import _token_version_mismatch

        payload = {"token_version": 2}
        user_row = {"token_version": 5}
        assert _token_version_mismatch(payload, user_row) is True

    def test_newer_token_version_is_not_stale(self):
        """A token issued after a version bump should be valid."""
        from dependencies import _token_version_mismatch

        payload = {"token_version": 6}
        user_row = {"token_version": 5}
        assert _token_version_mismatch(payload, user_row) is False

    def test_missing_claim_treated_as_zero(self):
        from dependencies import _token_version_mismatch

        # Old token with no claim, user hasn't bumped version yet
        assert _token_version_mismatch({}, {"token_version": 0}) is False
        # Old token with no claim, user HAS bumped version
        assert _token_version_mismatch({}, {"token_version": 1}) is True

    def test_missing_user_version_treated_as_zero(self):
        from dependencies import _token_version_mismatch

        assert _token_version_mismatch({"token_version": 0}, {}) is False


# ── /api/admin/auth/session endpoint ─────────────────────────────────


class TestAdminSessionEndpoint:
    """The session endpoint must reflect the modules embedded in the JWT."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from backend.server import app

        with TestClient(app) as c:
            yield c

    def test_session_returns_modules_from_jwt(self, client):
        modules = ["dashboard", "disputes", "support"]
        token = _mint(role="admin", modules=modules, user_id="staff_session_1")

        resp = client.get(
            "/api/admin/auth/session",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["user"]["modules"] == modules
        assert data["user"]["role"] == "admin"

    def test_session_returns_unauthenticated_without_token(self, client):
        resp = client.get("/api/admin/auth/session")
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is False
        assert data["user"] is None

    def test_session_rejects_expired_token(self, client):
        """An expired admin JWT must not authenticate."""
        from backend.core.config import settings

        now = datetime.now(timezone.utc)
        expired_token = jwt.encode(
            {
                "user_id": "admin_001",
                "email": "admin@spinr.ca",
                "role": "admin",
                "modules": ["dashboard"],
                "phone": "",
                "token_version": 0,
                "iat": now - timedelta(hours=25),
                "exp": now - timedelta(hours=1),
            },
            settings.JWT_SECRET,
            algorithm=settings.ALGORITHM,
        )

        resp = client.get(
            "/api/admin/auth/session",
            headers={"Authorization": f"Bearer {expired_token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        # Expired token → unauthenticated, not a 500
        assert data["authenticated"] is False

    def test_session_with_custom_role_and_limited_modules(self, client):
        """A 'custom' role staff member sees only their assigned modules."""
        modules = ["support", "disputes"]
        token = _mint(role="custom", modules=modules, email="support@spinr.ca")

        resp = client.get(
            "/api/admin/auth/session",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["user"]["role"] == "custom"
        assert data["user"]["modules"] == modules

    def test_tampered_token_rejected(self, client):
        """A JWT with an invalid signature must not authenticate."""
        token = _mint(role="admin", modules=["dashboard"])
        tampered = token[:-5] + "XXXXX"

        resp = client.get(
            "/api/admin/auth/session",
            headers={"Authorization": f"Bearer {tampered}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is False


# ── audience claim enforcement (regression for the InvalidAudienceError ───
#    privilege-escalation gap where /session decoded any token signed
#    with JWT_SECRET, including rider/driver tokens) ────────────────────


class TestAdminSessionAudienceEnforcement:
    """`/api/admin/auth/session` must only accept tokens whose ``aud``
    claim is ``spinr:admin``. Tokens minted for the rider app, or admin-
    shaped tokens with no ``aud`` at all, must be rejected — otherwise
    a same-secret rider token would mint a successful admin session."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from backend.server import app

        with TestClient(app) as c:
            yield c

    def test_rider_audience_token_rejected(self, client):
        """A token with ``aud=spinr:rider`` must NOT authenticate as admin,
        even when its claims happen to look admin-shaped."""
        from backend.core.config import settings
        from backend.dependencies import JWT_AUD_MOBILE

        now = datetime.now(timezone.utc)
        rider_aud_token = jwt.encode(
            {
                "user_id": "attacker",
                "email": "attacker@example.com",
                "role": "super_admin",  # forged role — must be ignored
                "modules": ["dashboard", "users", "drivers"],
                "phone": "",
                "aud": JWT_AUD_MOBILE,  # ← rider audience
                "token_version": 0,
                "iat": now,
                "exp": now + timedelta(minutes=15),
            },
            settings.JWT_SECRET,
            algorithm=settings.ALGORITHM,
        )

        resp = client.get(
            "/api/admin/auth/session",
            headers={"Authorization": f"Bearer {rider_aud_token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is False
        assert data["user"] is None

    def test_no_audience_token_rejected(self, client):
        """A token with no ``aud`` claim at all must NOT authenticate.
        Strict ``audience=`` decoding rejects it via MissingRequiredClaimError."""
        from backend.core.config import settings

        now = datetime.now(timezone.utc)
        no_aud_token = jwt.encode(
            {
                "user_id": "no_aud_user",
                "email": "no_aud@spinr.ca",
                "role": "admin",
                "modules": ["dashboard"],
                "phone": "",
                "token_version": 0,
                "iat": now,
                "exp": now + timedelta(hours=1),
            },
            settings.JWT_SECRET,
            algorithm=settings.ALGORITHM,
        )

        resp = client.get(
            "/api/admin/auth/session",
            headers={"Authorization": f"Bearer {no_aud_token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is False

    def test_admin_audience_token_accepted(self, client):
        """The positive path: a properly minted admin token (which carries
        ``aud=spinr:admin``) authenticates successfully."""
        token = _mint(role="admin", modules=["dashboard"], user_id="admin_aud_ok")

        resp = client.get(
            "/api/admin/auth/session",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["user"]["id"] == "admin_aud_ok"
