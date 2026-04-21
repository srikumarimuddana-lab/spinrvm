"""End-to-end happy path for the Plan-3 member lifecycle.

Walks the full flow through the route layer with DB and service calls
mocked. Catches integration drift between routes / services / schemas.

Follows the same dependency-override + route-local-binding patch pattern
used by the other Plan-3 route tests (see
`test_corporate_company_routes.py`, `test_corporate_rider_routes.py`).
"""
from unittest.mock import AsyncMock, patch

import pytest

_FAKE_USER = {"id": "u1", "phone": "+15550009999"}


@pytest.fixture
def user_override():
    """Same current_user for admin + rider endpoints — guard/auth distinction
    is made via the membership list mocked per-step."""
    from backend.server import app
    from dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    yield
    app.dependency_overrides.pop(get_current_user, None)


def test_full_member_lifecycle(test_client, user_override):
    # 1) Admin invites member.
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
    ), patch(
        "routes.corporate_company.invite_member",
        AsyncMock(return_value=(
            {"id": "m1", "status": "invited", "invite_token": "tok"},
            "app://join?token=tok",
        )),
    ):
        r = test_client.post(
            "/company/c1/members/invite",
            json={"email": "alice@acme.com", "role": "member"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["invite_url"].startswith("app://join?token=")

    # 2) Rider accepts invite.
    with patch(
        "routes.corporate_rider.accept_invite",
        AsyncMock(return_value=(
            {"id": "c1", "name": "Acme"},
            {"id": "m1", "status": "active", "user_id": "u1"},
        )),
    ):
        r = test_client.post(
            "/rider/work-profile/accept-invite",
            json={"token": "tok"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["member"]["status"] == "active"

    # 3) Admin sets fixed_recurring allowance.
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
    ), patch(
        "routes.corporate_company.get_corporate_member_by_id",
        AsyncMock(return_value={"id": "m1", "company_id": "c1"}),
    ), patch(
        "routes.corporate_company.upsert_member_allowance",
        AsyncMock(return_value={
            "id": "a1", "member_id": "m1", "type": "fixed_recurring",
            "amount": 500, "used": 0,
        }),
    ):
        r = test_client.put(
            "/company/c1/members/m1/allowance",
            json={
                "type": "fixed_recurring", "amount": 500,
                "period_start": "2026-04-01", "period_end": "2026-04-30",
            },
        )
    assert r.status_code == 200, r.text

    # 4) Rider views balance.
    with patch(
        "routes.corporate_rider.list_active_memberships_for_user",
        AsyncMock(return_value=[{"id": "m1", "company_id": "c1", "role": "member"}]),
    ), patch(
        "routes.corporate_rider.get_member_allowance",
        AsyncMock(return_value={
            "id": "a1", "member_id": "m1", "type": "fixed_recurring",
            "amount": 500, "used": 0, "period_end": "2026-04-30",
            "status": "active",
        }),
    ), patch(
        "routes.corporate_rider.get_corporate_account_by_id",
        AsyncMock(return_value={"id": "c1", "name": "Acme"}),
    ):
        r = test_client.get("/rider/work-profile/c1/balance")
    assert r.status_code == 200, r.text
    assert r.json()["remaining"] == 500

    # 5) Rider submits a manual allowance request (no auto-approve rule).
    with patch(
        "routes.corporate_rider.list_active_memberships_for_user",
        AsyncMock(return_value=[{"id": "m1", "company_id": "c1", "role": "member"}]),
    ), patch(
        "routes.corporate_rider.get_member_allowance",
        AsyncMock(return_value={"id": "a1", "auto_approve_topup_amount": None}),
    ), patch(
        "routes.corporate_rider.list_pending_allowance_requests_for_member",
        AsyncMock(return_value=[]),
    ), patch(
        "routes.corporate_rider.insert_allowance_request",
        AsyncMock(return_value={
            "id": "r1", "member_id": "m1", "amount": 100,
            "reason": "client dinner", "status": "pending",
        }),
    ):
        r = test_client.post(
            "/rider/work-profile/c1/allowance-requests",
            json={"amount": 100, "reason": "client dinner"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"

    # 6) Admin approves — grant fires, request flips to approved.
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
    ), patch(
        "routes.corporate_company.get_allowance_request_by_id",
        AsyncMock(return_value={
            "id": "r1", "member_id": "m1", "amount": 100, "status": "pending",
        }),
    ), patch(
        "routes.corporate_company.get_corporate_member_by_id",
        AsyncMock(return_value={"id": "m1", "company_id": "c1"}),
    ), patch(
        "routes.corporate_company.get_member_allowance",
        AsyncMock(return_value={"id": "a1", "member_id": "m1"}),
    ), patch(
        "routes.corporate_company.get_corporate_wallet_by_company",
        AsyncMock(return_value={"id": "w1", "soft_negative_floor": -50}),
    ), patch(
        "routes.corporate_company.apply_grant",
        AsyncMock(return_value={"master_balance_after": 400, "allowance_used_after": -100}),
    ) as m_grant, patch(
        "routes.corporate_company.update_allowance_request",
        AsyncMock(return_value={"id": "r1", "status": "approved"}),
    ):
        r = test_client.post(
            "/company/c1/allowance-requests/r1/decide",
            json={"approve": True, "note": "ok"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    m_grant.assert_awaited_once()

    # 7) Admin removes member.
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
    ), patch(
        "routes.corporate_company.get_corporate_member_by_id",
        AsyncMock(return_value={"id": "m1", "company_id": "c1", "status": "active"}),
    ), patch(
        "routes.corporate_company.update_corporate_member",
        AsyncMock(return_value={"id": "m1", "status": "removed"}),
    ):
        r = test_client.delete("/company/c1/members/m1")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "removed"
