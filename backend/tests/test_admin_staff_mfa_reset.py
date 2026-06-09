"""POST /admin/staff/{staff_id}/mfa-reset — super-admin MFA reset.

Lost-phone recovery path: a super_admin clears another staff member's
MFA so they can re-enroll. Pins:
  - happy path clears all MFA fields, bumps token_version, revokes
    refresh tokens, and writes an audit row
  - self-reset is rejected (400) — own account goes through the
    password+TOTP disable flow or a backup code
  - unknown staff → 404; MFA-not-enabled → 400
  - the route dependency requires the super_admin role (403 otherwise)

Run:
    pytest backend/tests/test_admin_staff_mfa_reset.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

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
