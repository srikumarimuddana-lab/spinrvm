"""Owner bootstrap (M1.2) — seeding a new company's first member.

bootstrap_owner has two modes:
  * user_id present (self-serve signup)  → directly-ACTIVE owner row
  * user_id absent  (staff + owner_email) → normal owner invite + link

Repo layer is mocked at the service module's imported names.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.corporate_membership_service as svc


@pytest.mark.asyncio
async def test_self_serve_creates_active_owner():
    created = {"id": "m1", "company_id": "c1", "role": "owner", "status": "active", "user_id": "u1"}
    with (
        patch.object(svc, "list_active_memberships_for_user", AsyncMock(return_value=[])),
        patch.object(svc, "create_active_member", AsyncMock(return_value=created)) as mock_create,
    ):
        member, invite_url = await svc.bootstrap_owner(company_id="c1", email="jane@acme.com", user_id="u1")

    assert member == created
    assert invite_url is None
    mock_create.assert_awaited_once_with(
        company_id="c1", user_id="u1", email="jane@acme.com", role="owner", invited_by="u1"
    )


@pytest.mark.asyncio
async def test_self_serve_is_idempotent_on_existing_membership():
    existing = {"id": "m1", "company_id": "c1", "role": "owner", "status": "active"}
    with (
        patch.object(svc, "list_active_memberships_for_user", AsyncMock(return_value=[existing])),
        patch.object(svc, "create_active_member", AsyncMock()) as mock_create,
    ):
        member, invite_url = await svc.bootstrap_owner(company_id="c1", email="jane@acme.com", user_id="u1")

    assert member == existing
    assert invite_url is None
    mock_create.assert_not_awaited()  # no duplicate insert → no unique-index violation


@pytest.mark.asyncio
async def test_staff_path_falls_back_to_owner_invite():
    invited = {"id": "m2", "company_id": "c1", "role": "owner", "status": "invited"}
    with patch.object(svc, "insert_corporate_member_invite", AsyncMock(return_value=invited)) as mock_invite:
        member, invite_url = await svc.bootstrap_owner(company_id="c1", email="owner@acme.com", invited_by="admin-001")

    assert member == invited
    assert invite_url is not None and "token=" in invite_url
    kwargs = mock_invite.await_args.kwargs
    assert kwargs["role"] == "owner"
    assert kwargs["invited_by"] == "admin-001"


@pytest.mark.asyncio
async def test_create_active_member_writes_active_row():
    fake = MagicMock()
    fake.data = [{"id": "m3", "company_id": "c1", "status": "active", "role": "owner"}]
    with patch("repositories.corporate_repo.supabase") as mock_sb:
        mock_sb.table.return_value.insert.return_value.execute.return_value = fake
        from db_supabase import create_active_member

        row = await create_active_member(company_id="c1", user_id="u1", email="jane@acme.com")

    assert row["status"] == "active"
    mock_sb.table.assert_called_with("corporate_members")
    inserted = mock_sb.table.return_value.insert.call_args.args[0]
    assert inserted["status"] == "active"
    assert inserted["role"] == "owner"
    assert inserted["user_id"] == "u1"
    assert inserted["joined_at"]  # stamped immediately — no invite round-trip
