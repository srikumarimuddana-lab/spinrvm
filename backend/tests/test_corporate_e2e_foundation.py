# backend/tests/test_corporate_e2e_foundation.py
"""End-to-end smoke: create → KYB approve → suspend → reactivate.

All DB calls are mocked — this test exercises the FastAPI wiring,
Pydantic schema round-trips, and the closed-is-terminal guard, not
the Supabase layer itself (that's covered by test_corporate_db_helpers
and the integration-marked schema smoke test).
"""
from unittest.mock import AsyncMock, patch

from backend.tests._factories import corporate_account_row


def test_create_approve_suspend_reactivate_flow(test_client, admin_override):
    created = corporate_account_row(
        "pending_verification",
        id="c_e2e",
        name="E2E Corp",
        legal_name="E2E Corp Inc.",
        business_number="123456789RT0001",
        tax_region="ON",
        billing_email="billing@example.com",
    )

    active_row = corporate_account_row(
        "active",
        id="c_e2e",
        name="E2E Corp",
        legal_name="E2E Corp Inc.",
        business_number="123456789RT0001",
        tax_region="ON",
        billing_email="billing@example.com",
    )
    suspended_row = corporate_account_row(
        "suspended",
        id="c_e2e",
        name="E2E Corp",
        legal_name="E2E Corp Inc.",
        business_number="123456789RT0001",
        tax_region="ON",
        billing_email="billing@example.com",
    )

    with patch(
        "routes.corporate_accounts.insert_corporate_account",
        AsyncMock(return_value=created),
    ), patch(
        "db_supabase.record_kyb_decision",
        AsyncMock(return_value=active_row),
    ), patch(
        "routes.corporate_accounts.get_corporate_account_by_id",
        AsyncMock(side_effect=[active_row, suspended_row]),
    ), patch(
        "db_supabase.update_corporate_account_status",
        AsyncMock(side_effect=[suspended_row, active_row]),
    ):
        # 1. Create the company
        resp = test_client.post(
            "/api/admin/corporate-accounts",
            json={
                "name": "E2E Corp",
                "contact_email": "billing@example.com",
            },
        )
        assert resp.status_code in (200, 201), resp.text

        # 2. Approve KYB → status flips to active
        resp = test_client.post(
            "/api/admin/corporate-accounts/c_e2e/kyb-review",
            json={"approve": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "active"

        # 3. Suspend the active company
        resp = test_client.post(
            "/api/admin/corporate-accounts/c_e2e/status",
            json={"status": "suspended", "reason": "overdue balance"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "suspended"

        # 4. Reactivate the suspended company
        resp = test_client.post(
            "/api/admin/corporate-accounts/c_e2e/status",
            json={"status": "active"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "active"


def test_cannot_reopen_closed_company_in_flow(test_client, admin_override):
    closed_row = corporate_account_row("closed", id="c_closed")

    with patch(
        "routes.corporate_accounts.get_corporate_account_by_id",
        AsyncMock(return_value=closed_row),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c_closed/status",
            json={"status": "active"},
        )
    assert resp.status_code == 409, resp.text
    assert "closed" in resp.json()["detail"].lower()
