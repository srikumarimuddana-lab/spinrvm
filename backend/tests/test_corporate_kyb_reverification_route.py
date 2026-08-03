# backend/tests/test_corporate_kyb_reverification_route.py
"""GET /admin/corporate-accounts/kyb-reverification-due (corporate + admin
portal review round 2, "automated KYB re-verification" — visibility only).

Reuses the admin_override fixture (role=admin, modules=["corporate_accounts"])
already established for this router's sibling endpoints
(test_corporate_wallet_routes.py). The route imports its dependencies
locally (function-body, not module-level) — patch targets are therefore
the DEFINING modules (db_supabase, settings_loader,
utils.kyb_reverification), not routes.corporate_accounts, matching the
lazy-import patch convention already established for
routes.corporate_company.build_full_month_statement in
test_corporate_statement_pdf_routes.py.
"""

from unittest.mock import AsyncMock, patch

_SETTINGS = {"corporate_kyb_reverify_after_months": 12}


def _company(**extra):
    return {
        "id": "c1",
        "name": "Acme Co",
        "legal_name": "Acme Co Ltd",
        "kyb_reviewed_at": "2024-01-01T00:00:00+00:00",
        "kyb_reviewed_by": "admin_9",
        **extra,
    }


def test_kyb_reverification_due_returns_companies(test_client, admin_override):
    with (
        patch("settings_loader.get_app_settings", AsyncMock(return_value=_SETTINGS)),
        patch(
            "db_supabase.list_companies_needing_kyb_reverification",
            AsyncMock(return_value=[_company()]),
        ) as m_list,
    ):
        resp = test_client.get("/api/admin/corporate-accounts/kyb-reverification-due")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["threshold_months"] == 12
    assert body["count"] == 1
    assert body["companies"][0]["id"] == "c1"
    assert body["companies"][0]["kyb_reviewed_at"] == "2024-01-01T00:00:00+00:00"
    m_list.assert_awaited_once()


def test_kyb_reverification_due_empty_when_none_stale(test_client, admin_override):
    with (
        patch("settings_loader.get_app_settings", AsyncMock(return_value=_SETTINGS)),
        patch("db_supabase.list_companies_needing_kyb_reverification", AsyncMock(return_value=[])),
    ):
        resp = test_client.get("/api/admin/corporate-accounts/kyb-reverification-due")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 0
    assert body["companies"] == []


def test_kyb_reverification_due_uses_default_threshold_when_unset(test_client, admin_override):
    with (
        patch("settings_loader.get_app_settings", AsyncMock(return_value={})),
        patch("db_supabase.list_companies_needing_kyb_reverification", AsyncMock(return_value=[])),
    ):
        resp = test_client.get("/api/admin/corporate-accounts/kyb-reverification-due")

    assert resp.status_code == 200, resp.text
    assert resp.json()["threshold_months"] == 12  # _DEFAULT_THRESHOLD_MONTHS


def test_route_registered_before_dynamic_account_id_route(test_client, admin_override):
    """Static path must not be swallowed by GET /{account_id} — a 200 (not
    a 404-from-treating-'kyb-reverification-due'-as-an-account-id) proves
    FastAPI matched the static route first."""
    with (
        patch("settings_loader.get_app_settings", AsyncMock(return_value=_SETTINGS)),
        patch("db_supabase.list_companies_needing_kyb_reverification", AsyncMock(return_value=[])),
    ):
        resp = test_client.get("/api/admin/corporate-accounts/kyb-reverification-due")

    assert resp.status_code == 200, resp.text


def test_module_gate_rejects_admin_without_corporate_accounts_module(test_client):
    from backend.dependencies import get_admin_user
    from backend.server import app

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_2", "role": "admin", "modules": ["dashboard"]}
    try:
        resp = test_client.get("/api/admin/corporate-accounts/kyb-reverification-due")
    finally:
        app.dependency_overrides.pop(get_admin_user, None)

    assert resp.status_code == 403, resp.text
