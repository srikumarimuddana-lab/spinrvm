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

import re
from unittest.mock import AsyncMock, patch

import pytest

_ADMIN = {"id": "admin-1", "role": "super_admin", "email": "admin@spinr.app"}

_RIDE_ROW = {
    "id": "r1",
    "ride_completed_at": "2026-07-05T10:00:00Z",
    "tax_breakdown": {"GST": {"amount": 5.0}, "PST": {"amount": 6.0}},
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
    if table == "drivers":
        return [_DRIVER_ROW]
    return []


# ── auth (denied path) ──────────────────────────────────────────────────────


def test_compliance_routes_require_admin_auth(test_client):
    gst = test_client.get("/api/admin/compliance/gst-pst-remittance")
    billing = test_client.get("/api/admin/compliance/insurance-billing-sgi")
    assert gst.status_code in (401, 403)
    assert billing.status_code in (401, 403)


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


def test_gst_pst_remittance_subtitle_is_period_label_and_totals_are_in_a_table_row(admin_client):
    # The subtitle states only the covered period (report_branding.
    # period_label) — GST/PST/HST totals now live in a TOTAL row in the
    # table body instead of being crammed into the subtitle text.
    from backend.utils import report_branding as rb

    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
        patch("backend.routes.admin.compliance.report_branding.new_branded_pdf", wraps=rb.new_branded_pdf) as new_pdf,
    ):
        resp = admin_client.get("/api/admin/compliance/gst-pst-remittance")
    assert resp.status_code == 200
    subtitle_arg = (
        new_pdf.call_args.args[1] if len(new_pdf.call_args.args) > 1 else new_pdf.call_args.kwargs.get("subtitle")
    )
    assert isinstance(subtitle_arg, str)
    assert subtitle_arg.startswith("Period:") and " to " in subtitle_arg


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


def test_gst_pst_remittance_rejects_invalid_date_from(admin_client):
    resp = admin_client.get("/api/admin/compliance/gst-pst-remittance?date_from=not-a-date")
    assert resp.status_code == 422


def test_gst_pst_remittance_rejects_date_from_after_date_to(admin_client):
    resp = admin_client.get("/api/admin/compliance/gst-pst-remittance?date_from=2026-07-20&date_to=2026-07-01")
    assert resp.status_code == 422


def test_gst_pst_remittance_defaults_to_month_to_date(admin_client):
    # No date_from/date_to supplied -> window defaults to the 1st of the
    # current month through today, not an unbounded/all-time query.
    from backend.utils import report_branding as rb

    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
        patch("backend.routes.admin.compliance.report_branding.new_branded_pdf", wraps=rb.new_branded_pdf) as new_pdf,
    ):
        resp = admin_client.get("/api/admin/compliance/gst-pst-remittance")
    assert resp.status_code == 200
    subtitle_arg = (
        new_pdf.call_args.args[1] if len(new_pdf.call_args.args) > 1 else new_pdf.call_args.kwargs.get("subtitle")
    )
    start_str, _, _end_str = subtitle_arg.partition(" to ")
    assert start_str.endswith("-01")  # 1st of the month


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
        patch(
            "routes.admin.compliance.get_app_settings", AsyncMock(return_value={"dual_approval_exports_enabled": False})
        ),
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
        patch(
            "routes.admin.compliance.get_app_settings", AsyncMock(return_value={"dual_approval_exports_enabled": True})
        ),
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
        patch(
            "routes.admin.compliance.get_app_settings", AsyncMock(return_value={"dual_approval_exports_enabled": True})
        ),
        patch("routes.admin.compliance._APPROVAL_GATE_ROW_THRESHOLD", 0),
        patch("routes.admin.compliance.admin_export_approvals.find_approved_grant", AsyncMock(return_value=None)),
        patch("routes.admin.compliance.admin_export_approvals.find_pending_request", AsyncMock(return_value=None)),
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
        patch(
            "routes.admin.compliance.get_app_settings", AsyncMock(return_value={"dual_approval_exports_enabled": True})
        ),
        patch("routes.admin.compliance._APPROVAL_GATE_ROW_THRESHOLD", 0),
        patch("routes.admin.compliance.admin_export_approvals.find_approved_grant", AsyncMock(return_value=None)),
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
        patch(
            "routes.admin.compliance.get_app_settings", AsyncMock(return_value={"dual_approval_exports_enabled": True})
        ),
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


