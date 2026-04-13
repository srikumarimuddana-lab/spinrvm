"""
Tests for admin auth endpoints: login + change-password.
"""

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


class TestAdminLogin:
    """Tests for POST /api/admin/auth/login."""

    @pytest.fixture
    def mock_settings(self):
        mock = MagicMock()
        mock.ADMIN_EMAIL = "admin@spinr.ca"
        mock.ADMIN_PASSWORD = "TestAdminPass123!"
        mock.JWT_SECRET = "test-secret-key-for-testing-only-32chars!!"
        mock.ALGORITHM = "HS256"
        return mock

    @pytest.mark.asyncio
    async def test_super_admin_login(self, mock_settings):
        """Super admin (env credentials) gets a token with all modules."""
        with patch("backend.routes.admin.auth.settings", mock_settings):
            from backend.routes.admin.auth import LoginRequest, admin_login

            mock_request = MagicMock()  # FastAPI Request for rate limiter
            body = LoginRequest(email="admin@spinr.ca", password="TestAdminPass123!")
            result = await admin_login(mock_request, body)

            assert "token" in result
            assert result["user"]["role"] == "super_admin"
            assert "dashboard" in result["user"]["modules"]

    @pytest.mark.asyncio
    async def test_staff_bcrypt_login(self, mock_settings):
        """Staff member with bcrypt hash gets a token."""
        from backend.utils.password import hash_password

        bcrypt_hash = hash_password("StaffPass12345!")
        staff_row = {
            "id": "staff_1",
            "email": "staff@spinr.ca",
            "password_hash": bcrypt_hash,
            "role": "operations",
            "modules": ["dashboard", "rides", "drivers"],
            "is_active": True,
            "first_name": "Jane",
            "last_name": "Ops",
        }

        with (
            patch("backend.routes.admin.auth.settings", mock_settings),
            patch("backend.routes.admin.auth.db") as mock_db,
        ):
            mock_db.admin_staff.find_one = AsyncMock(return_value=staff_row)
            mock_db.admin_staff.update_one = AsyncMock()

            from backend.routes.admin.auth import LoginRequest, admin_login

            body = LoginRequest(email="staff@spinr.ca", password="StaffPass12345!")
            result = await admin_login(MagicMock(), body)

            assert "token" in result
            assert result["user"]["role"] == "operations"

    @pytest.mark.asyncio
    async def test_staff_legacy_sha256_upgrades_to_bcrypt(self, mock_settings):
        """Staff with legacy SHA256 hash gets upgraded to bcrypt on login."""
        legacy_hash = hashlib.sha256(b"LegacyPass12345!").hexdigest()
        staff_row = {
            "id": "staff_2",
            "email": "legacy@spinr.ca",
            "password_hash": legacy_hash,
            "role": "support",
            "modules": ["dashboard", "support"],
            "is_active": True,
        }

        with (
            patch("backend.routes.admin.auth.settings", mock_settings),
            patch("backend.routes.admin.auth.db") as mock_db,
        ):
            mock_db.admin_staff.find_one = AsyncMock(return_value=staff_row)
            mock_db.admin_staff.update_one = AsyncMock()

            from backend.routes.admin.auth import LoginRequest, admin_login

            body = LoginRequest(email="legacy@spinr.ca", password="LegacyPass12345!")
            result = await admin_login(MagicMock(), body)

            assert "token" in result
            # Verify the update included a new bcrypt hash
            update_call = mock_db.admin_staff.update_one.call_args
            update_payload = update_call[0][1]["$set"]
            assert "password_hash" in update_payload
            assert update_payload["password_hash"].startswith("$2")

    @pytest.mark.asyncio
    async def test_wrong_password_returns_401(self, mock_settings):
        """Wrong password for both super admin and staff returns 401."""
        with (
            patch("backend.routes.admin.auth.settings", mock_settings),
            patch("backend.routes.admin.auth.db") as mock_db,
        ):
            mock_db.admin_staff.find_one = AsyncMock(return_value=None)

            from backend.routes.admin.auth import LoginRequest, admin_login

            body = LoginRequest(email="wrong@example.com", password="wrong")
            with pytest.raises(HTTPException) as exc_info:
                await admin_login(MagicMock(), body)
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_deactivated_staff_returns_403(self, mock_settings):
        """Deactivated staff member gets 403."""
        from backend.utils.password import hash_password

        staff_row = {
            "id": "staff_3",
            "email": "deactivated@spinr.ca",
            "password_hash": hash_password("ValidPass12345!"),
            "is_active": False,
        }

        with (
            patch("backend.routes.admin.auth.settings", mock_settings),
            patch("backend.routes.admin.auth.db") as mock_db,
        ):
            mock_db.admin_staff.find_one = AsyncMock(return_value=staff_row)

            from backend.routes.admin.auth import LoginRequest, admin_login

            body = LoginRequest(email="deactivated@spinr.ca", password="ValidPass12345!")
            with pytest.raises(HTTPException) as exc_info:
                await admin_login(MagicMock(), body)
            assert exc_info.value.status_code == 403


