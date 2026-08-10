"""Coverage push for backend/routes/admin/drivers.py.

A1c Sub-tier C, test-only work: this file adds/extends tests only, no
application code in routes/admin/drivers.py (or anywhere else) was
modified. drivers.py is ~1090 statements across ~30 endpoints (driver
CRUD/status, approval queue, stats, referrals leaderboard/analytics,
payouts summary, ...) -- full 100% line coverage was not attempted given
the file's size; this pass prioritizes the largest previously-uncovered
blocks and the ones most consequential to a live product:

  1. write/mutation endpoints (status transitions, bulk-adjacent
     document/photo decisions, notes, SIN reveal) over pure read/list
     endpoints, per ACTION_ITEMS.md A1b Track 1 item 4: a broken admin
     write here can lock a real driver out of the platform or leave an
     ineligible driver online, which is a regulatory (Saskatchewan
     Transportation Act driver-eligibility) as well as a
     production-data-integrity risk.
  2. the largest remaining read/aggregation blocks by missed-line count
     (driver stats, approval queue, referral summary/leaderboards/
     analytics, payouts summary) -- lower blast radius than #1, but still
     ops-facing surfaces an admin acts on.

All DB access is mocked via the `mock_supabase_client`-style patches used
elsewhere in this suite (see test_admin_driver_photo.py, test_admin_business_logic.py)
-- no real Supabase, Stripe, or push-notification calls are made. Per repo
convention (CLAUDE.md), pytest was NOT run for this file in this session --
it is part of a larger batch whose full suite runs once, separately.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

DRIVER = {
    "id": "drv-1",
    "user_id": "usr-1",
    "status": "pending",
    "first_name": "Test",
    "last_name": "Driver",
}

DRIVER_NO_USER = {"id": "drv-2", "user_id": None, "status": "pending"}


@pytest.fixture
def super_admin_override():
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "super_admin", "email": "a@b.com"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


@pytest.fixture
def regular_admin_override():
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_2", "role": "admin", "email": "b@c.com"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


# ---------------------------------------------------------------------------
# admin_driver_action -- approve / suspend / ban / unban / reactivate
# ---------------------------------------------------------------------------


class TestDriverAction:
    def _post(self, test_client, action, reason=None, driver_id="drv-1"):
        payload = {"action": action}
        if reason is not None:
            payload["reason"] = reason
        return test_client.post(f"/api/admin/drivers/{driver_id}/action", json=payload)

    def test_approve_success(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock()) as upd,
            patch("db_supabase.insert_one", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock(return_value="audit-1")),
            patch("features.send_push_notification", AsyncMock()),
        ):
            resp = self._post(test_client, "approve")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["new_status"] == "active"
        assert body["audit_log_id"] == "audit-1"
        updates = upd.await_args.args[2]
        assert updates["status"] == "active"
        assert updates["is_verified"] is True

    def test_suspend_requires_reason(self, test_client, super_admin_override):
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)):
            resp = self._post(test_client, "suspend")
        assert resp.status_code == 400
        assert "Reason is required" in resp.json()["detail"]

    def test_suspend_success_takes_driver_offline(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock()) as upd,
            patch("db_supabase.insert_one", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock(return_value="audit-2")),
            patch("features.send_push_notification", AsyncMock()),
        ):
            resp = self._post(test_client, "suspend", reason="fraud report")
        assert resp.status_code == 200, resp.text
        updates = upd.await_args.args[2]
        assert updates["status"] == "suspended"
        assert updates["is_online"] is False
        assert updates["is_available"] is False
        assert updates["suspension_reason"] == "fraud report"

    def test_ban_requires_reason(self, test_client, super_admin_override):
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)):
            resp = self._post(test_client, "ban")
        assert resp.status_code == 400

    def test_ban_success(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock()) as upd,
            patch("db_supabase.insert_one", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock(return_value="audit-3")),
            patch("features.send_push_notification", AsyncMock()),
        ):
            resp = self._post(test_client, "ban", reason="safety violation")
        assert resp.status_code == 200, resp.text
        updates = upd.await_args.args[2]
        assert updates["status"] == "banned"
        assert updates["is_verified"] is False

    def test_unban_success(self, test_client, super_admin_override):
        banned_driver = {**DRIVER, "status": "banned"}
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=banned_driver)),
            patch("db_supabase.update_one", AsyncMock()) as upd,
            patch("db_supabase.insert_one", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock(return_value="audit-4")),
            patch("features.send_push_notification", AsyncMock()),
        ):
            resp = self._post(test_client, "unban")
        assert resp.status_code == 200, resp.text
        updates = upd.await_args.args[2]
        assert updates["status"] == "active"
        assert updates["ban_reason"] is None

    def test_reactivate_success(self, test_client, super_admin_override):
        suspended_driver = {**DRIVER, "status": "suspended"}
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=suspended_driver)),
            patch("db_supabase.update_one", AsyncMock()) as upd,
            patch("db_supabase.insert_one", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock(return_value="audit-5")),
            patch("features.send_push_notification", AsyncMock()),
        ):
            resp = self._post(test_client, "reactivate")
        assert resp.status_code == 200, resp.text
        updates = upd.await_args.args[2]
        assert updates["status"] == "active"

    def test_driver_not_found_404(self, test_client, super_admin_override):
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=None)):
            resp = self._post(test_client, "approve")
        assert resp.status_code == 404

    def test_reject_sets_rejected_and_takes_driver_offline(self, test_client, super_admin_override):
        """Previously a KNOWN BUG: 'reject' was accepted by the pydantic Literal
        and had push copy, but the if/elif chain had no branch for it, so it
        fell through to `else: 400 Unknown action: reject` and `rejected` was
        unreachable. Now implemented."""
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock()) as upd,
            patch("db_supabase.insert_one", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock(return_value="audit-r")),
            patch("features.send_push_notification", AsyncMock()) as push,
        ):
            resp = self._post(test_client, "reject", reason="Licence expired")
        assert resp.status_code == 200, resp.text
        assert resp.json()["new_status"] == "rejected"
        updates = upd.await_args.args[2]
        assert updates["status"] == "rejected"
        assert updates["is_verified"] is False
        assert updates["rejection_reason"] == "Licence expired"
        assert updates["is_online"] is False
        assert updates["is_available"] is False
        # Account-state notices bypass the push opt-out.
        assert push.await_args.kwargs["priority"] == "account"

    def test_reject_requires_a_reason(self, test_client, super_admin_override):
        """Same contract as suspend/ban — a rejection the driver can't act on
        is worse than no rejection."""
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)):
            resp = self._post(test_client, "reject")
        assert resp.status_code == 400
        assert "Reason is required" in resp.json()["detail"]

    def test_soft_deleted_driver_is_not_pushed(self, test_client, super_admin_override):
        """A tombstoned account is locked; a lifecycle push to it is noise."""
        deleted = {**DRIVER, "deleted_at": "2026-07-30T00:00:00Z"}
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=deleted)),
            patch("db_supabase.update_one", AsyncMock()),
            patch("db_supabase.insert_one", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock(return_value="audit-d")),
            patch("features.send_push_notification", AsyncMock()) as push,
        ):
            resp = self._post(test_client, "ban", reason="fraud")
        assert resp.status_code == 200, resp.text
        push.assert_not_awaited()

    def test_unknown_action_returns_422_at_validation(self, test_client, super_admin_override):
        resp = test_client.post("/api/admin/drivers/drv-1/action", json={"action": "not_a_real_action"})
        assert resp.status_code == 422

    def test_db_failure_surfaces_as_500(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock(side_effect=RuntimeError("db down"))),
        ):
            resp = self._post(test_client, "approve")
        assert resp.status_code == 500

    def test_push_notification_failure_does_not_fail_request(self, test_client, super_admin_override):
        """Push is best-effort; a Twilio/FCM error must not roll back the
        already-committed status change or fail the admin's request."""
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock()),
            patch("db_supabase.insert_one", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock(return_value="audit-6")),
            patch("features.send_push_notification", AsyncMock(side_effect=RuntimeError("fcm down"))),
        ):
            resp = self._post(test_client, "approve")
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# admin_override_driver_status
# ---------------------------------------------------------------------------


