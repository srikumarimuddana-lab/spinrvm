"""Rider-app work-profile route tests.

Use `app.dependency_overrides` to fake `get_current_user`. Patch all DB /
service calls where the route module binds them (not at the source).
"""

from unittest.mock import AsyncMock, patch

import pytest

_FAKE_USER = {"id": "u1", "phone": "+15550002222"}


@pytest.fixture
def rider_override():
    from backend.server import app
    from dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    yield
    app.dependency_overrides.pop(get_current_user, None)


def test_work_profile_lists_active_memberships(test_client, rider_override):
    with (
        patch(
            "routes.corporate_rider.list_active_memberships_for_user",
            AsyncMock(
                return_value=[
                    {"id": "m1", "company_id": "c1", "role": "member"},
                    {"id": "m2", "company_id": "c2", "role": "admin"},
                ]
            ),
        ),
        patch(
            "routes.corporate_rider.get_corporate_account_by_id",
            AsyncMock(
                side_effect=[
                    {"id": "c1", "name": "Acme"},
                    {"id": "c2", "name": "Beta"},
                ]
            ),
        ),
    ):
        resp = test_client.get("/rider/work-profile")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 2
    assert rows[0]["company"]["name"] == "Acme"


def test_auto_match_returns_matches(test_client, rider_override):
    with patch(
        "routes.corporate_rider.auto_match_by_email",
        AsyncMock(return_value=[{"company": {"id": "c1", "name": "Acme"}}]),
    ):
        resp = test_client.get(
            "/rider/work-profile/auto-match?email=alice@acme.com",
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["company"]["name"] == "Acme"


def test_accept_invite_route_returns_company_and_member(test_client, rider_override):
    with patch(
        "routes.corporate_rider.accept_invite",
        AsyncMock(
            return_value=(
                {"id": "c1", "name": "Acme"},
                {"id": "m1", "status": "active"},
            )
        ),
    ):
        resp = test_client.post(
            "/rider/work-profile/accept-invite",
            json={"token": "tok"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["company"]["name"] == "Acme"


def test_accept_invite_returns_404_when_token_not_found(test_client, rider_override):
    from services.corporate_membership_service import InviteNotFound

    with patch(
        "routes.corporate_rider.accept_invite",
        AsyncMock(side_effect=InviteNotFound("nope")),
    ):
        resp = test_client.post(
            "/rider/work-profile/accept-invite",
            json={"token": "tok"},
        )
    assert resp.status_code == 404


def test_balance_returns_remaining(test_client, rider_override):
    with (
        patch(
            "routes.corporate_rider.list_active_memberships_for_user",
            AsyncMock(return_value=[{"id": "m1", "company_id": "c1", "role": "member"}]),
        ),
        patch(
            "routes.corporate_rider.get_member_allowance",
            AsyncMock(
                return_value={
                    "id": "a1",
                    "member_id": "m1",
                    "type": "fixed_recurring",
                    "amount": 500,
                    "used": -120,
                    "period_end": "2026-04-30",
                }
            ),
        ),
        patch(
            "routes.corporate_rider.get_corporate_account_by_id",
            AsyncMock(return_value={"id": "c1", "name": "Acme"}),
        ),
    ):
        resp = test_client.get("/rider/work-profile/c1/balance")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["company_name"] == "Acme"
    assert body["period_end"] == "2026-04-30"


def test_allowance_request_rate_limit_returns_409(test_client, rider_override):
    with (
        patch(
            "routes.corporate_rider.list_active_memberships_for_user",
            AsyncMock(return_value=[{"id": "m1", "company_id": "c1", "role": "member"}]),
        ),
        patch(
            "routes.corporate_rider.list_pending_allowance_requests_for_member",
            AsyncMock(return_value=[{"id": "r0", "status": "pending"}]),
        ),
    ):
        resp = test_client.post(
            "/rider/work-profile/c1/allowance-requests",
            json={"amount": 100, "reason": "client dinner"},
        )
    assert resp.status_code == 409


def test_work_profile_exposes_company_status(test_client, rider_override):
    # M2.4: the portal gates non-active companies onto /verification — that
    # gate is driven by company.status in this response.
    with (
        patch(
            "routes.corporate_rider.list_active_memberships_for_user",
            AsyncMock(return_value=[{"id": "m1", "company_id": "c1", "role": "owner"}]),
        ),
        patch(
            "routes.corporate_rider.get_corporate_account_by_id",
            AsyncMock(return_value={"id": "c1", "name": "Acme", "status": "pending_verification"}),
        ),
    ):
        resp = test_client.get("/rider/work-profile")
    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["company"]["status"] == "pending_verification"
