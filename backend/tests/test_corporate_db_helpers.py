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
    with patch("repositories.corporate_repo.supabase", mock_supabase_client):
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
    with patch("repositories.corporate_repo.supabase", mock_supabase_client):
        from db_supabase import update_corporate_account_status

        row = await update_corporate_account_status("c1", "active")
    assert row["status"] == "active"


@pytest.mark.asyncio
async def test_record_kyb_decision(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    table.update.return_value = table  # wire the chain
    table.execute = MagicMock(return_value=_fake_resp([{"id": "c1"}]))
    with patch("repositories.corporate_repo.supabase", mock_supabase_client):
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


# ── KYB v1 (migration 225 / M2.1) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_kyb_decision_stamps_last_decision_and_note():
    fake = MagicMock()
    fake.data = [{"id": "c1", "status": "suspended", "kyb_last_decision": "rejected"}]
    with patch("repositories.corporate_repo.supabase") as mock_sb:
        mock_sb.table.return_value.update.return_value.eq.return_value.execute.return_value = fake
        from db_supabase import record_kyb_decision

        row = await record_kyb_decision(company_id="c1", reviewer_id="admin-001", approved=False, note="BN mismatch")
    assert row["status"] == "suspended"
    patch_sent = mock_sb.table.return_value.update.call_args.args[0]
    assert patch_sent["kyb_last_decision"] == "rejected"
    assert patch_sent["kyb_review_note"] == "BN mismatch"  # column exists (225)


@pytest.mark.asyncio
async def test_record_kyb_decision_approval_activates():
    fake = MagicMock()
    fake.data = [{"id": "c1", "status": "active"}]
    with patch("repositories.corporate_repo.supabase") as mock_sb:
        mock_sb.table.return_value.update.return_value.eq.return_value.execute.return_value = fake
        from db_supabase import record_kyb_decision

        await record_kyb_decision(company_id="c1", reviewer_id="admin-001", approved=True, note=None)
    patch_sent = mock_sb.table.return_value.update.call_args.args[0]
    assert patch_sent["status"] == "active"
    assert patch_sent["kyb_last_decision"] == "approved"
    assert "kyb_review_note" not in patch_sent


@pytest.mark.asyncio
async def test_set_kyb_document_persists_path_and_submitted_at():
    # Regression: create_kyb_upload_url returned a path but nothing persisted
    # it, so /kyb/view read an always-NULL kyb_document_url.
    fake = MagicMock()
    fake.data = [{"id": "c1", "kyb_document_url": "kyb/c1/abc.pdf"}]
    with patch("repositories.corporate_repo.supabase") as mock_sb:
        mock_sb.table.return_value.update.return_value.eq.return_value.execute.return_value = fake
        from db_supabase import set_kyb_document

        row = await set_kyb_document(company_id="c1", path="kyb/c1/abc.pdf")
    assert row["kyb_document_url"] == "kyb/c1/abc.pdf"
    patch_sent = mock_sb.table.return_value.update.call_args.args[0]
    assert patch_sent["kyb_document_url"] == "kyb/c1/abc.pdf"
    assert patch_sent["kyb_submitted_at"]
