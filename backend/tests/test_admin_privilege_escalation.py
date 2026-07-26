"""P0: admin authorization must not be grantable via the users.role column.

Before the fix, get_admin_user did `role in {admin,...}` against the raw users
row returned by get_current_user. Any account whose users.role was set to an
admin string (make_admin.py, an ops data-fix, a migration bug) could log in
through the ordinary rider/driver flow and pass every admin endpoint with a
normal 15-min mobile token — no MFA, no admin_staff row. The gate now requires
the _admin_verified marker that ONLY _verify_admin_payload sets.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException


@pytest.mark.anyio
class TestGetAdminUserRequiresVerifiedMarker:
    async def test_role_admin_without_marker_is_rejected(self):
        """The exploit: a users-row-derived dict with role=super_admin but no
        _admin_verified marker must NOT pass."""
        from backend.dependencies import get_admin_user

        rider_with_admin_role = {
            "id": "u1",
            "role": "super_admin",  # attacker-controlled DB column value
            "email": "attacker@example.com",
            # no _admin_verified — this dict came from get_user_by_id
        }
        with pytest.raises(HTTPException) as exc:
            await get_admin_user(current_user=rider_with_admin_role)
        assert exc.value.status_code == 403

    async def test_every_admin_role_string_rejected_without_marker(self):
        from backend.dependencies import get_admin_user

        for role in ("admin", "super_admin", "operations", "support", "finance", "custom"):
            with pytest.raises(HTTPException) as exc:
                await get_admin_user(current_user={"id": "u1", "role": role})
            assert exc.value.status_code == 403, f"role={role} should be rejected"

    async def test_verified_marker_passes(self):
        from backend.dependencies import get_admin_user

        admin = {"id": "a1", "role": "admin", "_admin_verified": True}
        result = await get_admin_user(current_user=admin)
        assert result is admin

    async def test_marker_without_role_still_passes(self):
        """The marker is authoritative — _verify_admin_payload already checked
        role before setting it, so get_admin_user doesn't re-check the string."""
        from backend.dependencies import get_admin_user

        admin = {"id": "a1", "_admin_verified": True}
        assert await get_admin_user(current_user=admin) is admin


@pytest.mark.anyio
class TestVerifyAdminPayloadStampsMarker:
    async def test_marker_is_set_on_success(self):
        """A fully-verified admin token gets _admin_verified=True. Uses the
        break-glass path to avoid mocking the admin_staff DB lookup."""
        from unittest.mock import patch

        from backend import dependencies as deps

        payload = {
            "aud": deps.JWT_AUD_ADMIN,
            "role": "super_admin",
            "email": "root@spinr.ca",
            "user_id": "break-glass",
            "jti": "jti-1",
            "break_glass": True,
        }

        async def fake_redis_get(key):
            # Not on the revocation denylist; present on the break-glass allowlist.
            return "1" if key.startswith("admin:breakglass:") else None

        with patch("backend.dependencies.redis_get", fake_redis_get):
            result = await deps._verify_admin_payload(payload)
        assert result is not None
        assert result["_admin_verified"] is True

    async def test_non_admin_payload_returns_none(self):
        from backend import dependencies as deps

        payload = {"aud": deps.JWT_AUD_MOBILE, "role": "rider", "user_id": "u1"}
        assert await deps._verify_admin_payload(payload) is None