# ── driver-roster (formerly knight-archer-driver-onboarding) ────────────────

_ROSTER_DRIVER_ROW = {
    "id": "d1",
    "name": "Jane Doe",
    "first_name": "Jane",
    "last_name": "Doe",
    "license_number": "D12345",
    "license_class": "5",
    "status": "pending",
    "created_at": "2026-07-01T09:00:00Z",
}


def test_driver_roster_requires_admin_auth(test_client):
    resp = test_client.get("/api/admin/compliance/driver-roster")
    assert resp.status_code in (401, 403)


def test_driver_roster_returns_pdf_and_includes_all_statuses(admin_client):
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=[_ROSTER_DRIVER_ROW])),
        patch("backend.routes.drivers._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")) as log,
    ):
        resp = admin_client.get("/api/admin/compliance/driver-roster")
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")
    assert log.call_args[0][1]["report_type"] == "driver_roster"
    # no status filter passed -> every status is included, not just active
    assert log.call_args[0][1]["params"]["status"] is None


def test_driver_roster_filters_by_status(admin_client):
    captured = {}

    async def get_rows_side(table, filters=None, **kw):
        captured["filters"] = filters
        return [_ROSTER_DRIVER_ROW]

    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side)),
        patch("backend.routes.drivers._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
    ):
        resp = admin_client.get("/api/admin/compliance/driver-roster?status=pending")
    assert resp.status_code == 200
    # `deleted_at: None` compiles to PostgREST `is.null` — account deletion
    # cannot change `drivers.status`, so a status filter alone does not exclude
    # a driver who left.
    assert captured["filters"] == {"status": "pending", "deleted_at": None}


def test_driver_roster_excludes_deleted_accounts_by_default(admin_client):
    captured = {}

    async def get_rows_side(table, filters=None, **kw):
        captured["filters"] = filters
        return [_ROSTER_DRIVER_ROW]

    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side)),
        patch("backend.routes.drivers._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
    ):
        resp = admin_client.get("/api/admin/compliance/driver-roster")
    assert resp.status_code == 200
    assert captured["filters"] == {"deleted_at": None}


def test_driver_roster_include_deleted_drops_the_filter(admin_client):
    captured = {}

    async def get_rows_side(table, filters=None, **kw):
        captured["filters"] = filters
        return [_ROSTER_DRIVER_ROW]

    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side)),
        patch("backend.routes.drivers._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
    ):
        resp = admin_client.get("/api/admin/compliance/driver-roster?include_deleted=true")
    assert resp.status_code == 200
    assert "deleted_at" not in captured["filters"]


@pytest.mark.anyio
async def test_driver_roster_renders_deleted_status_not_the_stale_one():
    """A soft-deleted row keeps its pre-deletion status (usually 'active').
    Rendering that verbatim on an insurer's roster would describe someone who
    left as a working driver."""
    from backend.routes.admin import compliance as _c

    deleted_row = {**_ROSTER_DRIVER_ROW, "status": "active", "deleted_at": "2026-07-30T00:00:00Z"}
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=[deleted_row])),
        patch("backend.routes.drivers._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
    ):
        rows, _ = await _c._driver_roster_rows(None, include_deleted=True)
    assert rows[0]["status"] == "deleted"


def test_driver_roster_503_on_db_failure(admin_client):
    with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=RuntimeError("db down"))):
        resp = admin_client.get("/api/admin/compliance/driver-roster")
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


# ── T4A filer handoff — SIN-free export ─────────────────────────────────────

