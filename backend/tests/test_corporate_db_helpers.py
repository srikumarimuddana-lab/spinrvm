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
async def test_list_companies_search_uses_shared_or_escaping(mock_supabase_client):
    """Corporate + admin portal review, gap #42: get_all_corporate_accounts
    and list_corporate_accounts_filtered used to hand-roll ilike escaping
    and then STRIP reserved characters (,.()) from the search term instead
    of escaping them — silently mangling a legitimate search like
    "Acme, Inc". Now routed through repositories._base._apply_filters'
    shared $or/$regex handling, which escapes (not strips) them."""
    table = mock_supabase_client.table.return_value
    table.range.return_value = table
    table.or_.return_value = table
    table.execute = MagicMock(return_value=_fake_resp([{"id": "c1", "name": "Acme, Inc"}]))
    with patch("repositories.corporate_repo.supabase", mock_supabase_client):
        from db_supabase import list_corporate_accounts_filtered

        rows = await list_corporate_accounts_filtered(
            status=None, size_tier=None, search="Acme, Inc", skip=0, limit=50
        )
    assert rows == [{"id": "c1", "name": "Acme, Inc"}]
    or_arg = table.or_.call_args.args[0]
    # The comma must be escaped (\,), not silently dropped from the term.
    assert r"Acme\, Inc" in or_arg
    assert "name.ilike." in or_arg
    assert "legal_name.ilike." in or_arg


