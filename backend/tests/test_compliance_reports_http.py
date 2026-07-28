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
