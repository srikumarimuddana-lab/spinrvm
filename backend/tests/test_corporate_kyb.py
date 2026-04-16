# backend/tests/test_corporate_kyb.py
from unittest.mock import AsyncMock, patch

from backend.tests._factories import corporate_account_row


def test_approve_kyb_flips_status_to_active(test_client, admin_override):
    with patch(
        "db_supabase.record_kyb_decision",
        AsyncMock(return_value=corporate_account_row("active")),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-review",
            json={"approve": True},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"


def test_reject_kyb_flips_status_to_suspended(test_client, admin_override):
    with patch(
        "db_supabase.record_kyb_decision",
        AsyncMock(return_value=corporate_account_row("suspended")),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-review",
            json={"approve": False, "note": "doc unreadable"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "suspended"


def test_kyb_review_404_on_missing_company(test_client, admin_override):
    with patch(
        "db_supabase.record_kyb_decision",
        AsyncMock(return_value=None),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/nonexistent/kyb-review",
            json={"approve": True},
        )
    assert resp.status_code == 404, resp.text
