# backend/tests/test_corporate_company_gap_coverage.py
"""Additional branch coverage for routes/corporate_company.py: geofence
validation, invite email/audit failure isolation, member removal/
reactivation audit branches, allowance CRUD 404s, allowed-domains, allowance
request decisions, policy audit-failure isolation, and billing pagination.

Follows the `rider_override` + `list_active_memberships_for_user` patching
convention already established in test_corporate_company_routes.py.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

_FAKE_USER = {"id": "u_admin", "phone": "+15550001111"}
_ROUTE = "routes.corporate_company."


@pytest.fixture
def rider_override():
    from backend.server import app
    from dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _as_admin():
    return patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin", "id": "m_admin"}]),
    )


def _as_member():
    return patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "member", "id": "m_member"}]),
    )


# ── _validate_geofence (via PUT /policy) ────────────────────────────────


def test_geofence_wrong_type_is_422(test_client, rider_override):
    with _as_admin():
        resp = test_client.put(
            "/company/c1/policy",
            json={"allowed_payment_source": "allowance_only", "allowed_geofence": {"type": "Point"}},
        )
    assert resp.status_code == 422


def test_geofence_features_not_a_list_is_422(test_client, rider_override):
    with _as_admin():
        resp = test_client.put(
            "/company/c1/policy",
            json={
                "allowed_payment_source": "allowance_only",
                "allowed_geofence": {"type": "FeatureCollection", "features": "nope"},
            },
        )
    assert resp.status_code == 422


def test_geofence_feature_not_object_is_422(test_client, rider_override):
    with _as_admin():
        resp = test_client.put(
            "/company/c1/policy",
            json={
                "allowed_payment_source": "allowance_only",
                "allowed_geofence": {"type": "FeatureCollection", "features": ["not-a-dict"]},
            },
        )
    assert resp.status_code == 422


def test_geofence_missing_geometry_is_422(test_client, rider_override):
    with _as_admin():
        resp = test_client.put(
            "/company/c1/policy",
            json={
                "allowed_payment_source": "allowance_only",
                "allowed_geofence": {"type": "FeatureCollection", "features": [{"type": "Feature"}]},
            },
        )
    assert resp.status_code == 422


def test_geofence_valid_passes(test_client, rider_override):
    with (
        _as_admin(),
        patch(_ROUTE + "upsert_corporate_policy", AsyncMock(return_value={"id": "p1"})),
        patch(_ROUTE + "log_user_action", AsyncMock()),
    ):
        resp = test_client.put(
            "/company/c1/policy",
            json={
                "allowed_payment_source": "allowance_only",
                "allowed_geofence": {
                    "type": "FeatureCollection",
                    "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}}],
                },
            },
        )
    assert resp.status_code == 200, resp.text


# ── geofence_enforced=False annotation ──────────────────────────────────
# Corporate + admin portal review, round 2: "geofence policy is
# configurable ... and silently does nothing" — services/
# corporate_policy_service.py's evaluate_policy_for_ride permanently
# defers this rule pending PostGIS, but nothing told an API caller that.
# No UI control exists to hide (checked — no frontend references
# allowed_geofence at all), so the fix is response-level transparency.


def test_get_policy_flags_geofence_unenforced_when_set(test_client, rider_override):
    stored = {"id": "p1", "allowed_geofence": {"type": "FeatureCollection", "features": []}}
    with (
        _as_admin(),
        patch(_ROUTE + "get_corporate_policy", AsyncMock(return_value=stored)),
    ):
        resp = test_client.get("/company/c1/policy")
    assert resp.status_code == 200, resp.text
    assert resp.json()["geofence_enforced"] is False


def test_get_policy_no_flag_when_geofence_unset(test_client, rider_override):
    stored = {"id": "p1", "max_fare_per_ride": 80}
    with (
        _as_admin(),
        patch(_ROUTE + "get_corporate_policy", AsyncMock(return_value=stored)),
    ):
        resp = test_client.get("/company/c1/policy")
    assert resp.status_code == 200, resp.text
    assert "geofence_enforced" not in resp.json()


def test_replace_policy_flags_geofence_unenforced(test_client, rider_override):
    upserted = {
        "id": "p1",
        "allowed_geofence": {"type": "FeatureCollection", "features": []},
    }
    with (
        _as_admin(),
        patch(_ROUTE + "upsert_corporate_policy", AsyncMock(return_value=upserted)),
        patch(_ROUTE + "log_user_action", AsyncMock()),
    ):
        resp = test_client.put(
            "/company/c1/policy",
            json={
                "allowed_payment_source": "allowance_only",
                "allowed_geofence": {
                    "type": "FeatureCollection",
                    "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}}],
                },
            },
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["geofence_enforced"] is False


def test_patch_policy_flags_geofence_unenforced():
    """Unit-level: doesn't need the app/RBAC scaffolding — directly proves
    _annotate_geofence_enforcement's contract used by patch_policy."""
    from routes.corporate_company import _annotate_geofence_enforcement

    with_geofence = {"id": "p1", "allowed_geofence": {"type": "FeatureCollection", "features": []}}
    without = {"id": "p1", "max_fare_per_ride": 80}
    empty = {}

    assert _annotate_geofence_enforcement(with_geofence)["geofence_enforced"] is False
    assert "geofence_enforced" not in _annotate_geofence_enforcement(without)
    assert _annotate_geofence_enforcement(empty) == {}
    # Never mutates the input dict in place.
    assert "geofence_enforced" not in with_geofence


