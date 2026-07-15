"""Company-side KYB route tests (M2.2).

/company/{id}/kyb — derived state; /kyb/upload-url; /kyb/submit
(prefix/tenancy validation, resubmit-after-reject transition, 409 for
staff-suspended companies).
"""

from unittest.mock import AsyncMock, patch

import pytest

from routes.corporate_company_kyb import derive_kyb_state

_FAKE_USER = {"id": "u_admin", "phone": "+15550001111"}


@pytest.fixture
def rider_override():
    from backend.server import app
    from dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _admin_guard():
    return patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "admin", "id": "m1"}]),
    )


def _company(status="pending_verification", **over):
    row = {
        "id": "c1",
        "status": status,
        "kyb_document_url": None,
        "kyb_last_decision": None,
        "kyb_submitted_at": None,
        "kyb_reviewed_at": None,
        "kyb_review_note": None,
    }
    row.update(over)
    return row


# ── derived state (pure) ─────────────────────────────────────────────────────


def test_derive_kyb_state_matrix():
    assert derive_kyb_state(_company()) == "not_submitted"
    assert derive_kyb_state(_company(kyb_document_url="kyb/c1/a.pdf")) == "under_review"
    assert derive_kyb_state(_company(status="active")) == "approved"
    assert derive_kyb_state(_company(status="closed")) == "closed"
    assert derive_kyb_state(_company(status="suspended", kyb_last_decision="rejected")) == "rejected"
    # Staff-suspended (no KYB rejection) — must NOT look resubmittable.
    assert derive_kyb_state(_company(status="suspended")) == "suspended"


# ── GET /kyb ─────────────────────────────────────────────────────────────────


def test_get_kyb_state_rejected_includes_note_never_path(test_client, rider_override):
    company = _company(
        status="suspended",
        kyb_last_decision="rejected",
        kyb_review_note="BN mismatch",
        kyb_document_url="kyb/c1/secret.pdf",
    )
    with (
        _admin_guard(),
        patch("routes.corporate_company_kyb.get_corporate_account_by_id", AsyncMock(return_value=company)),
    ):
        resp = test_client.get("/company/c1/kyb")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["state"] == "rejected"
    assert data["review_note"] == "BN mismatch"
    assert data["can_resubmit"] is True
    assert "secret.pdf" not in resp.text  # storage key never leaks


def test_get_kyb_state_requires_admin_role(test_client, rider_override):
    with patch(
        "dependencies.company_guard.list_active_memberships_for_user",
        AsyncMock(return_value=[{"company_id": "c1", "role": "member", "id": "m1"}]),
    ):
        resp = test_client.get("/company/c1/kyb")
    assert resp.status_code == 403


# ── POST /kyb/upload-url ─────────────────────────────────────────────────────


def test_upload_url_rejects_disallowed_content_type(test_client, rider_override):
    with _admin_guard():
        resp = test_client.post("/company/c1/kyb/upload-url", json={"content_type": "application/zip"})
    assert resp.status_code == 415


def test_upload_url_staff_suspended_409(test_client, rider_override):
    with (
        _admin_guard(),
        patch(
            "routes.corporate_company_kyb.get_corporate_account_by_id",
            AsyncMock(return_value=_company(status="suspended")),
        ),
    ):
        resp = test_client.post("/company/c1/kyb/upload-url", json={"content_type": "application/pdf"})
    assert resp.status_code == 409


# ── POST /kyb/submit ─────────────────────────────────────────────────────────


def test_submit_rejects_foreign_or_traversal_paths(test_client, rider_override):
    with _admin_guard():
        for bad in ("kyb/OTHER/doc.pdf", "kyb/c1/../c2/doc.pdf", "etc/passwd"):
            resp = test_client.post("/company/c1/kyb/submit", json={"path": bad})
            assert resp.status_code == 400, bad


def test_submit_requires_uploaded_object(test_client, rider_override):
    with (
        _admin_guard(),
        patch("routes.corporate_company_kyb.get_corporate_account_by_id", AsyncMock(return_value=_company())),
        patch("routes.corporate_company_kyb.kyb_object_exists", AsyncMock(return_value=False)),
    ):
        resp = test_client.post("/company/c1/kyb/submit", json={"path": "kyb/c1/doc.pdf"})
    assert resp.status_code == 400
    assert "upload" in resp.json()["detail"].lower()


def test_submit_happy_path_persists_document(test_client, rider_override):
    updated = _company(kyb_document_url="kyb/c1/doc.pdf", kyb_submitted_at="2026-07-10T00:00:00Z")
    with (
        _admin_guard(),
        patch("routes.corporate_company_kyb.get_corporate_account_by_id", AsyncMock(return_value=_company())),
        patch("routes.corporate_company_kyb.kyb_object_exists", AsyncMock(return_value=True)),
        patch("routes.corporate_company_kyb.set_kyb_document", AsyncMock(return_value=updated)) as m_set,
        patch("routes.corporate_company_kyb.update_corporate_account_status", AsyncMock()) as m_status,
        patch("routes.corporate_company_kyb.log_admin_action", AsyncMock()),
    ):
        resp = test_client.post("/company/c1/kyb/submit", json={"path": "kyb/c1/doc.pdf"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["state"] == "under_review"
    assert data["resubmitted"] is False
    m_set.assert_awaited_once_with(company_id="c1", path="kyb/c1/doc.pdf")
    m_status.assert_not_awaited()  # initial submit doesn't touch status


def test_submit_after_rejection_reenters_queue(test_client, rider_override):
    # New tested transition: suspended(kyb_last_decision=rejected) →
    # pending_verification on resubmit.
    rejected = _company(status="suspended", kyb_last_decision="rejected")
    updated = _company(kyb_document_url="kyb/c1/doc2.pdf", kyb_submitted_at="2026-07-10T00:00:00Z")
    with (
        _admin_guard(),
        patch("routes.corporate_company_kyb.get_corporate_account_by_id", AsyncMock(return_value=rejected)),
        patch("routes.corporate_company_kyb.kyb_object_exists", AsyncMock(return_value=True)),
        patch("routes.corporate_company_kyb.set_kyb_document", AsyncMock(return_value=updated)),
        patch(
            "routes.corporate_company_kyb.update_corporate_account_status",
            AsyncMock(return_value=_company()),
        ) as m_status,
        patch("routes.corporate_company_kyb.log_admin_action", AsyncMock()),
    ):
        resp = test_client.post("/company/c1/kyb/submit", json={"path": "kyb/c1/doc2.pdf"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["resubmitted"] is True
    m_status.assert_awaited_once_with("c1", "pending_verification")


def test_submit_staff_suspended_cannot_self_unsuspend(test_client, rider_override):
    with (
        _admin_guard(),
        patch(
            "routes.corporate_company_kyb.get_corporate_account_by_id",
            AsyncMock(return_value=_company(status="suspended")),  # staff-suspended
        ),
    ):
        resp = test_client.post("/company/c1/kyb/submit", json={"path": "kyb/c1/doc.pdf"})
    assert resp.status_code == 409
    assert "support" in resp.json()["detail"].lower()
