"""Self-serve company signup route tests (M1.3).

POST /api/portal/companies/signup — authenticated-first: identity comes from
the session (dependency override), the payload is only the company form.
"""

from unittest.mock import AsyncMock, patch

import pytest

_SIGNUP_PATH = "/api/portal/companies/signup"

_FAKE_EMAIL_USER = {"id": "u1", "email": "jane@acme.com", "phone": "email:abc123"}
_FAKE_PHONE_USER = {"id": "u2", "phone": "+13065550100"}  # no email → rejected


def _valid_body() -> dict:
    return {
        "name": "Acme Corp",
        "legal_name": "Acme Corporation Inc.",
        "business_number": "123456789RT0001",
        "industry": "Automotive",
        "contact_name": "Jane Doe",
        "contact_phone": "+13065550100",
        "address_line1": "123 Main St",
        "city": "Saskatoon",
        "province": "SK",
        "postal_code": "S7K 1M1",
        "terms_accepted": True,
        "terms_version": "biz-tos-2026-01",
    }


@pytest.fixture
def _user_override():
    from backend.server import app
    from dependencies import get_current_user

    def _set(user):
        app.dependency_overrides[get_current_user] = lambda: user

    yield _set
    from backend.server import app as _app  # re-import safe

    _app.dependency_overrides.pop(__import__("dependencies").get_current_user, None)


def _patches(pending=0, company=None, member=None):
    if company is None:
        company = {"id": "c9", "name": "Acme Corp", "status": "pending_verification"}
    if member is None:
        member = {"id": "m9", "role": "owner", "status": "active"}
    return (
        patch("routes.corporate_signup.count_pending_signups_for_user", AsyncMock(return_value=pending)),
        patch("routes.corporate_signup.insert_corporate_account", AsyncMock(return_value=company)),
        patch("routes.corporate_signup.bootstrap_owner", AsyncMock(return_value=(member, None))),
        patch("routes.corporate_signup.delete_corporate_account", AsyncMock(return_value=True)),
        patch("routes.corporate_signup.log_admin_action", AsyncMock(return_value="audit1")),
        patch("routes.corporate_signup.send_transactional_email", AsyncMock(return_value=True)),
    )


def test_signup_happy_path(test_client, _user_override):
    _user_override(_FAKE_EMAIL_USER)
    p_count, p_insert, p_boot, p_del, p_audit, p_mail = _patches()
    with p_count, p_insert as m_insert, p_boot as m_boot, p_del, p_audit as m_audit, p_mail:
        resp = test_client.post(_SIGNUP_PATH, json=_valid_body())

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["company"]["status"] == "pending_verification"
    assert data["member"]["role"] == "owner"

    inserted = m_insert.await_args.args[0]
    # Identity comes from the session, never the payload.
    assert inserted["contact_email"] == "jane@acme.com"
    assert inserted["signup_user_id"] == "u1"
    assert inserted["signup_source"] == "self_serve"
    assert inserted["status"] == "pending_verification"
    # Province doubles as tax region.
    assert inserted["tax_region"] == "SK"
    assert inserted["terms_accepted_version"] == "biz-tos-2026-01"

    m_boot.assert_awaited_once_with(company_id="c9", email="jane@acme.com", user_id="u1")
    m_audit.assert_awaited_once()


def test_signup_requires_email_session(test_client, _user_override):
    _user_override(_FAKE_PHONE_USER)
    resp = test_client.post(_SIGNUP_PATH, json=_valid_body())
    assert resp.status_code == 400
    assert "work-email" in resp.json()["detail"].lower()


def test_signup_pending_cap_enforced(test_client, _user_override):
    _user_override(_FAKE_EMAIL_USER)
    p_count, p_insert, p_boot, p_del, p_audit, p_mail = _patches(pending=3)
    with p_count, p_insert as m_insert, p_boot, p_del, p_audit, p_mail:
        resp = test_client.post(_SIGNUP_PATH, json=_valid_body())

    assert resp.status_code == 429
    m_insert.assert_not_awaited()


def test_signup_rolls_back_company_when_owner_bootstrap_fails(test_client, _user_override):
    _user_override(_FAKE_EMAIL_USER)
    p_count, p_insert, _p_boot, p_del, p_audit, p_mail = _patches()
    boot_fail = patch("routes.corporate_signup.bootstrap_owner", AsyncMock(side_effect=RuntimeError("db down")))
    with p_count, p_insert, boot_fail, p_del as m_del, p_audit as m_audit, p_mail:
        resp = test_client.post(_SIGNUP_PATH, json=_valid_body())

    assert resp.status_code == 503
    m_del.assert_awaited_once_with("c9")  # orphan company removed
    m_audit.assert_not_awaited()


def test_signup_insert_failure_is_503_not_swallowed(test_client, _user_override):
    _user_override(_FAKE_EMAIL_USER)
    p_count, _p_insert, p_boot, p_del, p_audit, p_mail = _patches()
    insert_fail = patch(
        "routes.corporate_signup.insert_corporate_account", AsyncMock(side_effect=RuntimeError("supabase 5xx"))
    )
    with p_count, insert_fail, p_boot as m_boot, p_del, p_audit, p_mail:
        resp = test_client.post(_SIGNUP_PATH, json=_valid_body())

    assert resp.status_code == 503
    m_boot.assert_not_awaited()


def test_signup_ops_email_failure_does_not_fail_signup(test_client, _user_override):
    _user_override(_FAKE_EMAIL_USER)
    p_count, p_insert, p_boot, p_del, p_audit, _p_mail = _patches()
    mail_fail = patch(
        "routes.corporate_signup.send_transactional_email", AsyncMock(side_effect=RuntimeError("ses down"))
    )
    with (
        p_count,
        p_insert,
        p_boot,
        p_del,
        p_audit,
        mail_fail,
        patch("routes.corporate_signup.settings") as m_settings,
    ):
        m_settings.CORPORATE_OPS_EMAIL = "ops@spinr.ca"
        resp = test_client.post(_SIGNUP_PATH, json=_valid_body())

    assert resp.status_code == 200  # courtesy email is best-effort


def test_signup_rejects_invalid_payload(test_client, _user_override):
    _user_override(_FAKE_EMAIL_USER)
    bad = _valid_body()
    del bad["business_number"]  # KYB field mandatory on self-serve
    resp = test_client.post(_SIGNUP_PATH, json=bad)
    assert resp.status_code == 422
