# backend/tests/test_corporate_admin_routes.py
from unittest.mock import AsyncMock, patch

from backend.tests._factories import corporate_account_row


def test_list_filters_by_status(test_client, admin_override):
    rows = [corporate_account_row("pending_verification", name="A")]
    with patch(
        "db_supabase.list_corporate_accounts_filtered",
        AsyncMock(return_value=rows),
    ):
        resp = test_client.get(
            "/api/admin/corporate-accounts?status=pending_verification",
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    assert data[0]["status"] == "pending_verification"


def test_status_filter_validates_enum(test_client, admin_override):
    resp = test_client.get(
        "/api/admin/corporate-accounts?status=bogus",
    )
    assert resp.status_code == 422
