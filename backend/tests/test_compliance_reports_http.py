"""HTTP-level (TestClient) tests for routes/admin/compliance.py.

test_compliance_reports.py already covers the module's internal aggregation
helpers (_gst_pst_rows, _insurance_period_rows) directly. This file covers
what that one doesn't: the actual route wiring — auth gating, format
selection, the 503-on-DB-failure path, and the audit-log call — per gap G3
in reports/audits/2026-07-28-compliance-reporting-module-lifecycle-audit-v1.md
(57-61% coverage on compliance.py, below CLAUDE.md's 70% admin-route bar).

Mirrors the admin_client/TestClient pattern from
test_admin_subscription_invoice.py rather than the module-level mocking
style test_compliance_reports.py uses, since these tests exercise the
FastAPI dependency chain (get_admin_user / require_module) and Response
plumbing, not the aggregation logic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

_ADMIN = {"id": "admin-1", "role": "super_admin", "email": "admin@spinr.app"}

_RIDE_ROW = {
    "id": "r1",
    "ride_completed_at": "2026-07-05T10:00:00Z",
    "tax_breakdown": {"GST": {"amount": 5.0}, "PST": {"amount": 6.0}},
}
_PERIOD_ROW = {
    "id": "p1",
    "driver_id": "d1",
    "period": 2,
    "started_at": "2026-07-01T09:00:00Z",
    "ended_at": None,
    "ride_id": "r1",
}
_DRIVER_ROW = {"id": "d1", "name": "Jane Doe", "first_name": "Jane", "last_name": "Doe"}


@pytest.fixture
def admin_client(test_client):
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: dict(_ADMIN)
    yield test_client
    app.dependency_overrides.pop(get_admin_user, None)


def _get_rows_side(table, filters=None, **kw):
    if table == "rides":
        return [_RIDE_ROW]
    if table == "driver_insurance_periods":
        return [_PERIOD_ROW]
    if table == "drivers":
        return [_DRIVER_ROW]
    return []


# ── auth (denied path) ──────────────────────────────────────────────────────


def test_compliance_routes_require_admin_auth(test_client):
    gst = test_client.get("/api/admin/compliance/gst-pst-remittance")
    audit = test_client.get("/api/admin/compliance/insurance-period-audit")
    assert gst.status_code in (401, 403)
    assert audit.status_code in (401, 403)


def test_compliance_routes_denied_without_module_grant(test_client):
    from backend.server import app
    from dependencies import get_admin_user

    # A real (non-super) admin missing the "compliance" module grant — the
    # exact scenario G1 fixed by making the module grantable at all.
    app.dependency_overrides[get_admin_user] = lambda: {"id": "a1", "role": "admin", "modules": ["dashboard"]}
    try:
        resp = test_client.get("/api/admin/compliance/gst-pst-remittance")
    finally:
        app.dependency_overrides.pop(get_admin_user, None)
    assert resp.status_code == 403


# ── gst-pst-remittance ───────────────────────────────────────────────────────


def test_gst_pst_remittance_returns_pdf_by_default(admin_client):
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")) as log,
    ):
        resp = admin_client.get("/api/admin/compliance/gst-pst-remittance")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")
    log.assert_awaited_once()
    assert log.call_args[0][0] == "compliance_export_events"
    assert log.call_args[0][1]["report_type"] == "gst_pst_remittance"


def test_gst_pst_remittance_csv_format(admin_client):
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
    ):
        resp = admin_client.get("/api/admin/compliance/gst-pst-remittance?format=csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert b"gst" in resp.content.lower()


def test_gst_pst_remittance_xlsx_format(admin_client):
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
    ):
        resp = admin_client.get("/api/admin/compliance/gst-pst-remittance?format=xlsx")
    assert resp.status_code == 200
    assert "spreadsheetml" in resp.headers["content-type"]


def test_gst_pst_remittance_docx_format(admin_client):
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
    ):
        resp = admin_client.get("/api/admin/compliance/gst-pst-remittance?format=docx")
    assert resp.status_code == 200
    assert "wordprocessingml" in resp.headers["content-type"]


def test_gst_pst_remittance_rejects_invalid_format(admin_client):
    resp = admin_client.get("/api/admin/compliance/gst-pst-remittance?format=exe")
    assert resp.status_code == 422


def test_gst_pst_remittance_rejects_invalid_date_range(admin_client):
    resp = admin_client.get("/api/admin/compliance/gst-pst-remittance?date_range=lifetime")
    assert resp.status_code == 422


def test_gst_pst_remittance_503_on_db_failure(admin_client):
    with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=RuntimeError("db down"))):
        resp = admin_client.get("/api/admin/compliance/gst-pst-remittance")
    assert resp.status_code == 503


# ── dual-approval gate (ACTION_ITEMS.md B10) ─────────────────────────────────
# The gate is dark-launched behind settings.dual_approval_exports_enabled
# (migration 268, default false); all the tests above run with the flag
# unset (falsy) and confirm the report is generated exactly as before this
# gate existed. These tests exercise the gate itself with the flag mocked on.


def test_gst_pst_remittance_gate_off_ignores_row_count(admin_client):
    """Flag off (the real default): even with row_count above the
    threshold, the report generates normally -- zero behavior change."""
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
        patch("routes.admin.compliance.get_app_settings", AsyncMock(return_value={"dual_approval_exports_enabled": False})),
        patch("routes.admin.compliance._APPROVAL_GATE_ROW_THRESHOLD", 0),
    ):
        resp = admin_client.get("/api/admin/compliance/gst-pst-remittance")
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")


def test_gst_pst_remittance_gate_under_threshold_ignores_flag(admin_client):
    """Flag on, but row_count is under threshold: still generates normally."""
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
        patch("routes.admin.compliance.get_app_settings", AsyncMock(return_value={"dual_approval_exports_enabled": True})),
    ):
        resp = admin_client.get("/api/admin/compliance/gst-pst-remittance")
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")


def test_gst_pst_remittance_gate_blocks_without_approval(admin_client):
    """Flag on, over threshold, no existing approved/pending request:
    returns 202 approval_required and creates a pending request instead of
    generating the file."""
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_side)),
        patch("routes.admin.compliance.get_app_settings", AsyncMock(return_value={"dual_approval_exports_enabled": True})),
        patch("routes.admin.compliance._APPROVAL_GATE_ROW_THRESHOLD", 0),
        patch(
            "routes.admin.compliance.admin_export_approvals.find_approved_grant", AsyncMock(return_value=None)
        ),
        patch(
            "routes.admin.compliance.admin_export_approvals.find_pending_request", AsyncMock(return_value=None)
        ),
        patch(
            "routes.admin.compliance.admin_export_approvals.create_request",
            AsyncMock(return_value={"id": "req-1", "status": "pending"}),
        ) as create,
    ):
        resp = admin_client.get("/api/admin/compliance/gst-pst-remittance")
    assert resp.status_code == 202
    body = resp.json()
    assert body["approval_required"] is True
    assert body["request_id"] == "req-1"
    create.assert_awaited_once()
    assert create.call_args.kwargs["route_key"] == "compliance.gst_pst_remittance"


def test_gst_pst_remittance_gate_reuses_existing_pending_request(admin_client):
    """A second call while a request is still pending must not create a
    duplicate pending row."""
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_side)),
        patch("routes.admin.compliance.get_app_settings", AsyncMock(return_value={"dual_approval_exports_enabled": True})),
        patch("routes.admin.compliance._APPROVAL_GATE_ROW_THRESHOLD", 0),
        patch(
            "routes.admin.compliance.admin_export_approvals.find_approved_grant", AsyncMock(return_value=None)
        ),
        patch(
            "routes.admin.compliance.admin_export_approvals.find_pending_request",
            AsyncMock(return_value={"id": "req-existing", "status": "pending"}),
        ),
        patch("routes.admin.compliance.admin_export_approvals.create_request", AsyncMock()) as create,
    ):
        resp = admin_client.get("/api/admin/compliance/gst-pst-remittance")
    assert resp.status_code == 202
    assert resp.json()["request_id"] == "req-existing"
    create.assert_not_awaited()


def test_gst_pst_remittance_gate_consumes_approved_grant_and_proceeds(admin_client):
    """An approved grant matching the exact request params lets the export
    through, and consumes the grant (single-use)."""
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
        patch("routes.admin.compliance.get_app_settings", AsyncMock(return_value={"dual_approval_exports_enabled": True})),
        patch("routes.admin.compliance._APPROVAL_GATE_ROW_THRESHOLD", 0),
        patch(
            "routes.admin.compliance.admin_export_approvals.find_approved_grant",
            AsyncMock(return_value={"id": "req-approved", "status": "approved"}),
        ),
        patch("routes.admin.compliance.admin_export_approvals.consume", AsyncMock()) as consume,
    ):
        resp = admin_client.get("/api/admin/compliance/gst-pst-remittance")
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")
    consume.assert_awaited_once_with("req-approved")


def test_insurance_period_audit_gate_blocks_without_approval(admin_client):
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_side)),
        patch("routes.admin.compliance.get_app_settings", AsyncMock(return_value={"dual_approval_exports_enabled": True})),
        patch("routes.admin.compliance._APPROVAL_GATE_ROW_THRESHOLD", 0),
        patch(
            "routes.admin.compliance.admin_export_approvals.find_approved_grant", AsyncMock(return_value=None)
        ),
        patch(
            "routes.admin.compliance.admin_export_approvals.find_pending_request", AsyncMock(return_value=None)
        ),
        patch(
            "routes.admin.compliance.admin_export_approvals.create_request",
            AsyncMock(return_value={"id": "req-2", "status": "pending"}),
        ) as create,
    ):
        resp = admin_client.get("/api/admin/compliance/insurance-period-audit")
    assert resp.status_code == 202
    assert resp.json()["request_id"] == "req-2"
    assert create.call_args.kwargs["route_key"] == "compliance.insurance_period_audit"


# ── insurance-period-audit ──────────────────────────────────────────────────


def test_insurance_period_audit_returns_pdf_by_default(admin_client):
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")) as log,
    ):
        resp = admin_client.get("/api/admin/compliance/insurance-period-audit")
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")
    assert log.call_args[0][1]["report_type"] == "insurance_period_audit"


def test_insurance_period_audit_filters_by_driver_id(admin_client):
    captured = {}

    async def get_rows_side(table, filters=None, **kw):
        if table == "driver_insurance_periods":
            captured.update(filters or {})
            return [_PERIOD_ROW]
        if table == "drivers":
            return [_DRIVER_ROW]
        return []

    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
    ):
        resp = admin_client.get("/api/admin/compliance/insurance-period-audit?driver_id=d1")
    assert resp.status_code == 200
    assert captured.get("driver_id") == "d1"


def test_insurance_period_audit_503_on_db_failure(admin_client):
    with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=RuntimeError("db down"))):
        resp = admin_client.get("/api/admin/compliance/insurance-period-audit")
    assert resp.status_code == 503


def test_insurance_period_audit_empty_result_still_200(admin_client):
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
    ):
        resp = admin_client.get("/api/admin/compliance/insurance-period-audit")
    assert resp.status_code == 200


# ── knight-archer-driver-onboarding ─────────────────────────────────────────

_KA_DRIVER_ROW = {
    "id": "d1",
    "name": "Jane Doe",
    "first_name": "Jane",
    "last_name": "Doe",
    "license_number": "D12345",
    "license_class": "5",
    "status": "pending",
    "created_at": "2026-07-01T09:00:00Z",
}


def test_knight_archer_report_requires_admin_auth(test_client):
    resp = test_client.get("/api/admin/compliance/knight-archer-driver-onboarding")
    assert resp.status_code in (401, 403)


def test_knight_archer_report_returns_pdf_and_includes_all_statuses(admin_client):
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=[_KA_DRIVER_ROW])),
        patch("backend.routes.drivers._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")) as log,
    ):
        resp = admin_client.get("/api/admin/compliance/knight-archer-driver-onboarding")
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")
    assert log.call_args[0][1]["report_type"] == "knight_archer_driver_onboarding"
    # no status filter passed -> every status is included, not just active
    assert log.call_args[0][1]["params"]["status"] is None


def test_knight_archer_report_filters_by_status(admin_client):
    captured = {}

    async def get_rows_side(table, filters=None, **kw):
        captured["filters"] = filters
        return [_KA_DRIVER_ROW]

    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side)),
        patch("backend.routes.drivers._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
    ):
        resp = admin_client.get("/api/admin/compliance/knight-archer-driver-onboarding?status=pending")
    assert resp.status_code == 200
    assert captured["filters"] == {"status": "pending"}


def test_knight_archer_report_503_on_db_failure(admin_client):
    with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=RuntimeError("db down"))):
        resp = admin_client.get("/api/admin/compliance/knight-archer-driver-onboarding")
    assert resp.status_code == 503


# ── audit logging is best-effort ────────────────────────────────────────────


def test_report_still_returns_when_audit_log_write_fails(admin_client):
    """CLAUDE.md: audit logging is best-effort — a failure to write
    compliance_export_events must not block the admin from getting their
    report, even though the failure itself must surface loudly (logged, not
    swallowed — covered at the unit level in test_compliance_reports.py)."""
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(side_effect=RuntimeError("audit db down"))),
    ):
        resp = admin_client.get("/api/admin/compliance/gst-pst-remittance")
    assert resp.status_code == 200


# ── email_to (spinr.ca report delivery) ─────────────────────────────────────


def test_email_to_non_spinr_ca_rejected(admin_client):
    resp = admin_client.get("/api/admin/compliance/gst-pst-remittance?email_to=someone@gmail.com")
    assert resp.status_code == 422


def test_email_to_spinr_ca_sends_and_returns_confirmation(admin_client):
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
        patch("routes.admin.compliance.send_transactional_email", AsyncMock(return_value=True)) as send,
    ):
        resp = admin_client.get("/api/admin/compliance/gst-pst-remittance?email_to=ops@spinr.ca")
    assert resp.status_code == 200
    assert resp.json() == {"emailed_to": "ops@spinr.ca"}
    assert send.call_args.kwargs["to"] == "ops@spinr.ca"
    assert send.call_args.kwargs["attachments"][0]["filename"] == "gst_pst_remittance.pdf"


def test_email_to_send_failure_returns_502(admin_client):
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
        patch("routes.admin.compliance.send_transactional_email", AsyncMock(return_value=False)),
    ):
        resp = admin_client.get("/api/admin/compliance/gst-pst-remittance?email_to=ops@spinr.ca")
    assert resp.status_code == 502
