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


# ── Policy CRUD ───────────────────────────────────────────────────────────────

_FAKE_POLICY = {
    "id": "pol_1",
    "company_id": "c1",
    "active": True,
    "max_fare_per_ride": 80.0,
    "allowed_payment_source": "both",
    "allowed_time_windows": None,
    "allowed_geofence": None,
}

_ADMIN_MEMBERSHIPS = [{"company_id": "c1", "role": "admin"}]
_MEMBER_MEMBERSHIPS = [{"company_id": "c1", "role": "member"}]


def test_get_policy_accessible_to_member(test_client, rider_override):
    """Any active member can read the policy (rider Work Profile needs it)."""
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=_MEMBER_MEMBERSHIPS),
    ), patch(
        "routes.corporate_company.get_corporate_policy",
        AsyncMock(return_value=_FAKE_POLICY),
    ):
        resp = test_client.get("/company/c1/policy")
    assert resp.status_code == 200, resp.text
    assert resp.json()["max_fare_per_ride"] == 80.0


def test_get_policy_returns_empty_when_none_configured(test_client, rider_override):
    """No policy configured → empty dict, not 404."""
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=_ADMIN_MEMBERSHIPS),
    ), patch(
        "routes.corporate_company.get_corporate_policy",
        AsyncMock(return_value=None),
    ):
        resp = test_client.get("/company/c1/policy")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {}


def test_get_policy_blocked_for_non_member(test_client, rider_override):
    """Non-member cannot read the policy."""
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[]),
    ):
        resp = test_client.get("/company/c1/policy")
    assert resp.status_code == 403


def test_put_policy_creates_when_none_exists(test_client, rider_override):
    """PUT replaces/creates policy; upsert_corporate_policy is called with full payload."""
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=_ADMIN_MEMBERSHIPS),
    ), patch(
        "routes.corporate_company.upsert_corporate_policy",
        AsyncMock(return_value={**_FAKE_POLICY, "max_fare_per_ride": 100.0}),
    ) as m_upsert:
        resp = test_client.put(
            "/company/c1/policy",
            json={"max_fare_per_ride": 100.0, "allowed_payment_source": "both"},
        )
    assert resp.status_code == 200, resp.text
    m_upsert.assert_awaited_once()
    assert m_upsert.await_args.args[0] == "c1"
    assert m_upsert.await_args.args[1]["max_fare_per_ride"] == 100.0


def test_put_policy_requires_admin(test_client, rider_override):
    """Plain member cannot write policy."""
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=_MEMBER_MEMBERSHIPS),
    ):
        resp = test_client.put(
            "/company/c1/policy",
            json={"allowed_payment_source": "both"},
        )
    assert resp.status_code == 403


def test_put_policy_rejects_invalid_time_window(test_client, rider_override):
    """end <= start in a time window is rejected with 422."""
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=_ADMIN_MEMBERSHIPS),
    ):
        resp = test_client.put(
            "/company/c1/policy",
            json={
                "allowed_payment_source": "both",
                "allowed_time_windows": [
                    {"day": "mon", "start": "18:00", "end": "09:00"}
                ],
            },
        )
    assert resp.status_code == 422


def test_patch_policy_partial_update(test_client, rider_override):
    """PATCH only sends changed fields to upsert_corporate_policy."""
    updated = {**_FAKE_POLICY, "allowed_payment_source": "allowance_only"}
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=_ADMIN_MEMBERSHIPS),
    ), patch(
        "routes.corporate_company.upsert_corporate_policy",
        AsyncMock(return_value=updated),
    ) as m_upsert:
        resp = test_client.patch(
            "/company/c1/policy",
            json={"allowed_payment_source": "allowance_only"},
        )
    assert resp.status_code == 200, resp.text
    called_patch = m_upsert.await_args.args[1]
    assert called_patch == {"allowed_payment_source": "allowance_only"}


def test_patch_policy_empty_body_is_noop(test_client, rider_override):
    """Empty PATCH body returns current policy without calling upsert."""
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=_ADMIN_MEMBERSHIPS),
    ), patch(
        "routes.corporate_company.get_corporate_policy",
        AsyncMock(return_value=_FAKE_POLICY),
    ), patch(
        "routes.corporate_company.upsert_corporate_policy",
        AsyncMock(),
    ) as m_upsert:
        resp = test_client.patch("/company/c1/policy", json={})
    assert resp.status_code == 200, resp.text
    m_upsert.assert_not_awaited()


def test_patch_policy_with_time_windows(test_client, rider_override):
    """Time windows are serialised to plain dicts before reaching upsert."""
    windows = [{"day": "mon", "start": "09:00", "end": "18:00"}]
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=_ADMIN_MEMBERSHIPS),
    ), patch(
        "routes.corporate_company.upsert_corporate_policy",
        AsyncMock(return_value={**_FAKE_POLICY, "allowed_time_windows": windows}),
    ) as m_upsert:
        resp = test_client.patch("/company/c1/policy", json={"allowed_time_windows": windows})
    assert resp.status_code == 200, resp.text
    stored_windows = m_upsert.await_args.args[1]["allowed_time_windows"]
    assert stored_windows == windows