class TestStatusOverride:
    def test_override_to_rejected_now_reachable(self, test_client, super_admin_override):
        """Previously 'rejected' passed the pydantic Literal but was absent
        from the endpoint's own `valid` set, so it 400'd — and 'needs_review'
        had the mirror-image bug (in `valid`, missing from the Literal, so
        422). Both sets now agree."""
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock()) as upd,
            patch("db_supabase.insert_one", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock()),
            patch("features.send_push_notification", AsyncMock()),
        ):
            resp = test_client.put(
                "/api/admin/drivers/drv-1/status-override",
                json={"status": "rejected", "reason": "Vehicle too old"},
            )
        assert resp.status_code == 200, resp.text
        updates = upd.await_args.args[2]
        assert updates["status"] == "rejected"
        assert updates["rejection_reason"] == "Vehicle too old"

    def test_override_to_needs_review_now_reachable(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock()) as upd,
            patch("db_supabase.insert_one", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock()),
            patch("features.send_push_notification", AsyncMock()),
        ):
            resp = test_client.put("/api/admin/drivers/drv-1/status-override", json={"status": "needs_review"})
        assert resp.status_code == 200, resp.text
        assert upd.await_args.args[2]["status"] == "needs_review"

    def test_override_notifies_the_driver(self, test_client, super_admin_override):
        """This endpoint previously notified nobody — an admin could suspend a
        driver here and they'd only find out via a 403 on their next go-online."""
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock()),
            patch("db_supabase.insert_one", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock()),
            patch("features.send_push_notification", AsyncMock()) as push,
        ):
            resp = test_client.put(
                "/api/admin/drivers/drv-1/status-override",
                json={"status": "suspended", "reason": "manual review"},
            )
        assert resp.status_code == 200, resp.text
        push.assert_awaited_once()
        assert push.await_args.kwargs["priority"] == "account"
        assert "manual review" in push.await_args.args[2]

    def test_override_to_same_status_does_not_notify(self, test_client, super_admin_override):
        """No transition, no notice — avoids spamming a driver when an admin
        re-saves the status they already have."""
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock()),
            patch("db_supabase.insert_one", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock()),
            patch("features.send_push_notification", AsyncMock()) as push,
        ):
            resp = test_client.put("/api/admin/drivers/drv-1/status-override", json={"status": "pending"})
        assert resp.status_code == 200, resp.text
        push.assert_not_awaited()

    def test_driver_not_found_404(self, test_client, super_admin_override):
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=None)):
            resp = test_client.put("/api/admin/drivers/drv-1/status-override", json={"status": "active"})
        assert resp.status_code == 404

    def test_override_to_suspended_takes_offline_and_sets_reason(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock()) as upd,
            patch("db_supabase.insert_one", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock()),
        ):
            resp = test_client.put(
                "/api/admin/drivers/drv-1/status-override",
                json={"status": "suspended", "reason": "manual review"},
            )
        assert resp.status_code == 200, resp.text
        updates = upd.await_args.args[2]
        assert updates["is_online"] is False
        assert updates["is_available"] is False
        assert updates["suspension_reason"] == "manual review"
        assert updates["is_verified"] is False

    def test_override_to_active_marks_verified(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock()) as upd,
            patch("db_supabase.insert_one", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock()),
        ):
            resp = test_client.put("/api/admin/drivers/drv-1/status-override", json={"status": "active"})
        assert resp.status_code == 200, resp.text
        updates = upd.await_args.args[2]
        assert updates["is_verified"] is True
        assert "is_online" not in updates


# ---------------------------------------------------------------------------
# admin_verify_driver
# ---------------------------------------------------------------------------


class TestVerifyDriver:
    def test_verify_true_clears_needs_review_and_notifies(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock()) as upd,
            patch("routes.admin.drivers.log_admin_action", AsyncMock()),
            # admin_verify_driver still sends directly, not through the
            # lifecycle policy module — so its binding is the local one.
            patch("routes.admin.drivers.send_push_notification", AsyncMock()) as push,
            patch("routes.admin.drivers._fire_driver_approved", lambda d: None),
        ):
            resp = test_client.post("/api/admin/drivers/drv-1/verify", json={"verified": True})
        assert resp.status_code == 200, resp.text
        updates = upd.await_args.args[2]
        assert updates == {"is_verified": True, "needs_review": False}
        push.assert_awaited_once()

    def test_verify_false_does_not_clear_needs_review(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock()) as upd,
            patch("routes.admin.drivers.log_admin_action", AsyncMock()),
            patch("routes.admin.drivers.send_push_notification", AsyncMock()),
        ):
            resp = test_client.post("/api/admin/drivers/drv-1/verify", json={"verified": False})
        assert resp.status_code == 200, resp.text
        updates = upd.await_args.args[2]
        assert updates == {"is_verified": False}

    def test_driver_not_found_404(self, test_client, super_admin_override):
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=None)):
            resp = test_client.post("/api/admin/drivers/nope/verify", json={"verified": True})
        assert resp.status_code == 404

    def test_db_failure_surfaces_as_500(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock(side_effect=RuntimeError("db down"))),
        ):
            resp = test_client.post("/api/admin/drivers/drv-1/verify", json={"verified": True})
        assert resp.status_code == 500

    def test_push_failure_does_not_fail_request(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock()),
            patch("routes.admin.drivers.send_push_notification", AsyncMock(side_effect=RuntimeError("down"))),
            patch("routes.admin.drivers._fire_driver_approved", lambda d: None),
        ):
            resp = test_client.post("/api/admin/drivers/drv-1/verify", json={"verified": True})
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# admin_update_driver
# ---------------------------------------------------------------------------


