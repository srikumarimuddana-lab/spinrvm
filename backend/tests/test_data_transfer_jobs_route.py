"""HTTP-level (TestClient) tests for routes/admin/data_transfer_jobs.py —
gap H4 from reports/audits/2026-07-28-data-transfer-module-lifecycle-audit-v1.md.
No test file existed for this route before this one — the 404/409/410/503/502
branches on the download-link regeneration path were entirely untested.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

_ADMIN = {"id": "admin-1", "role": "super_admin", "email": "admin@spinr.app"}
_JOB_ROW = {
    "id": "job-1",
    "requested_by_admin_id": "admin-1",
    "entity_type": "driver",
    "entity_ids": ["d1", "d2"],
    "doc_type_filter": None,
    "format": "zip",
    "status": "completed",
    "error_message": None,
    "created_at": "2026-07-01T00:00:00Z",
    "completed_at": "2026-07-01T00:05:00Z",
    "expires_at": "2026-07-08T00:05:00Z",
}


@pytest.fixture
def admin_client(test_client):
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: dict(_ADMIN)
    yield test_client
    app.dependency_overrides.pop(get_admin_user, None)


def test_jobs_routes_require_admin_auth(test_client):
    a = test_client.get("/api/admin/data-transfer/jobs")
    b = test_client.get("/api/admin/data-transfer/jobs/job-1")
    c = test_client.get("/api/admin/data-transfer/jobs/job-1/download")
    assert a.status_code in (401, 403)
    assert b.status_code in (401, 403)
    assert c.status_code in (401, 403)


class TestListJobs:
    def test_returns_jobs_with_entity_count(self, admin_client):
        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[_JOB_ROW])):
            resp = admin_client.get("/api/admin/data-transfer/jobs")
        assert resp.status_code == 200
        jobs = resp.json()["jobs"]
        assert jobs[0]["entity_count"] == 2

    def test_empty_result_is_still_200(self, admin_client):
        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])):
            resp = admin_client.get("/api/admin/data-transfer/jobs")
        assert resp.status_code == 200
        assert resp.json() == {"jobs": []}


class TestGetJob:
    def test_404_when_missing(self, admin_client):
        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])):
            resp = admin_client.get("/api/admin/data-transfer/jobs/nope")
        assert resp.status_code == 404

    def test_200_with_entity_count(self, admin_client):
        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[_JOB_ROW])):
            resp = admin_client.get("/api/admin/data-transfer/jobs/job-1")
        assert resp.status_code == 200
        assert resp.json()["entity_count"] == 2


class TestDownloadLink:
    def test_404_when_job_missing(self, admin_client):
        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])):
            resp = admin_client.get("/api/admin/data-transfer/jobs/nope/download")
        assert resp.status_code == 404

    def test_410_when_purged(self, admin_client):
        purged = {**_JOB_ROW, "deleted_at": "2026-07-15T00:00:00Z"}
        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[purged])):
            resp = admin_client.get("/api/admin/data-transfer/jobs/job-1/download")
        assert resp.status_code == 410

    def test_410_when_no_storage_path(self, admin_client):
        no_path = {**_JOB_ROW, "storage_path": None, "deleted_at": None}
        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[no_path])):
            resp = admin_client.get("/api/admin/data-transfer/jobs/job-1/download")
        assert resp.status_code == 410

    def test_409_when_not_completed(self, admin_client):
        pending = {**_JOB_ROW, "status": "pending", "storage_path": "exports/x.zip", "deleted_at": None}
        with patch("backend.db_supabase.get_rows", AsyncMock(return_value=[pending])):
            resp = admin_client.get("/api/admin/data-transfer/jobs/job-1/download")
        assert resp.status_code == 409

    def test_200_regenerates_signed_url(self, admin_client):
        completed = {**_JOB_ROW, "storage_path": "exports/x.zip", "deleted_at": None}
        with (
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[completed])),
            patch("backend.routes.admin.data_transfer_jobs.supabase") as mock_supabase,
        ):
            mock_supabase.storage.from_.return_value.create_signed_url.return_value = {
                "signedURL": "https://example.com/x.zip"
            }
            resp = admin_client.get("/api/admin/data-transfer/jobs/job-1/download")
        assert resp.status_code == 200
        assert resp.json()["download_url"] == "https://example.com/x.zip"

    def test_502_on_storage_error(self, admin_client):
        completed = {**_JOB_ROW, "storage_path": "exports/x.zip", "deleted_at": None}
        with (
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[completed])),
            patch("backend.routes.admin.data_transfer_jobs.supabase") as mock_supabase,
        ):
            mock_supabase.storage.from_.return_value.create_signed_url.side_effect = RuntimeError("storage down")
            resp = admin_client.get("/api/admin/data-transfer/jobs/job-1/download")
        assert resp.status_code == 502

    def test_503_when_storage_client_not_configured(self, admin_client):
        completed = {**_JOB_ROW, "storage_path": "exports/x.zip", "deleted_at": None}
        with (
            patch("backend.db_supabase.get_rows", AsyncMock(return_value=[completed])),
            patch("backend.routes.admin.data_transfer_jobs.supabase", None),
        ):
            resp = admin_client.get("/api/admin/data-transfer/jobs/job-1/download")
        assert resp.status_code == 503