# ─── Billing (Plan 6) ───────────────────────────────────────────────────────

_BILLING_ROWS = [
    {
        "ride_id": "r1",
        "member_id": "m1",
        "allowance_debit_amount": 20.0,
        "master_fallback_amount": 5.0,
        "created_at": "2026-04-02T10:00:00",
    },
    {
        "ride_id": "r2",
        "member_id": "m1",
        "allowance_debit_amount": 15.0,
        "master_fallback_amount": 0.0,
        "created_at": "2026-04-05T10:00:00",
    },
    {
        "ride_id": "r3",
        "member_id": "m2",
        "allowance_debit_amount": 0.0,
        "master_fallback_amount": 40.0,
        "created_at": "2026-04-10T10:00:00",
    },
]


def test_billing_summary_aggregates_rows(test_client, rider_override):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=_ADMIN_MEMBERSHIPS),
    ), patch(
        "routes.corporate_company.list_company_ride_payment_sources",
        AsyncMock(return_value=_BILLING_ROWS),
    ), patch(
        "routes.corporate_company.get_corporate_wallet_by_company",
        AsyncMock(return_value={"balance": 120.0, "currency": "CAD"}),
    ):
        resp = test_client.get("/company/c1/billing/summary?month=2026-04")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["month"] == "2026-04"
    assert body["ride_count"] == 3
    assert body["allowance_total"] == 35.0
    assert body["master_total"] == 45.0
    assert body["total"] == 80.0
    assert body["wallet_balance"] == 120.0
    # by_member sorted by total desc
    assert body["by_member"][0]["member_id"] == "m2"
    assert body["by_member"][0]["total"] == 40.0
    assert body["by_member"][1]["member_id"] == "m1"
    assert body["by_member"][1]["ride_count"] == 2


def test_billing_summary_defaults_to_current_month(test_client, rider_override):
    list_mock = AsyncMock(return_value=[])
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=_ADMIN_MEMBERSHIPS),
    ), patch(
        "routes.corporate_company.list_company_ride_payment_sources",
        list_mock,
    ), patch(
        "routes.corporate_company.get_corporate_wallet_by_company",
        AsyncMock(return_value={}),
    ):
        resp = test_client.get("/company/c1/billing/summary")
    assert resp.status_code == 200, resp.text
    # month bounds were derived from a YYYY-MM string (first of the month)
    kwargs = list_mock.await_args.kwargs
    assert kwargs["from_iso"].endswith("-01T00:00:00")


def test_billing_summary_rejects_non_admin(test_client, rider_override):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "member"}]),
    ):
        resp = test_client.get("/company/c1/billing/summary?month=2026-04")
    assert resp.status_code == 403


def test_billing_statement_returns_line_items(test_client, rider_override):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=_ADMIN_MEMBERSHIPS),
    ), patch(
        "routes.corporate_company.list_company_ride_payment_sources",
        AsyncMock(return_value=_BILLING_ROWS),
    ):
        resp = test_client.get("/company/c1/billing/statements/2026-04")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["line_items"]) == 3
    assert body["summary"]["total"] == 80.0
    assert body["summary"]["avg_fare"] == round(80.0 / 3, 2)


def test_billing_statement_rejects_bad_month(test_client, rider_override):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=_ADMIN_MEMBERSHIPS),
    ):
        resp = test_client.get("/company/c1/billing/statements/bad")
    assert resp.status_code == 422


def test_billing_transactions_returns_paged_ledger(test_client, rider_override):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=_ADMIN_MEMBERSHIPS),
    ), patch(
        "routes.corporate_company.get_corporate_wallet_by_company",
        AsyncMock(return_value={"id": "w1", "balance": 250.0, "currency": "CAD"}),
    ), patch(
        "routes.corporate_company.list_wallet_transactions",
        AsyncMock(return_value=[{"id": "t1", "amount": 100}]),
    ) as m_list:
        resp = test_client.get("/company/c1/billing/transactions?skip=0&limit=25")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["wallet_id"] == "w1"
    assert body["balance"] == 250.0
    assert len(body["transactions"]) == 1
    m_list.assert_awaited_once_with(wallet_id="w1", skip=0, limit=25)


def test_billing_transactions_404_when_no_wallet(test_client, rider_override):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=_ADMIN_MEMBERSHIPS),
    ), patch(
        "routes.corporate_company.get_corporate_wallet_by_company",
        AsyncMock(return_value=None),
    ):
        resp = test_client.get("/company/c1/billing/transactions")
    assert resp.status_code == 404
