"""
A-P2-8: RBAC matrix tests — verify require_module() enforces module boundaries.

Each (role, modules) combination is tested against endpoints that require a
specific module. Support role must not reach wallet; finance must not reach
staff; etc.
"""

from unittest.mock import AsyncMock, patch

import pytest


def _make_admin(role: str, modules: list[str]) -> dict:
    return {"id": f"{role}_user", "role": role, "modules": modules, "email": f"{role}@spinr.app"}


SUPPORT_MODULES = ["dashboard", "support", "disputes", "notifications", "users"]
FINANCE_MODULES = ["dashboard", "earnings", "promotions", "corporate_accounts"]
OPERATIONS_MODULES = ["dashboard", "rides", "drivers", "service_areas", "vehicle_types"]
SUPER_ADMIN_MODULES = [
    "dashboard",
    "users",
    "drivers",
    "rides",
    "earnings",
    "promotions",
    "service_areas",
    "vehicle_types",
    "support",
    "disputes",
    "notifications",
    "settings",
    "corporate_accounts",
    "documents",
    "staff",
]


@pytest.fixture
def client(test_client):
    return test_client


def _set_admin(app, admin_dict):
    """Install a dependency override for get_admin_user."""
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: admin_dict
    return app


@pytest.fixture
def app():
    from backend.server import app as _app

    yield _app
    _app.dependency_overrides.clear()


# ── Wallet (module: "earnings") ──────────────────────────────────────────────


class TestWalletModuleEnforcement:
    def test_support_cannot_access_wallet_credit(self, app, client):
        _set_admin(app, _make_admin("support", SUPPORT_MODULES))
        resp = client.post("/api/admin/wallet/credit", json={"user_id": "u1", "amount": 10, "reason": "test"})
        assert resp.status_code == 403

    def test_finance_can_access_wallet_credit_route(self, app, client):
        """Finance has 'earnings' module — endpoint is reachable (422 on bad body, not 403)."""
        _set_admin(app, _make_admin("finance", FINANCE_MODULES))
        # Missing required fields → 422 (schema validation), not 403 (module check)
        resp = client.post("/api/admin/wallet/credit", json={})
        assert resp.status_code != 403

    def test_super_admin_can_access_wallet(self, app, client):
        _set_admin(app, _make_admin("super_admin", SUPER_ADMIN_MODULES))
        resp = client.post("/api/admin/wallet/credit", json={})
        assert resp.status_code != 403


# ── Staff (module: "staff") ──────────────────────────────────────────────────


class TestStaffModuleEnforcement:
    def test_support_cannot_list_staff(self, app, client):
        _set_admin(app, _make_admin("support", SUPPORT_MODULES))
        resp = client.get("/api/admin/staff")
        assert resp.status_code == 403

    def test_finance_cannot_list_staff(self, app, client):
        _set_admin(app, _make_admin("finance", FINANCE_MODULES))
        resp = client.get("/api/admin/staff")
        assert resp.status_code == 403

    def test_super_admin_can_list_staff(self, app, client):
        _set_admin(app, _make_admin("super_admin", SUPER_ADMIN_MODULES))
        with patch("db_supabase.get_rows", new_callable=AsyncMock, return_value=[]):
            resp = client.get("/api/admin/staff")
        assert resp.status_code != 403


# ── Settings (module: "settings") ────────────────────────────────────────────


class TestSettingsModuleEnforcement:
    def test_support_cannot_access_settings(self, app, client):
        _set_admin(app, _make_admin("support", SUPPORT_MODULES))
        resp = client.get("/api/admin/settings")
        assert resp.status_code == 403

    def test_operations_cannot_access_settings(self, app, client):
        _set_admin(app, _make_admin("operations", OPERATIONS_MODULES))
        resp = client.get("/api/admin/settings")
        assert resp.status_code == 403

    def test_super_admin_can_access_settings(self, app, client):
        _set_admin(app, _make_admin("super_admin", SUPER_ADMIN_MODULES))
        with patch("settings_loader.get_app_settings", new_callable=AsyncMock, return_value={}):
            resp = client.get("/api/admin/settings")
        assert resp.status_code != 403


# ── Users (module: "users") ──────────────────────────────────────────────────


class TestUsersModuleEnforcement:
    def test_finance_cannot_list_users(self, app, client):
        _set_admin(app, _make_admin("finance", FINANCE_MODULES))
        resp = client.get("/api/admin/users")
        assert resp.status_code == 403

    def test_support_can_list_users(self, app, client):
        """Support has 'users' module — should be reachable."""
        _set_admin(app, _make_admin("support", SUPPORT_MODULES))
        with (
            patch("db_supabase.get_rows", new_callable=AsyncMock, return_value=[]),
            patch("db_supabase.count_documents", new_callable=AsyncMock, return_value=0),
        ):
            resp = client.get("/api/admin/users")
        assert resp.status_code != 403


# ── Analytics / Dashboard (module: "dashboard") ──────────────────────────────


class TestAnalyticsModuleEnforcement:
    def test_support_can_access_analytics(self, app, client):
        """Support has 'dashboard' module — analytics should be reachable."""
        _set_admin(app, _make_admin("support", SUPPORT_MODULES))
        # We just care it's not a module-403; DB errors are fine here
        resp = client.get("/api/admin/analytics/overview")
        assert resp.status_code != 403

    def test_finance_can_access_analytics(self, app, client):
        _set_admin(app, _make_admin("finance", FINANCE_MODULES))
        resp = client.get("/api/admin/analytics/overview")
        assert resp.status_code != 403
