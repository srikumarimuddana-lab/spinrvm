"""Driver document expiry alerts — proactive notifications for expiring documents.

Checks every 12 hours for documents expiring within 7 days and sends push
notifications so drivers can renew before being blocked from going online.
Also suspends drivers whose documents have already expired and disconnects
their active WebSocket session.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

try:
    from ..db import db
    from ..features import send_push_notification
    from ..socket_manager import manager
except ImportError:
    from db import db
    from features import send_push_notification
    from socket_manager import manager

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
            try:
                if isinstance(expiry_val, str):
                    expiry_dt = datetime.fromisoformat(expiry_val.replace("Z", "+00:00").replace("+00:00", ""))
                else:
                    expiry_dt = expiry_val

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
            except (ValueError, TypeError):
                continue

        # Also check document_files / driver_documents for expiry_date
        try:
            docs = await db.get_rows(
                "driver_documents",
                {"driver_id": driver["id"], "status": "approved"},
                limit=20,
            )
            for doc in docs:
                exp = doc.get("expiry_date") or doc.get("expires_at")
                if not exp:
                    continue
                try:
                    if isinstance(exp, str):
                        exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00").replace("+00:00", ""))
                    else:
                        exp_dt = exp
                    doc_name = doc.get("requirement_name") or doc.get("type") or "Document"
                    # P2-6: same gate — process expired OR expiring within warning window
                    if not (exp_dt < now or exp_dt < warning_cutoff):
                        continue
                    if exp_dt <= now:
                        expired_docs.append(doc_name)
                    else:
                        days_left = (exp_dt - now).days
                        expiring_docs.append({"label": doc_name, "days_left": days_left})
                except (ValueError, TypeError):
                    continue
        except Exception as e:
            logger.debug(f"Failed to check driver_documents: {e}")

        # P2-6: Expired documents trigger BOTH suspension AND a push notification.
        # The suspension runs first so the driver cannot accept new rides even if
        # the notification fails.  The `continue` at the end bypasses the 24 h
        # spam-guard below — expiry events must always trigger a new notification.
        if expired_docs:
            doc_list = ", ".join(expired_docs)
            logger.warning(
                f"Doc expiry: driver {driver['id']} has expired docs ({doc_list}) — suspending"
            )
            # 1. Suspension
            try:
                await db.update_one(
                    "drivers",
                    {"id": driver["id"]},
                    {"$set": {"is_online": False, "is_available": False, "status": "suspended"}},
                )
            except Exception as e:
                logger.error(f"Doc expiry: failed to suspend driver {driver['id']}: {e}")
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
            notif_body = (
                f"Your {soonest['label']} expires today. "
                "Renew now to avoid account suspension."
            )
            notif_type = "document_expiry_today"
        elif days_left == 1:
            notif_title = f"{soonest['label']} expires tomorrow"
            notif_body = (
                f"Your {soonest['label']} expires tomorrow — "
                "renew now or you'll be suspended."
            )
            notif_type = "document_expiry_1day"
        else:
            notif_title = f"Document expiring in {days_left} days"
            notif_body = (
                f"Please renew: {doc_list}. "
                "You won't be able to go online with expired documents."
            )
            notif_type = "document_expiry_warning"

        # Spam-guard: apply 24 h throttle only for the 7-day tier.
        # 1-day and day-of warnings bypass the guard — these are urgent enough
        # that they must reach the driver even if a 7-day reminder was sent
        # within the past 24 h.
        if days_left >= 2:
            last_warned = driver.get("doc_expiry_warned_at")
            if last_warned:
                try:
                    if isinstance(last_warned, str):
                        warned_dt = datetime.fromisoformat(
                            last_warned.replace("Z", "+00:00").replace("+00:00", "")
                        )
                    else:
                        warned_dt = last_warned
                    if (now - warned_dt).total_seconds() < 86400:
                        continue
                except (ValueError, TypeError):
                    pass

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
                {"$set": {"doc_expiry_warned_at": now.isoformat()}},
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
        try:
            await check_expiring_documents()
        except Exception as e:
            logger.error(f"Document expiry loop error: {e}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
