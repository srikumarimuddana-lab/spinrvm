# backend/tests/test_corporate_subscriptions_route.py
"""HTTP-level tests for routes/corporate_subscriptions.py — admin endpoints
for flat SaaS corporate subscription billing (corporate + admin portal
review round 2, business decision: full Stripe Subscriptions automation).

Uses the same `admin_override` fixture (role=admin, modules=["corporate_accounts"])
already established for routes/corporate_wallet.py's HTTP tests
(test_corporate_wallet_routes.py) — this router is mounted under the exact
same require_module("corporate_accounts") gate.
"""

from unittest.mock import AsyncMock, patch

from services.corporate_subscription_service import CorporateSubscriptionError


def _sub_row(**extra):
    return {
        "id": "corp-sub-1",
        "company_id": "c1",
        "plan_id": "plan_pro",
        "plan_name": "Pro",
        "price": "199.00",
        "status": "active",
        **extra,
    }


def test_list_plans(test_client, admin_override):
    with patch(
        "routes.corporate_subscriptions.db_supabase.list_corporate_subscription_plans",
        AsyncMock(return_value=[{"id": "plan_pro", "name": "Pro", "monthly_price": "199.00"}]),
    ) as m_list:
        resp = test_client.get("/api/admin/corporate-accounts/subscription-plans")

    assert resp.status_code == 200, resp.text
    assert resp.json()["plans"][0]["id"] == "plan_pro"
    m_list.assert_awaited_once_with(active_only=True)


def test_get_company_subscription(test_client, admin_override):
    with (
        patch(
            "routes.corporate_subscriptions.db_supabase.get_active_corporate_subscription",
            AsyncMock(return_value=_sub_row()),
        ),
        patch(
            "routes.corporate_subscriptions.db_supabase.list_corporate_subscriptions_for_company",
            AsyncMock(return_value=[_sub_row()]),
        ),
    ):
        resp = test_client.get("/api/admin/corporate-accounts/c1/subscription")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["current"]["status"] == "active"
    assert len(body["history"]) == 1


class TestAssignGatedByFlag:
    def test_assign_blocked_when_flag_unset(self, test_client, admin_override):
        """Default (unset) app_settings means the flag is off — ships dark."""
        with patch("routes.corporate_subscriptions.get_app_settings", AsyncMock(return_value={})):
            resp = test_client.post(
                "/api/admin/corporate-accounts/c1/subscription",
                json={"plan_id": "plan_pro"},
            )
        assert resp.status_code == 403, resp.text
        assert "not yet enabled" in resp.json()["detail"]

    def test_assign_blocked_when_flag_explicitly_false(self, test_client, admin_override):
        with patch(
            "routes.corporate_subscriptions.get_app_settings",
            AsyncMock(return_value={"corporate_subscription_billing_enabled": False}),
        ):
            resp = test_client.post(
                "/api/admin/corporate-accounts/c1/subscription",
                json={"plan_id": "plan_pro"},
            )
        assert resp.status_code == 403, resp.text

    def test_assign_succeeds_when_flag_enabled(self, test_client, admin_override):
        with (
            patch(
                "routes.corporate_subscriptions.get_app_settings",
                AsyncMock(return_value={"corporate_subscription_billing_enabled": True}),
            ),
            patch(
                "routes.corporate_subscriptions.assign_subscription",
                AsyncMock(return_value=_sub_row()),
            ) as m_assign,
        ):
            resp = test_client.post(
                "/api/admin/corporate-accounts/c1/subscription",
                json={"plan_id": "plan_pro"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "active"
        m_assign.assert_awaited_once_with(company_id="c1", plan_id="plan_pro", admin_id="admin_1")

    def test_assign_maps_service_error_to_http_status(self, test_client, admin_override):
        with (
            patch(
                "routes.corporate_subscriptions.get_app_settings",
                AsyncMock(return_value={"corporate_subscription_billing_enabled": True}),
            ),
            patch(
                "routes.corporate_subscriptions.assign_subscription",
                AsyncMock(side_effect=CorporateSubscriptionError("subscription_already_active")),
            ),
        ):
            resp = test_client.post(
                "/api/admin/corporate-accounts/c1/subscription",
                json={"plan_id": "plan_pro"},
            )
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"] == "subscription_already_active"

    def test_assign_rejects_extra_fields(self, test_client, admin_override):
        with patch(
            "routes.corporate_subscriptions.get_app_settings",
            AsyncMock(return_value={"corporate_subscription_billing_enabled": True}),
        ):
            resp = test_client.post(
                "/api/admin/corporate-accounts/c1/subscription",
                json={"plan_id": "plan_pro", "price": "1.00"},
            )
        assert resp.status_code == 422, resp.text


class TestCancelNeverGatedByFlag:
    def test_cancel_succeeds_even_when_flag_unset(self, test_client, admin_override):
        """Cancellation is never blocked by the billing-enabled flag — an
        admin must always be able to stop an existing charge."""
        with (
            patch("routes.corporate_subscriptions.get_app_settings", AsyncMock(return_value={})),
            patch(
                "routes.corporate_subscriptions.cancel_subscription",
                AsyncMock(return_value=_sub_row(cancel_at_period_end=True)),
            ) as m_cancel,
        ):
            resp = test_client.post(
                "/api/admin/corporate-accounts/c1/subscription/cancel",
                json={},
            )
        assert resp.status_code == 200, resp.text
        m_cancel.assert_awaited_once_with(company_id="c1", admin_id="admin_1", at_period_end=True)

    def test_cancel_immediate_passes_flag_through(self, test_client, admin_override):
        with patch(
            "routes.corporate_subscriptions.cancel_subscription",
            AsyncMock(return_value=_sub_row(status="cancelled")),
        ) as m_cancel:
            resp = test_client.post(
                "/api/admin/corporate-accounts/c1/subscription/cancel",
                json={"at_period_end": False},
            )
        assert resp.status_code == 200, resp.text
        m_cancel.assert_awaited_once_with(company_id="c1", admin_id="admin_1", at_period_end=False)

    def test_cancel_maps_no_active_subscription_to_404(self, test_client, admin_override):
        with patch(
            "routes.corporate_subscriptions.cancel_subscription",
            AsyncMock(side_effect=CorporateSubscriptionError("no_active_subscription")),
        ):
            resp = test_client.post(
                "/api/admin/corporate-accounts/c1/subscription/cancel",
                json={},
            )
        assert resp.status_code == 404, resp.text


def test_module_gate_rejects_admin_without_corporate_accounts_module(test_client):
    """An admin lacking the corporate_accounts module grant must be
    rejected at the router-include gate, before any endpoint logic runs —
    same require_module("corporate_accounts") pattern already proven for
    routes/corporate_wallet.py."""
    from backend.dependencies import get_admin_user
    from backend.server import app

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_2", "role": "admin", "modules": ["dashboard"]}
    try:
        resp = test_client.get("/api/admin/corporate-accounts/subscription-plans")
    finally:
        app.dependency_overrides.pop(get_admin_user, None)

    assert resp.status_code == 403, resp.text
