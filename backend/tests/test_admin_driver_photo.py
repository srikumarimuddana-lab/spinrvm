"""Endpoint tests for admin driver profile-photo upload.

store_profile_image and DB access are mocked so no real storage/Supabase is hit.
"""

from unittest.mock import AsyncMock, patch

import pytest

DRIVER = {"id": "drv-1", "user_id": "u1", "status": "active"}


@pytest.fixture
def super_admin_override():
    """The drivers router sits behind require_module("drivers")."""
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "super_admin"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


def _upload(test_client, content=b"\x89PNG fake", content_type="image/png"):
    return test_client.post(
        "/api/admin/drivers/drv-1/photo",
        files={"file": ("selfie.png", content, content_type)},
    )


def test_upload_sets_approved_photo(test_client, super_admin_override):
    update = AsyncMock()
    with (
        patch(
            "routes.admin.drivers.store_profile_image", AsyncMock(return_value="https://cdn/profile-photos/u1/x.jpg")
        ),
        patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
        patch("db_supabase.update_one", update),
        patch("routes.admin.drivers.log_admin_action", AsyncMock()),
    ):
        resp = _upload(test_client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["profile_image_status"] == "approved"
    assert body["profile_image"].startswith("https://")
    # Wrote the photo + approved status onto the driver's user row.
    args = update.await_args.args
    assert args[0] == "users" and args[1] == {"id": "u1"}
    assert args[2] == {"profile_image": "https://cdn/profile-photos/u1/x.jpg", "profile_image_status": "approved"}


def test_upload_rejects_non_image(test_client, super_admin_override):
    resp = _upload(test_client, content=b"%PDF-1.4", content_type="application/pdf")
    assert resp.status_code == 400, resp.text


def test_upload_driver_not_found(test_client, super_admin_override):
    with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=None)):
        resp = _upload(test_client)
    assert resp.status_code == 404, resp.text


def test_upload_requires_admin_auth(test_client):
    resp = _upload(test_client)
    assert resp.status_code in (401, 403)