_T4A_RIDE_ROW = {
    "driver_id": "d1",
    "driver_earnings": 750.00,
    "base_fare": None,
    "distance_fare": None,
    "time_fare": None,
    "tip_amount": None,
}
_T4A_DRIVER_ROW = {
    "id": "d1",
    "name": "Jane Doe",
    "first_name": "Jane",
    "last_name": "Doe",
    "stripe_account_id": "acct_123",
    "stripe_id_number_provided": True,
}
_STRIPE_ADDRESS = {
    "legal_name": "Jane A. Doe",
    "address_line1": "123 Main St",
    "address_line2": None,
    "city": "Regina",
    "province": "SK",
    "postal_code": "S4P 1A1",
    "country": "CA",
}


def _t4a_get_rows_side(table, filters=None, **kw):
    if table == "rides":
        return [_T4A_RIDE_ROW]
    if table == "drivers":
        return [_T4A_DRIVER_ROW]
    return []


def test_t4a_filer_handoff_requires_admin_auth(test_client):
    resp = test_client.get("/api/admin/compliance/t4a-filer-handoff?year=2026")
    assert resp.status_code in (401, 403)


def test_t4a_filer_handoff_requires_super_admin(test_client):
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "a1", "role": "admin", "email": "a@spinr.app"}
    try:
        resp = test_client.get("/api/admin/compliance/t4a-filer-handoff?year=2026")
    finally:
        app.dependency_overrides.pop(get_admin_user, None)
    assert resp.status_code == 403


def test_t4a_filer_handoff_never_includes_sin(admin_client):
    # The whole point of this export: earnings + Stripe-verified legal
    # name/address for the filer, but the SIN itself must never appear —
    # not the real value, not any field even named "sin".
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_t4a_get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
        patch(
            "backend.routes.admin.compliance.get_legal_name_and_address_from_stripe",
            AsyncMock(return_value=dict(_STRIPE_ADDRESS)),
        ),
    ):
        resp = admin_client.get("/api/admin/compliance/t4a-filer-handoff?year=2026&format=csv")
    assert resp.status_code == 200
    body = resp.content.decode("utf-8")
    assert "Jane A. Doe" in body
    assert "123 Main St" in body
    assert "750.00" in body
    # Only readiness metadata may appear: a Yes/No flag and a collection
    # timestamp. Never the number, and never any part of it — not even the
    # last 4, which internal admin views do show but this export must not,
    # because it leaves Spinr for a third-party filer.
    allowed = {"sin_on_file", "sin_collected_at"}
    header = body.splitlines()[0] if body.splitlines() else ""
    sin_columns = {c for c in header.split(",") if "sin" in c.lower()}
    assert sin_columns <= allowed, f"unexpected SIN column(s): {sin_columns - allowed}"
    assert "last4" not in body.lower()
    # And no 9-digit run anywhere in the payload, whatever it is called.
    assert not re.search(r"(?<!\d)\d{9}(?!\d)", body)


def test_t4a_filer_handoff_filters_by_500_threshold(admin_client):
    under_threshold_ride = dict(_T4A_RIDE_ROW, driver_id="d2", driver_earnings=200.00)

    async def get_rows_side(table, filters=None, **kw):
        if table == "rides":
            return [_T4A_RIDE_ROW, under_threshold_ride]
        if table == "drivers":
            return [_T4A_DRIVER_ROW]  # only d1 queried — d2 never crosses the threshold
        return []

    captured_ids = {}

    async def get_rows_capture(table, filters=None, **kw):
        if table == "drivers" and filters:
            captured_ids["ids"] = filters.get("id", {}).get("$in")
        return await get_rows_side(table, filters, **kw)

    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows_capture)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
        patch(
            "backend.routes.admin.compliance.get_legal_name_and_address_from_stripe",
            AsyncMock(return_value=dict(_STRIPE_ADDRESS)),
        ),
    ):
        resp = admin_client.get("/api/admin/compliance/t4a-filer-handoff?year=2026&format=csv")
    assert resp.status_code == 200
    assert captured_ids["ids"] == ["d1"]  # d2 (under $500) never queried


def test_t4a_filer_handoff_503_on_db_failure(admin_client):
    with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=RuntimeError("db down"))):
        resp = admin_client.get("/api/admin/compliance/t4a-filer-handoff?year=2026")
    assert resp.status_code == 503