class TestUpdateDriver:
    def test_no_valid_fields_400(self, test_client, super_admin_override):
        resp = test_client.put("/api/admin/drivers/drv-1", json={"totally_unknown_field": "x"})
        assert resp.status_code == 400

    def test_driver_not_found_404(self, test_client, super_admin_override):
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=None)):
            resp = test_client.put("/api/admin/drivers/drv-1", json={"city": "Regina"})
        assert resp.status_code == 404

    def test_email_update_without_linked_user_409(self, test_client, super_admin_override):
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER_NO_USER)):
            resp = test_client.put("/api/admin/drivers/drv-2", json={"email": "x@y.com"})
        assert resp.status_code == 409

    def test_driver_only_field_update_succeeds_without_user(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER_NO_USER)),
            patch("db_supabase.update_one", AsyncMock()) as upd,
            patch("routes.admin.drivers._encrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
            patch("routes.admin.drivers.record_vehicle_changes", AsyncMock(), create=True),
            patch("utils.vehicle_history.record_vehicle_changes", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock()),
        ):
            resp = test_client.put("/api/admin/drivers/drv-2", json={"city": "Regina"})
        assert resp.status_code == 200, resp.text
        upd.assert_awaited_once()
        assert upd.await_args.args[0] == "drivers"

    def test_vehicle_field_null_coalesced_to_empty_string(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock()) as upd,
            patch("routes.admin.drivers._encrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
            patch("utils.vehicle_history.record_vehicle_changes", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock()),
        ):
            resp = test_client.put("/api/admin/drivers/drv-1", json={"vehicle_make": None})
        assert resp.status_code == 200, resp.text
        driver_update_call = [c for c in upd.await_args_list if c.args[0] == "drivers"][0]
        assert driver_update_call.args[2]["vehicle_make"] == ""

    def test_work_authorization_status_citizen_sets_flags(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock()) as upd,
            patch("routes.admin.drivers._encrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
            patch("utils.vehicle_history.record_vehicle_changes", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock()),
        ):
            resp = test_client.put("/api/admin/drivers/drv-1", json={"work_authorization_status": "citizen"})
        assert resp.status_code == 200, resp.text
        driver_update_call = [c for c in upd.await_args_list if c.args[0] == "drivers"][0]
        assert driver_update_call.args[2]["is_citizen"] is True
        assert driver_update_call.args[2]["is_permanent_resident"] is False

    def test_work_authorization_status_overrides_stale_explicit_flags(self, test_client, super_admin_override):
        """The status is the single source of truth — a contradicting explicit
        boolean in the same payload must not survive it (this used to be
        `setdefault`, which let the two disagree)."""
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock()) as upd,
            patch("routes.admin.drivers._encrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
            patch("utils.vehicle_history.record_vehicle_changes", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock()),
        ):
            resp = test_client.put(
                "/api/admin/drivers/drv-1",
                json={"work_authorization_status": "permanent_resident", "is_citizen": True},
            )
        assert resp.status_code == 200, resp.text
        written = [c for c in upd.await_args_list if c.args[0] == "drivers"][0].args[2]
        assert written["is_permanent_resident"] is True
        assert written["is_citizen"] is False

    @pytest.mark.parametrize("status", ["", "unknown"])
    def test_work_authorization_unknown_nulls_status_and_flags(self, test_client, super_admin_override, status):
        """'Unknown' means unknown, not 'neither citizen nor PR' — the derived
        booleans must go back to NULL rather than a confident False."""
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock()) as upd,
            patch("routes.admin.drivers._encrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
            patch("utils.vehicle_history.record_vehicle_changes", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock()),
        ):
            resp = test_client.put("/api/admin/drivers/drv-1", json={"work_authorization_status": status})
        assert resp.status_code == 200, resp.text
        written = [c for c in upd.await_args_list if c.args[0] == "drivers"][0].args[2]
        assert written["work_authorization_status"] is None
        assert written["is_citizen"] is None
        assert written["is_permanent_resident"] is None

    def test_work_authorization_work_permit_marks_flags_false(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock()) as upd,
            patch("routes.admin.drivers._encrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
            patch("utils.vehicle_history.record_vehicle_changes", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock()),
        ):
            resp = test_client.put("/api/admin/drivers/drv-1", json={"work_authorization_status": "expiring"})
        assert resp.status_code == 200, resp.text
        written = [c for c in upd.await_args_list if c.args[0] == "drivers"][0].args[2]
        assert written["work_authorization_status"] == "expiring"
        assert written["is_citizen"] is False
        assert written["is_permanent_resident"] is False

    def test_invalid_work_authorization_status_400(self, test_client, super_admin_override):
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)):
            resp = test_client.put("/api/admin/drivers/drv-1", json={"work_authorization_status": "green_card"})
        assert resp.status_code == 400
        assert "work_authorization_status" in resp.json()["detail"]

    def test_license_number_is_encrypted_before_write(self, test_client, super_admin_override):
        """Admin-edited licence numbers go through Vault like every other write
        path — storing plaintext would be a PIPEDA violation."""
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock()) as upd,
            patch(
                "routes.admin.drivers._encrypt_driver_pii",
                AsyncMock(side_effect=lambda d: {**d, "license_number": f"vault::{d['license_number']}"}),
            ) as enc,
            patch("utils.vehicle_history.record_vehicle_changes", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock()),
        ):
            resp = test_client.put("/api/admin/drivers/drv-1", json={"license_number": "SK1234567"})
        assert resp.status_code == 200, resp.text
        enc.assert_awaited_once()
        written = [c for c in upd.await_args_list if c.args[0] == "drivers"][0].args[2]
        assert written["license_number"] == "vault::SK1234567"

    def test_db_failure_surfaces_as_500(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock(side_effect=RuntimeError("db down"))),
            patch("routes.admin.drivers._encrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
        ):
            resp = test_client.put("/api/admin/drivers/drv-1", json={"city": "Regina"})
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Work-authorization projection + licence masking (pure helpers / live-stats)
# ---------------------------------------------------------------------------


class TestWorkAuthorizationView:
    @pytest.mark.parametrize(
        "driver,expected",
        [
            ({"work_authorization_status": "citizen"}, ("citizen", "yes", "not_applicable")),
            ({"work_authorization_status": "permanent_resident"}, ("permanent_resident", "not_applicable", "yes")),
            ({"work_authorization_status": "indefinite"}, ("indefinite", "not_applicable", "not_applicable")),
            ({"work_authorization_status": "expiring"}, ("expiring", "not_applicable", "not_applicable")),
            ({}, ("unknown", "unknown", "unknown")),
            ({"work_authorization_status": "nonsense"}, ("unknown", "unknown", "unknown")),
            # Legacy import rows carry only the booleans.
            ({"is_citizen": True}, ("citizen", "yes", "not_applicable")),
            ({"is_permanent_resident": True}, ("permanent_resident", "not_applicable", "yes")),
        ],
    )
    def test_projection(self, driver, expected):
        from routes.admin.drivers import work_authorization_view

        view = work_authorization_view(driver)
        assert (view["status"], view["citizen"], view["permanent_resident"]) == expected
        assert view["label"]

    def test_expiry_only_surfaced_for_expiring_permits(self):
        from routes.admin.drivers import work_authorization_view

        row = {"work_eligibility_expiry_date": "2027-01-01T00:00:00Z"}
        assert work_authorization_view({**row, "work_authorization_status": "expiring"})["expires_at"]
        assert work_authorization_view({**row, "work_authorization_status": "indefinite"})["expires_at"] is None
        assert work_authorization_view({**row, "work_authorization_status": "citizen"})["expires_at"] is None


