"""Admin approve/deny for allowance requests (Task 8)."""

from unittest.mock import AsyncMock, patch

import pytest

_FAKE_USER = {"id": "u_admin", "phone": "+15550003333"}


@pytest.fixture
def rider_override():
    from backend.server import app
    from dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    yield
    app.dependency_overrides.pop(get_current_user, None)


def test_approve_request_grants_amount(test_client, rider_override):
    with (
        patch(
            "dependencies.company_guard.list_active_memberships_for_user",
            AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
        ),
        patch(
            "routes.corporate_company.get_allowance_request_by_id",
            AsyncMock(
                return_value={
                    "id": "r1",
                    "member_id": "m1",
                    "amount": 200,
                    "status": "pending",
                }
            ),
        ),
        patch(
            "routes.corporate_company.get_corporate_member_by_id",
            AsyncMock(return_value={"id": "m1", "company_id": "c1"}),
        ),
        patch(
            "routes.corporate_company.get_member_allowance",
            AsyncMock(return_value={"id": "a1", "member_id": "m1"}),
        ),
        patch(
            "routes.corporate_company.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1", "soft_negative_floor": -50}),
        ),
        patch(
            "routes.corporate_company.apply_grant",
            AsyncMock(return_value={"master_balance_after": 800, "allowance_used_after": -200}),
        ) as m_grant,
        patch(
            "routes.corporate_company.update_allowance_request",
            AsyncMock(return_value={"id": "r1", "status": "approved"}),
        ) as m_upd,
    ):
        resp = test_client.post(
            "/company/c1/allowance-requests/r1/decide",
            json={"approve": True, "note": "ok"},
        )
    assert resp.status_code == 200, resp.text
    m_grant.assert_awaited_once()
    m_upd.assert_awaited_once()
    kwargs = m_grant.await_args.kwargs
    assert kwargs["amount"] == 200
    assert kwargs["wallet_id"] == "w1"


def test_deny_request_skips_grant(test_client, rider_override):
    with (
        patch(
            "dependencies.company_guard.list_active_memberships_for_user",
            AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
        ),
        patch(
            "routes.corporate_company.get_allowance_request_by_id",
            AsyncMock(
                return_value={
                    "id": "r1",
                    "member_id": "m1",
                    "amount": 200,
                    "status": "pending",
                }
            ),
        ),
        patch(
            "routes.corporate_company.get_corporate_member_by_id",
            AsyncMock(return_value={"id": "m1", "company_id": "c1"}),
        ),
        patch(
            "routes.corporate_company.apply_grant",
            AsyncMock(),
        ) as m_grant,
        patch(
            "routes.corporate_company.update_allowance_request",
            AsyncMock(return_value={"id": "r1", "status": "denied"}),
        ),
    ):
        resp = test_client.post(
            "/company/c1/allowance-requests/r1/decide",
            json={"approve": False, "note": "over budget"},
        )
    assert resp.status_code == 200, resp.text
    m_grant.assert_not_awaited()


def test_approve_request_writes_audit_log(test_client, rider_override):
    """Corporate + admin portal review, gap #38: allowance-request
    approve/deny decisions never wrote an audit trail."""
    with (
        patch(
            "dependencies.company_guard.list_active_memberships_for_user",
            AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
        ),
        patch(
            "routes.corporate_company.get_allowance_request_by_id",
            AsyncMock(
                return_value={
                    "id": "r1",
                    "member_id": "m1",
                    "amount": 200,
                    "status": "pending",
                }
            ),
        ),
        patch(
            "routes.corporate_company.get_corporate_member_by_id",
            AsyncMock(return_value={"id": "m1", "company_id": "c1"}),
        ),
        patch(
            "routes.corporate_company.get_member_allowance",
            AsyncMock(return_value={"id": "a1", "member_id": "m1"}),
        ),
        patch(
            "routes.corporate_company.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1", "soft_negative_floor": -50}),
        ),
        patch(
            "routes.corporate_company.apply_grant",
            AsyncMock(return_value={"master_balance_after": 800, "allowance_used_after": -200}),
        ),
        patch(
            "routes.corporate_company.update_allowance_request",
            AsyncMock(return_value={"id": "r1", "status": "approved"}),
        ),
        patch("routes.corporate_company.log_user_action", AsyncMock()) as mock_audit,
    ):
        resp = test_client.post(
            "/company/c1/allowance-requests/r1/decide",
            json={"approve": True, "note": "ok"},
        )
    assert resp.status_code == 200, resp.text
    mock_audit.assert_awaited_once()
    assert mock_audit.await_args.kwargs["action"] == "corporate_allowance_request_decided"
    assert mock_audit.await_args.kwargs["details"]["decision"] == "approved"


def test_deny_request_writes_audit_log(test_client, rider_override):
    with (
        patch(
            "dependencies.company_guard.list_active_memberships_for_user",
            AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
        ),
        patch(
            "routes.corporate_company.get_allowance_request_by_id",
            AsyncMock(
                return_value={
                    "id": "r1",
                    "member_id": "m1",
                    "amount": 200,
                    "status": "pending",
                }
            ),
        ),
        patch(
            "routes.corporate_company.get_corporate_member_by_id",
            AsyncMock(return_value={"id": "m1", "company_id": "c1"}),
        ),
        patch("routes.corporate_company.apply_grant", AsyncMock()),
        patch(
            "routes.corporate_company.update_allowance_request",
            AsyncMock(return_value={"id": "r1", "status": "denied"}),
        ),
        patch("routes.corporate_company.log_user_action", AsyncMock()) as mock_audit,
    ):
        resp = test_client.post(
            "/company/c1/allowance-requests/r1/decide",
            json={"approve": False, "note": "over budget"},
        )
    assert resp.status_code == 200, resp.text
    mock_audit.assert_awaited_once()
    assert mock_audit.await_args.kwargs["details"]["decision"] == "denied"
    assert mock_audit.await_args.kwargs["details"]["amount"] is None


def test_already_decided_request_returns_409(test_client, rider_override):
    with (
        patch(
            "dependencies.company_guard.list_active_memberships_for_user",
            AsyncMock(return_value=[{"company_id": "c1", "role": "admin"}]),
        ),
        patch(
            "routes.corporate_company.get_allowance_request_by_id",
            AsyncMock(
                return_value={
                    "id": "r1",
                    "member_id": "m1",
                    "amount": 200,
                    "status": "approved",
                }
            ),
        ),
        patch(
            "routes.corporate_company.get_corporate_member_by_id",
            AsyncMock(return_value={"id": "m1", "company_id": "c1"}),
        ),
    ):
        resp = test_client.post(
            "/company/c1/allowance-requests/r1/decide",
            json={"approve": True, "note": "dupe"},
        )
    assert resp.status_code == 409
