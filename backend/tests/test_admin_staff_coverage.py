"""Coverage tests for backend/routes/admin/staff.py.

Complements test_admin_staff_mfa_reset.py (MFA-reset path) and
test_admin_security.py (RBAC gates via TestClient). This file exercises
the staff account CRUD endpoints directly against mocked db_supabase,
focusing on the admin-access-control-relevant branches:

  - create_staff: password validation, duplicate email, role presets vs
    custom modules, audit logging, password hash never returned
  - update_staff: super_admin promotion re-auth, last-super_admin
    demotion guard, is_active=False token_version bump + session revoke,
    role-preset module snap, audit logging
  - delete_staff: self-delete block, session revoke before delete, audit
  - list_staff / get_staff: credential stripping
  - list_modules: static payload

TEST-ONLY change; no application code modified.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, Request

from backend.routes.admin import staff as staff_mod

_STRONG_TEST_PW = "Str0ng-Enough-" + "Passw0rd!"  # synthetic, not a real credential

SUPER = {"id": "admin-super", "role": "super_admin"}
OPS = {"id": "admin-ops", "role": "operations"}


def _staff_row(**extra) -> dict:
    return {
        "id": "staff-1",
        "email": "person@spinr.ca",
        "password_hash": "hashed",
        "first_name": "Jane",
        "last_name": "Doe",
        "role": "operations",
        "modules": ["dashboard"],
        "is_active": True,
        "token_version": 0,
        **extra,
    }


def _fake_request() -> Request:
    scope = {
        "type": "http",
        "method": "DELETE",
        "path": "/api/admin/staff/staff-1",
        "headers": [],
        "client": ("testclient", 123),
        "query_string": b"",
        "app": None,
    }
    return Request(scope)


# ---------------------------------------------------------------------------
# list_staff / get_staff — credential stripping
# ---------------------------------------------------------------------------


class TestListAndGetStaff:
    @pytest.mark.asyncio
    async def test_list_staff_strips_credentials_and_sets_headers(self):
        rows = [_staff_row(mfa_secret="topsecret", mfa_backup_codes=[{"hash": "x"}])]
        response = AsyncMock()
        response.headers = {}
        with (
            patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=rows)),
            patch.object(staff_mod.db_supabase, "count_documents", AsyncMock(return_value=1)),
        ):
            result = await staff_mod.list_staff(response, admin=OPS, limit=500, offset=0)

        assert result[0].get("password_hash") is None
        assert result[0].get("mfa_secret") is None
        assert result[0].get("mfa_backup_codes") is None
        assert response.headers["X-Total-Count"] == "1"
        assert response.headers["X-Limit"] == "500"

    @pytest.mark.asyncio
    async def test_get_staff_strips_credentials(self):
        with patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[_staff_row()])):
            result = await staff_mod.get_staff("staff-1")
        assert result["id"] == "staff-1"
        assert "password_hash" not in result

    @pytest.mark.asyncio
    async def test_get_staff_404(self):
        with patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await staff_mod.get_staff("missing")
        assert exc.value.status_code == 404

    def test_list_modules_returns_static_payload(self):
        # list_modules is a plain async function with no deps; call via asyncio.
        import asyncio

        result = asyncio.run(staff_mod.list_modules())
        assert "staff" in result["modules"]
        assert set(result["role_presets"].keys()) == {
            "super_admin",
            "operations",
            "support",
            "finance",
        }


# ---------------------------------------------------------------------------
# create_staff
# ---------------------------------------------------------------------------


class TestCreateStaff:
    @pytest.mark.asyncio
    async def test_create_requires_password(self):
        req = staff_mod.StaffCreateRequest(email="a@spinr.ca", password="", first_name="A", last_name="B")
        with pytest.raises(HTTPException) as exc:
            await staff_mod.create_staff(req, admin=SUPER)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_create_rejects_weak_password(self):
        req = staff_mod.StaffCreateRequest(email="a@spinr.ca", password="short", first_name="A", last_name="B")
        with pytest.raises(HTTPException):
            await staff_mod.create_staff(req, admin=SUPER)

    @pytest.mark.asyncio
    async def test_create_rejects_duplicate_email(self):
        req = staff_mod.StaffCreateRequest(
            email="dupe@spinr.ca",
            password=_STRONG_TEST_PW,
            first_name="A",
            last_name="B",
        )
        with patch.object(
            staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[_staff_row(email="dupe@spinr.ca")])
        ):
            with pytest.raises(HTTPException) as exc:
                await staff_mod.create_staff(req, admin=SUPER)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_create_applies_role_preset_modules(self):
        req = staff_mod.StaffCreateRequest(
            email="ops@spinr.ca",
            password=_STRONG_TEST_PW,
            first_name="A",
            last_name="B",
            role="operations",
        )
        inserted = []

        async def _capture(table, row):
            if table == "admin_staff":
                inserted.append(row)

        with (
            patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[])),
            patch.object(staff_mod.db_supabase, "insert_one", AsyncMock(side_effect=_capture)),
        ):
            result = await staff_mod.create_staff(req, admin=SUPER)

        assert result["modules"] == staff_mod.ROLE_PRESETS["operations"]
        assert "password_hash" not in result
        assert inserted[0]["modules"] == staff_mod.ROLE_PRESETS["operations"]

    @pytest.mark.asyncio
    async def test_create_custom_role_filters_unknown_modules(self):
        req = staff_mod.StaffCreateRequest(
            email="custom@spinr.ca",
            password=_STRONG_TEST_PW,
            first_name="A",
            last_name="B",
            role="custom",
            modules=["dashboard", "not_a_real_module"],
        )
        with (
            patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[])),
            patch.object(staff_mod.db_supabase, "insert_one", AsyncMock(return_value=None)),
        ):
            result = await staff_mod.create_staff(req, admin=SUPER)
        assert result["modules"] == ["dashboard"]

    @pytest.mark.asyncio
    async def test_create_custom_role_no_modules_defaults_to_dashboard(self):
        req = staff_mod.StaffCreateRequest(
            email="nomod@spinr.ca",
            password=_STRONG_TEST_PW,
            first_name="A",
            last_name="B",
            role="custom",
        )
        with (
            patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[])),
            patch.object(staff_mod.db_supabase, "insert_one", AsyncMock(return_value=None)),
        ):
            result = await staff_mod.create_staff(req, admin=SUPER)
        assert result["modules"] == ["dashboard"]

    @pytest.mark.asyncio
    async def test_create_writes_audit_log_with_masked_email(self):
        req = staff_mod.StaffCreateRequest(
            email="audited@spinr.ca",
            password=_STRONG_TEST_PW,
            first_name="A",
            last_name="B",
        )
        audit_rows = []

        async def _capture(table, row):
            if table == "audit_logs":
                audit_rows.append(row)

        with (
            patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[])),
            patch.object(staff_mod.db_supabase, "insert_one", AsyncMock(side_effect=_capture)),
        ):
            await staff_mod.create_staff(req, admin=SUPER)

        assert audit_rows and audit_rows[0]["action"] == "staff_created"
        assert audit_rows[0]["details"]["email_masked"] != "audited@spinr.ca"


# ---------------------------------------------------------------------------
# update_staff
# ---------------------------------------------------------------------------


class TestUpdateStaff:
    @pytest.mark.asyncio
    async def test_non_super_admin_rejected(self):
        req = staff_mod.StaffUpdateRequest(first_name="X")
        with pytest.raises(HTTPException) as exc:
            await staff_mod.update_staff("staff-1", req, admin=OPS)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_update_404_unknown_staff(self):
        req = staff_mod.StaffUpdateRequest(first_name="X")
        with patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await staff_mod.update_staff("missing", req, admin=SUPER)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_promotion_to_super_admin_requires_password_confirmation(self):
        req = staff_mod.StaffUpdateRequest(role="super_admin")
        with patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[_staff_row(role="operations")])):
            with pytest.raises(HTTPException) as exc:
                await staff_mod.update_staff("staff-1", req, admin=SUPER)
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_promotion_wrong_password_rejected(self):
        req = staff_mod.StaffUpdateRequest(role="super_admin", password_confirmation="wrong")

        async def _get_rows(table, match=None, **kw):
            if match and match.get("id") == "staff-1":
                return [_staff_row(role="operations")]
            if match and match.get("id") == "admin-super":
                return [_staff_row(id="admin-super", password_hash="realhash")]
            return []

        with (
            patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)),
            patch.object(staff_mod, "verify_password", return_value=(False, None)),
        ):
            with pytest.raises(HTTPException) as exc:
                await staff_mod.update_staff("staff-1", req, admin=SUPER)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_promotion_correct_password_succeeds(self):
        req = staff_mod.StaffUpdateRequest(role="super_admin", password_confirmation="correct")

        async def _get_rows(table, match=None, **kw):
            if match and match.get("id") == "staff-1":
                return [_staff_row(role="operations")]
            if match and match.get("id") == "admin-super":
                return [_staff_row(id="admin-super", password_hash="realhash")]
            return []

        with (
            patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)),
            patch.object(staff_mod.db_supabase, "update_one", AsyncMock()),
            patch.object(staff_mod.db_supabase, "insert_one", AsyncMock()),
            patch.object(staff_mod, "verify_password", return_value=(True, None)),
        ):
            result = await staff_mod.update_staff("staff-1", req, admin=SUPER)
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_promotion_skips_password_check_for_admin_001(self):
        """Legacy bootstrap admin (id == 'admin-001') bypasses the re-auth lookup."""
        req = staff_mod.StaffUpdateRequest(role="super_admin", password_confirmation="anything")
        actor = {"id": "admin-001", "role": "super_admin"}

        with (
            patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[_staff_row(role="operations")])),
            patch.object(staff_mod.db_supabase, "update_one", AsyncMock()),
            patch.object(staff_mod.db_supabase, "insert_one", AsyncMock()),
        ):
            result = await staff_mod.update_staff("staff-1", req, admin=actor)
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_cannot_demote_last_super_admin(self):
        req = staff_mod.StaffUpdateRequest(role="operations")
        with (
            patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[_staff_row(role="super_admin")])),
            patch.object(staff_mod.db_supabase, "count_documents", AsyncMock(return_value=1)),
        ):
            with pytest.raises(HTTPException) as exc:
                await staff_mod.update_staff("staff-1", req, admin=SUPER)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_demote_super_admin_allowed_when_others_remain(self):
        req = staff_mod.StaffUpdateRequest(role="operations")
        with (
            patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[_staff_row(role="super_admin")])),
            patch.object(staff_mod.db_supabase, "count_documents", AsyncMock(return_value=2)),
            patch.object(staff_mod.db_supabase, "update_one", AsyncMock()),
            patch.object(staff_mod.db_supabase, "insert_one", AsyncMock()),
        ):
            result = await staff_mod.update_staff("staff-1", req, admin=SUPER)
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_deactivation_bumps_token_version_and_revokes_sessions(self):
        req = staff_mod.StaffUpdateRequest(is_active=False)
        revoke = AsyncMock()
        updates_seen = {}

        async def _capture_update(table, match, updates):
            updates_seen.update(updates)

        with (
            patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[_staff_row(token_version=3)])),
            patch.object(staff_mod.db_supabase, "update_one", AsyncMock(side_effect=_capture_update)),
            patch.object(staff_mod.db_supabase, "insert_one", AsyncMock()),
            patch.object(staff_mod, "revoke_all_for_user", revoke),
        ):
            result = await staff_mod.update_staff("staff-1", req, admin=SUPER)

        assert result == {"success": True}
        assert updates_seen["is_active"] is False
        assert updates_seen["token_version"] == 4
        revoke.assert_awaited_once_with("staff-1")

    @pytest.mark.asyncio
    async def test_role_preset_snaps_modules(self):
        req = staff_mod.StaffUpdateRequest(role="finance")
        updates_seen = {}

        async def _capture_update(table, match, updates):
            updates_seen.update(updates)

        with (
            patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[_staff_row(role="operations")])),
            patch.object(staff_mod.db_supabase, "update_one", AsyncMock(side_effect=_capture_update)),
            patch.object(staff_mod.db_supabase, "insert_one", AsyncMock()),
        ):
            await staff_mod.update_staff("staff-1", req, admin=SUPER)

        assert updates_seen["modules"] == staff_mod.ROLE_PRESETS["finance"]

    @pytest.mark.asyncio
    async def test_custom_modules_filtered(self):
        req = staff_mod.StaffUpdateRequest(modules=["dashboard", "bogus_module"])
        updates_seen = {}

        async def _capture_update(table, match, updates):
            updates_seen.update(updates)

        with (
            patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[_staff_row()])),
            patch.object(staff_mod.db_supabase, "update_one", AsyncMock(side_effect=_capture_update)),
            patch.object(staff_mod.db_supabase, "insert_one", AsyncMock()),
        ):
            await staff_mod.update_staff("staff-1", req, admin=SUPER)

        assert updates_seen["modules"] == ["dashboard"]

    @pytest.mark.asyncio
    async def test_no_op_update_skips_db_write(self):
        """Empty update body (all None) should not call update_one/insert_one."""
        req = staff_mod.StaffUpdateRequest()
        update_mock = AsyncMock()
        insert_mock = AsyncMock()
        with (
            patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[_staff_row()])),
            patch.object(staff_mod.db_supabase, "update_one", update_mock),
            patch.object(staff_mod.db_supabase, "insert_one", insert_mock),
        ):
            result = await staff_mod.update_staff("staff-1", req, admin=SUPER)
        assert result == {"success": True}
        update_mock.assert_not_awaited()
        insert_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_writes_audit_log(self):
        req = staff_mod.StaffUpdateRequest(first_name="Renamed")
        audit_rows = []

        async def _capture_insert(table, row):
            if table == "audit_logs":
                audit_rows.append(row)

        with (
            patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[_staff_row()])),
            patch.object(staff_mod.db_supabase, "update_one", AsyncMock()),
            patch.object(staff_mod.db_supabase, "insert_one", AsyncMock(side_effect=_capture_insert)),
        ):
            await staff_mod.update_staff("staff-1", req, admin=SUPER)

        assert audit_rows and audit_rows[0]["action"] == "staff_updated"
        assert "updated_at" not in audit_rows[0]["details"]


# ---------------------------------------------------------------------------
# delete_staff
# ---------------------------------------------------------------------------


class TestDeleteStaff:
    @pytest.mark.asyncio
    async def test_cannot_delete_self(self):
        with pytest.raises(HTTPException) as exc:
            await staff_mod.delete_staff.__wrapped__(_fake_request(), "admin-super", admin=SUPER)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_delete_revokes_sessions_before_deleting_and_audits(self):
        revoke = AsyncMock()
        delete_mock = AsyncMock()
        audit_rows = []

        async def _capture_insert(table, row):
            if table == "audit_logs":
                audit_rows.append(row)

        with (
            patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[_staff_row(id="staff-1")])),
            patch.object(staff_mod, "revoke_all_for_user", revoke),
            patch.object(staff_mod.db_supabase, "delete_many", delete_mock),
            patch.object(staff_mod.db_supabase, "insert_one", AsyncMock(side_effect=_capture_insert)),
        ):
            result = await staff_mod.delete_staff.__wrapped__(_fake_request(), "staff-1", admin=SUPER)

        assert result == {"success": True}
        revoke.assert_awaited_once_with("staff-1")
        delete_mock.assert_awaited_once_with("admin_staff", {"id": "staff-1"})
        assert audit_rows and audit_rows[0]["action"] == "staff_deleted"

    @pytest.mark.asyncio
    async def test_delete_unknown_staff_still_succeeds_gracefully(self):
        """s is None when the row doesn't exist; audit details fall back to None."""
        audit_rows = []

        async def _capture_insert(table, row):
            if table == "audit_logs":
                audit_rows.append(row)

        with (
            patch.object(staff_mod.db_supabase, "get_rows", AsyncMock(return_value=[])),
            patch.object(staff_mod, "revoke_all_for_user", AsyncMock()),
            patch.object(staff_mod.db_supabase, "delete_many", AsyncMock()),
            patch.object(staff_mod.db_supabase, "insert_one", AsyncMock(side_effect=_capture_insert)),
        ):
            result = await staff_mod.delete_staff.__wrapped__(_fake_request(), "ghost-id", admin=SUPER)

        assert result == {"success": True}
        assert audit_rows[0]["details"]["email_masked"] is None
        assert audit_rows[0]["details"]["role"] is None


# ---------------------------------------------------------------------------
# require_role dependency factory
# ---------------------------------------------------------------------------


class TestRequireRole:
    @pytest.mark.asyncio
    async def test_require_role_allows_matching_role(self):
        dep = staff_mod.require_role("super_admin")
        result = await dep(admin=SUPER)
        assert result == SUPER

    @pytest.mark.asyncio
    async def test_require_role_rejects_mismatched_role(self):
        dep = staff_mod.require_role("super_admin")
        with pytest.raises(HTTPException) as exc:
            await dep(admin=OPS)
        assert exc.value.status_code == 403
        assert "super_admin" in exc.value.detail