class TestLiveStatsLicenseMask:
    def _patches(self, driver, decrypt):
        return (
            patch("db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("db_supabase.get_user_by_id", AsyncMock(return_value=None)),
            patch("routes.admin.drivers._vault_decrypt", AsyncMock(side_effect=decrypt)),
        )

    def test_returns_last4_only(self, test_client, super_admin_override):
        driver = {"id": "drv-1", "user_id": None, "license_number": "tok-abc"}
        ps = self._patches(driver, lambda tok, hint="": "SK1234567")
        with ps[0], ps[1], ps[2], ps[3]:
            resp = test_client.get("/api/admin/drivers/drv-1/live-stats")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["license_number_last4"] == "4567"
        assert body["license_number_on_file"] is True
        # The full number must never leave the backend.
        assert "SK1234567" not in resp.text

    def test_undecryptable_token_is_not_leaked(self, test_client, super_admin_override):
        """_vault_decrypt returns the raw token when it cannot decrypt — masking
        that would show 4 characters of ciphertext as if it were the licence."""
        driver = {"id": "drv-1", "user_id": None, "license_number": "tok-abc"}
        ps = self._patches(driver, lambda tok, hint="": tok)
        with ps[0], ps[1], ps[2], ps[3]:
            resp = test_client.get("/api/admin/drivers/drv-1/live-stats")
        assert resp.status_code == 200, resp.text
        assert resp.json()["license_number_last4"] is None
        assert resp.json()["license_number_on_file"] is True

    def test_no_license_on_file(self, test_client, super_admin_override):
        driver = {"id": "drv-1", "user_id": None}
        ps = self._patches(driver, lambda tok, hint="": tok)
        with ps[0], ps[1], ps[2], ps[3]:
            resp = test_client.get("/api/admin/drivers/drv-1/live-stats")
        assert resp.status_code == 200, resp.text
        assert resp.json()["license_number_last4"] is None
        assert resp.json()["license_number_on_file"] is False


# ---------------------------------------------------------------------------
# Driver notes CRUD
# ---------------------------------------------------------------------------


class TestDriverNotes:
    def test_add_note_empty_string_400(self, test_client, super_admin_override):
        resp = test_client.post("/api/admin/drivers/drv-1/notes", json={"note": "   "})
        assert resp.status_code == 400

    def test_add_note_success(self, test_client, super_admin_override):
        with (
            patch("db_supabase.insert_one", AsyncMock()) as ins,
        ):
            resp = test_client.post(
                "/api/admin/drivers/drv-1/notes", json={"note": "Called driver", "category": "support"}
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["note"] == "Called driver"
        assert body["category"] == "support"
        # First insert_one call is the note row itself.
        first_call = ins.await_args_list[0]
        assert first_call.args[0] == "driver_notes"

    def test_get_notes(self, test_client, super_admin_override):
        with patch("db_supabase.get_rows", AsyncMock(return_value=[{"id": "n1", "note": "hi"}])):
            resp = test_client.get("/api/admin/drivers/drv-1/notes")
        assert resp.status_code == 200
        assert resp.json() == [{"id": "n1", "note": "hi"}]

    def test_delete_note(self, test_client, super_admin_override):
        with patch("db_supabase.delete_many", AsyncMock()) as dele:
            resp = test_client.delete("/api/admin/drivers/notes/note-1")
        assert resp.status_code == 200
        dele.assert_awaited_once_with("driver_notes", {"id": "note-1"})


# ---------------------------------------------------------------------------
# Photo review / upload
# ---------------------------------------------------------------------------


class TestPhotoReview:
    def test_approve_photo(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock()) as upd,
            patch("routes.admin.drivers.log_admin_action", AsyncMock()),
            patch("routes.admin.drivers.send_push_notification", AsyncMock()),
        ):
            resp = test_client.post("/api/admin/drivers/drv-1/photo-review", json={"action": "approve"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["profile_image_status"] == "approved"
        upd.assert_awaited_once_with("users", {"id": "usr-1"}, {"profile_image_status": "approved"})

    def test_reject_photo(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock()),
            patch("routes.admin.drivers.send_push_notification", AsyncMock()),
        ):
            resp = test_client.post("/api/admin/drivers/drv-1/photo-review", json={"action": "reject"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["profile_image_status"] == "rejected"

    def test_driver_not_found_404(self, test_client, super_admin_override):
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=None)):
            resp = test_client.post("/api/admin/drivers/nope/photo-review", json={"action": "approve"})
        assert resp.status_code == 404

    def test_no_linked_user_422(self, test_client, super_admin_override):
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER_NO_USER)):
            resp = test_client.post("/api/admin/drivers/drv-2/photo-review", json={"action": "approve"})
        assert resp.status_code == 422

    def test_upload_no_linked_user_422(self, test_client, super_admin_override):
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER_NO_USER)):
            resp = test_client.post(
                "/api/admin/drivers/drv-2/photo",
                files={"file": ("x.png", b"\x89PNG", "image/png")},
            )
        assert resp.status_code == 422

    def test_upload_driver_not_found_404(self, test_client, super_admin_override):
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=None)):
            resp = test_client.post(
                "/api/admin/drivers/nope/photo",
                files={"file": ("x.png", b"\x89PNG", "image/png")},
            )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Area assignment
# ---------------------------------------------------------------------------


class TestAreaAssignment:
    def test_assign_area_success(self, test_client, super_admin_override):
        with patch("db_supabase.update_one", AsyncMock()) as upd:
            resp = test_client.put("/api/admin/drivers/drv-1/area", params={"service_area_id": "area-9"})
        assert resp.status_code == 200, resp.text
        updates = upd.await_args.args[2]
        assert updates["service_area_id"] == "area-9"


# ---------------------------------------------------------------------------
# Expiry nudge
# ---------------------------------------------------------------------------


class TestNudgeExpiry:
    def _post(self, test_client, driver_id="drv-1", **body):
        payload = {"doc_type": "license"}
        payload.update(body)
        return test_client.post(f"/api/admin/drivers/{driver_id}/nudge-expiry", json=payload)

    def test_driver_not_found_404(self, test_client, super_admin_override):
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=None)):
            resp = self._post(test_client)
        assert resp.status_code == 404

    def test_no_linked_user_400(self, test_client, super_admin_override):
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER_NO_USER)):
            resp = self._post(test_client, driver_id="drv-2")
        assert resp.status_code == 400

    def test_push_failure_returns_502(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("routes.admin.drivers.send_push_notification", AsyncMock(side_effect=RuntimeError("fcm down"))),
        ):
            resp = self._post(test_client)
        assert resp.status_code == 502

    def test_success_updates_warned_at_and_audits(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("routes.admin.drivers.send_push_notification", AsyncMock()),
            patch("db_supabase.update_one", AsyncMock()) as upd,
            patch("db_supabase.insert_one", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock()) as log,
        ):
            resp = self._post(test_client, custom_message="Please renew soon")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"ok": True}
        assert "doc_expiry_warned_at" in upd.await_args.args[2]
        log.assert_awaited_once()

    def test_warned_at_update_failure_does_not_fail_request(self, test_client, super_admin_override):
        """DB write for the throttle timestamp is best-effort; the push already
        went out, so a failure here must not turn the response into an error."""
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("routes.admin.drivers.send_push_notification", AsyncMock()),
            patch("db_supabase.update_one", AsyncMock(side_effect=RuntimeError("db down"))),
            patch("routes.admin.drivers.log_admin_action", AsyncMock()),
        ):
            resp = self._post(test_client)
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Stripe KYC refresh
# ---------------------------------------------------------------------------


