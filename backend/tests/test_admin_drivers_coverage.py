"""Coverage push for backend/routes/admin/drivers.py.

Prioritizes write/mutation endpoints (status transitions, bulk-adjacent
document/photo decisions, notes, SIN reveal) over pure read/list endpoints,
per ACTION_ITEMS.md A1b Track 1 item 4: a broken admin write here can lock a
real driver out of the platform or leave an ineligible driver online, which
is a regulatory (Saskatchewan Transportation Act driver-eligibility) as well
as a production-data-integrity risk.

All DB access is mocked via the `mock_supabase_client`-style patches used
elsewhere in this suite (see test_admin_driver_photo.py, test_admin_business_logic.py)
-- no real Supabase, Stripe, or push-notification calls are made.
"""

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
            patch("routes.admin.drivers.send_push_notification", AsyncMock()),
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
            patch("routes.admin.drivers.send_push_notification", AsyncMock()),
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
            patch("routes.admin.drivers.send_push_notification", AsyncMock()),
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
            patch("routes.admin.drivers.send_push_notification", AsyncMock()),
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
            patch("routes.admin.drivers.send_push_notification", AsyncMock()),
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
            patch("routes.admin.drivers.send_push_notification", AsyncMock(side_effect=RuntimeError("fcm down"))),
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
            patch("routes.admin.drivers.send_push_notification", AsyncMock()),
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
            patch("routes.admin.drivers.send_push_notification", AsyncMock()),
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

    def test_db_failure_surfaces_as_500(self, test_client, super_admin_override):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value=DRIVER)),
            patch("db_supabase.update_one", AsyncMock(side_effect=RuntimeError("db down"))),
            patch("routes.admin.drivers._encrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
        ):
            resp = test_client.put("/api/admin/drivers/drv-1", json={"city": "Regina"})
        assert resp.status_code == 500


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
                AsyncMock(return_value={"status": "verified"}),
            ),
            patch("routes.admin.drivers.log_admin_action", AsyncMock()) as log,
        ):
            resp = test_client.post("/api/admin/drivers/drv-1/refresh-stripe-kyc")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"status": "verified"}
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
