"""Driver document expiry alerts — proactive notifications for expiring documents.

Checks every 12 hours for documents expiring within 7 days and sends push
notifications so drivers can renew before being blocked from going online.
Also suspends drivers whose documents have already expired and disconnects
their active WebSocket session.
"""

import asyncio
import logging
import random
import time
from datetime import datetime, timedelta, timezone

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from ..db import db
    from ..features import send_push_notification
    from ..socket_manager import manager
    from .datetime_utils import parse_iso_utc
    from .driver_presence import clear_presence
    from .driver_status_notifications import ACCOUNT_PRIORITY
    from .email_layout import render_email
    from .email_notifications import EmailClass, resolve_recipient, send_lifecycle_email
    from .metrics import inc as _metric_inc
    from .metrics import set_gauge as _metric_gauge
except ImportError:
    from db import db
    from features import send_push_notification
    from socket_manager import manager
    from utils.datetime_utils import parse_iso_utc
    from utils.driver_presence import clear_presence
    from utils.driver_status_notifications import ACCOUNT_PRIORITY
    from utils.email_layout import render_email
    from utils.email_notifications import EmailClass, resolve_recipient, send_lifecycle_email
    from utils.metrics import inc as _metric_inc
    from utils.metrics import set_gauge as _metric_gauge

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 43200  # 12 hours
EXPIRY_WARNING_DAYS = 7


async def _email_expiry_notice(
    user_id: str,
    driver_id: str,
    subject: str,
    body: str,
    next_step: str,
    email_type: str,
) -> None:
    """Mirror an expiry notice to email. Never raises.

    Deliberately called from INSIDE the same claimed block as the push, so it
    inherits that claim rather than needing one of its own — the suspension CAS
    for the expired branch, the `doc_expiry_warned_at` CAS for the warning
    branch. Adding a second claim would mean two replicas could each win one,
    and the driver would get duplicate mail.

    TRANSACTIONAL: a driver cannot opt out of being told their licence expired.
    Expiring documents are a Saskatchewan eligibility requirement checked on
    every go-online, not a marketing preference.
    """
    try:
        user = await resolve_recipient(user_id)
        first_name = ((user or {}).get("first_name") or "").strip()
        await send_lifecycle_email(
            user_id=user_id,
            user=user,
            subject=subject,
            rendered=render_email(
                greeting=f"Hi {first_name}," if first_name else None,
                heading=subject,
                paragraphs=[body, next_step],
            ),
            email_type=email_type,
            email_class=EmailClass.TRANSACTIONAL,
            context="document_expiry",
        )
    except Exception as e:
        logger.warning(f"Doc expiry: email failed for driver {driver_id}: {e}")