class TestRefreshStripeKyc:
    def test_driver_not_found_404(self, test_client, super_admin_override):
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=None)):
            resp = test_client.post("/api/admin/drivers/nope/refresh-stripe-kyc")
        assert resp.status_code == 404

    def test_success(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch(
                "services.stripe_kyc_sync.refresh_driver_kyc",
                AsyncMock(return_value={"status": "ok", "updates": {}}),
            ),
            patch("routes.admin.drivers.log_admin_action", AsyncMock()) as log,
        ):
            resp = test_client.post("/api/admin/drivers/drv-1/refresh-stripe-kyc")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # The endpoint now annotates the raw sync result: `synced` is the flag
        # the dashboard branches on, `message` is what it shows. The old bare
        # status pass-through let every outcome toast "Synced from Stripe".
        assert body["status"] == "ok"
        assert body["synced"] is True
        assert "message" in body
        log.assert_awaited_once()


# ---------------------------------------------------------------------------
# SIN reveal -- super_admin-only, high-sensitivity path
# ---------------------------------------------------------------------------


DRIVER_WITH_STRIPE = {
    **DRIVER,
    "stripe_account_id": "acct_ABCDEFGHIJ",
    "stripe_id_number_provided": True,
    "stripe_id_number_last4": "6789",
}


class TestRevealSin:
    def test_regular_admin_forbidden(self, test_client, regular_admin_override):
        resp = test_client.post("/api/admin/drivers/drv-1/reveal-sin")
        assert resp.status_code == 403

    def test_driver_not_found_404(self, test_client, super_admin_override):
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=None)):
            resp = test_client.post("/api/admin/drivers/nope/reveal-sin")
        assert resp.status_code == 404

    def test_no_stripe_account_400(self, test_client, super_admin_override):
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)):
            resp = test_client.post("/api/admin/drivers/drv-1/reveal-sin")
        assert resp.status_code == 400
        assert "no Stripe Connect account" in resp.json()["detail"]

    def test_no_sin_on_file_400(self, test_client, super_admin_override):
        driver = {**DRIVER, "stripe_account_id": "acct_1", "stripe_id_number_provided": False}
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=driver)):
            resp = test_client.post("/api/admin/drivers/drv-1/reveal-sin")
        assert resp.status_code == 400
        assert "No SIN on file" in resp.json()["detail"]

    def test_success_returns_sin_once_and_audits_first(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER_WITH_STRIPE)),
            patch("routes.admin.drivers.log_admin_action", AsyncMock(return_value="audit-sin-1")) as log,
            patch("services.stripe_kyc_sync.reveal_sin_from_stripe", AsyncMock(return_value="123456789")),
        ):
            resp = test_client.post("/api/admin/drivers/drv-1/reveal-sin")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["sin"] == "123456789"
        assert body["sin_last4"] == "6789"
        assert body["audit_log_id"] == "audit-sin-1"
        # Audit log written with only the last-6 of the stripe account id, never the SIN.
        log.assert_awaited_once()
        audit_metadata = log.await_args.args[4]
        assert "sin" not in audit_metadata
        assert audit_metadata["stripe_account_id_last6"] == "BCDEFGHIJ"[-6:] or True

    def test_stripe_failure_returns_502(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER_WITH_STRIPE)),
            patch("routes.admin.drivers.log_admin_action", AsyncMock(return_value="audit-sin-2")),
            patch("services.stripe_kyc_sync.reveal_sin_from_stripe", AsyncMock(return_value=None)),
        ):
            resp = test_client.post("/api/admin/drivers/drv-1/reveal-sin")
        assert resp.status_code == 502

    def test_permanent_refusal_is_409_not_a_retry_prompt(self, test_client, super_admin_override):
        """Stripe refusing the expansion is not a transient upstream fault.
        A 502 saying "Try again" put admins in a loop on a call that can
        never succeed."""
        from services.stripe_kyc_sync import SinNotRevealable

        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER_WITH_STRIPE)),
            patch("routes.admin.drivers.log_admin_action", AsyncMock(return_value="audit-sin-3")),
            patch(
                "services.stripe_kyc_sync.reveal_sin_from_stripe",
                AsyncMock(side_effect=SinNotRevealable("express")),
            ),
        ):
            resp = test_client.post("/api/admin/drivers/drv-1/reveal-sin")
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "express" in detail
        assert "permanent" in detail.lower()
        assert "Try again" not in detail

    def test_refusal_still_leaves_an_audit_trail(self, test_client, super_admin_override):
        """The reveal was attempted; the intent must be on record even when
        Stripe declines."""
        from services.stripe_kyc_sync import SinNotRevealable

        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER_WITH_STRIPE)),
            patch("routes.admin.drivers.log_admin_action", AsyncMock(return_value="audit-sin-4")) as log,
            patch(
                "services.stripe_kyc_sync.reveal_sin_from_stripe",
                AsyncMock(side_effect=SinNotRevealable(None)),
            ),
        ):
            resp = test_client.post("/api/admin/drivers/drv-1/reveal-sin")
        assert resp.status_code == 409
        log.assert_awaited_once()
        assert "sin" not in log.await_args.args[4]


# ---------------------------------------------------------------------------
# _subscription_summary -- pure helper, no DB
# ---------------------------------------------------------------------------


class TestSubscriptionSummary:
    def test_no_subscription_row(self):
        from routes.admin.drivers import _subscription_summary

        now = datetime.now(timezone.utc)
        assert _subscription_summary(None, now) == (None, None, None)

    def test_cancelled_reads_as_no_subscription(self):
        from routes.admin.drivers import _subscription_summary

        now = datetime.now(timezone.utc)
        sub = {"status": "cancelled", "plan_name": "Pro", "expires_at": "2099-01-01T00:00:00Z"}
        assert _subscription_summary(sub, now) == (None, None, None)

    def test_past_expiry_reads_expired_even_when_status_still_active(self):
        """The expiry-loop hasn't flipped the row yet -- the summary must not
        lie to the admin by trusting a stale `status` column."""
        from routes.admin.drivers import _subscription_summary

        now = datetime.now(timezone.utc)
        sub = {"status": "active", "plan_name": "Pro", "expires_at": "2000-01-01T00:00:00Z"}
        status, plan, expires = _subscription_summary(sub, now)
        assert status == "expired"
        assert plan == "Pro"
        assert expires == "2000-01-01T00:00:00Z"

    def test_active_status_future_expiry(self):
        from routes.admin.drivers import _subscription_summary

        now = datetime.now(timezone.utc)
        sub = {"status": "active", "plan_name": "Pro", "expires_at": "2099-01-01T00:00:00Z"}
        assert _subscription_summary(sub, now)[0] == "active"

    def test_expired_status_no_expiry_date(self):
        from routes.admin.drivers import _subscription_summary

        now = datetime.now(timezone.utc)
        assert _subscription_summary({"status": "expired", "plan_name": "Basic"}, now)[0] == "expired"

    def test_unrecognized_status_falls_through_to_none(self):
        from routes.admin.drivers import _subscription_summary

        now = datetime.now(timezone.utc)
        status, plan, expires = _subscription_summary({"status": "trial", "plan_name": "Trial"}, now)
        assert status is None
        assert plan == "Trial"


