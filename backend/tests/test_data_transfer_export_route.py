"""HTTP-level (TestClient) tests for routes/admin/data_transfer_export.py —
gap H4 from reports/audits/2026-07-28-data-transfer-module-lifecycle-audit-v1.md,
plus route-level tests for the `reason` field (PIA recommendation R-C,
ACTION_ITEMS.md B11, migration 264).

No test file existed for this route at all before this one; it's the
highest-blast-radius endpoint in either module (unredacted PII + document
bytes for up to 100 entities/request), backgrounded, and the entity-count
cap / background-task dispatch / status wiring had zero coverage.

Starlette's TestClient runs BackgroundTasks synchronously as part of the
request lifecycle, so `_run_export_job`'s success/failure paths are
exercised end-to-end through the actual HTTP call, not by importing the
background function directly.

Every request body below includes a valid `reason` (required, 10-200 chars,
per R-C) unless the test is specifically exercising `reason` validation —
without it, every other test would 422 before reaching the behavior under test.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

_ADMIN = {"id": "admin-1", "role": "super_admin", "email": "admin@spinr.app"}

_VALID_REASON = "seeding staging with realistic driver data"

_BUNDLE = {
    "entity_type": "driver",
    "entity_id": "d1",
    "user": {"id": "d1", "full_name": "Jane Doe"},
    "driver_profile": {"id": "d1"},
    "notification_preferences": [],
    "rides": [],
    "documents": [],
    "driver_insurance_periods": [],
}


@pytest.fixture
def admin_client(test_client):
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: dict(_ADMIN)
    yield test_client
    app.dependency_overrides.pop(get_admin_user, None)


def test_export_requires_admin_auth(test_client):
    resp = test_client.post(
        "/api/admin/data-transfer/export",
        json={"entities": [{"entity_type": "driver", "entity_id": "d1"}], "reason": _VALID_REASON},
    )
    assert resp.status_code in (401, 403)


def test_export_rejects_empty_entities(admin_client):
    resp = admin_client.post("/api/admin/data-transfer/export", json={"entities": [], "reason": _VALID_REASON})
    assert resp.status_code == 400


def test_export_rejects_batch_over_cap(admin_client):
    entities = [{"entity_type": "driver", "entity_id": f"d{i}"} for i in range(101)]
    resp = admin_client.post("/api/admin/data-transfer/export", json={"entities": entities, "reason": _VALID_REASON})
    assert resp.status_code == 422


def test_export_rejects_invalid_entity_type(admin_client):
    resp = admin_client.post(
        "/api/admin/data-transfer/export",
        json={"entities": [{"entity_type": "admin", "entity_id": "d1"}], "reason": _VALID_REASON},
    )
    assert resp.status_code == 422


def test_export_rejects_invalid_format(admin_client):
    resp = admin_client.post(
        "/api/admin/data-transfer/export",
        json={"entities": [{"entity_type": "driver", "entity_id": "d1"}], "format": "pdf", "reason": _VALID_REASON},
    )
    assert resp.status_code == 422


def test_export_202_and_creates_pending_job(admin_client):
    captured = {}

    async def insert_one_side(table, doc):
        captured["table"] = table
        captured["doc"] = doc
        return doc["id"]

    with (
        patch("backend.db_supabase.insert_one", AsyncMock(side_effect=insert_one_side)),
        patch(
            "backend.services.data_transfer.entity_export_service.gather_entity_bundles",
            AsyncMock(return_value=[_BUNDLE]),
        ),
        patch("backend.services.data_transfer.bundle_zip_builder.build_export_zip", lambda bundles: b"PK\x03\x04zip"),
        patch("backend.db_supabase.update_one", AsyncMock(return_value=None)),
        patch("backend.routes.admin.data_transfer_export.log_admin_action", AsyncMock(return_value="aud-1")),
        patch("backend.routes.admin.data_transfer_export.supabase") as mock_supabase,
    ):
        mock_supabase.storage.from_.return_value.upload.return_value = None
        mock_supabase.storage.from_.return_value.create_signed_url.return_value = {"signedURL": "https://example.com/x"}
        resp = admin_client.post(
            "/api/admin/data-transfer/export",
            json={"entities": [{"entity_type": "driver", "entity_id": "d1"}], "reason": _VALID_REASON},
        )

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    assert body["requested_count"] == 1
    assert captured["table"] == "data_transfer_export_jobs"
    assert captured["doc"]["status"] == "pending"
    assert captured["doc"]["entity_type"] == "driver"
    assert captured["doc"]["reason"] == _VALID_REASON


def test_export_gate_off_creates_job_normally(admin_client):
    """Flag off (the real default): behaves exactly as before the gate
    existed -- confirms zero behavior change."""
    with (
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="job-1")),
        patch(
            "backend.services.data_transfer.entity_export_service.gather_entity_bundles",
            AsyncMock(return_value=[_BUNDLE]),
        ),
        patch("backend.services.data_transfer.bundle_zip_builder.build_export_zip", lambda bundles: b"PK\x03\x04zip"),
        patch("backend.db_supabase.update_one", AsyncMock(return_value=None)),
        patch("backend.routes.admin.data_transfer_export.log_admin_action", AsyncMock(return_value="aud-1")),
        patch("backend.routes.admin.data_transfer_export.supabase") as mock_supabase,
        patch(
            "backend.routes.admin.data_transfer_export.get_app_settings",
            AsyncMock(return_value={"dual_approval_exports_enabled": False}),
        ),
    ):
        mock_supabase.storage.from_.return_value.upload.return_value = None
        resp = admin_client.post(
            "/api/admin/data-transfer/export",
            json={"entities": [{"entity_type": "driver", "entity_id": "d1"}], "reason": _VALID_REASON},
        )
    assert resp.status_code == 202
    assert resp.json()["status"] == "pending"
    assert "approval_required" not in resp.json()


def test_export_gate_blocks_without_approval(admin_client):
    with (
        patch(
            "backend.routes.admin.data_transfer_export.get_app_settings",
            AsyncMock(return_value={"dual_approval_exports_enabled": True}),
        ),
        patch(
            "backend.routes.admin.data_transfer_export.admin_export_approvals.find_approved_grant",
            AsyncMock(return_value=None),
        ),
        patch(
            "backend.routes.admin.data_transfer_export.admin_export_approvals.find_pending_request",
            AsyncMock(return_value=None),
        ),
        patch(
            "backend.routes.admin.data_transfer_export.admin_export_approvals.create_request",
            AsyncMock(return_value={"id": "req-1", "status": "pending"}),
        ) as create,
        patch("backend.db_supabase.insert_one", AsyncMock()) as insert_job,
    ):
        resp = admin_client.post(
            "/api/admin/data-transfer/export",
            json={"entities": [{"entity_type": "driver", "entity_id": "d1"}], "reason": _VALID_REASON},
        )
    assert resp.status_code == 202
    body = resp.json()
    assert body["approval_required"] is True
    assert body["request_id"] == "req-1"
    create.assert_awaited_once()
    assert create.call_args.kwargs["route_key"] == "data_transfer.export"
    # The gate short-circuits before any job row is written.
    insert_job.assert_not_awaited()


def test_export_gate_consumes_approved_grant_and_proceeds(admin_client):
    with (
        patch(
            "backend.routes.admin.data_transfer_export.get_app_settings",
            AsyncMock(return_value={"dual_approval_exports_enabled": True}),
        ),
        patch(
            "backend.routes.admin.data_transfer_export.admin_export_approvals.find_approved_grant",
            AsyncMock(return_value={"id": "req-approved", "status": "approved"}),
        ),
        patch(
            "backend.routes.admin.data_transfer_export.admin_export_approvals.consume", AsyncMock()
        ) as consume,
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="job-1")),
        patch(
            "backend.services.data_transfer.entity_export_service.gather_entity_bundles",
            AsyncMock(return_value=[_BUNDLE]),
        ),
        patch("backend.services.data_transfer.bundle_zip_builder.build_export_zip", lambda bundles: b"PK\x03\x04zip"),
        patch("backend.db_supabase.update_one", AsyncMock(return_value=None)),
        patch("backend.routes.admin.data_transfer_export.log_admin_action", AsyncMock(return_value="aud-1")),
        patch("backend.routes.admin.data_transfer_export.supabase") as mock_supabase,
    ):
        mock_supabase.storage.from_.return_value.upload.return_value = None
        resp = admin_client.post(
            "/api/admin/data-transfer/export",
            json={"entities": [{"entity_type": "driver", "entity_id": "d1"}], "reason": _VALID_REASON},
        )
    assert resp.status_code == 202
    assert resp.json()["status"] == "pending"
    assert "approval_required" not in resp.json()
    consume.assert_awaited_once_with("req-approved")


def test_export_job_row_write_failure_returns_503(admin_client):
    with patch("backend.db_supabase.insert_one", AsyncMock(side_effect=RuntimeError("db down"))):
        resp = admin_client.post(
            "/api/admin/data-transfer/export",
            json={"entities": [{"entity_type": "driver", "entity_id": "d1"}], "reason": _VALID_REASON},
        )
    assert resp.status_code == 503


def test_export_background_failure_marks_job_failed_not_500(admin_client):
    """No entities resolve -> _run_export_job's own RuntimeError path.
    The HTTP response is still 202 (job accepted); failure surfaces only on
    the job row, since there's no request left to fail by the time the
    background task runs."""
    update_calls = []

    async def update_one_side(table, filters, patch_doc):
        update_calls.append((table, filters, patch_doc))

    with (
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="job-1")),
        patch("backend.services.data_transfer.entity_export_service.gather_entity_bundles", AsyncMock(return_value=[])),
        patch("backend.db_supabase.update_one", AsyncMock(side_effect=update_one_side)),
        patch("backend.services.data_transfer.observability.capture_failure") as capture,
    ):
        resp = admin_client.post(
            "/api/admin/data-transfer/export",
            json={"entities": [{"entity_type": "driver", "entity_id": "d1"}], "reason": _VALID_REASON},
        )

    assert resp.status_code == 202
    assert update_calls, "expected the job row to be updated to failed status"
    table, filters, patch_doc = update_calls[-1]
    assert table == "data_transfer_export_jobs"
    assert patch_doc["status"] == "failed"
    assert "error_message" in patch_doc
    capture.assert_called_once()


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
