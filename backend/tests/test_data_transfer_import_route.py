"""HTTP-level (TestClient) tests for routes/admin/data_transfer_import.py —
gap H4 from reports/audits/2026-07-28-data-transfer-module-lifecycle-audit-v1.md.
No test file existed for this route before this one; test_entity_import_service.py
covers the service layer (parse_bundle_zip/build_plan/commit_plan) directly,
never through the actual HTTP endpoints.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.services.data_transfer import entity_import_service

_ADMIN = {"id": "admin-1", "role": "super_admin", "email": "admin@spinr.app"}
_ZIP_FILE = {"bundle_zip": ("bundle.zip", b"not a real zip", "application/zip")}


def _plan(can_commit: bool) -> entity_import_service.ImportPlan:
    errors = [] if can_commit else [entity_import_service.ImportReportItem(entity_id="d1", field="name", message="conflict")]
    return entity_import_service.ImportPlan(entities=[], resolutions={}, warnings=[], errors=errors)


@pytest.fixture
def admin_client(test_client):
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: dict(_ADMIN)
    yield test_client
    app.dependency_overrides.pop(get_admin_user, None)


def test_import_routes_require_admin_auth(test_client):
    v = test_client.post("/api/admin/data-transfer/import/validate", files=_ZIP_FILE)
    c = test_client.post("/api/admin/data-transfer/import/commit", files=_ZIP_FILE)
    assert v.status_code in (401, 403)
    assert c.status_code in (401, 403)


class TestValidate:
    def test_parse_error_returns_422(self, admin_client):
        with patch(
            "backend.services.data_transfer.entity_import_service.parse_bundle_zip",
            side_effect=entity_import_service.BundleParseError("bad zip"),
        ):
            resp = admin_client.post("/api/admin/data-transfer/import/validate", files=_ZIP_FILE)
        assert resp.status_code == 422

    def test_valid_bundle_returns_report_no_writes(self, admin_client):
        with (
            patch("backend.services.data_transfer.entity_import_service.parse_bundle_zip", return_value=[]),
            patch(
                "backend.services.data_transfer.entity_import_service.build_plan",
                AsyncMock(return_value=_plan(can_commit=True)),
            ),
        ):
            resp = admin_client.post("/api/admin/data-transfer/import/validate", files=_ZIP_FILE)
        assert resp.status_code == 200
        body = resp.json()
        assert body["can_commit"] is True
        assert "committed" not in body

    def test_zip_over_size_limit_returns_413(self, admin_client):
        big = b"0" * 200_000_001
        resp = admin_client.post(
            "/api/admin/data-transfer/import/validate",
            files={"bundle_zip": ("bundle.zip", big, "application/zip")},
        )
        assert resp.status_code == 413


class TestCommit:
    def test_uncommittable_plan_returns_committed_false(self, admin_client):
        with (
            patch("backend.services.data_transfer.entity_import_service.parse_bundle_zip", return_value=[]),
            patch(
                "backend.services.data_transfer.entity_import_service.build_plan",
                AsyncMock(return_value=_plan(can_commit=False)),
            ),
        ):
            resp = admin_client.post("/api/admin/data-transfer/import/commit", files=_ZIP_FILE)
        assert resp.status_code == 200
        body = resp.json()
        assert body["can_commit"] is False
        assert body["committed"] is False

    def test_commit_success_logs_and_returns_counts(self, admin_client):
        with (
            patch("backend.services.data_transfer.entity_import_service.parse_bundle_zip", return_value=[]),
            patch(
                "backend.services.data_transfer.entity_import_service.build_plan",
                AsyncMock(return_value=_plan(can_commit=True)),
            ),
            patch(
                "backend.services.data_transfer.entity_import_service.commit_plan",
                AsyncMock(return_value={"created": 1, "updated": 0, "skipped": 0}),
            ),
            patch("backend.routes.admin.data_transfer_import.log_admin_action", AsyncMock(return_value="aud-1")) as log,
        ):
            resp = admin_client.post("/api/admin/data-transfer/import/commit", files=_ZIP_FILE)
        assert resp.status_code == 200
        body = resp.json()
        assert body["committed"] is True
        assert body["created"] == 1
        log.assert_awaited_once()

    def test_commit_failure_returns_502_and_captures_failure(self, admin_client):
        with (
            patch("backend.services.data_transfer.entity_import_service.parse_bundle_zip", return_value=[]),
            patch(
                "backend.services.data_transfer.entity_import_service.build_plan",
                AsyncMock(return_value=_plan(can_commit=True)),
            ),
            patch(
                "backend.services.data_transfer.entity_import_service.commit_plan",
                AsyncMock(side_effect=RuntimeError("db down mid-commit")),
            ),
            patch("backend.services.data_transfer.observability.capture_failure") as capture,
        ):
            resp = admin_client.post("/api/admin/data-transfer/import/commit", files=_ZIP_FILE)
        assert resp.status_code == 502
        capture.assert_called_once()

    def test_update_existing_flag_is_forwarded(self, admin_client):
        with (
            patch("backend.services.data_transfer.entity_import_service.parse_bundle_zip", return_value=[]),
            patch(
                "backend.services.data_transfer.entity_import_service.build_plan",
                AsyncMock(return_value=_plan(can_commit=True)),
            ),
            patch(
                "backend.services.data_transfer.entity_import_service.commit_plan", AsyncMock(return_value={})
            ) as commit,
            patch("backend.routes.admin.data_transfer_import.log_admin_action", AsyncMock(return_value="aud-1")),
        ):
            resp = admin_client.post(
                "/api/admin/data-transfer/import/commit",
                files=_ZIP_FILE,
                data={"update_existing": "true"},
            )
        assert resp.status_code == 200
        commit.assert_awaited_once()
        assert commit.call_args.kwargs.get("update_existing") is True
