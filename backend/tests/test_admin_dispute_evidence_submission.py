"""C23 item 5: POST /api/admin/disputes/{dispute_id}/submit-evidence
(routes/admin/dispute_evidence_submission.py). The highest-risk piece of
C23 -- a real, effectively irreversible Stripe write -- so this suite pins
every guard: flag-off rejection, missing-confirm rejection, idempotency
(already-submitted 409), Stripe-error surfacing (502, not swallowed), and
require_super_admin (stricter than item 4's read-only pack, which stays on
the general "support" module gate)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_SUPER_ADMIN = {"id": "admin-1", "role": "super_admin", "email": "a@spinr.app", "modules": []}
_SUPPORT_ADMIN = {"id": "admin-2", "role": "admin", "email": "b@spinr.app", "modules": ["support"]}

_DISPUTE_ROW = {
    "id": "sd-1",
    "stripe_dispute_id": "dp_1",
    "ride_id": "ride-1",
    "amount_cents": 1150,
    "reason": "fraudulent",
    "status": "needs_response",
    "evidence_submitted_at": None,
}

_RIDE = {
    "id": "ride-1",
    "ride_code": "SPN-1",
    "rider_id": "rider-1",
    "created_at": "2026-01-01T10:00:00+00:00",
}


@pytest.fixture
def client(test_client):
    return test_client


@pytest.fixture
def app_fixture():
    from backend.server import app

    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def as_super_admin(app_fixture):
    from dependencies import get_admin_user

    app_fixture.dependency_overrides[get_admin_user] = lambda: _SUPER_ADMIN
    yield
    app_fixture.dependency_overrides.clear()


@pytest.fixture
def as_support_admin(app_fixture):
    """A non-super_admin with the 'support' module -- enough for item 4's
    read-only pack, but must be REJECTED here (require_super_admin)."""
    from dependencies import get_admin_user

    app_fixture.dependency_overrides[get_admin_user] = lambda: _SUPPORT_ADMIN
    yield
    app_fixture.dependency_overrides.clear()


def _post(client, dispute_id="sd-1", **body):
    return client.post(f"/api/admin/disputes/{dispute_id}/submit-evidence", json=body)


class TestAuthz:
    def test_denied_without_admin(self, client):
        resp = _post(client, confirm=True)
        assert resp.status_code in (401, 403)

    def test_denied_for_non_super_admin(self, client, as_support_admin):
        resp = _post(client, confirm=True)
        assert resp.status_code == 403


class TestConfirmGuard:
    def test_missing_confirm_rejected_before_any_db_call(self, client, as_super_admin):
        with patch("db_supabase.get_rows", AsyncMock()) as get_rows:
            resp = _post(client)  # confirm defaults to False
        assert resp.status_code == 400
        get_rows.assert_not_awaited()


class TestFlagGuard:
    def test_flag_off_rejected_with_503_and_audited(self, client, as_super_admin):
        log_mock = AsyncMock(return_value="audit-1")
        with (
            patch("routes.admin.dispute_evidence_submission.get_app_settings", AsyncMock(return_value={})),
            patch("routes.admin.dispute_evidence_submission.log_admin_action", log_mock),
        ):
            resp = _post(client, confirm=True)
        assert resp.status_code == 503
        log_mock.assert_awaited_once()
        assert log_mock.await_args.args[1] == "dispute_evidence_submit_rejected_flag_off"

    def test_flag_explicitly_false_also_rejected(self, client, as_super_admin):
        with patch(
            "routes.admin.dispute_evidence_submission.get_app_settings",
            AsyncMock(return_value={"dispute_stripe_evidence_submission_enabled": False}),
        ):
            resp = _post(client, confirm=True)
        assert resp.status_code == 503


def _flag_on_settings(**extra):
    return {"dispute_stripe_evidence_submission_enabled": True, "stripe_secret_key": "sk_test_x", **extra}


class TestDisputeLookup:
    def test_dispute_not_found_404(self, client, as_super_admin):
        with (
            patch(
                "routes.admin.dispute_evidence_submission.get_app_settings",
                AsyncMock(return_value=_flag_on_settings()),
            ),
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            resp = _post(client, confirm=True)
        assert resp.status_code == 404

    def test_already_submitted_returns_409_not_double_submitted(self, client, as_super_admin):
        already = {**_DISPUTE_ROW, "evidence_submitted_at": "2026-01-01T00:00:00+00:00"}
        with (
            patch(
                "routes.admin.dispute_evidence_submission.get_app_settings",
                AsyncMock(return_value=_flag_on_settings()),
            ),
            patch("db_supabase.get_rows", AsyncMock(return_value=[already])),
        ):
            resp = _post(client, confirm=True)
        assert resp.status_code == 409


class TestStripeCall:
    def test_stripe_error_surfaces_502_not_swallowed(self, client, as_super_admin):
        with (
            patch(
                "routes.admin.dispute_evidence_submission.get_app_settings",
                AsyncMock(return_value=_flag_on_settings()),
            ),
            patch("db_supabase.get_rows", AsyncMock(return_value=[_DISPUTE_ROW])),
            patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=_RIDE)),
            patch("stripe.Dispute.modify", MagicMock(side_effect=Exception("stripe down"))),
        ):
            resp = _post(client, confirm=True)
        assert resp.status_code == 502

    def test_happy_path_submits_and_claims(self, client, as_super_admin):
        update_mock = AsyncMock(return_value={"id": "sd-1"})
        log_mock = AsyncMock(return_value="audit-2")
        modify_mock = MagicMock(return_value={"id": "dp_1"})
        with (
            patch(
                "routes.admin.dispute_evidence_submission.get_app_settings",
                AsyncMock(return_value=_flag_on_settings()),
            ),
            patch("db_supabase.get_rows", AsyncMock(return_value=[_DISPUTE_ROW])),
            patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=_RIDE)),
            patch("db_supabase.update_one", update_mock),
            patch("routes.admin.dispute_evidence_submission.log_admin_action", log_mock),
            patch("stripe.Dispute.modify", modify_mock),
        ):
            resp = _post(client, confirm=True)
        assert resp.status_code == 200
        body = resp.json()
        assert body["submitted"] is True
        assert body["stripe_dispute_id"] == "dp_1"

        modify_mock.assert_called_once()
        assert modify_mock.call_args.args[0] == "dp_1"
        assert modify_mock.call_args.kwargs["api_key"] == "sk_test_x"

        update_mock.assert_awaited_once()
        assert update_mock.await_args.args[0] == "stripe_disputes"
        assert update_mock.await_args.args[1] == {"id": "sd-1", "evidence_submitted_at": None}

        log_mock.assert_awaited_once()
        assert log_mock.await_args.args[1] == "dispute_evidence_submitted"

    def test_custom_evidence_text_used_when_provided(self, client, as_super_admin):
        modify_mock = MagicMock(return_value={"id": "dp_1"})
        with (
            patch(
                "routes.admin.dispute_evidence_submission.get_app_settings",
                AsyncMock(return_value=_flag_on_settings()),
            ),
            patch("db_supabase.get_rows", AsyncMock(return_value=[_DISPUTE_ROW])),
            patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=_RIDE)),
            patch("db_supabase.update_one", AsyncMock(return_value={"id": "sd-1"})),
            patch("routes.admin.dispute_evidence_submission.log_admin_action", AsyncMock()),
            patch("stripe.Dispute.modify", modify_mock),
        ):
            resp = _post(client, confirm=True, uncategorized_text="Edited by support agent.")
        assert resp.status_code == 200
        assert modify_mock.call_args.kwargs["evidence"]["uncategorized_text"] == "Edited by support agent."

    def test_no_stripe_secret_configured_503(self, client, as_super_admin):
        settings = _flag_on_settings()
        settings.pop("stripe_secret_key")
        with (
            patch("routes.admin.dispute_evidence_submission.get_app_settings", AsyncMock(return_value=settings)),
            patch("db_supabase.get_rows", AsyncMock(return_value=[_DISPUTE_ROW])),
            patch("db_supabase.get_ride_details_enriched", AsyncMock(return_value=_RIDE)),
        ):
            resp = _post(client, confirm=True)
        assert resp.status_code == 503

    def test_missing_ride_still_submits_with_fallback_text(self, client, as_super_admin):
        """A dispute may have no linked ride_id -- must not crash building
        the fallback cover-letter text."""
        dispute_no_ride = {**_DISPUTE_ROW, "ride_id": None}
        modify_mock = MagicMock(return_value={"id": "dp_1"})
        with (
            patch(
                "routes.admin.dispute_evidence_submission.get_app_settings",
                AsyncMock(return_value=_flag_on_settings()),
            ),
            patch("db_supabase.get_rows", AsyncMock(return_value=[dispute_no_ride])),
            patch("db_supabase.update_one", AsyncMock(return_value={"id": "sd-1"})),
            patch("routes.admin.dispute_evidence_submission.log_admin_action", AsyncMock()),
            patch("stripe.Dispute.modify", modify_mock),
        ):
            resp = _post(client, confirm=True)
        assert resp.status_code == 200
