"""Schema tests for Plan 3 (members, allowances, requests, domains)."""

from datetime import date

import pytest
from pydantic import ValidationError

from schemas.corporate import (
    AllowanceCreate,
    AllowanceRequestCreate,
    AllowanceType,
    AllowedDomainCreate,
    MemberInvite,
    MemberRole,
)


def test_member_invite_normalizes_email():
    m = MemberInvite(email="  Alice@Acme.COM ", role="member")
    assert m.email == "alice@acme.com"
    assert m.role == MemberRole.MEMBER


def test_member_invite_rejects_bad_role():
    with pytest.raises(ValidationError):
        MemberInvite(email="a@b.com", role="superuser")


def test_allowance_create_fixed_recurring_requires_amount_and_period():
    with pytest.raises(ValidationError):
        AllowanceCreate(type=AllowanceType.FIXED_RECURRING)
    ok = AllowanceCreate(
        type=AllowanceType.FIXED_RECURRING,
        amount=500,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )
    assert ok.amount == 500


def test_allowance_create_fixed_recurring_rejects_inverted_period():
    with pytest.raises(ValidationError):
        AllowanceCreate(
            type=AllowanceType.FIXED_RECURRING,
            amount=500,
            period_start=date(2026, 4, 30),
            period_end=date(2026, 4, 1),
        )


def test_allowance_create_unlimited_forbids_amount():
    with pytest.raises(ValidationError):
        AllowanceCreate(type=AllowanceType.UNLIMITED, amount=100)
    ok = AllowanceCreate(type=AllowanceType.UNLIMITED)
    assert ok.amount is None


def test_allowance_create_one_time_requires_amount():
    with pytest.raises(ValidationError):
        AllowanceCreate(type=AllowanceType.ONE_TIME)
    ok = AllowanceCreate(type=AllowanceType.ONE_TIME, amount=200)
    assert ok.amount == 200


def test_allowed_domain_normalizes_and_rejects_at_prefix():
    d = AllowedDomainCreate(domain="  Acme.COM ")
    assert d.domain == "acme.com"
    with pytest.raises(ValidationError):
        AllowedDomainCreate(domain="@acme.com")
    with pytest.raises(ValidationError):
        AllowedDomainCreate(domain="nodot")


def test_allowance_request_caps_amount():
    with pytest.raises(ValidationError):
        AllowanceRequestCreate(amount=0, reason="none")
    with pytest.raises(ValidationError):
        AllowanceRequestCreate(amount=10001, reason="excessive")
    ok = AllowanceRequestCreate(amount=150, reason="client lunch")
    assert ok.amount == 150