# ── Insurance billing (SGI / Knight Archer, per-trip per-phase) ─────────────

_PD_PERIOD_2 = {
    "driver_id": "d1",
    "period": 2,
    "ride_id": "r1",
    "distance_km": 1.5,
    "started_at": "2026-07-05T09:00:00Z",
}
_PD_PERIOD_3 = {
    "driver_id": "d1",
    "period": 3,
    "ride_id": "r1",
    "distance_km": 12.5,
    "started_at": "2026-07-05T09:10:00Z",
}
_PD_DRIVER = {"id": "d1", "name": "Jane Doe", "first_name": "Jane", "last_name": "Doe"}


def _period_distance_get_rows_side(table, filters=None, **kw):
    if table == "driver_period_distances":
        return [_PD_PERIOD_2, _PD_PERIOD_3]
    if table == "drivers":
        return [_PD_DRIVER]
    return []


def test_insurance_billing_sgi_requires_admin_auth(test_client):
    resp = test_client.get("/api/admin/compliance/insurance-billing-sgi")
    assert resp.status_code in (401, 403)


def test_insurance_billing_sgi_uses_fixed_rate_and_shows_each_phase_separately(admin_client):
    # Regression vs. the retired aggregate report: Period 2 and Period 3
    # each show their own GPS-measured leg distance (1.5 km, 12.5 km), not
    # a de-duplicated/summed total — the whole point of the per-trip,
    # per-phase detail rows. Rate is fixed at $0.11/km, no query param.
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_period_distance_get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")) as log,
    ):
        resp = admin_client.get("/api/admin/compliance/insurance-billing-sgi?format=csv")
    assert resp.status_code == 200
    body = resp.content.decode("utf-8-sig")
    assert "1.500" in body
    assert "12.500" in body
    assert "0.11" in body  # rate_per_km column
    assert "Jane Doe" in body
    assert log.call_args[0][1]["report_type"] == "insurance_billing_sgi"


def test_insurance_billing_knight_archer_uses_its_own_fixed_rate(admin_client):
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_period_distance_get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
    ):
        resp = admin_client.get("/api/admin/compliance/insurance-billing-knight-archer?format=csv")
    assert resp.status_code == 200
    body = resp.content.decode("utf-8-sig")
    assert "0.011" in body  # rate_per_km column, Knight Archer's rate


def test_insurance_billing_sgi_503_on_db_failure(admin_client):
    with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=RuntimeError("db down"))):
        resp = admin_client.get("/api/admin/compliance/insurance-billing-sgi")
    assert resp.status_code == 503


# ── Airport trips ─────────────────────────────────────────────────────────

_AIRPORT_RIDE = {
    "id": "r1",
    "driver_id": "d1",
    "service_area_id": "sa1",
    "pickup_address": "Regina International Airport",
    "dropoff_address": "123 Main St, Regina",
    "distance_km": 18.2,
    "ride_completed_at": "2026-07-05T09:30:00Z",
}
_NON_AIRPORT_RIDE = {
    "id": "r2",
    "driver_id": "d1",
    "service_area_id": "sa1",
    "pickup_address": "456 Elm St, Regina",
    "dropoff_address": "789 Oak St, Regina",
    "distance_km": 5.0,
    "ride_completed_at": "2026-07-05T10:00:00Z",
}


_AIRPORT_DRIVER = {
    **_PD_DRIVER,
    "license_plate": "SGI-123",
    "vehicle_make": "Toyota",
    "vehicle_model": "Camry",
    "vehicle_color": "Black",
}


def _airport_get_rows_side(table, filters=None, **kw):
    if table == "rides":
        return [_AIRPORT_RIDE, _NON_AIRPORT_RIDE]
    if table == "drivers":
        return [_AIRPORT_DRIVER]
    if table == "service_areas":
        return [{"id": "sa1", "name": "Regina"}]
    return []


def test_airport_trips_requires_admin_auth(test_client):
    resp = test_client.get("/api/admin/compliance/airport-trips")
    assert resp.status_code in (401, 403)


