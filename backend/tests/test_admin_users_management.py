"""Endpoint coverage for backend/routes/admin/users.py.

test_admin_users_search.py already covers the id-match search regression.
This file adds account-action endpoints (suspend/ban/reactivate, role-flag
changes) plus the detail/export/DSAR read endpoints.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

MOD = "backend.routes.admin.users"

USER = {
    "id": "user-1",
    "first_name": "Jane",
    "last_name": "Doe",
    "email": "jane@example.com",
    "phone": "+13065550100",
    "role": "rider",
    "is_rider": True,
    "is_driver": False,
    "status": "active",
    "stripe_customer_id": None,
}


@pytest.fixture
def admin_override():
    """users_router sits behind require_module("users")."""
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "super_admin"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


def _patches(**overrides):
    targets = {
        "get_user_by_id": AsyncMock(return_value=dict(USER)),
        "insert_one": AsyncMock(return_value={"id": "audit-1"}),
        "update_one": AsyncMock(return_value=None),
    }
    targets.update(overrides)
    return [patch(f"{MOD}.db_supabase.{name}", fn) for name, fn in targets.items()]


def _apply(patches):
    for p in patches:
        p.start()
    return patches


def _stop(patches):
    for p in patches:
        p.stop()


# ---------- PUT /users/{id}/status ----------


def test_suspend_requires_reason(test_client, admin_override):
    patches = _apply(_patches())
    try:
        resp = test_client.put("/api/admin/users/user-1/status", json={"status": "suspended"})
    finally:
        _stop(patches)
    assert resp.status_code == 422


def test_suspend_user_happy_path(test_client, admin_override):
    push = AsyncMock()
    patches = _apply(_patches())
    with patch("backend.features.send_push_notification", push):
        try:
            resp = test_client.put(
                "/api/admin/users/user-1/status",
                json={"status": "suspended", "reason": "fraud review", "suspended_until": "2026-08-05T00:00:00Z"},
            )
        finally:
            _stop(patches)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "suspended"
    assert body["status_reason"] == "fraud review"
    assert body["suspended_until"] == "2026-08-05T00:00:00Z"


def test_ban_user_happy_path(test_client, admin_override):
    push = AsyncMock()
    patches = _apply(_patches())
    with patch("backend.features.send_push_notification", push):
        try:
            resp = test_client.put(
                "/api/admin/users/user-1/status",
                json={"status": "banned", "reason": "TOS violation"},
            )
        finally:
            _stop(patches)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "banned"


def test_reactivate_clears_status_reason_and_deletion_flags(test_client, admin_override):
    update_one = AsyncMock(return_value=None)
    suspended_user = {**USER, "status": "suspended", "status_reason": "fraud review"}
    patches = _apply(_patches(get_user_by_id=AsyncMock(return_value=suspended_user), update_one=update_one))
    push = AsyncMock()
    with patch("backend.features.send_push_notification", push):
        try:
            resp = test_client.put(
                "/api/admin/users/user-1/status",
                json={"status": "active"},
            )
        finally:
            _stop(patches)
    assert resp.status_code == 200, resp.text
    _, kwargs = update_one.call_args
    update_payload = update_one.call_args[0][2]
    assert update_payload["status_reason"] is None
    assert update_payload["suspended_until"] is None
    assert update_payload["deletion_requested_at"] is None
    assert update_payload["deletion_scheduled_at"] is None


def test_status_update_user_not_found(test_client, admin_override):
    patches = _apply(_patches(get_user_by_id=AsyncMock(return_value=None)))
    try:
        resp = test_client.put(
            "/api/admin/users/ghost/status",
            json={"status": "active"},
        )
    finally:
        _stop(patches)
    assert resp.status_code == 404


def test_status_update_db_failure_surfaces_503(test_client, admin_override):
    update_one = AsyncMock(side_effect=RuntimeError("db down"))
    patches = _apply(_patches(update_one=update_one))
    try:
        resp = test_client.put(
            "/api/admin/users/user-1/status",
            json={"status": "banned", "reason": "TOS violation"},
        )
    finally:
        _stop(patches)
    assert resp.status_code == 503


def test_status_update_push_failure_does_not_block_response(test_client, admin_override):
    """Push delivery is best-effort — CLAUDE.md says failures here must not
    block the admin action, just log a warning."""
    push = AsyncMock(side_effect=RuntimeError("fcm unreachable"))
    patches = _apply(_patches())
    with patch("backend.features.send_push_notification", push):
        try:
            resp = test_client.put(
                "/api/admin/users/user-1/status",
                json={"status": "banned", "reason": "TOS violation"},
            )
        finally:
            _stop(patches)
    assert resp.status_code == 200


# ---------- PATCH /users/{id}/role ----------


def test_update_flags_requires_at_least_one_field(test_client, admin_override):
    patches = _apply(_patches())
    try:
        resp = test_client.patch("/api/admin/users/user-1/role", json={})
    finally:
        _stop(patches)
    assert resp.status_code == 400


def test_update_flags_sets_is_driver_and_syncs_role(test_client, admin_override):
    update_one = AsyncMock(return_value=None)
    patches = _apply(_patches(update_one=update_one))
    try:
        resp = test_client.patch("/api/admin/users/user-1/role", json={"is_driver": True})
    finally:
        _stop(patches)
    assert resp.status_code == 200, resp.text
    update_payload = update_one.call_args[0][2]
    assert update_payload["is_driver"] is True
    assert update_payload["role"] == "driver"


def test_update_flags_user_not_found(test_client, admin_override):
    patches = _apply(_patches(get_user_by_id=AsyncMock(return_value=None)))
    try:
        resp = test_client.patch("/api/admin/users/ghost/role", json={"is_rider": False})
    finally:
        _stop(patches)
    assert resp.status_code == 404


# ---------- GET /users (list) ----------


def test_list_users_projects_legacy_import_metadata(test_client, admin_override):
    """Additional check, docs/audit/2026-08-19-legacy-migration-data-quality-
    audit.md: the rider list must expose legacy_import_metadata the same way
    the driver list already does (admin_get_drivers projects no restrictive
    columns=), so an admin can recognize a legacy-imported rider profile."""
    row = {**USER, "legacy_import_metadata": {"source": "legacy_mongo_rider_import", "batch": "b1"}}
    get_rows = AsyncMock(return_value=[row])
    with patch(f"{MOD}.db_supabase.get_rows", get_rows):
        resp = test_client.get("/api/admin/users")
    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["legacy_import_metadata"]["source"] == "legacy_mongo_rider_import"
    # Assert the projection itself requested the column, not just that the
    # fake happened to echo it back.
    _, kwargs = get_rows.call_args
    assert "legacy_import_metadata" in kwargs["columns"]


# ---------- GET /users/{id} ----------


def test_get_user_details_not_found(test_client, admin_override):
    patches = _apply(_patches(get_user_by_id=AsyncMock(return_value=None)))
    try:
        resp = test_client.get("/api/admin/users/ghost")
    finally:
        _stop(patches)
    assert resp.status_code == 404


def test_get_user_details_drops_profile_image_and_lists_rides(test_client, admin_override):
    user_with_image = {**USER, "profile_image": "data:image/png;base64,AAAA"}
    get_rows = AsyncMock(return_value=[{"id": "ride-1", "status": "completed"}])
    count_documents = AsyncMock(return_value=3)
    patches = _apply(
        _patches(
            get_user_by_id=AsyncMock(return_value=user_with_image),
            get_rows=get_rows,
            count_documents=count_documents,
        )
    )
    try:
        resp = test_client.get("/api/admin/users/user-1")
    finally:
        _stop(patches)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "profile_image" not in body
    assert body["total_rides"] == 3
    assert body["cards"] == []  # no stripe_customer_id → empty list, no Stripe call
    assert body["cards_error"] is None


# ---------- GET /export/users ----------


def test_export_users_masks_pii_for_non_super_admin(test_client):
    """Non-super-admin export must mask email/phone."""
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "admin", "modules": ["users"]}
    get_rows = AsyncMock(return_value=[dict(USER)])
    insert_one = AsyncMock(return_value={"id": "audit-1"})
    try:
        with (
            patch(f"{MOD}.db_supabase.get_rows", get_rows),
            patch(f"{MOD}.db_supabase.insert_one", insert_one),
        ):
            resp = test_client.get("/api/admin/export/users")
    finally:
        app.dependency_overrides.pop(get_admin_user, None)
    assert resp.status_code == 200, resp.text
    row = resp.json()["users"][0]
    assert row["email"] != USER["email"]
    assert row["email"].endswith("@example.com")
    assert row["phone"] != USER["phone"]


def test_export_users_unmasked_for_super_admin(test_client, admin_override):
    get_rows = AsyncMock(return_value=[dict(USER)])
    insert_one = AsyncMock(return_value={"id": "audit-1"})
    with (
        patch(f"{MOD}.db_supabase.get_rows", get_rows),
        patch(f"{MOD}.db_supabase.insert_one", insert_one),
    ):
        resp = test_client.get("/api/admin/export/users")
    assert resp.status_code == 200, resp.text
    row = resp.json()["users"][0]
    assert row["email"] == USER["email"]
    assert row["phone"] == USER["phone"]


# ---------- DSARs ----------


def test_list_dsars_flags_overdue(test_client, admin_override):
    rows = [
        {"id": "dsar-1", "status": "pending", "response_due_at": "2020-01-01T00:00:00Z"},
        {"id": "dsar-2", "status": "completed", "response_due_at": "2099-01-01T00:00:00Z"},
    ]
    get_rows = AsyncMock(return_value=rows)
    with patch(f"{MOD}.db_supabase.get_rows", get_rows):
        resp = test_client.get("/api/admin/dsars")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dsars"][0]["overdue"] is True
    assert "overdue" not in body["dsars"][1] or body["dsars"][1].get("overdue") is not True


def test_update_dsar_status_invalid_value(test_client, admin_override):
    resp = test_client.patch("/api/admin/dsars/dsar-1/status", params={"status": "bogus"})
    assert resp.status_code == 400


def test_update_dsar_status_calls_get_one_which_does_not_exist_on_db_supabase():
    """BUG (found, not fixed — out of scope per task instructions):
    admin_update_dsar_status (routes/admin/users.py) calls
    `db_supabase.get_one(...)`, but db_supabase only re-exports `find_one`
    (see db_supabase.py's `from .repositories... import find_one`). There is
    no `get_one` attribute anywhere in db_supabase, its repositories, or any
    dynamic __getattr__. Every call to PATCH /api/admin/dsars/{id}/status
    with a valid status value raises AttributeError in production — the DSAR
    status-update admin action is completely broken. This test pins the
    current (broken) behavior so a future fix is visible as an intentional
    diff rather than a silent behavior change."""
    import backend.db_supabase as db_supabase_mod

    assert not hasattr(db_supabase_mod, "get_one"), (
        "db_supabase.get_one now exists — routes/admin/users.py's "
        "admin_update_dsar_status should work; update/remove this pinning test "
        "and add real happy-path/404 coverage for that endpoint."
    )


def test_users_endpoints_require_admin_auth(test_client):
    resp = test_client.get("/api/admin/users/user-1")
    assert resp.status_code in (401, 403)
