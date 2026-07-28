"""Endpoint tests for the admin Data Transfer job-history routes.

The whole Data Transfer router (export/import/search/jobs/SGI-forms) is
gated on require_super_admin at include_router time (routes/admin/__init__.py)
— see docs/change-log/2026-07-28-data-transfer-jobs-super-admin-gate.md for
the original per-endpoint version of this fix, and ACTION_ITEMS.md B11/R-A
for why it moved to a router-level dependency instead: the previous
require_module("bulk_operations") gate was never actually grantable to a
non-super_admin (the flag isn't in AVAILABLE_MODULES/ALL_MODULES), so this
test module's non-super-admin fixture uses a plain "admin" role with no
special modules — there was never a real way to hold "bulk_operations" in
the first place.
"""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def super_admin_override():
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "super_admin"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


@pytest.fixture
def regular_admin_override():
    """A plain, non-super_admin admin — must be rejected by the router-level
    require_super_admin dependency before reaching any handler."""
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_2", "role": "admin", "modules": []}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


_JOB_ROW = {
    "id": "job_1",
    "requested_by_admin_id": "admin_2",
    "entity_type": "driver",
    "entity_ids": ["d1", "d2"],
    "doc_type_filter": None,
    "format": "zip",
    "status": "completed",
    "error_message": None,
    "created_at": "2026-07-28T00:00:00Z",
    "completed_at": "2026-07-28T00:05:00Z",
    "expires_at": "2026-08-04T00:00:00Z",
}


class TestListDataTransferJobs:
    def test_non_super_admin_is_403(self, test_client, regular_admin_override):
        resp = test_client.get("/api/admin/data-transfer/jobs")
        assert resp.status_code == 403
        assert "super_admin" in resp.json()["detail"]

    def test_super_admin_sees_jobs(self, test_client, super_admin_override):
        with patch(
            "backend.routes.admin.data_transfer_jobs.db_supabase.get_rows",
            new=AsyncMock(return_value=[_JOB_ROW]),
        ):
            resp = test_client.get("/api/admin/data-transfer/jobs")
        assert resp.status_code == 200
        jobs = resp.json()["jobs"]
        assert len(jobs) == 1
        assert jobs[0]["entity_count"] == 2


class TestGetDataTransferJob:
    def test_non_super_admin_is_403(self, test_client, regular_admin_override):
        resp = test_client.get("/api/admin/data-transfer/jobs/job_1")
        assert resp.status_code == 403
        assert "super_admin" in resp.json()["detail"]

    def test_super_admin_gets_job(self, test_client, super_admin_override):
        with patch(
            "backend.routes.admin.data_transfer_jobs.db_supabase.get_rows",
            new=AsyncMock(return_value=[_JOB_ROW]),
        ):
            resp = test_client.get("/api/admin/data-transfer/jobs/job_1")
        assert resp.status_code == 200
        assert resp.json()["entity_count"] == 2


class TestRegenerateJobDownload:
    def test_non_super_admin_is_403_before_touching_storage(self, test_client, regular_admin_override):
        """The 403 must fire before any Storage call — a non-super_admin
        should never reach the signed-URL path, not even to have it fail."""
        with patch("backend.routes.admin.data_transfer_jobs.supabase") as mock_supabase:
            resp = test_client.get("/api/admin/data-transfer/jobs/job_1/download")
        assert resp.status_code == 403
        assert "super_admin" in resp.json()["detail"]
        mock_supabase.storage.from_.assert_not_called()

    def test_super_admin_regenerates_link(self, test_client, super_admin_override):
        completed_row = {
            "id": "job_1",
            "status": "completed",
            "storage_path": "driver/job_1.zip",
            "deleted_at": None,
        }
        with (
            patch(
                "backend.routes.admin.data_transfer_jobs.db_supabase.get_rows",
                new=AsyncMock(return_value=[completed_row]),
            ),
            patch("backend.routes.admin.data_transfer_jobs.supabase") as mock_supabase,
            patch(
                "backend.routes.admin.data_transfer_jobs._extract_signed_url",
                return_value="https://example.com/signed",
            ),
        ):
            mock_supabase.storage.from_.return_value.create_signed_url.return_value = {
                "signedURL": "https://example.com/signed"
            }
            resp = test_client.get("/api/admin/data-transfer/jobs/job_1/download")
        assert resp.status_code == 200
        assert resp.json()["download_url"] == "https://example.com/signed"
