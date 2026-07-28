"""Route-level tests for POST /api/admin/data-transfer/export's `reason`
field (PIA recommendation R-C, ACTION_ITEMS.md B11, migration 264).
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


_VALID_BODY = {
    "entities": [{"entity_type": "driver", "entity_id": "d1"}],
    "format": "zip",
    "reason": "seeding staging with realistic driver data",
}


class TestExportReasonField:
    def test_missing_reason_is_422(self, test_client, super_admin_override):
        body = {k: v for k, v in _VALID_BODY.items() if k != "reason"}
        resp = test_client.post("/api/admin/data-transfer/export", json=body)
        assert resp.status_code == 422

    def test_too_short_reason_is_422(self, test_client, super_admin_override):
        body = {**_VALID_BODY, "reason": "short"}
        resp = test_client.post("/api/admin/data-transfer/export", json=body)
        assert resp.status_code == 422

    def test_too_long_reason_is_422(self, test_client, super_admin_override):
        body = {**_VALID_BODY, "reason": "x" * 201}
        resp = test_client.post("/api/admin/data-transfer/export", json=body)
        assert resp.status_code == 422

    def test_valid_reason_persisted_on_job_row(self, test_client, super_admin_override):
        mock_insert = AsyncMock()
        with (
            patch("backend.routes.admin.data_transfer_export.db_supabase.insert_one", mock_insert),
            patch("backend.routes.admin.data_transfer_export.BackgroundTasks.add_task"),
        ):
            resp = test_client.post("/api/admin/data-transfer/export", json=_VALID_BODY)
        assert resp.status_code == 202
        table, job_record = mock_insert.await_args.args
        assert table == "data_transfer_export_jobs"
        assert job_record["reason"] == _VALID_BODY["reason"]
