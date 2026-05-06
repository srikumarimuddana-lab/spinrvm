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
        patch(
            "services.corporate_membership_service.ensure_corporate_wallet",
            AsyncMock(return_value={"id": "w1"}),
        ) as m_wallet,
    ):
        from services.corporate_membership_service import accept_invite

        company, member = await accept_invite(token="tok", user_id="u1")
    assert member["status"] == "active"
    assert company["id"] == "c1"
    m_accept.assert_awaited_once_with(member_id="m1", user_id="u1")
    m_wallet.assert_awaited_once_with(company_id="c1")


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
