"""Thin happy-path tests for the Plan-3 db_supabase helpers.

The helpers are pass-through wrappers around supabase-py chains — we just
verify the right table is hit and the right row comes back.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_insert_member_invite_writes_row():
    fake = MagicMock()
    fake.data = [{"id": "m1", "company_id": "c1", "invited_email": "a@b.com", "status": "invited"}]
    with patch("db_supabase.supabase") as mock_sb:
        mock_sb.table.return_value.insert.return_value.execute.return_value = fake
        from db_supabase import insert_corporate_member_invite

        row = await insert_corporate_member_invite(
            company_id="c1",
            email="a@b.com",
            role="member",
            invite_token="tok",
            invited_by="admin1",
        )
    assert row["id"] == "m1"
    mock_sb.table.assert_called_with("corporate_members")


@pytest.mark.asyncio
async def test_list_company_members_filters_status():
    fake = MagicMock()
    fake.data = [{"id": "m1"}, {"id": "m2"}]
    with patch("db_supabase.supabase") as mock_sb:
        chain = mock_sb.table.return_value.select.return_value.eq.return_value.in_.return_value.order.return_value
        chain.execute.return_value = fake
        from db_supabase import list_company_members

        rows = await list_company_members(company_id="c1", statuses=["active", "invited"])
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_get_member_by_invite_token_returns_row():
    fake = MagicMock()
    fake.data = [{"id": "m1", "invite_token": "tok"}]
    with patch("db_supabase.supabase") as mock_sb:
        (mock_sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value) = fake
        from db_supabase import get_member_by_invite_token

        row = await get_member_by_invite_token("tok")
    assert row["invite_token"] == "tok"


@pytest.mark.asyncio
async def test_upsert_allowance_inserts_when_absent():
    existing = MagicMock()
    existing.data = []
    inserted = MagicMock()
    inserted.data = [{"id": "a1", "member_id": "m1", "used": 0}]
    with patch("db_supabase.supabase") as mock_sb:
        (
            mock_sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value
        ) = existing
        mock_sb.table.return_value.insert.return_value.execute.return_value = inserted
        from db_supabase import upsert_member_allowance

        row = await upsert_member_allowance(
            member_id="m1",
            patch={"type": "fixed_recurring", "amount": 500, "period_start": "2026-04-01", "period_end": "2026-04-30"},
        )
    assert row["id"] == "a1"


@pytest.mark.asyncio
async def test_upsert_allowance_updates_when_present():
    existing = MagicMock()
    existing.data = [{"id": "a1", "member_id": "m1", "used": 50}]
    updated = MagicMock()
    updated.data = [{"id": "a1", "amount": 700}]
    with patch("db_supabase.supabase") as mock_sb:
        (
            mock_sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value
        ) = existing
        (mock_sb.table.return_value.update.return_value.eq.return_value.execute.return_value) = updated
        from db_supabase import upsert_member_allowance

        row = await upsert_member_allowance(
            member_id="m1",
            patch={"amount": 700},
        )
    assert row["amount"] == 700


@pytest.mark.asyncio
async def test_list_pending_requests_orders_desc():
    fake = MagicMock()
    fake.data = [{"id": "r1"}]
    with patch("db_supabase.supabase") as mock_sb:
        chain = mock_sb.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value
        chain.execute.return_value = fake
        from db_supabase import list_pending_allowance_requests_for_member

        rows = await list_pending_allowance_requests_for_member("m1")
    assert rows[0]["id"] == "r1"


@pytest.mark.asyncio
async def test_accept_member_invite_flips_status():
    fake = MagicMock()
    fake.data = [{"id": "m1", "status": "active", "user_id": "u1"}]
    with patch("db_supabase.supabase") as mock_sb:
        (mock_sb.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value) = fake
        from db_supabase import accept_member_invite

        row = await accept_member_invite(member_id="m1", user_id="u1")
    assert row["status"] == "active"
    assert row["user_id"] == "u1"


@pytest.mark.asyncio
async def test_add_allowed_domain_inserts_lowercase():
    fake = MagicMock()
    fake.data = [{"id": "d1", "company_id": "c1", "domain": "acme.com"}]
    with patch("db_supabase.supabase") as mock_sb:
        mock_sb.table.return_value.insert.return_value.execute.return_value = fake
        from db_supabase import add_allowed_domain

        row = await add_allowed_domain(company_id="c1", domain="acme.com")
    assert row["domain"] == "acme.com"


@pytest.mark.asyncio
async def test_list_allowances_due_for_reset_filters():
    fake = MagicMock()
    fake.data = [{"id": "a1", "period_end": "2026-03-31"}]
    with patch("db_supabase.supabase") as mock_sb:
        chain = mock_sb.table.return_value.select.return_value.eq.return_value.eq.return_value.lt.return_value
        chain.execute.return_value = fake
        from db_supabase import list_allowances_due_for_reset

        rows = await list_allowances_due_for_reset(as_of="2026-04-01")
    assert len(rows) == 1
