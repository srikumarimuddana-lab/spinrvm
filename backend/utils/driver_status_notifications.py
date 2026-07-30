"""Driver status-change notification policy.

One-time lifecycle pushes, fired when a driver ENTERS a status. Distinct from
`driver_onboarding_reminder_rules.py`, which drives the recurring daily nudge
for drivers who still have onboarding work to do:

    lifecycle (here)   one push per transition, every status but `pending`
    reminders (there)  repeating daily push, `pending` only, capped at 7

Two lookup paths, deliberately:

* `action_message` — keyed on the admin action (approve / reject / suspend /
  ban / unban / reactivate). Copy is byte-identical to the map that used to
  live inline in routes/admin/drivers.py, so no already-shipped notification
  changes wording. `unban` and `reactivate` both land on `active` but say
  different things, which a status-only lookup could not express.
* `status_message` — keyed on the status being entered. Used by paths that
  have no admin action: the status-override endpoint and the driver-triggered
  `needs_review` transitions in routes/drivers/profile.py and documents.py.

Pure functions only — no DB, no push. Callers do the sending.
"""

from __future__ import annotations

from typing import Any

# Delivery tier for account-state notices. Bypasses the push_enabled opt-out
# and falls back to the retry queue (see features.send_push_notification and
# the push_retry_queue.priority CHECK added in migration 272). A driver who can
# no longer earn must be told why rather than discovering it via a 403.
ACCOUNT_PRIORITY = "account"
NORMAL_PRIORITY = "normal"

# Entering one of these means the driver cannot work. Guaranteed delivery.
BLOCKING_STATUSES = frozenset({"rejected", "suspended", "banned"})

# `pending` is deliberately absent from both maps: a driver enters it at signup,
# where the app itself shows the next step. The recurring onboarding reminder
# covers the follow-up. Per product decision (2026-07-30) a rejected driver gets
# the one-time notice below and nothing further — no re-apply nudge.
_ACTION_COPY: dict[str, tuple[str, str]] = {
    "approve": (
        "You're Approved! 🎉",
        "Your driver application has been approved. You can now go online and start earning!",
    ),
    "reject": (
        "Application Update",
        "Your driver application needs attention. Please check your documents.",
    ),
    "suspend": (
        "Account Suspended ⚠️",
        "Your account has been suspended.",
    ),
    "ban": (
        "Account Deactivated",
        "Your driver account has been deactivated. Contact support for more information.",
    ),
    "unban": (
        "Account Restored! ✅",
        "Your driver account has been restored. You can now go online again.",
    ),
    "reactivate": (
        "Account Reactivated! ✅",
        "Your account has been reactivated. You can now go online and accept rides!",
    ),
}

# Which status each admin action lands the driver on. Used to pick the delivery
# tier for an action-keyed message.
_ACTION_TARGET_STATUS: dict[str, str] = {
    "approve": "active",
    "reject": "rejected",
    "suspend": "suspended",
    "ban": "banned",
    "unban": "active",
    "reactivate": "active",
}

_STATUS_COPY: dict[str, tuple[str, str]] = {
    "active": (
        "You're Approved! 🎉",
        "Your driver account is active. You can now go online and start earning!",
    ),
    "needs_review": (
        "Changes Under Review",
        "We're reviewing your updated details. You've been taken offline until an admin approves them.",
    ),
    "rejected": (
        "Application Update",
        "Your driver application needs attention. Please check your documents.",
    ),
    "suspended": (
        "Account Suspended ⚠️",
        "Your account has been suspended.",
    ),
    "banned": (
        "Account Deactivated",
        "Your driver account has been deactivated. Contact support for more information.",
    ),
}

# Copy that reads naturally with a reason appended. `ban` deliberately omits it:
# the existing copy already routes the driver to support, and a raw admin-written
# ban reason is not vetted customer-facing text.
_REASON_STATUSES = frozenset({"suspended", "rejected"})


def _with_reason(status: str, body: str, reason: str | None) -> str:
    if status in _REASON_STATUSES and reason:
        return f"{body} Reason: {reason}"
    if status == "suspended":
        return f"{body} Contact support for details."
    return body


def should_notify_driver(driver: dict[str, Any]) -> bool:
    """False when there is nobody to notify.

    A soft-deleted driver (`deleted_at` set by account deletion) is tombstoned:
    the account is locked and its push token is on its way out, so a lifecycle
    push would be noise at best. `user_id` is the delivery key — without it
    there is no recipient.
    """
    return bool(driver.get("user_id")) and not driver.get("deleted_at")


def _build(status: str, copy: tuple[str, str], reason: str | None, data_type: str) -> dict[str, Any]:
    title, body = copy
    return {
        "title": title,
        "body": _with_reason(status, body, reason),
        "data": {"type": data_type, "new_status": status},
        "priority": ACCOUNT_PRIORITY if status in BLOCKING_STATUSES else NORMAL_PRIORITY,
    }


def action_message(action: str, reason: str | None = None) -> dict[str, Any] | None:
    """Push payload for an admin lifecycle action, or None if it has no notice."""
    copy = _ACTION_COPY.get(action)
    if not copy:
        return None
    status = _ACTION_TARGET_STATUS.get(action, "")
    return _build(status, copy, reason, f"driver_{action}")


def status_message(status: str, reason: str | None = None) -> dict[str, Any] | None:
    """Push payload for entering a status directly (no admin action), or None.

    Returns None for `pending` and for any unrecognised status — an unknown
    status must not generate a push with empty copy.
    """
    copy = _STATUS_COPY.get(status)
    if not copy:
        return None
    return _build(status, copy, reason, f"driver_status_{status}")