def test_airport_trips_rider_name_falls_back_when_null_not_none_none(admin_client):
    """Regression: users.first_name/last_name are real columns that are
    frequently NULL (not missing keys) — a plain `.get(k, "")` treated a
    present-but-None value as if the key were absent and never fell back,
    producing the literal string "None None" in the Rider Name column
    instead of a usable value."""
    ride_with_rider = {**_AIRPORT_RIDE, "rider_id": "u1"}

    def get_rows_side(table, filters=None, **kw):
        if table == "rides":
            return [ride_with_rider]
        if table == "drivers":
            return [_AIRPORT_DRIVER]
        if table == "users":
            return [{"id": "u1", "first_name": None, "last_name": None, "phone": "+14375551234"}]
        if table == "service_areas":
            return [{"id": "sa1", "name": "Regina"}]
        return []

    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
    ):
        resp = admin_client.get("/api/admin/compliance/airport-trips?format=csv")

    assert resp.status_code == 200
    body = resp.content.decode("utf-8-sig")
    assert "None None" not in body
    assert "1234" in body  # PIPEDA-safe phone-last-4 fallback, never the full number
    assert "+14375551234" not in body


def test_airport_trips_filters_out_non_airport_rides(admin_client):
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_airport_get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
    ):
        resp = admin_client.get("/api/admin/compliance/airport-trips?format=csv")
    assert resp.status_code == 200
    body = resp.content.decode("utf-8-sig")
    assert "Regina International Airport" in body
    assert "Airport Pickup" in body
    assert "18.2" in body or "18.20" in body
    assert "Black Toyota Camry — SGI-123" in body  # vehicle registration for the authority's invoice
    assert "456 Elm St" not in body  # non-airport ride excluded


def test_airport_trips_has_leading_serial_number_column(admin_client):
    """A report with no row numbers reads as an unfinished data dump —
    every Compliance report gets a leading serial-number column, applied
    once in _render_tabular_report so every report/format picks it up
    uniformly. CSV headers are the raw fieldnames (csv.DictWriter writes
    fieldnames verbatim, no title-casing) — "s_no", not "S No"."""
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_airport_get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
    ):
        resp = admin_client.get("/api/admin/compliance/airport-trips?format=csv")
    assert resp.status_code == 200
    lines = resp.content.decode("utf-8-sig").splitlines()
    header = lines[0].split(",")
    assert header[0] == "s_no"
    first_data_row = lines[1].split(",")
    assert first_data_row[0] == "1"


def test_airport_trips_503_on_db_failure(admin_client):
    with patch("backend.db_supabase.get_rows", AsyncMock(side_effect=RuntimeError("db down"))):
        resp = admin_client.get("/api/admin/compliance/airport-trips")
    assert resp.status_code == 503


# ── service-area scope (page-level Service Area multi-select) ───────────────


def _capturing_get_rows(captured):
    def side(table, filters=None, **kw):
        captured.setdefault(table, []).append(filters)
        if table == "rides":
            return [_RIDE_ROW]
        if table == "drivers":
            return [_DRIVER_ROW]
        return []

    return AsyncMock(side_effect=side)


@pytest.mark.parametrize(
    "path,filtered_table",
    [
        ("gst-pst-remittance", "rides"),
        ("airport-trips", "rides"),
        ("driver-roster", "drivers"),
    ],
)
def test_service_area_ids_query_param_reaches_the_source_query(admin_client, path, filtered_table):
    captured = {}
    with (
        patch("backend.db_supabase.get_rows", _capturing_get_rows(captured)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
    ):
        resp = admin_client.get(f"/api/admin/compliance/{path}?service_area_ids=a2,a1")
    assert resp.status_code == 200
    # Sorted+deduped by _parse_service_area_ids before it reaches the query.
    assert captured[filtered_table][0]["service_area_id"] == {"$in": ["a1", "a2"]}


def test_service_area_ids_is_recorded_on_the_audit_row(admin_client):
    """compliance_export_events is the record of what an admin actually
    pulled — an area-scoped export that logs like an unscoped one would
    misrepresent the export in a later privacy/regulatory audit."""
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")) as log,
    ):
        resp = admin_client.get("/api/admin/compliance/gst-pst-remittance?service_area_ids=a1,a2")
    assert resp.status_code == 200
    assert log.call_args[0][1]["params"]["service_area_ids"] == ["a1", "a2"]


