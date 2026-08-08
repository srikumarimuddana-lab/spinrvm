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

Message construction is pure; `notify_driver_status_change` at the bottom is
the one side-effecting function, shared by all three call sites (the admin
action endpoint, the status-override endpoint, and the driver-triggered
needs_review transitions) so the send contract cannot drift between them.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

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

# ── Email channel ────────────────────────────────────────────────────────────
# Statuses that ALSO get an email, on top of the push and the in-app inbox row.
#
# These are the transitions a driver needs to be able to find again later — an
# approval, and the three states where they can no longer earn. Push is lossy
# (uninstalled app, revoked token, stale FCM registration) and transient; an
# account decision that changes someone's ability to work should not depend on
# a notification tray.
#
# `needs_review` is deliberately excluded. It fires on every driver-triggered
# vehicle edit and document re-upload, so emailing it would turn routine
# self-service into inbox noise — and the push already covers the one thing
# that matters, that they've been taken offline.
EMAIL_STATUSES = frozenset({"active", "rejected", "suspended", "banned"})

# What the driver should actually DO next. This is the reason the email exists
# rather than being a copy of the push: a notification tray has no room for it.
_EMAIL_NEXT_STEPS: dict[str, str] = {
    "active": "Open the Spinr driver app, tap Go Online, and you'll start receiving ride offers.",
    "rejected": (
        "Open the Spinr driver app to review your documents and submit them again. "
        "If you think this decision is wrong, contact {support}."
    ),
    "suspended": (
        "You won't be able to go online while your account is suspended. "
        "Contact {support} if you have questions or want to appeal."
    ),
    "banned": "Contact {support} if you'd like more information about this decision.",
}


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
        # Present only for statuses in EMAIL_STATUSES; None means push-only.
        # Derived from the same `copy` tuple as the push so the two channels
        # cannot drift into saying different things about the same event.
        "email": _email_payload(status, title, body, reason, data_type),
    }


def _email_payload(
    status: str,
    title: str,
    body: str,
    reason: str | None,
    data_type: str,
) -> dict[str, Any] | None:
    """Email fields for a lifecycle notice, or None when the status is push-only."""
    if status not in EMAIL_STATUSES:
        return None
    return _email_fields(title, _with_reason(status, body, reason), _EMAIL_NEXT_STEPS.get(status), data_type)


def _email_fields(
    title: str,
    body: str,
    next_step: str | None,
    data_type: str,
) -> dict[str, Any]:
    return {
        # Subject mirrors the push title, so a driver who sees both recognises
        # them as the same notice rather than two separate events.
        "subject": title,
        "heading": title,
        "paragraphs": [body] + ([next_step] if next_step else []),
        "email_type": data_type,
    }


# ── Verification toggle (admin "verify" / "unverify") ────────────────────────
# A separate flag from `status`, so it does not fit the status-keyed maps above.
# Copy is byte-identical to what routes/admin/drivers.py sent inline before this
# was routed through the policy — no already-shipped notification changes wording.
_VERIFICATION_COPY: dict[bool, tuple[str, str]] = {
    True: (
        "Account Verified! ✅",
        "Your driver account has been verified. You can now go online and start accepting rides!",
    ),
    False: (
        "Verification Update ⚠️",
        "Your driver verification status has been updated. Please check your documents.",
    ),
}

_VERIFICATION_NEXT_STEPS: dict[bool, str] = {
    True: "Open the Spinr driver app, tap Go Online, and you'll start receiving ride offers.",
    False: (
        "Open the Spinr driver app to check your documents. Contact {support} if you're not sure what needs updating."
    ),
}