@pytest.mark.asyncio
async def test_get_all_corporate_accounts_search_uses_shared_or_escaping(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    table.range.return_value = table
    table.or_.return_value = table
    table.execute = MagicMock(return_value=_fake_resp([{"id": "c1", "name": "Acme, Inc"}]))
    with patch("repositories.corporate_repo.supabase", mock_supabase_client):
        from db_supabase import get_all_corporate_accounts

        rows = await get_all_corporate_accounts(search="Acme, Inc")
    assert rows == [{"id": "c1", "name": "Acme, Inc"}]
    or_arg = table.or_.call_args.args[0]
    assert r"Acme\, Inc" in or_arg
    assert "contact_name.ilike." in or_arg
    assert "contact_email.ilike." in or_arg


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


# ── wallet risk portfolio (Corporate + admin portal review, round 2) ───────


def _table_router(**by_name):
    """MagicMock().table(name) dispatcher — a distinct chainable mock per
    table name, so a test can wire corporate_wallets and corporate_accounts
    independently without one .execute() override clobbering the other."""

    def _get(name):
        return by_name[name]

    router = MagicMock(side_effect=_get)
    return router


@pytest.mark.asyncio
async def test_wallet_risk_portfolio_empty_when_no_wallets():
    wallets_table = MagicMock()
    wallets_table.select.return_value = wallets_table
    wallets_table.execute.return_value = _fake_resp([])
    with patch("repositories.corporate_repo.supabase") as mock_sb:
        mock_sb.table = _table_router(corporate_wallets=wallets_table)
        from db_supabase import list_wallet_risk_portfolio

        rows = await list_wallet_risk_portfolio()
    assert rows == []
    # No second query issued against an empty company_id list.
    assert "corporate_accounts" not in [c.args[0] for c in mock_sb.table.call_args_list]


@pytest.mark.asyncio
async def test_wallet_risk_portfolio_flags_negative_and_floor():
    wallets_table = MagicMock()
    wallets_table.select.return_value = wallets_table
    wallets_table.execute.return_value = _fake_resp(
        [
            {
                "id": "w1",
                "company_id": "c1",
                "balance": "-25.00",
                "soft_negative_floor": "-50.00",
                "auto_topup_enabled": False,
                "auto_topup_threshold": None,
            },
            {
                "id": "w2",
                "company_id": "c2",
                "balance": "-60.00",
                "soft_negative_floor": "-50.00",
                "auto_topup_enabled": False,
                "auto_topup_threshold": None,
            },
        ]
    )
    accounts_table = MagicMock()
    accounts_table.select.return_value = accounts_table
    accounts_table.in_.return_value = accounts_table
    accounts_table.execute.return_value = _fake_resp(
        [{"id": "c1", "name": "Acme", "status": "active"}, {"id": "c2", "name": "Beta", "status": "active"}]
    )
    with patch("repositories.corporate_repo.supabase") as mock_sb:
        mock_sb.table = _table_router(corporate_wallets=wallets_table, corporate_accounts=accounts_table)
        from db_supabase import list_wallet_risk_portfolio

        rows = await list_wallet_risk_portfolio()

    by_id = {r["wallet_id"]: r for r in rows}
    assert "negative_balance" in by_id["w1"]["risk_flags"]
    assert "at_floor" not in by_id["w1"]["risk_flags"]  # -25 > floor of -50
    assert "negative_balance" in by_id["w2"]["risk_flags"]
    assert "at_floor" in by_id["w2"]["risk_flags"]  # -60 <= floor of -50
    assert by_id["w1"]["company_name"] == "Acme"
    assert by_id["w2"]["company_name"] == "Beta"


@pytest.mark.asyncio
async def test_wallet_risk_portfolio_low_balance_flag_depends_on_autotopup():
    wallets_table = MagicMock()
    wallets_table.select.return_value = wallets_table
    wallets_table.execute.return_value = _fake_resp(
        [
            {
                "id": "w1",
                "company_id": "c1",
                "balance": "10.00",
                "soft_negative_floor": "-50.00",
                "auto_topup_enabled": True,
                "auto_topup_threshold": "20.00",
            },
            {
                "id": "w2",
                "company_id": "c2",
                "balance": "10.00",
                "soft_negative_floor": "-50.00",
                "auto_topup_enabled": False,
                "auto_topup_threshold": "20.00",
            },
        ]
    )
    accounts_table = MagicMock()
    accounts_table.select.return_value = accounts_table
    accounts_table.in_.return_value = accounts_table
    accounts_table.execute.return_value = _fake_resp([])
    with patch("repositories.corporate_repo.supabase") as mock_sb:
        mock_sb.table = _table_router(corporate_wallets=wallets_table, corporate_accounts=accounts_table)
        from db_supabase import list_wallet_risk_portfolio

        rows = await list_wallet_risk_portfolio()

    by_id = {r["wallet_id"]: r for r in rows}
    assert "below_autotopup_threshold" in by_id["w1"]["risk_flags"]
    assert "low_balance_no_autotopup" in by_id["w2"]["risk_flags"]


@pytest.mark.asyncio
async def test_wallet_risk_portfolio_sorts_flagged_and_most_negative_first():
    wallets_table = MagicMock()
    wallets_table.select.return_value = wallets_table
    wallets_table.execute.return_value = _fake_resp(
        [
            {
                "id": "healthy",
                "company_id": "c1",
                "balance": "500.00",
                "soft_negative_floor": "-50.00",
                "auto_topup_enabled": False,
                "auto_topup_threshold": None,
            },
            {
                "id": "mild",
                "company_id": "c2",
                "balance": "-10.00",
                "soft_negative_floor": "-50.00",
                "auto_topup_enabled": False,
                "auto_topup_threshold": None,
            },
            {
                "id": "severe",
                "company_id": "c3",
                "balance": "-100.00",
                "soft_negative_floor": "-50.00",
                "auto_topup_enabled": False,
                "auto_topup_threshold": None,
            },
        ]
    )
    accounts_table = MagicMock()
    accounts_table.select.return_value = accounts_table
    accounts_table.in_.return_value = accounts_table
    accounts_table.execute.return_value = _fake_resp([])
    with patch("repositories.corporate_repo.supabase") as mock_sb:
        mock_sb.table = _table_router(corporate_wallets=wallets_table, corporate_accounts=accounts_table)
        from db_supabase import list_wallet_risk_portfolio

        rows = await list_wallet_risk_portfolio()

    assert [r["wallet_id"] for r in rows] == ["severe", "mild", "healthy"]


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