def test_unscoped_export_records_null_service_areas_rather_than_omitting_the_key(admin_client):
    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")) as log,
    ):
        resp = admin_client.get("/api/admin/compliance/gst-pst-remittance")
    assert resp.status_code == 200
    assert log.call_args[0][1]["params"]["service_area_ids"] is None


def test_report_subtitle_states_the_area_scope(admin_client):
    """A filtered regulatory export that does not say it is filtered reads
    as a complete one once it has left the dashboard."""
    from backend.utils import report_branding as rb

    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
        patch("backend.routes.admin.compliance.report_branding.new_branded_pdf", wraps=rb.new_branded_pdf) as new_pdf,
    ):
        resp = admin_client.get("/api/admin/compliance/gst-pst-remittance?service_area_ids=a1")
    assert resp.status_code == 200
    subtitle = new_pdf.call_args.args[1] if len(new_pdf.call_args.args) > 1 else new_pdf.call_args.kwargs["subtitle"]
    assert "Service areas:" in subtitle


def test_unscoped_report_subtitle_says_all_service_areas(admin_client):
    from backend.utils import report_branding as rb

    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
        patch("backend.routes.admin.compliance.report_branding.new_branded_pdf", wraps=rb.new_branded_pdf) as new_pdf,
    ):
        resp = admin_client.get("/api/admin/compliance/gst-pst-remittance")
    assert resp.status_code == 200
    subtitle = new_pdf.call_args.args[1] if len(new_pdf.call_args.args) > 1 else new_pdf.call_args.kwargs["subtitle"]
    assert "All service areas" in subtitle


def test_insurance_billing_scopes_by_driver_home_area_and_labels_it_as_such(admin_client):
    """SGI must not read this as "trips that happened in Regina" — the
    rows are scoped by the driver's home area, so the document says so."""
    from backend.utils import report_branding as rb

    captured = {}

    def side(table, filters=None, **kw):
        captured.setdefault(table, []).append(filters)
        if table == "drivers" and "service_area_id" in (filters or {}):
            return [_DRIVER_ROW]
        if table == "drivers":
            return [_DRIVER_ROW]
        if table == "driver_period_distances":
            return [
                {
                    "driver_id": "d1",
                    "ride_id": "r1",
                    "period": 2,
                    "distance_km": 2.0,
                    "started_at": "2026-07-05T10:00:00Z",
                }
            ]
        return []

    with (
        patch("backend.db_supabase.get_rows", AsyncMock(side_effect=side)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")),
        patch("backend.routes.admin.compliance.report_branding.new_branded_pdf", wraps=rb.new_branded_pdf) as new_pdf,
    ):
        resp = admin_client.get("/api/admin/compliance/insurance-billing-sgi?service_area_ids=a1")
    assert resp.status_code == 200
    assert captured["driver_period_distances"][0]["driver_id"] == {"$in": ["d1"]}
    subtitle = new_pdf.call_args.args[1] if len(new_pdf.call_args.args) > 1 else new_pdf.call_args.kwargs["subtitle"]
    assert any("by driver's home area" in line for line in subtitle)


def test_t4a_filer_handoff_ignores_service_area_ids(admin_client):
    """T4A is deliberately unscoped: a Part XX.1 / T4A return is per-driver
    and Canada-wide, so an area-scoped slice is never a valid filing. A
    stray param must not silently narrow the export."""
    captured = {}
    with (
        patch("backend.db_supabase.get_rows", _capturing_get_rows(captured)),
        patch("backend.db_supabase.insert_one", AsyncMock(return_value="audit-1")) as log,
    ):
        resp = admin_client.get("/api/admin/compliance/t4a-filer-handoff?year=2025&service_area_ids=a1")
    assert resp.status_code == 200
    assert all("service_area_id" not in (f or {}) for f in captured.get("rides", []))
    assert "service_area_ids" not in log.call_args[0][1]["params"]
