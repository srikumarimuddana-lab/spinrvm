"""Endpoint tests for the admin manual document-upload route.

save_upload / db access are mocked so no real storage or Supabase is touched.
"""

from unittest.mock import AsyncMock, patch

import pytest

DRIVER = {"id": "drv-1", "service_area_id": "sa-1", "status": "needs_review", "user_id": "u1"}
SERVICE_AREA = {
    "id": "sa-1",
    "required_documents": [
        {"key": "drivers_license", "label": "Driver's License", "has_expiry": True, "requires_back_side": True}
    ],
}


@pytest.fixture
def super_admin_override():
    """The documents router sits behind require_module("documents")."""
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "super_admin"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


async def _fake_get_rows(table, _filter=None, limit=None, **_kw):
    if table == "drivers":
        return [DRIVER]
    if table == "service_areas":
        return [SERVICE_AREA]
    # driver_documents (remaining pending) / document_requirements
    return []


def _upload(test_client, data):
    return test_client.post(
        "/api/admin/documents/upload",
        files={"file": ("license.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data=data,
    )


def _base_patches(supersede_mock=None, update_mock=None):
    return [
        patch("routes.admin.documents.save_upload", AsyncMock(return_value="https://signed/url")),
        patch(
            "routes.admin.documents._supersede_and_flag_pending_review",
            supersede_mock or AsyncMock(),
        ),
        patch("routes.admin.documents.log_admin_action", AsyncMock(return_value="audit-1")),
        patch("db_supabase.get_rows", AsyncMock(side_effect=_fake_get_rows)),
        patch("db_supabase.insert_one", AsyncMock(return_value=None)),
        patch("db_supabase.update_one", update_mock or AsyncMock(return_value=None)),
        patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
    ]


def test_upload_pending_flags_review(test_client, super_admin_override):
    supersede = AsyncMock()
    with patch("routes.admin.documents.save_upload", AsyncMock(return_value="https://signed/url")), patch(
        "routes.admin.documents._supersede_and_flag_pending_review", supersede
    ), patch("routes.admin.documents.log_admin_action", AsyncMock()), patch(
        "db_supabase.get_rows", AsyncMock(side_effect=_fake_get_rows)
    ), patch("db_supabase.insert_one", AsyncMock()), patch(
        "db_supabase.update_one", AsyncMock()
    ), patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)):
        resp = _upload(
            test_client,
            {"driver_id": "drv-1", "requirement_key": "drivers_license", "side": "front", "status": "pending"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["document_type"] == "Driver's License"
    assert body["requirement_key"] == "drivers_license"
    # Pending upload flags the driver for review.
    assert supersede.await_args.kwargs["flag_review"] is True


def test_upload_approved_mirrors_legacy_expiry(test_client, super_admin_override):
    update = AsyncMock()
    supersede = AsyncMock()
    with patch("routes.admin.documents.save_upload", AsyncMock(return_value="https://signed/url")), patch(
        "routes.admin.documents._supersede_and_flag_pending_review", supersede
    ), patch("routes.admin.documents.log_admin_action", AsyncMock()), patch(
        "db_supabase.get_rows", AsyncMock(side_effect=_fake_get_rows)
    ), patch("db_supabase.insert_one", AsyncMock()), patch(
        "db_supabase.update_one", update
    ), patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)):
        resp = _upload(
            test_client,
            {
                "driver_id": "drv-1",
                "requirement_key": "drivers_license",
                "expiry_date": "2027-01-15",
                "status": "approved",
            },
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"
    # Approved upload does NOT flag review, and mirrors expiry to the legacy col.
    assert supersede.await_args.kwargs["flag_review"] is False
    legacy_updates = [c for c in update.await_args_list if "license_expiry_date" in (c.args[2] if len(c.args) > 2 else {})]
    assert legacy_updates, "expected a drivers.license_expiry_date update"


def test_upload_unknown_requirement_404(test_client, super_admin_override):
    for p in _base_patches():
        p.start()
    try:
        resp = _upload(
            test_client,
            {"driver_id": "drv-1", "requirement_key": "not_a_real_doc", "status": "pending"},
        )
    finally:
        patch.stopall()
    assert resp.status_code == 404, resp.text


def test_upload_driver_not_found_404(test_client, super_admin_override):
    async def _no_driver(table, *_a, **_k):
        return []

    with patch("routes.admin.documents.save_upload", AsyncMock(return_value="u")), patch(
        "db_supabase.get_rows", AsyncMock(side_effect=_no_driver)
    ):
        resp = _upload(test_client, {"driver_id": "ghost", "requirement_key": "drivers_license", "status": "pending"})
    assert resp.status_code == 404, resp.text


def test_upload_bad_status_400(test_client, super_admin_override):
    resp = _upload(test_client, {"driver_id": "drv-1", "requirement_key": "drivers_license", "status": "superseded"})
    assert resp.status_code == 400, resp.text


def test_upload_requires_admin_auth(test_client):
    resp = _upload(test_client, {"driver_id": "drv-1", "requirement_key": "drivers_license", "status": "pending"})
    assert resp.status_code in (401, 403)
