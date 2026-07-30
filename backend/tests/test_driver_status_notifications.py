"""Driver lifecycle notification policy.

Guards two things that are easy to break silently: the copy for
already-shipped notifications, and which notices bypass the push opt-out.
"""

import pytest

from utils.driver_status_notifications import (
    ACCOUNT_PRIORITY,
    NORMAL_PRIORITY,
    action_message,
    should_notify_driver,
    status_message,
)


class TestRecipientGuard:
    def test_soft_deleted_driver_is_never_notified(self):
        """Account deletion tombstones the row and locks the account; a
        lifecycle push to it is noise at best."""
        assert should_notify_driver({"user_id": "u1", "deleted_at": "2026-07-30T00:00:00Z"}) is False

    def test_driver_without_user_id_is_never_notified(self):
        assert should_notify_driver({"user_id": None}) is False

    def test_live_driver_is_notified(self):
        assert should_notify_driver({"user_id": "u1", "deleted_at": None}) is True


class TestOptOutTier:
    @pytest.mark.parametrize("action", ["reject", "suspend", "ban"])
    def test_blocking_actions_bypass_the_opt_out(self, action):
        """A driver who can no longer earn must be told, opted out or not."""
        assert action_message(action, reason="policy")["priority"] == ACCOUNT_PRIORITY

    @pytest.mark.parametrize("action", ["approve", "unban", "reactivate"])
    def test_restoring_actions_respect_the_opt_out(self, action):
        """Good news is informational — no reason to override a preference."""
        assert action_message(action)["priority"] == NORMAL_PRIORITY

    @pytest.mark.parametrize("status", ["rejected", "suspended", "banned"])
    def test_blocking_statuses_bypass_via_the_status_path_too(self, status):
        assert status_message(status)["priority"] == ACCOUNT_PRIORITY

    def test_needs_review_is_informational(self):
        assert status_message("needs_review")["priority"] == NORMAL_PRIORITY


class TestActionCopy:
    def test_suspend_with_reason_matches_the_previously_shipped_string(self):
        """This copy shipped from the inline map in routes/admin/drivers.py.
        Moving it must not reword it."""
        msg = action_message("suspend", reason="Too many cancellations")
        assert msg["title"] == "Account Suspended ⚠️"
        assert msg["body"] == "Your account has been suspended. Reason: Too many cancellations"

    def test_approve_copy_unchanged(self):
        msg = action_message("approve")
        assert msg["title"] == "You're Approved! 🎉"
        assert msg["body"] == ("Your driver application has been approved. You can now go online and start earning!")

    def test_ban_does_not_echo_the_admin_reason(self):
        """Ban reasons are internal admin text, not vetted customer copy."""
        msg = action_message("ban", reason="fraud ring #4412")
        assert "fraud ring" not in msg["body"]
        assert "Contact support" in msg["body"]

    def test_reject_carries_the_reason(self):
        msg = action_message("reject", reason="Licence expired")
        assert msg["body"].endswith("Reason: Licence expired")

    def test_data_payload_carries_action_and_target_status(self):
        assert action_message("reject")["data"] == {"type": "driver_reject", "new_status": "rejected"}

    def test_unknown_action_has_no_message(self):
        assert action_message("teleport") is None


class TestStatusCopy:
    def test_needs_review_explains_the_forced_offline(self):
        """The driver is knocked offline by this transition — the push is the
        only thing that tells them why."""
        msg = status_message("needs_review")
        assert msg["title"] == "Changes Under Review"
        assert "offline" in msg["body"]

    def test_pending_has_no_message(self):
        """Signup already shows the next step; the recurring reminder covers
        the follow-up."""
        assert status_message("pending") is None

    def test_unknown_status_has_no_message(self):
        assert status_message("hibernating") is None

    def test_data_payload_is_namespaced_apart_from_the_action_path(self):
        assert status_message("suspended")["data"] == {
            "type": "driver_status_suspended",
            "new_status": "suspended",
        }