# ── list_members status filter ──────────────────────────────────────────


def test_list_members_splits_comma_separated_status(test_client, rider_override):
    with (
        _as_admin(),
        patch(_ROUTE + "list_company_members", AsyncMock(return_value=[])) as m_list,
    ):
        resp = test_client.get("/company/c1/members?status=active, suspended")
    assert resp.status_code == 200, resp.text
    m_list.assert_awaited_once_with(company_id="c1", statuses=["active", "suspended"])


# ── invite: email + audit-log failure isolation ─────────────────────────


def test_invite_email_delivery_exception_does_not_fail_request(test_client, rider_override):
    with (
        _as_admin(),
        patch(
            _ROUTE + "invite_member",
            AsyncMock(return_value=({"id": "m1", "invite_token": "tok"}, "app://join?token=tok")),
        ),
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value={"id": "c1", "name": "Acme"})),
        patch("utils.email_provider.send_transactional_email", AsyncMock(side_effect=Exception("smtp down"))),
        patch(_ROUTE + "log_user_action", AsyncMock()),
    ):
        resp = test_client.post("/company/c1/members/invite", json={"email": "a@b.com", "role": "member"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["email_sent"] is False


def test_invite_audit_log_failure_does_not_fail_request(test_client, rider_override):
    with (
        _as_admin(),
        patch(
            _ROUTE + "invite_member",
            AsyncMock(return_value=({"id": "m1", "invite_token": None}, "app://join?token=xyz")),
        ),
        patch(_ROUTE + "log_user_action", AsyncMock(side_effect=Exception("audit down"))),
    ):
        resp = test_client.post("/company/c1/members/invite", json={"email": "a@b.com", "role": "member"})
    assert resp.status_code == 200, resp.text
    # No token → no web_invite_url, so email delivery is skipped entirely.
    assert resp.json()["web_invite_url"] is None
    assert resp.json()["email_sent"] is False


# ── update_member: section reassignment + audit isolation ──────────────


def test_update_member_not_found_404(test_client, rider_override):
    with (
        _as_admin(),
        patch(_ROUTE + "get_corporate_member_by_id", AsyncMock(return_value=None)),
    ):
        resp = test_client.patch("/company/c1/members/m_missing", json={"role": "member"})
    assert resp.status_code == 404


def test_update_member_section_cross_company_is_404(test_client, rider_override):
    existing = {"id": "m2", "company_id": "c1", "status": "active"}
    with (
        _as_admin(),
        patch(_ROUTE + "get_corporate_member_by_id", AsyncMock(return_value=existing)),
        patch("db_supabase.get_rows", AsyncMock(return_value=[])),
    ):
        resp = test_client.patch("/company/c1/members/m2", json={"section_id": "sec_other"})
    assert resp.status_code == 404


def test_update_member_clears_section_with_empty_string(test_client, rider_override):
    existing = {"id": "m2", "company_id": "c1", "status": "active", "section_id": "sec1"}
    with (
        _as_admin(),
        patch(_ROUTE + "get_corporate_member_by_id", AsyncMock(return_value=existing)),
        patch(_ROUTE + "update_corporate_member", AsyncMock(return_value={**existing, "section_id": None})) as m_upd,
    ):
        resp = test_client.patch("/company/c1/members/m2", json={"section_id": ""})
    assert resp.status_code == 200, resp.text
    assert m_upd.call_args.args[1]["section_id"] is None


def test_update_member_status_change_triggers_reactivation_audit(test_client, rider_override):
    existing = {"id": "m2", "company_id": "c1", "status": "suspended"}
    with (
        _as_admin(),
        patch(_ROUTE + "get_corporate_member_by_id", AsyncMock(return_value=existing)),
        patch(_ROUTE + "update_corporate_member", AsyncMock(return_value={**existing, "status": "active"})),
        patch(_ROUTE + "log_user_action", AsyncMock()) as m_log,
    ):
        resp = test_client.patch("/company/c1/members/m2", json={"status": "active"})
    assert resp.status_code == 200, resp.text
    m_log.assert_awaited_once()
    assert m_log.call_args.kwargs["action"] == "corporate_member_status_changed"
    assert m_log.call_args.kwargs["details"]["new_status"] == "active"


def test_update_member_removal_ride_cancel_failure_is_logged_not_raised(test_client, rider_override):
    existing = {"id": "m2", "company_id": "c1", "status": "active"}
    with (
        _as_admin(),
        patch(_ROUTE + "get_corporate_member_by_id", AsyncMock(return_value=existing)),
        patch(_ROUTE + "update_corporate_member", AsyncMock(return_value={**existing, "status": "suspended"})),
        patch(
            _ROUTE + "get_app_settings",
            AsyncMock(return_value={"corporate_member_removal_cancels_pre_pickup_rides": True}),
        ),
        patch(_ROUTE + "cancel_pre_pickup_rides_for_member", AsyncMock(side_effect=RuntimeError("dispatch down"))),
        patch(_ROUTE + "log_user_action", AsyncMock()),
    ):
        resp = test_client.patch("/company/c1/members/m2", json={"status": "suspended"})
    assert resp.status_code == 200, resp.text


def test_update_member_removal_audit_log_failure_isolated(test_client, rider_override):
    existing = {"id": "m2", "company_id": "c1", "status": "active"}
    with (
        _as_admin(),
        patch(_ROUTE + "get_corporate_member_by_id", AsyncMock(return_value=existing)),
        patch(_ROUTE + "update_corporate_member", AsyncMock(return_value={**existing, "status": "removed"})),
        patch(
            _ROUTE + "get_app_settings",
            AsyncMock(return_value={"corporate_member_removal_cancels_pre_pickup_rides": False}),
        ),
        patch(_ROUTE + "log_user_action", AsyncMock(side_effect=Exception("audit down"))),
    ):
        resp = test_client.patch("/company/c1/members/m2", json={"status": "removed"})
    assert resp.status_code == 200, resp.text


# ── remove_member ────────────────────────────────────────────────────────


def test_remove_member_not_found_404(test_client, rider_override):
    with (
        _as_admin(),
        patch(_ROUTE + "get_corporate_member_by_id", AsyncMock(return_value=None)),
    ):
        resp = test_client.delete("/company/c1/members/m_missing")
    assert resp.status_code == 404


# ── Allowances ───────────────────────────────────────────────────────────


def test_get_allowance_member_not_found_404(test_client, rider_override):
    with (
        _as_admin(),
        patch(_ROUTE + "get_corporate_member_by_id", AsyncMock(return_value=None)),
    ):
        resp = test_client.get("/company/c1/members/m_missing/allowance")
    assert resp.status_code == 404


def test_set_allowance_member_not_found_404(test_client, rider_override):
    with (
        _as_admin(),
        patch(_ROUTE + "get_corporate_member_by_id", AsyncMock(return_value=None)),
    ):
        resp = test_client.put(
            "/company/c1/members/m_missing/allowance",
            json={"type": "one_time", "amount": "100.00"},
        )
    assert resp.status_code == 404


def test_patch_allowance_member_not_found_404(test_client, rider_override):
    with (
        _as_admin(),
        patch(_ROUTE + "get_corporate_member_by_id", AsyncMock(return_value=None)),
    ):
        resp = test_client.patch("/company/c1/members/m_missing/allowance", json={"amount": "50.00"})
    assert resp.status_code == 404


# ── Allowed domains ──────────────────────────────────────────────────────


def test_list_domains_returns_rows(test_client, rider_override):
    with (
        _as_admin(),
        patch(_ROUTE + "list_allowed_domains", AsyncMock(return_value=[{"domain": "acme.com"}])),
    ):
        resp = test_client.get("/company/c1/allowed-domains")
    assert resp.status_code == 200, resp.text
    assert resp.json() == [{"domain": "acme.com"}]


def test_remove_domain_lowercases_and_returns_ok(test_client, rider_override):
    with (
        _as_admin(),
        patch(_ROUTE + "delete_allowed_domain", AsyncMock()) as m_del,
    ):
        resp = test_client.delete("/company/c1/allowed-domains/ACME.COM")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "ok"}
    m_del.assert_awaited_once_with(company_id="c1", domain="acme.com")


# ── decide_allowance_request ────────────────────────────────────────────


def test_decide_request_not_found_404(test_client, rider_override):
    with (
        _as_admin(),
        patch(_ROUTE + "get_allowance_request_by_id", AsyncMock(return_value=None)),
    ):
        resp = test_client.post("/company/c1/allowance-requests/req1/decide", json={"approve": True})
    assert resp.status_code == 404


def test_decide_request_member_wrong_company_404(test_client, rider_override):
    with (
        _as_admin(),
        patch(
            _ROUTE + "get_allowance_request_by_id",
            AsyncMock(return_value={"id": "req1", "member_id": "m1", "status": "pending"}),
        ),
        patch(_ROUTE + "get_corporate_member_by_id", AsyncMock(return_value={"id": "m1", "company_id": "other_co"})),
    ):
        resp = test_client.post("/company/c1/allowance-requests/req1/decide", json={"approve": True})
    assert resp.status_code == 404


def test_decide_request_already_decided_409(test_client, rider_override):
    with (
        _as_admin(),
        patch(
            _ROUTE + "get_allowance_request_by_id",
            AsyncMock(return_value={"id": "req1", "member_id": "m1", "status": "approved"}),
        ),
        patch(_ROUTE + "get_corporate_member_by_id", AsyncMock(return_value={"id": "m1", "company_id": "c1"})),
    ):
        resp = test_client.post("/company/c1/allowance-requests/req1/decide", json={"approve": True})
    assert resp.status_code == 409


def test_decide_request_approve_missing_allowance_or_wallet_409(test_client, rider_override):
    with (
        _as_admin(),
        patch(
            _ROUTE + "get_allowance_request_by_id",
            AsyncMock(return_value={"id": "req1", "member_id": "m1", "status": "pending", "amount": "50.00"}),
        ),
        patch(_ROUTE + "get_corporate_member_by_id", AsyncMock(return_value={"id": "m1", "company_id": "c1"})),
        patch(_ROUTE + "get_member_allowance", AsyncMock(return_value=None)),
        patch(_ROUTE + "get_corporate_wallet_by_company", AsyncMock(return_value=None)),
    ):
        resp = test_client.post("/company/c1/allowance-requests/req1/decide", json={"approve": True})
    assert resp.status_code == 409


def test_decide_request_approve_missing_amount_422(test_client, rider_override):
    with (
        _as_admin(),
        patch(
            _ROUTE + "get_allowance_request_by_id",
            AsyncMock(return_value={"id": "req1", "member_id": "m1", "status": "pending", "amount": None}),
        ),
        patch(_ROUTE + "get_corporate_member_by_id", AsyncMock(return_value={"id": "m1", "company_id": "c1"})),
        patch(_ROUTE + "get_member_allowance", AsyncMock(return_value={"id": "a1"})),
        patch(_ROUTE + "get_corporate_wallet_by_company", AsyncMock(return_value={"id": "w1"})),
    ):
        resp = test_client.post("/company/c1/allowance-requests/req1/decide", json={"approve": True})
    assert resp.status_code == 422


def test_decide_request_approve_applies_grant(test_client, rider_override):
    with (
        _as_admin(),
        patch(
            _ROUTE + "get_allowance_request_by_id",
            AsyncMock(return_value={"id": "req1", "member_id": "m1", "status": "pending", "amount": "50.00"}),
        ),
        patch(_ROUTE + "get_corporate_member_by_id", AsyncMock(return_value={"id": "m1", "company_id": "c1"})),
        patch(_ROUTE + "get_member_allowance", AsyncMock(return_value={"id": "a1"})),
        patch(
            _ROUTE + "get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1", "soft_negative_floor": "-50"}),
        ),
        patch(_ROUTE + "apply_grant", AsyncMock(return_value={"master_balance_after": "450.00"})) as m_grant,
        patch(_ROUTE + "update_allowance_request", AsyncMock(return_value={"id": "req1", "status": "approved"})),
    ):
        resp = test_client.post("/company/c1/allowance-requests/req1/decide", json={"approve": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"
    m_grant.assert_awaited_once()
    assert m_grant.call_args.kwargs["wallet_id"] == "w1"


def test_decide_request_deny_skips_grant(test_client, rider_override):
    with (
        _as_admin(),
        patch(
            _ROUTE + "get_allowance_request_by_id",
            AsyncMock(return_value={"id": "req1", "member_id": "m1", "status": "pending", "amount": "50.00"}),
        ),
        patch(_ROUTE + "get_corporate_member_by_id", AsyncMock(return_value={"id": "m1", "company_id": "c1"})),
        patch(_ROUTE + "apply_grant", AsyncMock()) as m_grant,
        patch(_ROUTE + "update_allowance_request", AsyncMock(return_value={"id": "req1", "status": "denied"})),
    ):
        resp = test_client.post(
            "/company/c1/allowance-requests/req1/decide", json={"approve": False, "note": "over budget"}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "denied"
    m_grant.assert_not_called()


# ── policy patch: geofence validation + audit-failure isolation ────────


def test_patch_policy_geofence_validated(test_client, rider_override):
    with _as_admin():
        resp = test_client.patch(
            "/company/c1/policy",
            json={"allowed_geofence": {"type": "Wrong"}},
        )
    assert resp.status_code == 422


def test_patch_policy_audit_log_failure_isolated(test_client, rider_override):
    with (
        _as_admin(),
        patch(_ROUTE + "upsert_corporate_policy", AsyncMock(return_value={"max_fare_per_ride": "80.00"})),
        patch(_ROUTE + "log_user_action", AsyncMock(side_effect=Exception("audit down"))),
    ):
        resp = test_client.patch("/company/c1/policy", json={"max_fare_per_ride": "80.00"})
    assert resp.status_code == 200, resp.text


def test_replace_policy_audit_log_failure_isolated(test_client, rider_override):
    with (
        _as_admin(),
        patch(_ROUTE + "upsert_corporate_policy", AsyncMock(return_value={"id": "p1"})),
        patch(_ROUTE + "log_user_action", AsyncMock(side_effect=Exception("audit down"))),
    ):
        resp = test_client.put("/company/c1/policy", json={"allowed_payment_source": "allowance_only"})
    assert resp.status_code == 200, resp.text


# ── _month_bounds edge cases (December rollover) ────────────────────────


def test_month_bounds_december_rolls_to_next_year():
    from routes.corporate_company import _month_bounds

    from_iso, to_iso = _month_bounds("2026-12")
    assert from_iso.startswith("2026-12-01")
    assert to_iso.startswith("2027-01-01")


def test_month_bounds_invalid_string_is_422():
    from routes.corporate_company import _month_bounds

    with pytest.raises(HTTPException) as exc_info:
        _month_bounds("not-a-month")
    assert exc_info.value.status_code == 422


# ── billing pagination (offset += page_size continuation branch) ───────


def test_billing_summary_pages_through_all_rows(test_client, rider_override):
    page1 = [{"amount": "10.00", "member_id": "m1"} for _ in range(1000)]
    page2 = [{"amount": "5.00", "member_id": "m1"}]

    with (
        _as_admin(),
        patch(_ROUTE + "list_company_ride_payment_sources", AsyncMock(side_effect=[page1, page2])) as m_list,
        patch(_ROUTE + "get_corporate_wallet_by_company", AsyncMock(return_value={"balance": "500.00"})),
    ):
        resp = test_client.get("/company/c1/billing/summary?month=2026-07")
    assert resp.status_code == 200, resp.text
    assert m_list.await_count == 2
    assert resp.json()["ride_count"] == 1001


def test_billing_statement_pages_through_all_rows(test_client, rider_override):
    page1 = [{"amount": "10.00", "member_id": "m1"} for _ in range(1000)]
    page2 = [{"amount": "5.00", "member_id": "m1"}]

    async def _list(*, company_id, from_iso, to_iso, limit, offset):
        if limit == 200:
            return page1[:200]
        if offset == 0:
            return page1
        return page2

    with (
        _as_admin(),
        patch(_ROUTE + "list_company_ride_payment_sources", AsyncMock(side_effect=_list)),
    ):
        resp = test_client.get("/company/c1/billing/statements/2026-07")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"]["ride_count"] == 1001
    assert len(body["line_items"]) == 200


# ── GST/PST tax breakdown on statements ─────────────────────────────────
# Corporate + admin portal review, round 2: "no GST/PST breakdown on
# corporate statements" — tax was already computed and stored per-ride
# (rides.tax_amount / tax_breakdown) but never surfaced here.


@pytest.mark.asyncio
async def test_attach_ride_tax_merges_amount_and_breakdown():
    from routes.corporate_company import _attach_ride_tax

    rows = [{"ride_id": "r1", "member_id": "m1"}, {"ride_id": "r2", "member_id": "m2"}]
    rides = [
        {"id": "r1", "tax_amount": "5.00", "tax_breakdown": {"GST": {"rate": 0.05, "amount": "3.00"}}},
        {"id": "r2", "tax_amount": "0", "tax_breakdown": {}},
    ]
    with patch("backend.db_supabase.get_rows", AsyncMock(return_value=rides)):
        out = await _attach_ride_tax(rows)
    assert out[0]["tax_amount"] == "5.00"
    assert out[0]["tax_breakdown"] == {"GST": {"rate": 0.05, "amount": "3.00"}}
    assert out[1]["tax_amount"] == "0"


@pytest.mark.asyncio
async def test_attach_ride_tax_short_circuits_when_no_ride_ids():
    """No ride_id on any row (e.g. a legacy/malformed payment-source row)
    must not issue an $in query with an empty list."""
    from routes.corporate_company import _attach_ride_tax

    rows = [{"member_id": "m1"}]
    with patch("backend.db_supabase.get_rows", AsyncMock()) as mock_get_rows:
        out = await _attach_ride_tax(rows)
    mock_get_rows.assert_not_awaited()
    assert out == rows


def test_aggregate_rows_sums_tax_total_and_by_type():
    from routes.corporate_company import _aggregate_rows

    rows = [
        {
            "allowance_debit_amount": "10.00",
            "master_fallback_amount": "0",
            "member_id": "m1",
            "tax_amount": "1.30",
            "tax_breakdown": {"GST": {"rate": 0.05, "amount": "0.50"}, "PST": {"rate": 0.06, "amount": "0.80"}},
        },
        {
            "allowance_debit_amount": "20.00",
            "master_fallback_amount": "0",
            "member_id": "m1",
            "tax_amount": "2.60",
            "tax_breakdown": {"GST": {"rate": 0.05, "amount": "1.00"}, "PST": {"rate": 0.06, "amount": "1.60"}},
        },
    ]
    result = _aggregate_rows(rows)
    assert result["tax_total"] == "3.90"
    assert result["tax_by_type"] == {"GST": "1.50", "PST": "2.40"}


def test_aggregate_rows_tax_defaults_to_zero_when_absent():
    """Rows never passed through _attach_ride_tax (e.g. a caller that
    forgets to call it) must not KeyError -- tax fields default to zero,
    same posture as every other money field's _d() default."""
    from routes.corporate_company import _aggregate_rows

    result = _aggregate_rows([{"allowance_debit_amount": "10.00", "master_fallback_amount": "0", "member_id": "m1"}])
    assert result["tax_total"] == "0.00"
    assert result["tax_by_type"] == {}


def test_billing_summary_includes_tax_fields(test_client, rider_override):
    rows = [
        {
            "amount": "10.00",
            "allowance_debit_amount": "10.00",
            "master_fallback_amount": "0",
            "member_id": "m1",
            "ride_id": "r1",
        }
    ]
    rides = [{"id": "r1", "tax_amount": "1.30", "tax_breakdown": {"GST": {"rate": 0.05, "amount": "0.50"}}}]
    with (
        _as_admin(),
        patch(_ROUTE + "list_company_ride_payment_sources", AsyncMock(return_value=rows)),
        patch(_ROUTE + "get_corporate_wallet_by_company", AsyncMock(return_value={"balance": "500.00"})),
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=rides)),
    ):
        resp = test_client.get("/company/c1/billing/summary?month=2026-07")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tax_total"] == "1.30"
    assert body["tax_by_type"] == {"GST": "0.50"}
