# backend/tests/test_corporate_statement_pdf_routes.py
"""GET .../billing/statements/{month}/pdf — both audiences (corporate +
admin portal review round 2, "invoicing"):

  - company-portal: routes/corporate_company.py::billing_statement_pdf
  - internal admin: routes/corporate_accounts.py::admin_download_corporate_statement_pdf

Follows the established rider_override/_as_admin (company-portal) and
admin_override (internal-admin, corporate_accounts module grant)
fixtures already proven in test_corporate_company_gap_coverage.py and
test_corporate_wallet_routes.py respectively.
"""

from unittest.mock import AsyncMock, patch

import pytest

_FAKE_USER = {"id": "u_admin", "phone": "+15550001111"}
_COMPANY_ROUTE = "routes.corporate_company."
_ACCOUNTS_ROUTE = "routes.corporate_accounts."

_STATEMENT = {
    "month": "2026-07",
    "from": "2026-07-01T00:00:00",
    "to": "2026-08-01T00:00:00",
    "line_items": [],
    "summary": {"ride_count": 0, "allowance_total": "0.00", "master_total": "0.00", "total": "0.00"},
}


def _company(**extra):
    return {"id": "c1", "name": "Acme Co", **extra}


@pytest.fixture
def rider_override():
    from backend.server import app
    from dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _as_admin(company_id="c1"):
    return patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": company_id, "role": "admin", "id": "m_admin"}]),
    )


# ── Company-portal endpoint ─────────────────────────────────────────────


def test_company_portal_pdf_download(test_client, rider_override):
    with (
        _as_admin(),
        patch(_COMPANY_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=_company())),
        patch(_COMPANY_ROUTE + "build_full_month_statement", AsyncMock(return_value=_STATEMENT)),
        patch(_COMPANY_ROUTE + "generate_corporate_statement_pdf", return_value=b"%PDF-company"),
        patch(_COMPANY_ROUTE + "log_user_action", AsyncMock()) as m_audit,
    ):
        resp = test_client.get("/company/c1/billing/statements/2026-07/pdf")

    assert resp.status_code == 200, resp.text
    assert resp.content == b"%PDF-company"
    assert resp.headers["content-type"] == "application/pdf"
    assert "spinr-corporate-statement-c1" in resp.headers["content-disposition"]
    m_audit.assert_awaited_once()


def test_company_portal_pdf_unknown_company_404(test_client, rider_override):
    with (
        _as_admin(),
        patch(_COMPANY_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=None)),
    ):
        resp = test_client.get("/company/c1/billing/statements/2026-07/pdf")

    assert resp.status_code == 404, resp.text


def test_company_portal_pdf_rejects_non_admin_member(test_client, rider_override):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "member", "id": "m1"}]),
    ):
        resp = test_client.get("/company/c1/billing/statements/2026-07/pdf")

    assert resp.status_code == 403, resp.text


def test_company_portal_pdf_cross_company_rejected(test_client, rider_override):
    """An admin of a different company cannot download c1's invoice."""
    with _as_admin(company_id="some-other-company"):
        resp = test_client.get("/company/c1/billing/statements/2026-07/pdf")

    assert resp.status_code == 403, resp.text


# ── Internal-admin mirror endpoint ──────────────────────────────────────


@pytest.fixture
def admin_module_override():
    """Grant the corporate_accounts module — same posture as
    test_corporate_wallet_routes.py's admin_override fixture."""
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {
        "id": "admin_1",
        "role": "admin",
        "modules": ["corporate_accounts"],
    }
    yield
    app.dependency_overrides.pop(get_admin_user, None)


def test_internal_admin_pdf_download(test_client, admin_module_override):
    with (
        patch(_ACCOUNTS_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=_company())),
        patch(_COMPANY_ROUTE + "build_full_month_statement", AsyncMock(return_value=_STATEMENT)),
        patch(_ACCOUNTS_ROUTE + "generate_corporate_statement_pdf", return_value=b"%PDF-admin"),
        patch(_ACCOUNTS_ROUTE + "log_admin_action", AsyncMock()) as m_audit,
    ):
        resp = test_client.get("/api/admin/corporate-accounts/c1/billing/statements/2026-07/pdf")

    assert resp.status_code == 200, resp.text
    assert resp.content == b"%PDF-admin"
    assert resp.headers["content-type"] == "application/pdf"
    assert "spinr-corporate-statement-c1" in resp.headers["content-disposition"]
    m_audit.assert_awaited_once()


def test_internal_admin_pdf_unknown_company_404(test_client, admin_module_override):
    with patch(_ACCOUNTS_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=None)):
        resp = test_client.get("/api/admin/corporate-accounts/c1/billing/statements/2026-07/pdf")

    assert resp.status_code == 404, resp.text


def test_internal_admin_pdf_requires_corporate_accounts_module(test_client):
    """An admin missing the corporate_accounts module grant is rejected at
    the router-include gate (same require_module pattern proven for the
    sibling wallet endpoints)."""
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_2", "role": "admin", "modules": ["dashboard"]}
    try:
        resp = test_client.get("/api/admin/corporate-accounts/c1/billing/statements/2026-07/pdf")
    finally:
        app.dependency_overrides.pop(get_admin_user, None)

    assert resp.status_code == 403, resp.text


def test_both_endpoints_call_the_same_shared_aggregation(test_client, rider_override, admin_module_override):
    """The product decision requires a company admin and a Spinr admin to
    see byte-identical documents. Both routes call
    routes.corporate_company.build_full_month_statement — same function,
    same module — so this asserts that shared call rather than two
    independently-mocked copies that could silently diverge."""
    shared_statement_calls = []

    async def _capture(company_id, month):
        shared_statement_calls.append((company_id, month))
        return _STATEMENT

    with (
        _as_admin(),
        patch(_COMPANY_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=_company())),
        patch(_COMPANY_ROUTE + "build_full_month_statement", AsyncMock(side_effect=_capture)),
        patch(_COMPANY_ROUTE + "generate_corporate_statement_pdf", return_value=b"%PDF"),
        patch(_COMPANY_ROUTE + "log_user_action", AsyncMock()),
    ):
        test_client.get("/company/c1/billing/statements/2026-07/pdf")

    with (
        patch(_ACCOUNTS_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=_company())),
        patch(_COMPANY_ROUTE + "build_full_month_statement", AsyncMock(side_effect=_capture)),
        patch(_ACCOUNTS_ROUTE + "generate_corporate_statement_pdf", return_value=b"%PDF"),
        patch(_ACCOUNTS_ROUTE + "log_admin_action", AsyncMock()),
    ):
        test_client.get("/api/admin/corporate-accounts/c1/billing/statements/2026-07/pdf")

    assert shared_statement_calls == [("c1", "2026-07"), ("c1", "2026-07")]
