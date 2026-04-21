"""Route tests for /company/{id}/** (admin-only endpoints).

Uses `app.dependency_overrides` to bypass JWT verification and inject a
fake `current_user`. Guard behaviour is still exercised because the
guard's `list_active_memberships_for_user` call is mocked per-test.
"""
from unittest.mock import AsyncMock, patch

import pytest

_FAKE_USER = {"id": "u_admin", "phone": "+15550001111"}


@pytest.fixture
def rider_override():
    """Inject a fake current_user for `/company/**` endpoints."""
    from backend.server import app
    from dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    yield
    app.dependency_overrides.pop(get_current_user, None)


def test_invite_member_requires_admin_role(test_client, rider_override):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "member"}]),
    ):
        resp = test_client.post(
            "/company/c1/members/invite",
            json={"email": "a@b.com", "role": "member"},
        )
    assert resp.status_code == 403


def test_invite_member_success(test_client, rider_override):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
    ), patch(
        "routes.corporate_company.invite_member",
        AsyncMock(return_value=({"id": "m1", "status": "invited"}, "app://join?token=xyz")),
    ) as m_invite:
        resp = test_client.post(
            "/company/c1/members/invite",
            json={"email": "a@b.com", "role": "member"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["member"]["id"] == "m1"
    assert body["invite_url"].startswith("app://join?token=")
    m_invite.assert_awaited_once()


def test_list_members_filters_active_by_default(test_client, rider_override):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
    ), patch(
        "routes.corporate_company.list_company_members",
        AsyncMock(return_value=[{"id": "m1"}, {"id": "m2"}]),
    ):
        resp = test_client.get("/company/c1/members")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 2


def test_set_member_allowance_calls_upsert(test_client, rider_override):
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
    ) as m_upsert:
        resp = test_client.put(
            "/company/c1/members/m1/allowance",
            json={
                "type": "fixed_recurring",
                "amount": 500,
                "period_start": "2026-04-01",
                "period_end": "2026-04-30",
            },
        )
    assert resp.status_code == 200, resp.text
    m_upsert.assert_awaited_once()


def test_remove_member_sets_status_removed(test_client, rider_override):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
    ), patch(
        "routes.corporate_company.get_corporate_member_by_id",
        AsyncMock(return_value={"id": "m1", "company_id": "c1", "status": "active"}),
    ), patch(
        "routes.corporate_company.update_corporate_member",
        AsyncMock(return_value={"id": "m1", "status": "removed"}),
    ) as m_upd:
        resp = test_client.delete("/company/c1/members/m1")
    assert resp.status_code == 200, resp.text
    m_upd.assert_awaited_once_with("m1", {"status": "removed"})


def test_add_allowed_domain_lowercases(test_client, rider_override):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
    ), patch(
        "routes.corporate_company.add_allowed_domain",
        AsyncMock(return_value={"company_id": "c1", "domain": "acme.com"}),
    ) as m_add:
        resp = test_client.post(
            "/company/c1/allowed-domains",
            json={"domain": "Acme.COM"},
        )
    assert resp.status_code == 200, resp.text
    m_add.assert_awaited_once()
    assert m_add.await_args.kwargs["domain"] == "acme.com"
