"""Master/section wallet helpers + section-head guard (M5.3).

The reviewer-flagged landmine: after migration 226 a company can hold master
AND section wallet rows; every master lookup must filter section_id IS NULL
or .limit(1) returns a non-deterministic row on money-critical paths.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _query_mock(rows):
    """Build a supabase-py chain mock whose execute() returns rows, and
    record the chained filter calls for assertions."""
    fake_res = MagicMock()
    fake_res.data = rows
    chain = MagicMock()
    # every chained method returns the same chain; execute returns the rows
    for name in ("select", "eq", "is_", "limit", "insert"):
        getattr(chain, name).return_value = chain
    chain.execute.return_value = fake_res
    table = MagicMock(return_value=chain)
    sb = MagicMock()
    sb.table = table
    return sb, chain


@pytest.mark.asyncio
async def test_master_lookup_filters_section_id_null():
    # Regression (sequencing gate): with a master + a section wallet on the
    # same company, the master lookup must be pinned by section_id IS NULL.
    master = {"id": "w1", "company_id": "c1", "section_id": None, "balance": 100}
    sb, chain = _query_mock([master])
    with patch("repositories.corporate_repo.supabase", sb):
        from db_supabase import get_corporate_wallet_by_company

        row = await get_corporate_wallet_by_company("c1")
    assert row == master
    chain.is_.assert_called_once_with("section_id", "null")


@pytest.mark.asyncio
async def test_get_master_wallet_alias_is_same_function():
    from db_supabase import get_corporate_wallet_by_company, get_master_wallet

    assert get_master_wallet is get_corporate_wallet_by_company


@pytest.mark.asyncio
async def test_ensure_master_wallet_ignores_section_rows():
    # A pre-existing SECTION wallet must not satisfy the master existence
    # check (the master would never get created).
    sb, chain = _query_mock([])  # select finds nothing (section rows filtered out)
    created = {"id": "w9", "company_id": "c1", "section_id": None, "balance": 0}
    insert_res = MagicMock()
    insert_res.data = [created]
    chain.execute.side_effect = [MagicMock(data=[]), insert_res]
    with patch("repositories.corporate_repo.supabase", sb):
        from db_supabase import ensure_corporate_wallet

        row = await ensure_corporate_wallet(company_id="c1")
    assert row == created
    chain.is_.assert_called_with("section_id", "null")


@pytest.mark.asyncio
async def test_ensure_section_wallet_hard_floor_zero():
    sb, chain = _query_mock([])
    created = {"id": "w2", "company_id": "c1", "section_id": "s1", "balance": 0}
    chain.execute.side_effect = [MagicMock(data=[]), MagicMock(data=[created])]
    with patch("repositories.corporate_repo.supabase", sb):
        from db_supabase import ensure_section_wallet

        row = await ensure_section_wallet(company_id="c1", section_id="s1")
    assert row == created
    inserted = chain.insert.call_args.args[0]
    assert inserted["section_id"] == "s1"
    assert inserted["soft_negative_floor"] == 0  # Q9: sections never go negative
    assert inserted["auto_topup_enabled"] is False


@pytest.mark.asyncio
async def test_autotopup_scan_excludes_section_wallets():
    sb, chain = _query_mock([])
    with patch("repositories.corporate_repo.supabase", sb):
        from db_supabase import list_wallets_needing_autotopup

        await list_wallets_needing_autotopup()
    chain.is_.assert_called_once_with("section_id", "null")


# ── require_section_head guard ───────────────────────────────────────────────


def _guard_ctx():
    import dependencies.company_guard as guard_mod

    return guard_mod


@pytest.mark.asyncio
async def test_section_head_allows_admin_and_head_only():
    guard_mod = _guard_ctx()
    section = {"id": "s1", "company_id": "c1", "head_member_id": "m2"}

    async def run(role, member_id):
        with (
            patch.object(
                guard_mod,
                "list_active_memberships_for_user",
                AsyncMock(return_value=[{"company_id": "c1", "role": role, "id": member_id}]),
            ),
            patch("db_supabase.get_rows", AsyncMock(return_value=[section])),
        ):
            return await guard_mod.require_section_head(company_id="c1", section_id="s1", current_user={"id": "u1"})

    ctx = await run("admin", "m1")  # admin, not head
    assert ctx["is_head"] is False and ctx["section"]["id"] == "s1"

    ctx = await run("member", "m2")  # plain member who IS the head
    assert ctx["is_head"] is True

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await run("member", "m3")  # plain member, not the head
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_section_head_cross_company_section_is_404():
    guard_mod = _guard_ctx()
    with (
        patch.object(
            guard_mod,
            "list_active_memberships_for_user",
            AsyncMock(return_value=[{"company_id": "c1", "role": "admin", "id": "m1"}]),
        ),
        patch("db_supabase.get_rows", AsyncMock(return_value=[])),  # section not in c1
    ):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await guard_mod.require_section_head(company_id="c1", section_id="s-foreign", current_user={"id": "u1"})
    assert exc.value.status_code == 404