async def check_expiring_documents():
    """Find drivers with documents expiring within EXPIRY_WARNING_DAYS and notify them."""
    now = datetime.now(timezone.utc)
    warning_cutoff = now + timedelta(days=EXPIRY_WARNING_DAYS)

    _PAGE_SIZE = 100
    all_drivers: list = []
    offset = 0
    while True:
        try:
            page = await db.get_rows("drivers", {}, limit=_PAGE_SIZE, offset=offset)
        except Exception as e:
            logger.error(f"Doc expiry: failed to fetch drivers (offset={offset}): {e}", exc_info=True)
            return
        if not page:
            break
        all_drivers.extend(page)
        if len(page) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE

    notified = 0
    for driver in all_drivers:
        user_id = driver.get("user_id")
        if not user_id:
            continue

        # Check legacy expiry fields on driver record
        expiry_fields = {
            "license_expiry_date": "Driver's License",
            "insurance_expiry_date": "Insurance",
            "vehicle_inspection_expiry_date": "Vehicle Inspection",
            "background_check_expiry_date": "Background Check",
            "work_eligibility_expiry_date": "Work Eligibility",
        }

        expired_docs = []
        expiring_docs = []
        for field, label in expiry_fields.items():
            expiry_val = driver.get(field)
            if not expiry_val:
                continue
            expiry_dt = parse_iso_utc(expiry_val)
            if expiry_dt is None:
                continue

            # P2-6: process docs that have already expired OR expire within the
            # warning window.  Without this gate the original code only processed
            # future expiries (now < expiry_dt), silently skipping expired docs.
            if expiry_dt > now and expiry_dt > warning_cutoff:
                continue

            if expiry_dt <= now:
                expired_docs.append(label)
            else:
                days_left = (expiry_dt - now).days
                expiring_docs.append({"label": label, "days_left": days_left})

        # Also check document_files / driver_documents for expiry_date
        try:
            _DOC_PAGE_SIZE = 200
            _doc_offset = 0
            while True:
                docs = await db.get_rows(
                    "driver_documents",
                    {"driver_id": driver["id"], "status": "approved"},
                    limit=_DOC_PAGE_SIZE,
                    offset=_doc_offset,
                )
                for doc in docs:
                    exp_dt = parse_iso_utc(doc.get("expiry_date") or doc.get("expires_at"))
                    if exp_dt is None:
                        continue
                    doc_name = doc.get("requirement_name") or doc.get("type") or "Document"
                    # P2-6: same gate — process expired OR expiring within warning window
                    if exp_dt > now and exp_dt > warning_cutoff:
                        continue
                    if exp_dt <= now:
                        expired_docs.append(doc_name)
                    else:
                        days_left = (exp_dt - now).days
                        expiring_docs.append({"label": doc_name, "days_left": days_left})
                if len(docs) < _DOC_PAGE_SIZE:
                    break
                _doc_offset += _DOC_PAGE_SIZE
        except Exception as e:
            logger.debug(f"Failed to check driver_documents: {e}")

        # P2-6: Expired documents trigger BOTH suspension AND a push notification.
        # The suspension runs first so the driver cannot accept new rides even if
        # the notification fails.  The `continue` at the end bypasses the 24 h
        # spam-guard below — expiry events must always trigger a new notification.
        if expired_docs:
            doc_list = ", ".join(expired_docs)
            # Replay-safety (F6): make the suspension itself the atomic claim.
            # Filtering on status != 'suspended' means only the replica that
            # actually transitions the driver into suspension gets a row back; it
            # alone clears presence, disconnects, and sends the single suspension
            # push. Re-ticks (driver already suspended) and sibling replicas match
            # zero rows, so the driver is not re-suspended or re-notified every 12h.
            try:
                claimed = await db.update_one(
                    "drivers",
                    {"id": driver["id"], "status": {"$ne": "suspended"}},
                    {"is_online": False, "is_available": False, "status": "suspended"},
                )
            except Exception as e:
                logger.error(f"Doc expiry: failed to suspend driver {driver['id']}: {e}", exc_info=True)
                continue
            if not claimed:
                # Already suspended by a prior tick or another replica — nothing to do.
                continue
            logger.warning(f"Doc expiry: driver {driver['id']} suspended for expired docs ({doc_list})")
            # Clear Redis presence so dispatch filters drop this driver
            # immediately — otherwise they'd remain eligible for up to
            # PRESENCE_TTL (90 s) and could still be assigned a ride.
            try:
                await clear_presence(driver["id"])
            except Exception as e:
                logger.error(f"Doc expiry: clear_presence failed for {driver['id']}: {e}", exc_info=True)
            manager.disconnect(f"driver_{user_id}")
            _suspend_title = "Account suspended — expired documents"
            _suspend_body = f"Your account has been suspended: {doc_list}. Please renew to continue driving."
            try:
                await send_push_notification(
                    user_id,
                    _suspend_title,
                    _suspend_body,
                    data={"type": "document_expired_suspension", "driver_id": driver["id"]},
                    # This driver can no longer earn. That is exactly what the
                    # account tier exists for (see driver_status_notifications):
                    # it bypasses the push opt-out and falls back to the retry
                    # queue. On the default tier an opted-out driver got no
                    # notice at all that they'd been taken offline.
                    priority=ACCOUNT_PRIORITY,
                    target_app="driver",
                )
            except Exception as e:
                logger.warning(f"Doc expiry: push failed for driver {driver['id']}: {e}")
            # Inside the suspension claim — see _email_expiry_notice.
            await _email_expiry_notice(
                user_id,
                driver["id"],
                _suspend_title,
                _suspend_body,
                "Upload a current copy in the Spinr driver app under Profile → Documents. "
                "Your account is restored once an admin approves it.",
                "document_expired_suspension",
            )
            continue

        if not expiring_docs:
            continue

        # P2-9: Classify the soonest-expiring document into an urgency tier
        # and compose a tier-appropriate message.
        soonest = min(expiring_docs, key=lambda d: d["days_left"])
        days_left = soonest["days_left"]
        doc_list = ", ".join(d["label"] for d in expiring_docs)

        if days_left == 0:
            notif_title = f"{soonest['label']} expires today"
            notif_body = f"Your {soonest['label']} expires today. Renew now to avoid account suspension."
            notif_type = "document_expiry_today"
        elif days_left == 1:
            notif_title = f"{soonest['label']} expires tomorrow"
            notif_body = f"Your {soonest['label']} expires tomorrow — renew now or you'll be suspended."
            notif_type = "document_expiry_1day"
        else:
            notif_title = f"Document expiring in {days_left} days"
            notif_body = f"Please renew: {doc_list}. You won't be able to go online with expired documents."
            notif_type = "document_expiry_warning"

        # Replay-safety (F6) + spam-guard: claim the notification slot atomically
        # with a compare-and-swap on doc_expiry_warned_at BEFORE sending, so two
        # replicas can't both notify in the same tick (the old read-then-write let
        # every replica pass the throttle and send). The 7-day tier keeps the 24 h
        # throttle; urgent tiers (today / tomorrow) use a 6 h window so they still
        # fire on each 12 h tick but exactly once across replicas.
        throttle_seconds = 86400 if days_left >= 2 else 21600
        cutoff = (now - timedelta(seconds=throttle_seconds)).isoformat()
        try:
            claimed = await db.update_one(
                "drivers",
                {
                    "id": driver["id"],
                    "$or": [f"doc_expiry_warned_at.is.null,doc_expiry_warned_at.lt.{cutoff}"],
                },
                {"doc_expiry_warned_at": now.isoformat()},
            )
        except Exception as e:
            logger.warning(f"Doc expiry: warn-claim failed for driver {driver['id']}: {e}")
            continue
        if not claimed:
            # Another replica already notified within the throttle window.
            continue

        try:
            await send_push_notification(
                user_id,
                notif_title,
                notif_body,
                data={"type": notif_type, "driver_id": driver["id"]},
                target_app="driver",
            )
            notified += 1
        except Exception as e:
            logger.warning(f"Doc expiry: failed to notify driver {driver['id']}: {e}")

        # Inside the doc_expiry_warned_at claim — see _email_expiry_notice.
        # Email matters more here than almost anywhere else in the product: a
        # renewal needs the driver to find a document, photograph it and upload
        # it, which is not something a notification that vanishes from the tray
        # supports. It also survives an uninstalled app or a stale FCM token.
        await _email_expiry_notice(
            user_id,
            driver["id"],
            notif_title,
            notif_body,
            "Upload the renewed document in the Spinr driver app under Profile → Documents. "
            "You'll stay online as long as it's approved before the expiry date.",
            notif_type,
        )

    if notified > 0:
        logger.info(f"Doc expiry: notified {notified} drivers about expiring documents")


async def document_expiry_loop():
    """Background loop that checks for expiring documents every 12 hours."""
    logger.info("Document expiry checker started (every 12h)")
    while True:
        _t0 = time.monotonic()
        _had_error = False
        try:
            await check_expiring_documents()
        except Exception as e:
            logger.error(f"Document expiry loop error: {e}", exc_info=True)
            _had_error = True
        _metric_gauge("spinr_bgloop_duration_ms", (time.monotonic() - _t0) * 1000, {"loop": "document_expiry"})
        if _had_error:
            _metric_inc("spinr_bgloop_errors_total", {"loop": "document_expiry"})
        _record_heartbeat("document_expiry (12h)")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS * (0.9 + random.random() * 0.2))
