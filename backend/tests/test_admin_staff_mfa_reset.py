"""POST /admin/staff/{staff_id}/mfa-reset — super-admin MFA reset.

Lost-phone recovery path: a super_admin clears another staff member's
MFA so they can re-enroll. Pins:
  - happy path clears all MFA fields, bumps token_version, revokes
    refresh tokens, and writes an audit row
  - self-reset is rejected (400) — own account goes through the
    password+TOTP disable flow or a backup code
  - unknown staff → 404; MFA-not-enabled → 400
  - the route dependency requires the super_admin role (403 otherwise)

The classes above call `reset_staff_mfa`/`require_role` directly — they
pin the function's own logic but never exercise the route through the
app's actual dependency-injection chain (`require_module("staff")` at
router-include time -> `require_role("super_admin")` at the endpoint,
both layered on `get_admin_user`). `TestMfaResetHttp` below closes that
gap with real `TestClient` calls against `/api/admin/staff/...`, the
same pattern `test_admin_security.py::TestStaffRBAC` uses for the
sibling staff endpoints.

Run:
    pytest backend/tests/test_admin_staff_mfa_reset.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.routes.admin import staff as staff_mod

SUPER = {"id": "admin-super", "role": "super_admin"}
TARGET_ID = "staff-lost-phone"


def _staff_row(**extra) -> dict:
    return {
        "id": TARGET_ID,
        "email": "ops@spinr.ca",
        "role": "operations",
        "mfa_enabled": True,
        "mfa_secret": "SECRET",
        "mfa_backup_codes": [{"hash": "x"}],
        "token_version": 2,
        **extra,
    }


@pytest.mark.anyio
async def test_reset_clears_mfa_revokes_sessions_and_audits():
    update_calls = []
    audit_rows = []

    async def _capture_update(table, match, updates):
        update_calls.append((table, match, updates))

    async def _capture_insert(table, row):
        if table == "audit_logs":
            audit_rows.append(row)

    revoke = AsyncMock()
    with (
        patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[_staff_row()])),
        patch.object(staff_mod.db_supabase, "update_one", AsyncMock(side_effect=_capture_update)),
        patch.object(staff_mod.db_supabase, "insert_one", AsyncMock(side_effect=_capture_insert)),
        patch.object(staff_mod, "revoke_all_for_user", revoke),
    ):
        result = await staff_mod.reset_staff_mfa(TARGET_ID, admin=SUPER)

    assert result == {"success": True}
    (_, match, updates) = update_calls[0]
    assert match == {"id": TARGET_ID}
    assert updates["mfa_enabled"] is False
    assert updates["mfa_secret"] is None
    assert updates["mfa_secret_pending"] is None
    assert updates["mfa_backup_codes"] is None
    assert updates["token_version"] == 3, "existing sessions must be invalidated"
    revoke.assert_awaited_once_with(TARGET_ID)
    assert audit_rows and audit_rows[0]["action"] == "staff_mfa_reset"
    assert audit_rows[0]["entity_id"] == TARGET_ID
    # PIPEDA: audit details carry the masked email only, never the raw one
    assert audit_rows[0]["details"]["email_masked"] != "ops@spinr.ca"


@pytest.mark.anyio
async def test_self_reset_rejected():
    with pytest.raises(HTTPException) as exc_info:
        await staff_mod.reset_staff_mfa(SUPER["id"], admin=SUPER)
    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_unknown_staff_404():
    with patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[])):
        with pytest.raises(HTTPException) as exc_info:
            await staff_mod.reset_staff_mfa(TARGET_ID, admin=SUPER)
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_mfa_not_enabled_400():
    row = _staff_row(mfa_enabled=False, mfa_secret=None, mfa_secret_pending=None)
    with patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[row])):
        with pytest.raises(HTTPException) as exc_info:
            await staff_mod.reset_staff_mfa(TARGET_ID, admin=SUPER)
    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_pending_enrollment_alone_is_resettable():
    """A half-finished enrollment (pending secret, never confirmed) can also
    be cleared so the staff member can start over."""
    row = _staff_row(mfa_enabled=False, mfa_secret=None, mfa_secret_pending="PENDING")
    with (
        patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[row])),
        patch.object(staff_mod.db_supabase, "update_one", AsyncMock()),
        patch.object(staff_mod.db_supabase, "insert_one", AsyncMock()),
        patch.object(staff_mod, "revoke_all_for_user", AsyncMock()),
    ):
        result = await staff_mod.reset_staff_mfa(TARGET_ID, admin=SUPER)
    assert result == {"success": True}


@pytest.mark.anyio
async def test_route_dependency_requires_super_admin():
    dep = staff_mod.require_role("super_admin")
    with pytest.raises(HTTPException) as exc_info:
        await dep(admin={"id": "staff-ops", "role": "operations"})
    assert exc_info.value.status_code == 403


@pytest.mark.anyio
async def test_staff_list_and_get_never_leak_totp_secrets():
    """mfa_secret / pending secret / backup-code hashes are credentials —
    a TOTP secret mints valid codes. list_staff is readable by every staff
    role, so these must be stripped alongside password_hash."""
    from unittest.mock import MagicMock

    row = _staff_row(password_hash="bcrypt$x", mfa_secret_pending="PENDING")
    with (
        patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[dict(row)])),
        patch.object(staff_mod.db_supabase, "count_documents", AsyncMock(return_value=1)),
    ):
        listed = await staff_mod.list_staff(response=MagicMock(headers={}), admin=SUPER, limit=500, offset=0)
    with patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[dict(row)])):
        single = await staff_mod.get_staff(TARGET_ID)

    for payload in (listed[0], single):
        for cred in ("password_hash", "password", "mfa_secret", "mfa_secret_pending", "mfa_backup_codes"):
            assert cred not in payload, f"{cred} must never reach the dashboard"
        assert payload["mfa_enabled"] is True  # the boolean flag is the only MFA field exposed


# ---------------------------------------------------------------------------
# Real HTTP-level access control — through the app's dependency-injection
# chain (require_module("staff") at include-time -> require_role at the
# endpoint), not the function called directly. This is the coverage gap the
# classes above leave open: a route registration mistake, a missing
# module/role decorator, or a broken Depends() chain would not fail any of
# the tests above, since they call `reset_staff_mfa`/`require_role` in
# isolation rather than going through `app.routes`.
# ---------------------------------------------------------------------------


def _override_admin(app, identity: dict):
    from backend.dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: identity
    return get_admin_user


class TestMfaResetHttp:
    def _app_client(self) -> TestClient:
        from backend.server import app

        return TestClient(app)

    def test_super_admin_resets_other_staff_over_http(self):
        from backend.server import app

        dep_key = _override_admin(app, {"id": "admin-super", "role": "super_admin", "modules": ["staff"]})
        try:
            with (
                patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[_staff_row()])),
                patch.object(staff_mod.db_supabase, "update_one", AsyncMock()),
                patch.object(staff_mod.db_supabase, "insert_one", AsyncMock()),
                patch.object(staff_mod, "revoke_all_for_user", AsyncMock()),
                self._app_client() as client,
            ):
                response = client.post(f"/api/admin/staff/{TARGET_ID}/mfa-reset")
        finally:
            app.dependency_overrides.pop(dep_key, None)

        assert response.status_code == 200, response.text
        assert response.json() == {"success": True}

    def test_non_super_admin_rejected_over_http(self):
        """An admin with the `staff` module (passes the router-level module
        gate) but a non-super_admin role must still be rejected by the
        endpoint's own `require_role("super_admin")` dependency."""
        from backend.server import app

        dep_key = _override_admin(app, {"id": "admin-ops", "role": "operations", "modules": ["staff", "dashboard"]})
        try:
            with self._app_client() as client:
                response = client.post(f"/api/admin/staff/{TARGET_ID}/mfa-reset")
        finally:
            app.dependency_overrides.pop(dep_key, None)

        assert response.status_code == 403
        assert "super_admin" in response.json().get("detail", "")

    def test_module_gate_rejects_admin_without_staff_module(self):
        """An admin missing the `staff` module claim entirely is rejected at
        router-include time, before the endpoint's own role check runs."""
        from backend.server import app

        dep_key = _override_admin(app, {"id": "admin-nomod", "role": "operations", "modules": ["dashboard"]})
        try:
            with self._app_client() as client:
                response = client.post(f"/api/admin/staff/{TARGET_ID}/mfa-reset")
        finally:
            app.dependency_overrides.pop(dep_key, None)

        assert response.status_code == 403

    def test_self_reset_rejected_over_http(self):
        from backend.server import app

        dep_key = _override_admin(app, {"id": TARGET_ID, "role": "super_admin", "modules": ["staff"]})
        try:
            with self._app_client() as client:
                response = client.post(f"/api/admin/staff/{TARGET_ID}/mfa-reset")
        finally:
            app.dependency_overrides.pop(dep_key, None)

        assert response.status_code == 400