def verification_message(verified: bool) -> dict[str, Any]:
    """Notice for the admin verify/unverify toggle.

    Stays on the NORMAL tier, deliberately. `is_verified` is not what gates
    earning: routes/drivers/status.py:361 records that `status` became the
    single source of truth for going online, and the remaining check at :174
    only applies when `status != "active"`. So un-verifying an already-active
    driver does not stop them working, and the notice must not bypass the push
    opt-out the way a genuine account block (rejected/suspended/banned) does.
    """
    title, body = _VERIFICATION_COPY[verified]
    data_type = "driver_verified" if verified else "driver_unverified"
    return {
        "title": title,
        "body": body,
        "data": {"type": data_type},
        "priority": NORMAL_PRIORITY,
        "email": _email_fields(title, body, _VERIFICATION_NEXT_STEPS[verified], data_type),
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


async def _send_status_email(
    driver: dict[str, Any],
    message: dict[str, Any],
    context: str,
) -> None:
    """Fan the same notice out to email. Never raises, never blocks the push.

    TRANSACTIONAL class: an account decision that stops someone earning is not
    something they may opt out of receiving.

    The blanket except is deliberate and belongs *here* rather than being
    delegated to `send_lifecycle_email`'s own guard. Email is the secondary
    channel; nothing it can do — a failed recipient lookup, a rendering error,
    an import problem — may cost the driver the push. Making the guarantee
    local means it holds no matter how the downstream module changes.
    """
    payload = message.get("email")
    if not payload:
        return
    try:
        try:
            from ..utils.company_details import load_company_details
            from ..utils.email_layout import render_email
            from ..utils.email_notifications import EmailClass, resolve_recipient, send_lifecycle_email
        except ImportError:  # pragma: no cover - direct module imports in tests
            from utils.company_details import load_company_details  # type: ignore
            from utils.email_layout import render_email  # type: ignore
            from utils.email_notifications import (  # type: ignore
                EmailClass,
                resolve_recipient,
                send_lifecycle_email,
            )

        company = await load_company_details()
        user = await resolve_recipient(driver["user_id"])
        first_name = ((user or {}).get("first_name") or "").strip()
        await send_lifecycle_email(
            user_id=driver["user_id"],
            user=user,
            subject=payload["subject"],
            rendered=await render_email(
                greeting=f"Hi {first_name}," if first_name else None,
                heading=payload["heading"],
                # The `{support}` placeholder is resolved here rather than in
                # the copy maps because those are read by the synchronous
                # `action_message` / `status_message`, and the address lives in
                # DB-backed settings. Substituting at send time keeps those
                # pure and keeps the body's support address identical to the
                # one in the footer.
                #
                # A literal replace, not str.format: these paragraphs carry the
                # admin-written suspension/rejection reason, and a reason
                # containing a brace would make format() raise.
                paragraphs=[p.replace("{support}", company.support_email) for p in payload["paragraphs"]],
                company=company,
            ),
            email_type=payload["email_type"],
            email_class=EmailClass.TRANSACTIONAL,
            context=context,
        )
    except Exception as exc:
        logger.warning(
            "driver status email failed (%s) for driver %s: %s",
            context,
            driver.get("id"),
            exc,
        )


async def notify_driver_status_change(
    driver: dict[str, Any],
    message: dict[str, Any] | None,
    context: str,
) -> bool:
    """Send a lifecycle notice. Returns True if the PUSH was delivered.

    Push is the primary channel and the return value; statuses in
    `EMAIL_STATUSES` additionally get an email, whose outcome deliberately does
    not affect the return value — callers use it to decide whether the driver
    was reached on their device, and an email that failed does not change that.

    Best-effort by contract: every caller has already committed the status
    change before reaching here, so a delivery failure must not propagate and
    undo the admin action or the driver's own profile save. Logged at warning
    rather than error because the state change succeeded and
    `send_push_notification` writes the in-app inbox row regardless of whether
    device delivery worked — the driver still sees it next time they open the app.
    """
    if not message or not should_notify_driver(driver):
        return False
    try:
        from ..features import send_push_notification
    except ImportError:
        from features import send_push_notification  # type: ignore

    # Secondary channel, guarded inside — cannot throw past here.
    await _send_status_email(driver, message, context)

    try:
        return bool(
            await send_push_notification(
                driver["user_id"],
                message["title"],
                message["body"],
                message["data"],
                priority=message["priority"],
                target_app="driver",
            )
        )
    except Exception as exc:
        logger.warning("driver status push failed (%s) for driver %s: %s", context, driver.get("id"), exc)
        return False
