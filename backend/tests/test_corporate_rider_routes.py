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


def test_accept_invite_returns_409_when_already_consumed(test_client, rider_override):
    from services.corporate_membership_service import InviteAlreadyConsumed

    with patch(
        "routes.corporate_rider.accept_invite",
        AsyncMock(side_effect=InviteAlreadyConsumed("used")),
    ):
        resp = test_client.post(
            "/rider/work-profile/accept-invite",
            json={"token": "tok"},
        )
    assert resp.status_code == 409


def test_balance_404s_when_not_a_member(test_client, rider_override):
    with patch(
        "routes.corporate_rider.list_active_memberships_for_user",
        AsyncMock(return_value=[{"id": "m1", "company_id": "other-co", "role": "member"}]),
    ):
        resp = test_client.get("/rider/work-profile/c1/balance")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "not a company member"


def test_balance_unlimited_allowance_has_no_remaining(test_client, rider_override):
    with (
        patch(
            "routes.corporate_rider.list_active_memberships_for_user",
            AsyncMock(return_value=[{"id": "m1", "company_id": "c1", "role": "member"}]),
        ),
        patch(
            "routes.corporate_rider.get_member_allowance",
            AsyncMock(return_value={"id": "a1", "type": "unlimited"}),
        ),
        patch(
            "routes.corporate_rider.get_corporate_account_by_id",
            AsyncMock(return_value={"id": "c1", "name": "Acme"}),
        ),
    ):
        resp = test_client.get("/rider/work-profile/c1/balance")
    assert resp.status_code == 200, resp.text
    assert resp.json()["remaining"] is None


def test_balance_missing_amount_has_no_remaining(test_client, rider_override):
    with (
        patch(
            "routes.corporate_rider.list_active_memberships_for_user",
            AsyncMock(return_value=[{"id": "m1", "company_id": "c1", "role": "member"}]),
        ),
        patch(
            "routes.corporate_rider.get_member_allowance",
            AsyncMock(return_value={"id": "a1", "type": "fixed_recurring", "amount": None}),
        ),
        patch(
            "routes.corporate_rider.get_corporate_account_by_id",
            AsyncMock(return_value={"id": "c1", "name": "Acme"}),
        ),
    ):
        resp = test_client.get("/rider/work-profile/c1/balance")
    assert resp.status_code == 200, resp.text
    assert resp.json()["remaining"] is None


def test_join_domain_rejects_unverified_email(test_client, rider_override):
    from backend.server import app
    from dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: {
        "id": "u1",
        "email_verified": False,
    }
    try:
        resp = test_client.post(
            "/rider/work-profile/join-domain",
            json={"company_id": "c1", "email": "a@acme.com"},
        )
    finally:
        app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    assert resp.status_code == 403
    assert resp.json()["detail"] == "ERR_EMAIL_UNVERIFIED"


def test_join_domain_rejects_account_with_no_email(test_client, rider_override):
    from backend.server import app
    from dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: {
        "id": "u1",
        "email_verified": True,
        "phone_or_email": "+15550002222",
    }
    try:
        resp = test_client.post(
            "/rider/work-profile/join-domain",
            json={"company_id": "c1", "email": "a@acme.com"},
        )
    finally:
        app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    assert resp.status_code == 400


def test_join_domain_rejects_unauthorized_domain(test_client, rider_override):
    from backend.server import app
    from dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: {
        "id": "u1",
        "email_verified": True,
        "email": "alice@notacme.com",
    }
    try:
        with patch(
            "routes.corporate_rider.get_rows",
            AsyncMock(return_value=[]),
        ):
            resp = test_client.post(
                "/rider/work-profile/join-domain",
                json={"company_id": "c1", "email": "alice@notacme.com"},
            )
    finally:
        app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Your email domain is not authorized for this company"


def test_join_domain_succeeds(test_client, rider_override):
    from backend.server import app
    from dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: {
        "id": "u1",
        "email_verified": True,
        "email": "alice@acme.com",
    }
    try:
        with (
            patch(
                "routes.corporate_rider.get_rows",
                AsyncMock(return_value=[{"company_id": "c1", "domain": "acme.com"}]),
            ),
            patch(
                "routes.corporate_rider.join_via_domain",
                AsyncMock(return_value={"id": "m1", "status": "active"}),
            ),
            patch(
                "routes.corporate_rider.get_corporate_account_by_id",
                AsyncMock(return_value={"id": "c1", "name": "Acme"}),
            ),
        ):
            resp = test_client.post(
                "/rider/work-profile/join-domain",
                json={"company_id": "c1", "email": "alice@acme.com"},
            )
    finally:
        app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    assert resp.status_code == 200, resp.text
    assert resp.json()["company"]["name"] == "Acme"
    assert resp.json()["member"]["status"] == "active"