class TestChangePassword:
    """Tests for POST /api/admin/auth/change-password."""

    @pytest.fixture
    def mock_settings(self):
        mock = MagicMock()
        mock.JWT_SECRET = "test-secret-key-for-testing-only-32chars!!"
        mock.ALGORITHM = "HS256"
        return mock

    @pytest.mark.asyncio
    async def test_success(self, mock_settings):
        """Valid current password + new password >= 12 chars succeeds."""
        from backend.utils.password import hash_password

        staff_row = {
            "id": "staff_1",
            "password_hash": hash_password("OldPassword123!"),
        }

        import jwt

        token = jwt.encode({"user_id": "staff_1"}, mock_settings.JWT_SECRET, algorithm="HS256")

        with (
            patch("backend.routes.admin.auth.settings", mock_settings),
            patch("backend.routes.admin.auth.db") as mock_db,
        ):
            mock_db.admin_staff.find_one = AsyncMock(return_value=staff_row)
            mock_db.admin_staff.update_one = AsyncMock()

            from backend.routes.admin.auth import ChangePasswordRequest, change_password

            body = ChangePasswordRequest(current_password="OldPassword123!", new_password="NewPassword456!")
            result = await change_password(MagicMock(), body, authorization=f"Bearer {token}")

            assert result["success"] is True
            mock_db.admin_staff.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_wrong_current_password(self, mock_settings):
        """Wrong current password returns 400."""
        import jwt

        from backend.utils.password import hash_password

        token = jwt.encode({"user_id": "staff_1"}, mock_settings.JWT_SECRET, algorithm="HS256")
        staff_row = {
            "id": "staff_1",
            "password_hash": hash_password("RealPassword123!"),
        }

        with (
            patch("backend.routes.admin.auth.settings", mock_settings),
            patch("backend.routes.admin.auth.db") as mock_db,
        ):
            mock_db.admin_staff.find_one = AsyncMock(return_value=staff_row)

            from backend.routes.admin.auth import ChangePasswordRequest, change_password

            body = ChangePasswordRequest(current_password="WrongPassword!", new_password="NewPassword456!")
            with pytest.raises(HTTPException) as exc_info:
                await change_password(MagicMock(), body, authorization=f"Bearer {token}")
            assert exc_info.value.status_code == 400
            assert "incorrect" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_new_password_too_short(self, mock_settings):
        """New password < 12 chars returns 400."""
        import jwt

        from backend.utils.password import hash_password

        token = jwt.encode({"user_id": "staff_1"}, mock_settings.JWT_SECRET, algorithm="HS256")
        staff_row = {
            "id": "staff_1",
            "password_hash": hash_password("OldPassword123!"),
        }

        with (
            patch("backend.routes.admin.auth.settings", mock_settings),
            patch("backend.routes.admin.auth.db") as mock_db,
        ):
            mock_db.admin_staff.find_one = AsyncMock(return_value=staff_row)

            from backend.routes.admin.auth import ChangePasswordRequest, change_password

            body = ChangePasswordRequest(current_password="OldPassword123!", new_password="short")
            with pytest.raises(HTTPException) as exc_info:
                await change_password(MagicMock(), body, authorization=f"Bearer {token}")
            assert exc_info.value.status_code == 400
            assert "12 characters" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_super_admin_rejected(self, mock_settings):
        """Super admin (id=admin-001) can't change password here."""
        import jwt

        token = jwt.encode({"user_id": "admin-001"}, mock_settings.JWT_SECRET, algorithm="HS256")

        with patch("backend.routes.admin.auth.settings", mock_settings):
            from backend.routes.admin.auth import ChangePasswordRequest, change_password

            body = ChangePasswordRequest(current_password="any", new_password="NewPassword456!")
            with pytest.raises(HTTPException) as exc_info:
                await change_password(MagicMock(), body, authorization=f"Bearer {token}")
            assert exc_info.value.status_code == 400
            assert "Super admin" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_unauthenticated(self, mock_settings):
        """No Authorization header returns 401."""
        with patch("backend.routes.admin.auth.settings", mock_settings):
            from backend.routes.admin.auth import ChangePasswordRequest, change_password

            body = ChangePasswordRequest(current_password="any", new_password="NewPassword456!")
            with pytest.raises(HTTPException) as exc_info:
                await change_password(MagicMock(), body, authorization=None)
            assert exc_info.value.status_code == 401
