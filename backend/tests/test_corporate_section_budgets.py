"""Section budget visibility fields (corporate + admin portal review round
2, business decision: "department/section budgets" — track and display,
never block). Follows the direct-handler-call + explicit ctx pattern
already established in test_corporate_sections.py.

Run:
    pytest backend/tests/test_corporate_section_budgets.py -v
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

_CCB = "backend.routes.corporate_company_bookings."
_COMPANY_ID = "company_sec"

_ADMIN_CTX = {
    "user": {"id": "user_admin"},
    "company_id": _COMPANY_ID,
    "role": "admin",
    "member_id": "member_admin",
    "member": {"id": "member_admin", "company_id": _COMPANY_ID, "role": "admin"},
}


@pytest.mark.anyio
async def test_create_section_with_budget_cap_converts_decimal_to_float():
    from backend.routes.corporate_company_bookings import SectionCreate, create_section

    with (
        patch(_CCB + "db_supabase.insert_one", AsyncMock(side_effect=lambda t, row: row)) as ins,
        patch(_CCB + "log_user_action", AsyncMock()),
    ):
        row = await create_section(SectionCreate(name="Service", monthly_budget_cap=Decimal("2500.00")), _ADMIN_CTX)

    inserted_row = ins.call_args.args[1]
    assert isinstance(inserted_row["monthly_budget_cap"], float)
    assert inserted_row["monthly_budget_cap"] == 2500.00
    assert row["monthly_budget_cap"] == 2500.00


@pytest.mark.anyio
async def test_create_section_without_budget_cap_is_none():
    from backend.routes.corporate_company_bookings import SectionCreate, create_section

    with (
        patch(_CCB + "db_supabase.insert_one", AsyncMock(side_effect=lambda t, row: row)),
        patch(_CCB + "log_user_action", AsyncMock()),
    ):
        row = await create_section(SectionCreate(name="Showroom"), _ADMIN_CTX)

    assert row["monthly_budget_cap"] is None


def test_negative_budget_cap_rejected_by_schema():
    import pydantic

    from backend.routes.corporate_company_bookings import SectionCreate, SectionUpdate

    with pytest.raises(pydantic.ValidationError):
        SectionCreate(name="Showroom", monthly_budget_cap=Decimal("-1.00"))
    with pytest.raises(pydantic.ValidationError):
        SectionUpdate(monthly_budget_cap=Decimal("-1.00"))


@pytest.mark.anyio
async def test_update_section_budget_cap_converts_decimal_to_float():
    from backend.routes.corporate_company_bookings import SectionUpdate, update_section

    existing = {"id": "sec1", "company_id": _COMPANY_ID, "name": "Service", "monthly_budget_cap": None}
    with (
        patch(_CCB + "db_supabase.get_rows", AsyncMock(return_value=[existing])),
        patch(_CCB + "db_supabase.update_one", AsyncMock(side_effect=lambda t, m, p: {**existing, **p})) as upd,
        patch(_CCB + "log_user_action", AsyncMock()),
    ):
        row = await update_section("sec1", SectionUpdate(monthly_budget_cap=Decimal("1000.00")), _ADMIN_CTX)

    patch_arg = upd.call_args.args[2]
    assert isinstance(patch_arg["monthly_budget_cap"], float)
    assert row["monthly_budget_cap"] == 1000.00


@pytest.mark.anyio
async def test_list_sections_includes_budget_spend_used():
    from backend.routes.corporate_company_bookings import list_sections

    async def _rows(table, filters=None, **kwargs):
        if table == "corporate_sections":
            return [
                {"id": "sec1", "company_id": _COMPANY_ID, "name": "Showroom", "monthly_budget_cap": "500.00"},
                {"id": "sec2", "company_id": _COMPANY_ID, "name": "Service", "monthly_budget_cap": None},
            ]
        if table == "corporate_members":
            return []
        return []

    with (
        patch(_CCB + "db_supabase.get_rows", AsyncMock(side_effect=_rows)),
        patch(
            _CCB + "db_supabase.get_section_spend_map",
            AsyncMock(return_value={"sec1": Decimal("123.45")}),
        ) as m_spend,
    ):
        result = await list_sections(_ADMIN_CTX)

    by_id = {s["id"]: s for s in result["sections"]}
    assert by_id["sec1"]["budget_spend_used"] == "123.45"
    # sec2 has no rows in the spend map (no settled rides this month yet) —
    # must default to "0.00", never be dropped or raise a KeyError.
    assert by_id["sec2"]["budget_spend_used"] == "0.00"
    assert by_id["sec1"]["budget_month"] == by_id["sec2"]["budget_month"]
    m_spend.assert_awaited_once()
    call_args = m_spend.call_args.args
    assert set(call_args[0]) == {"sec1", "sec2"}


@pytest.mark.anyio
async def test_list_sections_handles_zero_sections():
    """With no sections yet, get_section_spend_map is called with an empty
    id list — the repo function itself short-circuits before any DB call
    (round2-26), so this is cheap, not a real N+1 concern."""
    from backend.routes.corporate_company_bookings import list_sections

    async def _rows(table, filters=None, **kwargs):
        return []

    with (
        patch(_CCB + "db_supabase.get_rows", AsyncMock(side_effect=_rows)),
        patch(_CCB + "db_supabase.get_section_spend_map", AsyncMock(return_value={})) as m_spend,
    ):
        result = await list_sections(_ADMIN_CTX)

    assert result["sections"] == []
    m_spend.assert_awaited_once_with([], m_spend.call_args.args[1])