def test_my_rides_returns_empty_when_no_payment_sources(test_client, rider_override):
    with (
        patch(
            "routes.corporate_rider.list_active_memberships_for_user",
            AsyncMock(return_value=[{"id": "m1", "company_id": "c1", "role": "member"}]),
        ),
        patch(
            "routes.corporate_rider.get_rows",
            AsyncMock(return_value=[]),
        ),
    ):
        resp = test_client.get("/rider/work-profile/c1/rides")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_my_rides_joins_rides_and_applies_to_date_filter(test_client, rider_override):
    rps_rows = [
        {"ride_id": "r1", "created_at": "2026-04-01T00:00:00"},
        {"ride_id": "r2", "created_at": "2026-04-10T00:00:00"},
    ]
    rides_rows = [
        {"id": "r1", "status": "completed"},
        {"id": "r2", "status": "completed"},
    ]

    async def fake_get_rows(table, filters, limit=100):
        if table == "ride_payment_sources":
            return rps_rows
        if table == "rides":
            return rides_rows
        return []

    with (
        patch(
            "routes.corporate_rider.list_active_memberships_for_user",
            AsyncMock(return_value=[{"id": "m1", "company_id": "c1", "role": "member"}]),
        ),
        patch(
            "routes.corporate_rider.get_rows",
            AsyncMock(side_effect=fake_get_rows),
        ),
    ):
        resp = test_client.get("/rider/work-profile/c1/rides?from_=2026-03-01T00:00:00&to=2026-04-05T00:00:00")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["id"] == "r1"
    assert rows[0]["payment_source"]["ride_id"] == "r1"


def test_allowance_request_auto_approves_within_cap(test_client, rider_override):
    with (
        patch(
            "routes.corporate_rider.list_active_memberships_for_user",
            AsyncMock(return_value=[{"id": "m1", "company_id": "c1", "role": "member"}]),
        ),
        patch(
            "routes.corporate_rider.list_pending_allowance_requests_for_member",
            AsyncMock(return_value=[]),
        ),
        patch(
            "routes.corporate_rider.get_member_allowance",
            AsyncMock(
                return_value={
                    "id": "a1",
                    "auto_approve_topup_amount": 200,
                    "auto_approve_monthly_count": 2,
                    "auto_approved_this_period": 0,
                }
            ),
        ),
        patch(
            "routes.corporate_rider.insert_allowance_request",
            AsyncMock(return_value={"id": "r1", "status": "auto_approved"}),
        ),
        patch(
            "routes.corporate_rider.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1", "soft_negative_floor": -50}),
        ),
        patch(
            "routes.corporate_rider.apply_grant",
            AsyncMock(return_value={"ok": True}),
        ) as apply_grant_mock,
    ):
        resp = test_client.post(
            "/rider/work-profile/c1/allowance-requests",
            json={"amount": 100, "reason": "client dinner"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "auto_approved"
    apply_grant_mock.assert_awaited_once()


def test_allowance_request_falls_back_to_pending_over_cap(test_client, rider_override):
    with (
        patch(
            "routes.corporate_rider.list_active_memberships_for_user",
            AsyncMock(return_value=[{"id": "m1", "company_id": "c1", "role": "member"}]),
        ),
        patch(
            "routes.corporate_rider.list_pending_allowance_requests_for_member",
            AsyncMock(return_value=[]),
        ),
        patch(
            "routes.corporate_rider.get_member_allowance",
            AsyncMock(
                return_value={
                    "id": "a1",
                    "auto_approve_topup_amount": 50,
                    "auto_approve_monthly_count": 2,
                    "auto_approved_this_period": 0,
                }
            ),
        ),
        patch(
            "routes.corporate_rider.insert_allowance_request",
            AsyncMock(return_value={"id": "r1", "status": "pending"}),
        ) as insert_mock,
    ):
        resp = test_client.post(
            "/rider/work-profile/c1/allowance-requests",
            json={"amount": 100, "reason": "client dinner"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "pending"
    insert_mock.assert_awaited_once_with(member_id="m1", amount=100, reason="client dinner", status="pending")


def test_my_requests_returns_list_scoped_to_membership(test_client, rider_override):
    with (
        patch(
            "routes.corporate_rider.list_active_memberships_for_user",
            AsyncMock(return_value=[{"id": "m1", "company_id": "c1", "role": "member"}]),
        ),
        patch(
            "routes.corporate_rider.list_company_allowance_requests",
            AsyncMock(return_value=[{"id": "r1", "status": "pending"}]),
        ) as list_mock,
    ):
        resp = test_client.get("/rider/work-profile/c1/allowance-requests")
    assert resp.status_code == 200, resp.text
    assert resp.json() == [{"id": "r1", "status": "pending"}]
    list_mock.assert_awaited_once_with("c1", statuses=None, member_id="m1")


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
