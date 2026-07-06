"""Sections (company departments) — CRUD authz + tenancy scoping.

Handlers are called directly with an explicit ctx (what the company guards
return), so these tests pin the tenancy rules without the HTTP layer.

Run:
    pytest backend/tests/test_corporate_sections.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

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
async def test_create_section_scopes_to_company():
    from backend.routes.corporate_company_bookings import SectionCreate, create_section

    with patch(_CCB + "db_supabase.insert_one", AsyncMock(side_effect=lambda t, row: row)) as ins:
        row = await create_section(SectionCreate(name="  Service Department "), _ADMIN_CTX)

    assert ins.call_args.args[0] == "corporate_sections"
    assert row["company_id"] == _COMPANY_ID
    assert row["name"] == "Service Department"
    assert row["status"] == "active"
    assert row["created_by"] == "user_admin"


def test_blank_section_name_rejected_by_schema():
    """Codex F11: min_length=1 accepts a spaces-only name; the field_validator
    must reject it (422 at the API) so it never persists as ''."""
    import pydantic

    from backend.routes.corporate_company_bookings import SectionCreate, SectionUpdate

    for blank in ("   ", "\t", "\n "):
        with pytest.raises(pydantic.ValidationError):
            SectionCreate(name=blank)
        with pytest.raises(pydantic.ValidationError):
            SectionUpdate(name=blank)
    # Valid names trim and pass; SectionUpdate(name=None) is allowed (no-op).
    assert SectionCreate(name="  Showroom ").name == "Showroom"
    assert SectionUpdate(name=None).name is None


@pytest.mark.anyio
async def test_create_section_duplicate_name_is_409():
    from backend.routes.corporate_company_bookings import SectionCreate, create_section

    with patch(
        _CCB + "db_supabase.insert_one",
        AsyncMock(
            side_effect=Exception('duplicate key value violates unique constraint "corp_sections_company_name_unique"')
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await create_section(SectionCreate(name="Showroom"), _ADMIN_CTX)
    assert exc_info.value.status_code == 409


@pytest.mark.anyio
async def test_update_section_cross_company_is_404():
    from backend.routes.corporate_company_bookings import SectionUpdate, update_section

    # The company-scoped lookup returns nothing for another company's section.
    with patch(_CCB + "db_supabase.get_rows", AsyncMock(return_value=[])):
        with pytest.raises(HTTPException) as exc_info:
            await update_section("sec_other_co", SectionUpdate(name="X"), _ADMIN_CTX)
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_archive_never_deletes():
    from backend.routes.corporate_company_bookings import archive_section

    section = {"id": "sec1", "company_id": _COMPANY_ID, "name": "Showroom", "status": "active"}
    with (
        patch(_CCB + "db_supabase.get_rows", AsyncMock(return_value=[section])),
        patch(
            _CCB + "db_supabase.update_one",
            AsyncMock(return_value={**section, "status": "archived"}),
        ) as upd,
    ):
        row = await archive_section("sec1", _ADMIN_CTX)

    assert row["status"] == "archived"
    assert upd.call_args.args[2] == {"status": "archived"}, "archive is a status flip, not a delete"


@pytest.mark.anyio
async def test_list_sections_reports_member_counts():
    from backend.routes.corporate_company_bookings import list_sections

    async def _rows(table, filters=None, **kwargs):
        if table == "corporate_sections":
            return [
                {"id": "sec1", "company_id": _COMPANY_ID, "name": "Showroom", "status": "active"},
                {"id": "sec2", "company_id": _COMPANY_ID, "name": "Service", "status": "active"},
            ]
        if table == "corporate_members":
            return [
                {"id": "m1", "section_id": "sec1"},
                {"id": "m2", "section_id": "sec1"},
                {"id": "m3", "section_id": None},
            ]
        return []

    with patch(_CCB + "db_supabase.get_rows", AsyncMock(side_effect=_rows)):
        result = await list_sections(_ADMIN_CTX)

    counts = {s["id"]: s["member_count"] for s in result["sections"]}
    assert counts == {"sec1": 2, "sec2": 0}


@pytest.mark.anyio
async def test_member_section_assignment_validates_company():
    """PATCH /members/{id} with a section from another company must 404 —
    tenancy violation, not a typo."""
    from backend.routes.corporate_company import update_member
    from backend.schemas.corporate import MemberUpdate

    member = {"id": "m1", "company_id": _COMPANY_ID, "role": "member", "status": "active"}
    with (
        patch(
            "backend.routes.corporate_company.get_corporate_member_by_id",
            AsyncMock(return_value=member),
        ),
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=[])),  # section not in company
        patch("backend.routes.corporate_company.update_corporate_member", AsyncMock()) as upd,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await update_member(_COMPANY_ID, "m1", MemberUpdate(section_id="sec_foreign"), guard=_ADMIN_CTX)
    assert exc_info.value.status_code == 404
    upd.assert_not_called()


@pytest.mark.anyio
async def test_member_section_empty_string_clears():
    from backend.routes.corporate_company import update_member
    from backend.schemas.corporate import MemberUpdate

    member = {"id": "m1", "company_id": _COMPANY_ID, "role": "member", "status": "active"}
    with (
        patch(
            "backend.routes.corporate_company.get_corporate_member_by_id",
            AsyncMock(return_value=member),
        ),
        patch(
            "backend.routes.corporate_company.update_corporate_member",
            AsyncMock(return_value={**member, "section_id": None}),
        ) as upd,
    ):
        await update_member(_COMPANY_ID, "m1", MemberUpdate(section_id=""), guard=_ADMIN_CTX)

    assert upd.call_args.args[1] == {"section_id": None}, "empty string is the explicit unassign"
