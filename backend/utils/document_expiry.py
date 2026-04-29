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
    from .metrics import inc as _metric_inc
    from .metrics import set_gauge as _metric_gauge
except ImportError:
    from db import db
    from features import send_push_notification
    from socket_manager import manager
    from utils.datetime_utils import parse_iso_utc
    from utils.driver_presence import clear_presence
    from utils.metrics import inc as _metric_inc
    from utils.metrics import set_gauge as _metric_gauge

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 43200  # 12 hours
EXPIRY_WARNING_DAYS = 7


async def check_expiring_documents():
    """Find drivers with documents expiring within EXPIRY_WARNING_DAYS and notify them."""
    now = datetime.now(timezone.utc)
    warning_cutoff = now + timedelta(days=EXPIRY_WARNING_DAYS)

    try:
        all_drivers = await db.get_rows("drivers", {}, limit=1000)
    except Exception as e:
        logger.error(f"Doc expiry: failed to fetch drivers: {e}")
        return

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
            if not (expiry_dt < now or expiry_dt < warning_cutoff):
                continue

            if expiry_dt <= now:
                expired_docs.append(label)
            else:
                days_left = (expiry_dt - now).days
                expiring_docs.append({"label": label, "days_left": days_left})

        # Also check document_files / driver_documents for expiry_date
        try:
            docs = await db.get_rows(
                "driver_documents",
                {"driver_id": driver["id"], "status": "approved"},
                limit=20,
            )
            for doc in docs:
                exp_dt = parse_iso_utc(doc.get("expiry_date") or doc.get("expires_at"))
                if exp_dt is None:
                    continue
                doc_name = doc.get("requirement_name") or doc.get("type") or "Document"
                # P2-6: same gate — process expired OR expiring within warning window
                if not (exp_dt < now or exp_dt < warning_cutoff):
                    continue
                if exp_dt <= now:
                    expired_docs.append(doc_name)
                else:
                    days_left = (exp_dt - now).days
                    expiring_docs.append({"label": doc_name, "days_left": days_left})
        except Exception as e:
            logger.debug(f"Failed to check driver_documents: {e}")

        # P2-6: Expired documents trigger BOTH suspension AND a push notification.
        # The suspension runs first so the driver cannot accept new rides even if
        # the notification fails.  The `continue` at the end bypasses the 24 h
        # spam-guard below — expiry events must always trigger a new notification.
        if expired_docs:
            doc_list = ", ".join(expired_docs)
            logger.warning(f"Doc expiry: driver {driver['id']} has expired docs ({doc_list}) — suspending")
            # 1. Suspension
            try:
                await db.update_one(
                    "drivers",
                    {"id": driver["id"]},
                    {"is_online": False, "is_available": False, "status": "suspended"},
                )
            except Exception as e:
                logger.error(f"Doc expiry: failed to suspend driver {driver['id']}: {e}")
            # Clear Redis presence so dispatch filters drop this driver
            # immediately — otherwise they'd remain eligible for up to
            # PRESENCE_TTL (90 s) and could still be assigned a ride.
            try:
                await clear_presence(driver["id"])
            except Exception as e:
                logger.error(f"Doc expiry: clear_presence failed for {driver['id']}: {e}", exc_info=True)
            manager.disconnect(f"driver_{user_id}")
            # 2. Notification
            try:
                await send_push_notification(
                    user_id,
                    "Account suspended — expired documents",
                    f"Your account has been suspended: {doc_list}. Please renew to continue driving.",
                    data={"type": "document_expired_suspension", "driver_id": driver["id"]},
                )
            except Exception as e:
                logger.warning(f"Doc expiry: push failed for driver {driver['id']}: {e}")
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

        # Spam-guard: apply 24 h throttle only for the 7-day tier.
        # 1-day and day-of warnings bypass the guard — these are urgent enough
        # that they must reach the driver even if a 7-day reminder was sent
        # within the past 24 h.
        if days_left >= 2:
            warned_dt = parse_iso_utc(driver.get("doc_expiry_warned_at"))
            if warned_dt is not None and (now - warned_dt).total_seconds() < 86400:
                continue

        try:
            await send_push_notification(
                user_id,
                notif_title,
                notif_body,
                data={"type": notif_type, "driver_id": driver["id"]},
            )
            await db.update_one(
                "drivers",
                {"id": driver["id"]},
                {"doc_expiry_warned_at": now.isoformat()},
            )
            notified += 1
        except Exception as e:
            logger.warning(f"Doc expiry: failed to notify driver {driver['id']}: {e}")

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
            logger.error(f"Document expiry loop error: {e}")
            _had_error = True
        _metric_gauge("spinr_bgloop_duration_ms", (time.monotonic() - _t0) * 1000, {"loop": "document_expiry"})
        if _had_error:
            _metric_inc("spinr_bgloop_errors_total", {"loop": "document_expiry"})
        _record_heartbeat("document_expiry (12h)")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS * (0.9 + random.random() * 0.2))
