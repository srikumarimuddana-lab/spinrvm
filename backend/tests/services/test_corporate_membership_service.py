"""Membership service tests — invites, acceptance, domain auto-match."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_invite_member_generates_token_and_calls_insert():
    with patch(
        "services.corporate_membership_service.insert_corporate_member_invite",
        AsyncMock(return_value={"id": "m1"}),
    ) as m_ins:
        from services.corporate_membership_service import invite_member

        member, url = await invite_member(
            company_id="c1",
            email="a@b.com",
            role="member",
            invited_by="admin1",
        )
    assert member["id"] == "m1"
    assert "token=" in url
    args = m_ins.await_args.kwargs
    assert args["email"] == "a@b.com"
    assert len(args["invite_token"]) >= 32


@pytest.mark.asyncio
async def test_accept_invite_activates_and_stamps_user_id():
    with (
        patch(
            "services.corporate_membership_service.get_member_by_invite_token",
            AsyncMock(return_value={"id": "m1", "company_id": "c1", "status": "invited"}),
        ),
        patch(
            "services.corporate_membership_service.accept_member_invite",
            AsyncMock(return_value={"id": "m1", "status": "active", "user_id": "u1"}),
        ) as m_accept,
        patch(
            "services.corporate_membership_service.get_corporate_account_by_id",
            AsyncMock(return_value={"id": "c1", "name": "Acme"}),
        ),
    ):
        from services.corporate_membership_service import accept_invite

        company, member = await accept_invite(token="tok", user_id="u1")
    assert member["status"] == "active"
    assert company["id"] == "c1"
    m_accept.assert_awaited_once_with(member_id="m1", user_id="u1")


@pytest.mark.asyncio
async def test_accept_invite_raises_on_missing_token():
    with patch(
        "services.corporate_membership_service.get_member_by_invite_token",
        AsyncMock(return_value=None),
    ):
        from services.corporate_membership_service import InviteNotFound, accept_invite

        with pytest.raises(InviteNotFound):
            await accept_invite(token="tok", user_id="u1")


@pytest.mark.asyncio
async def test_accept_invite_raises_if_already_consumed():
    with patch(
        "services.corporate_membership_service.get_member_by_invite_token",
        AsyncMock(return_value={"id": "m1", "status": "active"}),
    ):
        from services.corporate_membership_service import (
            InviteAlreadyConsumed,
            accept_invite,
        )

        with pytest.raises(InviteAlreadyConsumed):
            await accept_invite(token="tok", user_id="u1")


@pytest.mark.asyncio
async def test_accept_invite_raises_when_concurrently_consumed():
    """accept_member_invite returns falsy when a race consumed the invite
    between the lookup and the update (0 rows updated)."""
    with (
        patch(
            "services.corporate_membership_service.get_member_by_invite_token",
            AsyncMock(return_value={"id": "m1", "company_id": "c1", "status": "invited"}),
        ),
        patch(
            "services.corporate_membership_service.accept_member_invite",
            AsyncMock(return_value=None),
        ),
    ):
        from services.corporate_membership_service import (
            InviteAlreadyConsumed,
            accept_invite,
        )

        with pytest.raises(InviteAlreadyConsumed):
            await accept_invite(token="tok", user_id="u1")


@pytest.mark.asyncio
async def test_auto_match_returns_empty_when_no_at_sign():
    from services.corporate_membership_service import auto_match_by_email

    matches = await auto_match_by_email(user_id="u1", email="not-an-email")
    assert matches == []


@pytest.mark.asyncio
async def test_auto_match_returns_empty_when_domain_blank():
    from services.corporate_membership_service import auto_match_by_email

    matches = await auto_match_by_email(user_id="u1", email="bob@")
    assert matches == []


@pytest.mark.asyncio
async def test_auto_match_excludes_companies_user_already_joined():
    with (
        patch(
            "services.corporate_membership_service.find_companies_by_email_domain",
            AsyncMock(
                return_value=[
                    {"company_id": "c1", "corporate_accounts": {"id": "c1", "name": "Acme"}},
                ]
            ),
        ),
        patch(
            "services.corporate_membership_service.list_active_memberships_for_user",
            AsyncMock(return_value=[{"company_id": "c1"}]),
        ),
    ):
        from services.corporate_membership_service import auto_match_by_email

        matches = await auto_match_by_email(user_id="u1", email="bob@acme.com")
    assert matches == []


@pytest.mark.asyncio
async def test_join_via_domain_creates_and_activates_membership():
    with (
        patch(
            "services.corporate_membership_service.insert_corporate_member_invite",
            AsyncMock(return_value={"id": "m1", "company_id": "c1"}),
        ) as m_ins,
        patch(
            "services.corporate_membership_service.accept_member_invite",
            AsyncMock(return_value={"id": "m1", "status": "active", "user_id": "u1"}),
        ) as m_accept,
    ):
        from services.corporate_membership_service import join_via_domain

        member = await join_via_domain(company_id="c1", user_id="u1", email="bob@acme.com")
    assert member["status"] == "active"
    m_ins.assert_awaited_once()
    m_accept.assert_awaited_once_with(member_id="m1", user_id="u1")


@pytest.mark.asyncio
async def test_join_via_domain_falls_back_to_invite_row_when_accept_returns_falsy():
    """If accept_member_invite races and returns nothing, join_via_domain
    should still hand back the freshly inserted member row rather than None."""
    with (
        patch(
            "services.corporate_membership_service.insert_corporate_member_invite",
            AsyncMock(return_value={"id": "m1", "company_id": "c1"}),
        ),
        patch(
            "services.corporate_membership_service.accept_member_invite",
            AsyncMock(return_value=None),
        ),
    ):
        from services.corporate_membership_service import join_via_domain

        member = await join_via_domain(company_id="c1", user_id="u1", email="bob@acme.com")
    assert member == {"id": "m1", "company_id": "c1"}


def test_uuid_or_none_valid_uuid():
    from services.corporate_membership_service import _uuid_or_none

    valid = "12345678-1234-5678-1234-567812345678"
    assert _uuid_or_none(valid) == valid


def test_uuid_or_none_non_uuid_actor_id_returns_none():
    from services.corporate_membership_service import _uuid_or_none

    assert _uuid_or_none("admin-001") is None


def test_uuid_or_none_falsy_returns_none():
    from services.corporate_membership_service import _uuid_or_none

    assert _uuid_or_none(None) is None
    assert _uuid_or_none("") is None


@pytest.mark.asyncio
async def test_bootstrap_owner_self_serve_creates_active_member():
    with (
        patch(
            "services.corporate_membership_service.list_active_memberships_for_user",
            AsyncMock(return_value=[]),
        ),
        patch(
            "services.corporate_membership_service.create_active_member",
            AsyncMock(return_value={"id": "m1", "status": "active", "role": "owner"}),
        ) as m_create,
    ):
        from services.corporate_membership_service import bootstrap_owner

        member, invite_url = await bootstrap_owner(
            company_id="c1", email="owner@acme.com", user_id="u1", invited_by="admin-001"
        )
    assert member["role"] == "owner"
    assert invite_url is None
    # 'admin-001' is not UUID-castable -> _uuid_or_none falls back to user_id
    m_create.assert_awaited_once_with(
        company_id="c1",
        user_id="u1",
        email="owner@acme.com",
        role="owner",
        invited_by="u1",
    )


@pytest.mark.asyncio
async def test_bootstrap_owner_self_serve_is_idempotent_on_existing_membership():
    """Retried signup request: user already has an active membership in this
    company -> return the existing row instead of violating the unique
    constraint via a second insert."""
    existing_row = {"id": "m0", "company_id": "c1", "status": "active"}
    with (
        patch(
            "services.corporate_membership_service.list_active_memberships_for_user",
            AsyncMock(return_value=[existing_row, {"company_id": "other"}]),
        ),
        patch(
            "services.corporate_membership_service.create_active_member",
            AsyncMock(),
        ) as m_create,
    ):
        from services.corporate_membership_service import bootstrap_owner

        member, invite_url = await bootstrap_owner(company_id="c1", email="owner@acme.com", user_id="u1")
    assert member == existing_row
    assert invite_url is None
    m_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_bootstrap_owner_staff_created_falls_back_to_invite_flow():
    with patch(
        "services.corporate_membership_service.insert_corporate_member_invite",
        AsyncMock(return_value={"id": "m1", "role": "owner"}),
    ) as m_ins:
        from services.corporate_membership_service import bootstrap_owner

        member, invite_url = await bootstrap_owner(company_id="c1", email="owner@acme.com", invited_by="admin-001")
    assert member["role"] == "owner"
    assert invite_url is not None and "token=" in invite_url
    args = m_ins.await_args.kwargs
    assert args["role"] == "owner"
    # staff-admin actor id is not UUID-castable -> stored as NULL
    assert args["invited_by"] is None


@pytest.mark.asyncio
async def test_auto_match_filters_active_companies_by_domain():
    with (
        patch(
            "services.corporate_membership_service.find_companies_by_email_domain",
            AsyncMock(
                return_value=[
                    {"company_id": "c1", "corporate_accounts": {"id": "c1", "name": "Acme", "status": "active"}},
                ]
            ),
        ),
        patch(
            "services.corporate_membership_service.list_active_memberships_for_user",
            AsyncMock(return_value=[]),
        ),
    ):
        from services.corporate_membership_service import auto_match_by_email

        matches = await auto_match_by_email(user_id="u1", email="bob@acme.com")
    assert len(matches) == 1
    assert matches[0]["company"]["name"] == "Acme"
