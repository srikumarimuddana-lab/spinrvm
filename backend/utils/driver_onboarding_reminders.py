"""Daily 08:00 local-time pushes for incomplete driver onboarding."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime
from typing import Any

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from .. import db_supabase as db
    from ..features import send_push_notification
    from ..onboarding_status import _has_vehicle as _has_vehicle_details
    from ..utils.driver_onboarding_reminder_rules import (
        VEHICLE_DETAILS,
        VEHICLE_DOCUMENTS,
        as_utc,
        local_date_for_send_window,
        missing_required_document_uploads,
        reminder_message,
        should_skip_driver,
    )
    from ..utils.error_handling import DuplicateRecordError
except ImportError:
    import db_supabase as db  # type: ignore
    from features import send_push_notification  # type: ignore
    from onboarding_status import _has_vehicle as _has_vehicle_details  # type: ignore
    from utils.driver_onboarding_reminder_rules import (  # type: ignore
        VEHICLE_DETAILS,
        VEHICLE_DOCUMENTS,
        as_utc,
        local_date_for_send_window,
        missing_required_document_uploads,
        reminder_message,
        should_skip_driver,
    )
    from utils.error_handling import DuplicateRecordError  # type: ignore


logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 15 * 60
PAGE_SIZE = 200
LOG_TABLE = "driver_onboarding_reminder_log"


async def _get_rows(table: str, filters: dict[str, Any] | None, **kwargs) -> list[dict[str, Any]]:
    try:
        return await db.get_rows(table, filters or {}, **kwargs)
    except Exception as exc:
        original = getattr(exc, "details", {}).get("original") if hasattr(exc, "details") else None
        logger.error("onboarding reminders: failed to read %s: %s original=%s", table, exc, original, exc_info=True)
        return []


async def _claim(driver_id: str, user_id: str, kind: str, local_date: str, now: datetime) -> dict[str, Any] | None:
    try:
        return await db.insert_one(
            LOG_TABLE,
            {
                "driver_id": driver_id,
                "user_id": user_id,
                "reminder_type": kind,
                "local_date": local_date,
                "claimed_at": now.isoformat(),
            },
        )
    except DuplicateRecordError:
        return None
    except Exception as exc:
        original = getattr(exc, "details", {}).get("original") if hasattr(exc, "details") else None
        logger.error(
            "onboarding reminders: claim failed for %s/%s: %s original=%s",
            driver_id,
            kind,
            exc,
            original,
            exc_info=True,
        )
        return None


async def _mark(claim: dict[str, Any], now: datetime, delivered: bool, error: str | None) -> None:
    if not claim.get("id"):
        return
    try:
        await db.update_one(
            LOG_TABLE,
            {"id": claim["id"]},
            {
                "attempted_at": now.isoformat(),
                "delivered_at": now.isoformat() if delivered else None,
                "send_success": delivered,
                "send_error": error[:500] if error else None,
            },
        )
    except Exception as exc:
        original = getattr(exc, "details", {}).get("original") if hasattr(exc, "details") else None
        logger.error("onboarding reminders: log update failed: %s original=%s", exc, original, exc_info=True)


async def _send(driver: dict[str, Any], kind: str, local_date: str, now: datetime) -> bool:
    driver_id = str(driver["id"])
    user_id = str(driver["user_id"])
    claim = await _claim(driver_id, user_id, kind, local_date, now)
    if not claim:
        return False

    title, body, data = reminder_message(driver_id, kind)
    delivered = False
    error = None
    try:
        delivered = bool(await send_push_notification(user_id, title, body, data=data, target_app="driver"))
    except Exception as exc:
        error = str(exc)
        logger.error("onboarding reminders: push failed for driver %s: %s", driver_id, exc, exc_info=True)
    await _mark(claim, now, delivered, None if delivered else error or "not_delivered")
    return delivered


async def check_driver_onboarding_reminders(now_utc: datetime | None = None) -> dict[str, int]:
    now = as_utc(now_utc)
    areas = {
        str(r["id"]): r
        for r in await _get_rows("service_areas", {}, limit=1000, columns="id,timezone,required_documents")
        if r.get("id")
    }
    global_reqs = await _get_rows("document_requirements", {}, limit=200, columns="id,name,is_mandatory")
    stats = {"drivers_scanned": 0, "claims_attempted": 0, "pushes_delivered": 0}

    offset = 0
    while True:
        drivers = await _get_rows(
            "drivers",
            {},
            limit=PAGE_SIZE,
            offset=offset,
            columns="id,user_id,status,deleted_at,service_area_id,vehicle_type_id,vehicle_make,vehicle_model,license_plate",
        )
        if not drivers:
            break

        ids = [str(d["id"]) for d in drivers if d.get("id")]
        docs = await _get_rows(
            "driver_documents",
            {"driver_id": {"$in": ids}},
            limit=max(1000, len(ids) * 10),
            columns="id,driver_id,requirement_id,requirement_key,document_type,status",
        )
        docs_by_driver: dict[str, list[dict[str, Any]]] = {}
        for doc in docs:
            if doc.get("driver_id"):
                docs_by_driver.setdefault(str(doc["driver_id"]), []).append(doc)

        for driver in drivers:
            stats["drivers_scanned"] += 1
            if should_skip_driver(driver):
                continue
            local_date = local_date_for_send_window(driver, areas, now)
            if not local_date:
                continue
            if not _has_vehicle_details(driver):
                stats["claims_attempted"] += 1
                stats["pushes_delivered"] += int(await _send(driver, VEHICLE_DETAILS, local_date, now))
            if missing_required_document_uploads(driver, docs_by_driver.get(str(driver["id"]), []), areas, global_reqs):
                stats["claims_attempted"] += 1
                stats["pushes_delivered"] += int(await _send(driver, VEHICLE_DOCUMENTS, local_date, now))

        if len(drivers) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    if stats["claims_attempted"]:
        logger.info("onboarding reminders: %s", stats)
    return stats


async def driver_onboarding_reminder_loop() -> None:
    logger.info("Driver onboarding reminder loop started")
    while True:
        started = time.monotonic()
        try:
            await check_driver_onboarding_reminders()
        except Exception as exc:
            logger.error("driver_onboarding_reminder_loop tick failed: %s", exc, exc_info=True)
        _record_heartbeat("driver_onboarding_reminders (15min)")
        logger.debug("driver_onboarding_reminder_loop tick took %.1fms", (time.monotonic() - started) * 1000)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS + random.uniform(-60, 60))
