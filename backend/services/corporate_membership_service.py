"""Membership state machine — invite issuance, acceptance, domain auto-match.

Invite flow:
    admin calls invite_member → row (status='invited', token=<32b>)
    user opens deep link → accept_invite(token) → row (status='active', user_id=<uid>)

Domain auto-match flow:
    rider app calls auto_match_by_email → returns companies where the
    rider's email domain is in corporate_allowed_domains AND the company
    is active. The rider app surfaces a confirm prompt; confirmation
    routes to join_via_domain.
"""

from __future__ import annotations

import secrets
from typing import Any, Dict, List, Tuple

try:
    from ..db_supabase import (  # type: ignore
        accept_member_invite,
        create_active_member,
        find_companies_by_email_domain,
        get_corporate_account_by_id,
        get_member_by_invite_token,
        insert_corporate_member_invite,
        list_active_memberships_for_user,
    )
except ImportError:
    from db_supabase import (  # type: ignore
        accept_member_invite,
        create_active_member,
        find_companies_by_email_domain,
        get_corporate_account_by_id,
        get_member_by_invite_token,
        insert_corporate_member_invite,
        list_active_memberships_for_user,
    )


class InviteNotFound(Exception):
    pass


class InviteAlreadyConsumed(Exception):
    pass


_DEEP_LINK_BASE = "app://join"


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


async def invite_member(
    *,
    company_id: str,
    email: str,
    role: str = "member",
    invited_by: str,
    policy_override: bool = False,
) -> Tuple[Dict[str, Any], str]:
    """Create an invited membership + return (row, deep-link url)."""
    token = _generate_token()
    row = await insert_corporate_member_invite(
        company_id=company_id,
        email=email,
        role=role,
        invite_token=token,
        invited_by=invited_by,
        policy_override=policy_override,
    )
    return row, f"{_DEEP_LINK_BASE}?token={token}"


async def accept_invite(*, token: str, user_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Look up token, flip invited→active. Returns (company, member)."""
    member = await get_member_by_invite_token(token)
    if not member:
        raise InviteNotFound("invite token not found")
    if member.get("status") != "invited":
        raise InviteAlreadyConsumed("invite already accepted or cancelled")
    updated = await accept_member_invite(member_id=member["id"], user_id=user_id)
    if not updated:
        raise InviteAlreadyConsumed("invite was just consumed")
    company = await get_corporate_account_by_id(member["company_id"])
    return company or {}, updated


async def auto_match_by_email(*, user_id: str, email: str) -> List[Dict[str, Any]]:
    """Return active companies that allow this rider's email domain AND
    haven't already enrolled them.
    """
    at = (email or "").rfind("@")
    if at < 0:
        return []
    domain = email[at + 1 :].strip().lower()
    if not domain:
        return []
    raw = await find_companies_by_email_domain(domain)
    existing = {m["company_id"] for m in await list_active_memberships_for_user(user_id)}
    matches = []
    for r in raw:
        company = r.get("corporate_accounts") or {}
        cid = company.get("id") or r.get("company_id")
        if cid and cid not in existing:
            matches.append({"company": company})
    return matches


async def join_via_domain(
    *,
    company_id: str,
    user_id: str,
    email: str,
) -> Dict[str, Any]:
    """Create an active membership in one shot (no token round-trip).

    Reused on the rider app's in-app "Join Acme Corp?" confirmation. We still
    write a corporate_members row, but skip the invited-status step because
    the domain match is itself proof of employment.
    """
    token = _generate_token()
    member = await insert_corporate_member_invite(
        company_id=company_id,
        email=email,
        role="member",
        invite_token=token,
        invited_by=user_id,
    )
    updated = await accept_member_invite(member_id=member["id"], user_id=user_id)
    return updated or member


async def bootstrap_owner(
    *,
    company_id: str,
    email: str,
    user_id: str | None = None,
    invited_by: str | None = None,
) -> Tuple[Dict[str, Any], str | None]:
    """Seed a brand-new company with its first (owner) member.

    Closes the owner-bootstrap gap: previously a freshly created company had
    ZERO members and the first coordinator had to be reached by a manually
    delivered invite link. Two modes:

    * ``user_id`` provided (self-serve signup — the authenticated,
      email-verified creator): insert a directly-ACTIVE owner membership.
      Returns ``(member, None)`` — no invite link needed.
    * ``user_id`` absent (staff-created company with an ``owner_email``):
      fall back to the normal invite flow with role='owner'. Returns
      ``(member, invite_url)`` for delivery.

    Idempotent for the active path: if the user already holds an active
    membership in this company (e.g. a retried signup request), the existing
    row is returned instead of violating corp_members_company_user_unique.
    """
    if user_id:
        existing = await list_active_memberships_for_user(user_id)
        for m in existing:
            if m.get("company_id") == company_id:
                return m, None
        member = await create_active_member(
            company_id=company_id,
            user_id=user_id,
            email=email,
            role="owner",
            invited_by=invited_by or user_id,
        )
        return member, None

    member, invite_url = await invite_member(
        company_id=company_id,
        email=email,
        role="owner",
        invited_by=invited_by or "staff",
    )
    return member, invite_url