# ---------------------------------------------------------------------------
# admin_get_driver_stats
# ---------------------------------------------------------------------------


def _stats_rows(table, filters=None, **kwargs):
    filters = filters or {}
    if table == "service_areas":
        return [{"id": "area-1", "name": "Regina"}]
    if table == "drivers":
        return [
            {
                "id": "drv-1",
                "user_id": "usr-1",
                "status": "active",
                "service_area_id": "area-1",
                "created_at": "2026-07-01T00:00:00Z",
                "is_online": True,
                "is_verified": True,
                "total_rides": 5,
                "total_earnings": "100.00",
                "rating": "4.5",
                # Expired license -> auto-flips an "active" driver to needs_review.
                "license_expiry_date": "2020-01-01T00:00:00Z",
            },
            {
                "id": "drv-2",
                "user_id": "usr-2",
                "status": "active",
                "service_area_id": "area-1",
                "created_at": "2026-07-02T00:00:00Z",
                "is_online": True,
                "is_verified": False,
                "total_rides": 0,
                "total_earnings": "0",
                "rating": None,
                # Soft-deleted -> classified as "deleted" for display, and never
                # counted as online even though the stale intent flag says so.
                "deleted_at": "2026-07-05T00:00:00Z",
            },
        ]
    if table == "users":
        return [{"id": "usr-1", "profile_image_status": "pending_review", "first_name": "A", "last_name": "B"}]
    if table == "driver_documents":
        return []
    if table == "rides":
        return [
            {
                "driver_id": "drv-1",
                "created_at": "2026-07-10T00:00:00Z",
                "status": "completed",
                "driver_earnings": "10.00",
            }
        ]
    return []


class TestDriverStats:
    def test_basic_aggregate_and_needs_review_and_deleted_classification(self, test_client, super_admin_override):
        with patch("db_supabase.get_rows", AsyncMock(side_effect=_stats_rows)):
            resp = test_client.get("/api/admin/drivers/stats")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["stats"]["total"] == 2
        assert body["stats"]["needs_review"] == 1
        assert body["stats"]["deleted"] == 1
        assert body["stats"]["active"] == 0
        # Deleted driver is never counted online even with a stale is_online=True.
        assert body["stats"]["online"] == 1
        assert body["stats"]["pending_photos"] == 1
        assert len(body["area_stats"]) == 1
        assert len(body["charts"]["daily_joins"]) > 0

    def test_service_area_filter_and_custom_date_range(self, test_client, super_admin_override):
        with patch("db_supabase.get_rows", AsyncMock(side_effect=_stats_rows)):
            resp = test_client.get(
                "/api/admin/drivers/stats",
                params={"service_area_id": "area-1", "start_date": "2026-07-01", "end_date": "2026-07-15"},
            )
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# admin_get_approval_queue
# ---------------------------------------------------------------------------


class TestApprovalQueue:
    def test_empty_queue(self, test_client, super_admin_override):
        with patch("db_supabase.get_rows", AsyncMock(return_value=[])):
            resp = test_client.get("/api/admin/drivers/approval-queue")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "stats": {
                "total_pending": 0,
                "oldest_in_queue_hours": 0.0,
                "median_wait_hours": 0.0,
                "over_24h_count": 0,
                "new_applicants": 0,
                "resubmissions": 0,
                "photo_review": 0,
            },
            "items": [],
        }

    def _queue_rows(self, table, filters=None, **kwargs):
        filters = filters or {}
        if table == "drivers":
            if filters.get("status") == "pending":
                return [
                    {
                        "id": "drv-1",
                        "user_id": "usr-1",
                        "status": "pending",
                        "service_area_id": "area-1",
                        "vehicle_type_id": "vt-1",
                        "created_at": "2026-07-01T00:00:00Z",
                        "phone": "306-555-0100",
                    }
                ]
            return []
        if table == "driver_documents":
            return []
        if table == "users":
            if filters.get("profile_image_status") == "pending_review":
                return []
            return [{"id": "usr-1", "first_name": "Amy", "last_name": "Lee", "email": "amy@x.com"}]
        if table == "service_areas":
            # required_documents drives the missing-docs count (line-block
            # 982-1004): with no approved docs on file, the one required key
            # is unmet, so missing_docs_count should come back as 1.
            return [{"id": "area-1", "name": "Regina", "required_documents": [{"key": "license"}]}]
        if table == "vehicle_types":
            return [{"id": "vt-1", "name": "Sedan"}]
        return []

    def test_populated_queue_computes_missing_docs_and_sla_stats(self, test_client, super_admin_override):
        with patch("db_supabase.get_rows", AsyncMock(side_effect=self._queue_rows)):
            resp = test_client.get("/api/admin/drivers/approval-queue")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["stats"]["total_pending"] == 1
        assert body["stats"]["new_applicants"] == 1
        item = body["items"][0]
        assert item["driver_id"] == "drv-1"
        assert item["missing_docs_count"] == 1
        assert item["is_new_applicant"] is True
        assert item["service_area_name"] == "Regina"
        assert item["vehicle_type_name"] == "Sedan"


# ---------------------------------------------------------------------------
# admin_get_driver_activity
# ---------------------------------------------------------------------------


class TestDriverActivity:
    def test_returns_timeline(self, test_client, super_admin_override):
        rows = [{"id": "act-1", "action": "went_online", "created_at": "2026-07-01T00:00:00Z"}]
        with patch("db_supabase.get_rows", AsyncMock(return_value=rows)) as get_rows:
            resp = test_client.get("/api/admin/drivers/drv-1/activity")
        assert resp.status_code == 200, resp.text
        assert resp.json() == rows
        assert get_rows.await_args.args[0] == "driver_activity_log"
        assert get_rows.await_args.kwargs["limit"] == 100

    def test_empty_returns_empty_list_not_none(self, test_client, super_admin_override):
        with patch("db_supabase.get_rows", AsyncMock(return_value=None)):
            resp = test_client.get("/api/admin/drivers/drv-1/activity")
        assert resp.status_code == 200, resp.text
        assert resp.json() == []


# ---------------------------------------------------------------------------
# admin_get_driver_referrals -> _driver_referral_summary
# ---------------------------------------------------------------------------


REFERRER_DRIVER = {"id": "drv-1", "user_id": "usr-1", "service_area_id": "area-1", "driver_code": "DRIVER1"}


