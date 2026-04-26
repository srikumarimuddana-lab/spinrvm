# backend/tests/test_corporate_db_helpers.py
from unittest.mock import MagicMock, patch

import pytest


def _fake_resp(data):
    return MagicMock(data=data, count=len(data) if isinstance(data, list) else 0)


@pytest.mark.asyncio
async def test_list_companies_by_status_filter(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    table.range.return_value = table  # wire the chain (range not in conftest)
    table.execute = MagicMock(return_value=_fake_resp([{"id": "c1", "status": "pending_verification"}]))
    with patch("db_supabase.supabase", mock_supabase_client):
        from db_supabase import list_corporate_accounts_filtered

        rows = await list_corporate_accounts_filtered(
            status="pending_verification", size_tier=None, search=None, skip=0, limit=50
        )
    assert rows == [{"id": "c1", "status": "pending_verification"}]
    mock_supabase_client.table.assert_called_with("corporate_accounts")


@pytest.mark.asyncio
async def test_update_company_status(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    table.update.return_value = table  # wire the chain
    table.execute = MagicMock(return_value=_fake_resp([{"id": "c1", "status": "active"}]))
    with patch("db_supabase.supabase", mock_supabase_client):
        from db_supabase import update_corporate_account_status

        row = await update_corporate_account_status("c1", "active")
    assert row["status"] == "active"


@pytest.mark.asyncio
async def test_record_kyb_decision(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    table.update.return_value = table  # wire the chain
    table.execute = MagicMock(return_value=_fake_resp([{"id": "c1"}]))
    with patch("db_supabase.supabase", mock_supabase_client):
        from db_supabase import record_kyb_decision

        await record_kyb_decision(
            company_id="c1",
            reviewer_id="admin_1",
            approved=True,
            note=None,
        )
    update_call = mock_supabase_client.table.return_value.update.call_args
    assert update_call is not None
    patch_body = update_call.args[0]
    assert patch_body["status"] == "active"
    assert patch_body["kyb_reviewed_by"] == "admin_1"
    assert "kyb_reviewed_at" in patch_body
