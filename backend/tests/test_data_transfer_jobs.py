"""Endpoint tests for the admin Data Transfer job-history routes.

These three endpoints (list, single-job lookup, download-link regeneration)
were tightened to require super_admin — the router-level gate is only the
"bulk_operations" module flag, which is broader than super_admin and was
previously letting any bulk_operations admin see (and, via the download
endpoint, actually pull) other admins' PII export bundles. See
docs/change-log/2026-07-28-data-transfer-jobs-super-admin-gate.md.
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
def bulk_operations_admin_override():
    """A non-super_admin who holds the router-level "bulk_operations" module
    flag — passes the router gate but must still be rejected by the
    endpoint-level _require_super_admin check."""
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {
        "id": "admin_2",
        "role": "admin",
        "modules": ["bulk_operations"],
    }
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
    def test_non_super_admin_is_403(self, test_client, bulk_operations_admin_override):
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
    def test_non_super_admin_is_403(self, test_client, bulk_operations_admin_override):
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
    def test_non_super_admin_is_403_before_touching_storage(self, test_client, bulk_operations_admin_override):
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
