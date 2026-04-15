"""Driver document expiry alerts — proactive notifications for expiring documents.

Checks every 12 hours for documents expiring within 7 days and sends push
notifications so drivers can renew before being blocked from going online.
"""

import asyncio
import logging
from datetime import datetime, timedelta

try:
    from ..db import db
    from ..features import send_push_notification
except ImportError:
    from db import db
    from features import send_push_notification

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 43200  # 12 hours
EXPIRY_WARNING_DAYS = 7


async def check_expiring_documents():
    """Find drivers with documents expiring within EXPIRY_WARNING_DAYS and notify them."""
    now = datetime.utcnow()
    warning_cutoff = now + timedelta(days=EXPIRY_WARNING_DAYS)

    try:
        all_drivers = await db.drivers.find({}).to_list(1000)
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

                if now < expiry_dt <= warning_cutoff:
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
                    if now < exp_dt <= warning_cutoff:
                        days_left = (exp_dt - now).days
                        doc_name = doc.get("requirement_name") or doc.get("type") or "Document"
                        expiring_docs.append({"label": doc_name, "days_left": days_left})
                except (ValueError, TypeError):
                    continue
        except Exception as e:
            logger.debug(f"Failed to check driver_documents: {e}")

        if not expiring_docs:
            continue

        # Check if we already warned recently (avoid spam)
        last_warned = driver.get("doc_expiry_warned_at")
        if last_warned:
            try:
                if isinstance(last_warned, str):
                    warned_dt = datetime.fromisoformat(last_warned.replace("Z", "+00:00").replace("+00:00", ""))
                else:
                    warned_dt = last_warned
                if (now - warned_dt).total_seconds() < 86400:  # Don't re-warn within 24h
                    continue
            except (ValueError, TypeError):
                pass

        # Send notification
        soonest = min(expiring_docs, key=lambda d: d["days_left"])
        doc_list = ", ".join(d["label"] for d in expiring_docs)

        try:
            await send_push_notification(
                user_id,
                f"Document expiring in {soonest['days_left']} days",
                f"Please renew: {doc_list}. You won't be able to go online with expired documents.",
                data={"type": "document_expiry_warning", "driver_id": driver["id"]},
            )
            await db.drivers.update_one(
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
