# backend/tests/test_corporate_self_serve_topup.py
"""POST /company/{company_id}/wallet/topup — company-admin self-serve
wallet funding (corporate + admin portal review round 2, business
decision: self-serve funding via Stripe).

Follows the rider_override + list_active_memberships_for_user patching
convention established in test_corporate_company_gap_coverage.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_FAKE_USER = {"id": "u_admin", "phone": "+15550001111"}
_ROUTE = "routes.corporate_company."


@pytest.fixture
def rider_override():
    from backend.server import app
    from dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _as_admin(company_id="c1"):
    return patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": company_id, "role": "admin", "id": "m_admin"}]),
    )


def _as_member(company_id="c1"):
    return patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": company_id, "role": "member", "id": "m_member"}]),
    )


def _company(**extra):
    return {"id": "c1", "status": "active", "stripe_customer_id": "cus_1", **extra}


def _wallet(**extra):
    return {"id": "w1", "company_id": "c1", "balance": "0.00", **extra}


def _settings():
    return {"stripe_secret_key": "sk_test_x"}


def test_self_serve_topup_uses_default_payment_method(test_client, rider_override):
    intent_obj = MagicMock(id="pi_1", client_secret="pi_1_secret")

    with (
        _as_admin(),
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=_company())),
        patch(_ROUTE + "get_corporate_wallet_by_company", AsyncMock(return_value=_wallet())),
        patch(_ROUTE + "get_app_settings", AsyncMock(return_value=_settings())),
        patch(_ROUTE + "get_default_payment_method", AsyncMock(return_value="pm_default")) as m_default_pm,
        patch(_ROUTE + "stripe") as m_stripe,
        patch(_ROUTE + "log_user_action", AsyncMock()) as m_audit,
    ):
        m_stripe.PaymentIntent.create.return_value = intent_obj
        resp = test_client.post("/company/c1/wallet/topup", json={"amount": 500})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"payment_intent_id": "pi_1", "client_secret": "pi_1_secret"}
    m_default_pm.assert_awaited_once_with("cus_1", "sk_test_x")
    create_kwargs = m_stripe.PaymentIntent.create.call_args.kwargs
    assert create_kwargs["payment_method"] == "pm_default"
    assert create_kwargs["metadata"] == {
        "scope": "corporate_topup",
        "company_id": "c1",
        "wallet_id": "w1",
        "initiated_by": "u_admin",
    }
    m_audit.assert_awaited_once()


def test_self_serve_topup_uses_supplied_payment_method(test_client, rider_override):
    intent_obj = MagicMock(id="pi_2", client_secret="pi_2_secret")

    with (
        _as_admin(),
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=_company())),
        patch(_ROUTE + "get_corporate_wallet_by_company", AsyncMock(return_value=_wallet())),
        patch(_ROUTE + "get_app_settings", AsyncMock(return_value=_settings())),
        patch(_ROUTE + "get_default_payment_method", AsyncMock()) as m_default_pm,
        patch(_ROUTE + "stripe") as m_stripe,
        patch(_ROUTE + "log_user_action", AsyncMock()),
    ):
        m_stripe.PaymentIntent.create.return_value = intent_obj
        resp = test_client.post(
            "/company/c1/wallet/topup",
            json={"amount": 500, "payment_method_id": "pm_supplied"},
        )

    assert resp.status_code == 200, resp.text
    m_default_pm.assert_not_awaited()  # supplied id short-circuits the fallback lookup
    create_kwargs = m_stripe.PaymentIntent.create.call_args.kwargs
    assert create_kwargs["payment_method"] == "pm_supplied"


def test_no_payment_method_anywhere_is_422(test_client, rider_override):
    with (
        _as_admin(),
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=_company())),
        patch(_ROUTE + "get_corporate_wallet_by_company", AsyncMock(return_value=_wallet())),
        patch(_ROUTE + "get_app_settings", AsyncMock(return_value=_settings())),
        patch(_ROUTE + "get_default_payment_method", AsyncMock(return_value=None)),
    ):
        resp = test_client.post("/company/c1/wallet/topup", json={"amount": 500})

    assert resp.status_code == 422, resp.text


def test_inactive_company_rejected(test_client, rider_override):
    with (
        _as_admin(),
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=_company(status="suspended"))),
    ):
        resp = test_client.post("/company/c1/wallet/topup", json={"amount": 500})

    assert resp.status_code == 409, resp.text


def test_no_stripe_customer_is_provisioned_not_rejected(test_client, rider_override):
    """A NULL stripe_customer_id no longer 409s — it is provisioned in place.

    The auto-topup loop NULLs this column to retire a customer stranded by a
    test→live key rotation, so a 409 here would leave the company's own billing
    admin with no way to ever fund the wallet again. They still get a clean 422
    if no card is on file.
    """
    with (
        _as_admin(),
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=_company(stripe_customer_id=None))),
        patch(_ROUTE + "get_corporate_wallet_by_company", AsyncMock(return_value={"id": "w1", "company_id": "c1"})),
        patch(_ROUTE + "get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test_x"})),
        patch(
            "services.corporate_stripe_identity.get_app_settings",
            AsyncMock(return_value={"stripe_reprovision_stale_ids": True}),
        ),
        patch("services.corporate_stripe_identity.db_supabase.update_one", AsyncMock()),
        patch("stripe.Customer.create", return_value=MagicMock(id="cus_new")) as m_cus,
        patch(_ROUTE + "get_default_payment_method", AsyncMock(return_value="pm_1")),
        patch("stripe.PaymentIntent.create", return_value=MagicMock(id="pi_1", client_secret="cs_1")) as m_pi,
        patch(_ROUTE + "log_user_action", AsyncMock()),
    ):
        resp = test_client.post("/company/c1/wallet/topup", json={"amount": 500})

    assert resp.status_code == 200, resp.text
    m_cus.assert_called_once()
    assert m_pi.call_args.kwargs["customer"] == "cus_new"


def test_unknown_company_404(test_client, rider_override):
    with (
        _as_admin(),
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=None)),
    ):
        resp = test_client.post("/company/c1/wallet/topup", json={"amount": 500})

    assert resp.status_code == 404, resp.text


def test_no_wallet_provisioned_500(test_client, rider_override):
    with (
        _as_admin(),
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=_company())),
        patch(_ROUTE + "get_corporate_wallet_by_company", AsyncMock(return_value=None)),
    ):
        resp = test_client.post("/company/c1/wallet/topup", json={"amount": 500})

    assert resp.status_code == 500, resp.text


def test_non_admin_member_rejected(test_client, rider_override):
    """A plain 'member' role (not owner/admin) fails require_company_admin."""
    with _as_member():
        resp = test_client.post("/company/c1/wallet/topup", json={"amount": 500})

    assert resp.status_code == 403, resp.text


def test_cross_company_membership_rejected(test_client, rider_override):
    """An admin of a DIFFERENT company cannot top up c1's wallet — proves
    require_company_admin scopes strictly to the path's company_id."""
    with _as_admin(company_id="some-other-company"):
        resp = test_client.post("/company/c1/wallet/topup", json={"amount": 500})

    assert resp.status_code == 403, resp.text


def test_amount_below_minimum_rejected(test_client, rider_override):
    with _as_admin():
        resp = test_client.post("/company/c1/wallet/topup", json={"amount": 50})

    assert resp.status_code == 422, resp.text


def test_amount_above_maximum_rejected(test_client, rider_override):
    with _as_admin():
        resp = test_client.post("/company/c1/wallet/topup", json={"amount": 20000})

    assert resp.status_code == 422, resp.text


def test_extra_fields_rejected(test_client, rider_override):
    with _as_admin():
        resp = test_client.post(
            "/company/c1/wallet/topup",
            json={"amount": 500, "unexpected_field": "x"},
        )

    assert resp.status_code == 422, resp.text