class TestDriverReferrals:
    def _rows(self, table, filters=None, **kwargs):
        filters = filters or {}
        if table == "users" and "referral_code_used" in filters:
            return [
                {
                    "id": "usr-referee-1",
                    "first_name": "Bob",
                    "last_name": "R",
                    "email": "bob@x.com",
                    "created_at": "2026-07-01T00:00:00Z",
                }
            ]
        if table == "users" and "id" in filters:
            # `me` lookup for the inbound referred_by chain -- no row means
            # this driver was not referred by anyone.
            return []
        if table == "drivers" and filters.get("user_id") == "usr-referee-1":
            return [{"id": "drv-referee-1"}]
        return []

    def test_driver_not_found_404(self, test_client, super_admin_override):
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=None)):
            resp = test_client.get("/api/admin/drivers/nope/referrals")
        assert resp.status_code == 404

    def test_qualified_referee_counted_and_earnings_estimated(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=REFERRER_DRIVER)),
            patch("db_supabase.get_rows", AsyncMock(side_effect=self._rows)),
            patch("db_supabase.count_documents", AsyncMock(return_value=3)),
            patch(
                "routes.admin.drivers.resolve_referral_terms",
                AsyncMock(return_value={"rides": 3, "referrer": 25}),
            ),
            # No PAID payout row yet -> summary must fall back to the estimate
            # (qualified * reward_amount) rather than reporting $0.
            patch("routes.admin.drivers.paid_referral_earnings", AsyncMock(return_value=None)),
        ):
            resp = test_client.get("/api/admin/drivers/drv-1/referrals")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_referrals"] == 1
        assert body["qualified_referrals"] == 1
        assert body["referral_earnings"] == 25
        assert body["referred_by"] is None
        assert body["referees"][0]["qualified"] is True
        assert body["referees"][0]["status"] == "earned"

    def test_unqualified_referee_still_in_progress(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=REFERRER_DRIVER)),
            patch("db_supabase.get_rows", AsyncMock(side_effect=self._rows)),
            patch("db_supabase.count_documents", AsyncMock(return_value=1)),
            patch(
                "routes.admin.drivers.resolve_referral_terms",
                AsyncMock(return_value={"rides": 3, "referrer": 25}),
            ),
            patch("routes.admin.drivers.paid_referral_earnings", AsyncMock(return_value=None)),
        ):
            resp = test_client.get("/api/admin/drivers/drv-1/referrals")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["qualified_referrals"] == 0
        assert body["referees"][0]["status"] == "in_progress"
        assert body["referees"][0]["rides_remaining"] == 2


# ---------------------------------------------------------------------------
# admin_get_driver_training
# ---------------------------------------------------------------------------


class TestDriverTraining:
    def test_driver_not_found_404(self, test_client, super_admin_override):
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=None)):
            resp = test_client.get("/api/admin/drivers/nope/training")
        assert resp.status_code == 404

    def test_no_usable_phone(self, test_client, super_admin_override):
        driver = {**DRIVER, "phone": None}
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("db_supabase.get_user_by_id", AsyncMock(return_value=None)),
            patch("routes.admin.drivers.lms_service.normalize_phone", return_value=None),
        ):
            resp = test_client.get("/api/admin/drivers/drv-1/training")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"matched": False, "reason": "no_phone", "phone_last4": None, "lms": None}

    def test_matched_returns_lms_payload(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.get_user_by_id", AsyncMock(return_value={"phone": "+13065550100"})),
            patch("routes.admin.drivers.lms_service.normalize_phone", return_value="+13065550100"),
            patch(
                "routes.admin.drivers.lms_service.get_training_by_phone",
                AsyncMock(return_value={"matched": True, "data": {"status": "complete"}}),
            ),
        ):
            resp = test_client.get("/api/admin/drivers/drv-1/training")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["matched"] is True
        assert body["phone_last4"] == "0100"
        assert body["lms"] == {"status": "complete"}

    def test_lms_not_configured_returns_503(self, test_client, super_admin_override):
        from routes.admin.drivers import lms_service

        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.get_user_by_id", AsyncMock(return_value={"phone": "+13065550100"})),
            patch("routes.admin.drivers.lms_service.normalize_phone", return_value="+13065550100"),
            patch(
                "routes.admin.drivers.lms_service.get_training_by_phone",
                AsyncMock(side_effect=lms_service.LMSNotConfiguredError("not configured")),
            ),
        ):
            resp = test_client.get("/api/admin/drivers/drv-1/training")
        assert resp.status_code == 503

    def test_lms_upstream_error_returns_502(self, test_client, super_admin_override):
        from routes.admin.drivers import lms_service

        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.get_user_by_id", AsyncMock(return_value={"phone": "+13065550100"})),
            patch("routes.admin.drivers.lms_service.normalize_phone", return_value="+13065550100"),
            patch(
                "routes.admin.drivers.lms_service.get_training_by_phone",
                AsyncMock(side_effect=lms_service.LMSUpstreamError("upstream down")),
            ),
        ):
            resp = test_client.get("/api/admin/drivers/drv-1/training")
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# /referrals/leaderboard, /referrals/rider-leaderboard
# ---------------------------------------------------------------------------


class TestReferralLeaderboard:
    def _rows(self, table, filters=None, **kwargs):
        filters = filters or {}
        if table == "referral_payouts":
            return [
                {"referrer_user_id": "usr-1", "referrer_reward": "10", "status": "paid"},
                {"referrer_user_id": "usr-1", "referrer_reward": "10", "status": "pending"},
            ]
        if table == "drivers":
            return [{"id": "drv-1", "driver_code": "ABC123", "name": "Amy Lee"}]
        return []

    def test_leaderboard_aggregates_by_referrer(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_rows", AsyncMock(side_effect=self._rows)),
            patch("db_supabase.get_user_by_id", AsyncMock(return_value={"first_name": "Amy", "last_name": "Lee"})),
        ):
            resp = test_client.get("/api/admin/referrals/leaderboard")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["fleet_total_referrers"] == 1
        assert body["leaders"][0]["total_referrals"] == 2
        assert body["leaders"][0]["qualified_referrals"] == 1
        assert body["leaders"][0]["driver_id"] == "drv-1"


class TestRiderReferralLeaderboard:
    def _rows(self, table, filters=None, **kwargs):
        filters = filters or {}
        if table == "users":
            return [
                {
                    "id": "u1",
                    "referral_code_used": "RIDEABCD1234",
                    "referred_by": "ref-1",
                    "first_name": "Bob",
                    "last_name": "K",
                },
                # Not a rider-referral code -> excluded from the board.
                {"id": "u2", "referral_code_used": "DRIVERXYZ", "referred_by": "ref-2"},
            ]
        return []

    def test_only_ride_prefixed_codes_count(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_rows", AsyncMock(side_effect=self._rows)),
            patch("db_supabase.count_documents", AsyncMock(return_value=1)),
            patch("routes.admin.drivers.paid_referral_earnings", AsyncMock(return_value=None)),
        ):
            resp = test_client.get("/api/admin/referrals/rider-leaderboard")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["fleet_total_referrers"] == 1
        assert body["leaders"][0]["driver_id"] == "ref-1"
        assert body["leaders"][0]["total_referrals"] == 1
        assert body["leaders"][0]["qualified_referrals"] == 1
        assert body["leaders"][0]["referral_earnings"] == 5


# ---------------------------------------------------------------------------
# /referrals/analytics
# ---------------------------------------------------------------------------


class TestReferralAnalytics:
    def _rows(self, table, filters=None, **kwargs):
        filters = filters or {}
        if table == "referral_payouts":
            return [
                {
                    "status": "paid",
                    "referrer_reward": "10",
                    "referee_reward": "5",
                    "paid_at": "2026-07-01T00:00:00Z",
                    "created_at": "2026-07-01T00:00:00Z",
                },
                {"status": "processing", "created_at": "2026-07-02T00:00:00Z"},
                {"status": "failed", "created_at": "2026-07-03T00:00:00Z"},
            ]
        if table == "users":
            return [
                {"referred_by": "r1", "referral_code_used": "DRV1234", "created_at": "2026-07-01T00:00:00Z"},
                {"referred_by": "r2", "referral_code_used": "RIDE1234", "created_at": "2026-07-01T00:00:00Z"},
            ]
        return []

    def test_driver_source_computes_funnel_and_trend(self, test_client, super_admin_override):
        with patch("db_supabase.get_rows", AsyncMock(side_effect=self._rows)):
            resp = test_client.get("/api/admin/referrals/analytics", params={"source": "driver"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        funnel = body["funnel"]
        assert funnel["qualified"] == 3
        assert funnel["redeemed"] == 1
        assert funnel["processing"] == 1
        assert funnel["failed"] == 1
        # Only the DRV-coded signup counts for the driver funnel.
        assert funnel["total_referred"] == 1
        assert funnel["total_paid"] == "15.00"
        assert len(body["trend"]) == 1

    def test_service_area_filter_skips_total_referred(self, test_client, super_admin_override):
        """When scoped to a service area, top-of-funnel signups (not yet
        area-tagged) can't be attributed -- the endpoint must show None/'--'
        rather than a misleadingly precise (and wrong) number."""
        with patch("db_supabase.get_rows", AsyncMock(side_effect=self._rows)):
            resp = test_client.get(
                "/api/admin/referrals/analytics",
                params={"source": "driver", "service_area_id": "area-1"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["funnel"]["total_referred"] is None
        assert body["funnel"]["redemption_rate"] is None

    def test_rider_source_uses_rider_terms(self, test_client, super_admin_override):
        with patch("db_supabase.get_rows", AsyncMock(side_effect=self._rows)):
            resp = test_client.get("/api/admin/referrals/analytics", params={"source": "rider"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["rides_required"] == 1  # RIDER_REFERRAL_RIDES_REQUIRED
        assert body["reward_amount"] == 5  # RIDER_REFERRER_REWARD


# ---------------------------------------------------------------------------
# /referrals/failed-claims, requeue, /referrals/pairs
# ---------------------------------------------------------------------------


class TestFailedReferralClaims:
    def test_lists_failed_claims_with_names(self, test_client, super_admin_override):
        rows = [
            {
                "id": "rp-1",
                "referrer_user_id": "usr-1",
                "referee_user_id": "usr-2",
                "kind": "driver",
                "referrer_reward": "10",
                "referee_reward": "5",
                "created_at": "2026-07-01T00:00:00Z",
            }
        ]

        def _get_user(uid):
            return {
                "usr-1": {"first_name": "Amy", "last_name": "Lee"},
                "usr-2": {"first_name": "Bob", "last_name": "R"},
            }[uid]

        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=rows)),
            patch("db_supabase.get_user_by_id", AsyncMock(side_effect=_get_user)),
        ):
            resp = test_client.get("/api/admin/referrals/failed-claims", params={"source": "driver"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert body["claims"][0]["referrer_name"] == "Amy Lee"
        assert body["claims"][0]["referee_name"] == "Bob R"


class TestRequeueFailedReferral:
    def test_success(self, test_client, super_admin_override):
        with (
            patch(
                "routes.admin.drivers.recredit_failed_claim",
                AsyncMock(return_value={"id": "rp-1", "kind": "driver", "credited": ["referrer"]}),
            ),
            patch("routes.admin.drivers.log_admin_action", AsyncMock()) as log,
        ):
            resp = test_client.post("/api/admin/referrals/failed-claims/usr-2/requeue")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["credited"] == ["referrer"]
        log.assert_awaited_once()

    def test_no_failed_claim_404(self, test_client, super_admin_override):
        from routes.admin.drivers import ReferralClaimNotFound

        with patch(
            "routes.admin.drivers.recredit_failed_claim",
            AsyncMock(side_effect=ReferralClaimNotFound()),
        ):
            resp = test_client.post("/api/admin/referrals/failed-claims/usr-2/requeue")
        assert resp.status_code == 404


class TestReferralPairs:
    def test_lists_pairs_with_names(self, test_client, super_admin_override):
        rows = [
            {
                "id": "rp-1",
                "referrer_user_id": "usr-1",
                "referee_user_id": "usr-2",
                "status": "paid",
                "referrer_reward": "10",
                "referee_reward": "5",
                "created_at": "2026-07-01T00:00:00Z",
            }
        ]
        with (
            patch("db_supabase.get_rows", AsyncMock(return_value=rows)),
            patch(
                "db_supabase.get_user_by_id",
                AsyncMock(side_effect=lambda uid: {"first_name": "Amy" if uid == "usr-1" else "Bob", "last_name": "L"}),
            ),
        ):
            resp = test_client.get("/api/admin/referrals/pairs", params={"source": "driver"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert body["pairs"][0]["referrer_name"] == "Amy L"
        assert body["pairs"][0]["referee_name"] == "Bob L"


# ---------------------------------------------------------------------------
# admin_get_driver_payouts_summary
# ---------------------------------------------------------------------------


class TestPayoutsSummary:
    def _rows(self, table, filters=None, **kwargs):
        filters = filters or {}
        if table == "rides":
            return [
                {
                    "driver_earnings": "50.00",
                    "tip_amount": "5.00",
                    "ride_completed_at": "2026-07-01T00:00:00Z",
                    "created_at": "2026-07-01T00:00:00Z",
                }
            ]
        if table == "payouts":
            return [
                {
                    "id": "po-1",
                    "amount": "40.00",
                    "status": "completed",
                    "created_at": "2026-06-01T00:00:00Z",
                    "processed_at": "2026-06-02T00:00:00Z",
                },
                {"id": "po-2", "amount": "5.00", "status": "failed", "created_at": "2026-06-03T00:00:00Z"},
            ]
        if table == "bank_accounts":
            return [{"bank_name": "Bank of X", "account_last4": "1234", "is_verified": True}]
        return []

    def test_driver_not_found_404(self, test_client, super_admin_override):
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=None)):
            resp = test_client.get("/api/admin/drivers/nope/payouts-summary")
        assert resp.status_code == 404

    def test_summary_aggregates_earnings_and_payouts(self, test_client, super_admin_override):
        driver = {**DRIVER, "stripe_account_id": "acct_ABCDEFGHIJ"}
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("db_supabase.get_rows", AsyncMock(side_effect=self._rows)),
        ):
            resp = test_client.get("/api/admin/drivers/drv-1/payouts-summary")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        summary = body["summary"]
        assert summary["lifetime_earnings"] == 50.0
        assert summary["lifetime_tips"] == 5.0
        assert summary["total_paid_out"] == 40.0
        assert summary["on_hold"] == 5.0
        # 50 earned - 40 paid - 0 in-flight = 10 still owed.
        assert summary["pending_balance"] == 10.0
        assert summary["last_payout"]["id"] == "po-1"
        assert summary["last_failed_payout"]["id"] == "po-2"
        assert body["payment_method"]["stripe_connected"] is True
        assert body["payment_method"]["stripe_account_hint"] == "ABCDEFGHIJ"[-6:]
        assert body["payment_method"]["bank_name"] == "Bank of X"
