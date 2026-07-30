"""Daily 08:00 local-time pushes for incomplete driver onboarding."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timedelta
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
    from ..settings_loader import get_app_settings as _get_app_settings
    from ..utils.driver_onboarding_reminder_rules import (
        DEFAULT_MAX_REMINDERS_PER_TYPE,
        VEHICLE_DETAILS,
        VEHICLE_DOCUMENTS,
        as_utc,
        local_date_for_send_window,
        missing_required_document_uploads,
        open_send_windows,
        parse_remindable_statuses,
        reminder_cap_reached,
        reminder_message,
        should_skip_driver,
    )
    from ..utils.error_handling import DuplicateRecordError, ServiceUnavailableException, db_error_text
except ImportError:
    import db_supabase as db  # type: ignore
    from features import send_push_notification  # type: ignore
    from onboarding_status import _has_vehicle as _has_vehicle_details  # type: ignore
    from settings_loader import get_app_settings as _get_app_settings  # type: ignore
    from utils.driver_onboarding_reminder_rules import (  # type: ignore
        DEFAULT_MAX_REMINDERS_PER_TYPE,
        VEHICLE_DETAILS,
        VEHICLE_DOCUMENTS,
        as_utc,
        local_date_for_send_window,
        missing_required_document_uploads,
        open_send_windows,
        parse_remindable_statuses,
        reminder_cap_reached,
        reminder_message,
        should_skip_driver,
    )
    from utils.error_handling import (  # type: ignore
        DuplicateRecordError,
        ServiceUnavailableException,
        db_error_text,
    )


logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 15 * 60
PAGE_SIZE = 200
LOG_TABLE = "driver_onboarding_reminder_log"

# Windows ('<tz>:<local-date>') this process has already scanned. The 15-min
# tick is only a clock check; the full drivers/documents scan runs once per
# timezone per day, inside the 08:00 local send hour. In-process only — the
# DB claim log keeps sends idempotent across replicas and restarts.
_completed_windows: set[str] = set()


async def _get_rows(table: str, filters: dict[str, Any] | None, **kwargs) -> list[dict[str, Any]] | None:
    """Rows, or None when the read failed.

    None (not []) so callers can tell "table is empty" from "DB error" —
    a failed read during the send window must leave the window incomplete
    so the next tick retries, instead of silently skipping the day.
    """
    try:
        return await db.get_rows(table, filters or {}, **kwargs)
    except Exception as exc:
        original = getattr(exc, "details", {}).get("original") if hasattr(exc, "details") else None
        logger.error("onboarding reminders: failed to read %s: %s original=%s", table, exc, original, exc_info=True)
        return None


# Sentinel distinguishing "claim insert failed" (retry next tick) from
# "already claimed today" (None — definitive, no retry needed).
_CLAIM_ERROR = object()


async def _claim(driver_id: str, user_id: str, kind: str, local_date: str, now: datetime):
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
    except ServiceUnavailableException:
        # Breaker-open / DB-down is systemic: every remaining claim in this
        # scan will fail identically. Propagate so the scan aborts with ONE
        # log line instead of a traceback per driver (2026-07-04 log storm);
        # the window stays incomplete so the next tick retries.
        raise
    except Exception as exc:
        # Already-claimed today is the expected, benign outcome of the daily
        # unique index (driver_id, reminder_type, local_date) — another replica,
        # an earlier tick, or a lost-response retry already wrote the row. It
        # should be a silent skip, not an error. We still reach here (rather than
        # the DuplicateRecordError branch above) when the wrapped exception class
        # differs by import path under the dual-import pattern, so fall back to
        # matching the unique-violation by its text, the same way scheduled_rides
        # and insurance_periods do.
        text = db_error_text(exc)
        if "duplicate key" in text or "unique constraint" in text or "23505" in text:
            return None
        original = getattr(exc, "details", {}).get("original") if hasattr(exc, "details") else None
        logger.error(
            "onboarding reminders: claim failed for %s/%s: %s original=%s",
            driver_id,
            kind,
            exc,
            original,
            exc_info=True,
        )
        return _CLAIM_ERROR


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


async def _send(driver: dict[str, Any], kind: str, local_date: str, now: datetime) -> bool | None:
    """True = delivered, False = not needed/failed-after-claim, None = claim
    insert failed (no dedupe row exists — the window must stay incomplete so
    the next tick retries)."""
    driver_id = str(driver["id"])
    user_id = str(driver["user_id"])
    claim = await _claim(driver_id, user_id, kind, local_date, now)
    if claim is _CLAIM_ERROR:
        return None
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
    stats = {"drivers_scanned": 0, "claims_attempted": 0, "pushes_delivered": 0, "capped_skips": 0}

    area_rows = await _get_rows("service_areas", {}, limit=1000, columns="id,timezone,required_documents")
    if area_rows is None:
        return stats  # read failed — retry next tick, window stays incomplete
    areas = {str(r["id"]): r for r in area_rows if r.get("id")}

    # Daily gate: the drivers + documents scan only runs while some known
    # timezone (area timezones + default) is inside its 08:00 send hour, and
    # at most once per window per process. Outside the window each tick costs
    # only the small service_areas read above. The drivers table has no
    # timezone column (the driver.timezone fallback in driver_timezone() is
    # defensive only), so drivers without a service area always resolve to
    # DEFAULT_TIMEZONE — whose window is always gated.
    windows = open_send_windows({(a.get("timezone") or "") for a in areas.values()}, now)
    if not windows - _completed_windows:
        return stats

    global_reqs = await _get_rows("document_requirements", {}, limit=200, columns="id,name,is_mandatory")
    if global_reqs is None:
        return stats  # read failed — retry next tick, window stays incomplete

    # Any failed read below leaves scan_ok False so the window is NOT marked
    # completed — the next 15-min tick inside the same send hour retries.
    # Claims already written for earlier pages dedupe via DuplicateRecordError.
    remindable_statuses, max_per_type = await _reminder_settings()

    try:
        scan_ok = await _scan_pages(areas, global_reqs, now, stats, remindable_statuses, max_per_type)
    except ServiceUnavailableException as exc:
        # Breaker-open / DB-down is systemic — _claim re-raises it so the
        # scan aborts with one log line, not a traceback per driver.
        logger.error("onboarding reminders: aborting scan, database unavailable: %s", exc)
        scan_ok = False

    if scan_ok:
        _completed_windows.update(windows)
    # Drop windows older than yesterday so the memo can't grow unbounded
    # (ISO dates compare lexicographically).
    cutoff = (now - timedelta(days=1)).date().isoformat()
    _completed_windows.difference_update({w for w in _completed_windows if w.rsplit(":", 1)[1] < cutoff})

    if stats["claims_attempted"] or stats["capped_skips"]:
        logger.info("onboarding reminders: %s", stats)
    return stats


async def _reminder_settings() -> tuple[frozenset[str], int]:
    """(remindable statuses, max reminders per type) from app_settings.

    Both are overridable without a redeploy — this loop pushes to real drivers
    daily, so narrowing it needs a config-only rollback path. On a settings
    read failure we fall back to the conservative built-in defaults rather than
    the old send-to-everyone behaviour.
    """
    try:
        settings = await _get_app_settings()
    except Exception:
        logger.error("onboarding reminders: failed to read app_settings; using defaults", exc_info=True)
        return parse_remindable_statuses(None), DEFAULT_MAX_REMINDERS_PER_TYPE

    statuses = parse_remindable_statuses(settings.get("driver_onboarding_reminder_statuses"))
    raw_max = settings.get("driver_onboarding_reminder_max_days")
    try:
        max_per_type = DEFAULT_MAX_REMINDERS_PER_TYPE if raw_max is None else int(raw_max)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid driver_onboarding_reminder_max_days %r; using %s",
            raw_max,
            DEFAULT_MAX_REMINDERS_PER_TYPE,
        )
        max_per_type = DEFAULT_MAX_REMINDERS_PER_TYPE
    return statuses, max_per_type


COUNTS_RPC = "driver_onboarding_reminder_counts"


async def _counts_via_rpc(driver_ids: list[str]) -> dict[tuple[str, str], int] | None:
    """Exact per-driver/per-type counts from the DB, or None if unavailable.

    Aggregating server-side keeps the row limit out of the path — see
    migration 273 for why a client-side count was not safe.
    """
    try:
        rows = await db.rpc(COUNTS_RPC, {"p_driver_ids": driver_ids})
    except Exception as exc:
        original = getattr(exc, "details", {}).get("original") if hasattr(exc, "details") else None
        logger.error(
            "onboarding reminders: %s RPC failed (is migration 273 applied?): %s original=%s",
            COUNTS_RPC,
            exc,
            original,
            exc_info=True,
        )
        return None
    if rows is None:
        logger.error("onboarding reminders: %s RPC unavailable (no supabase client)", COUNTS_RPC)
        return None
    return {(str(r.get("driver_id")), str(r.get("reminder_type"))): int(r.get("sent_count") or 0) for r in rows}


async def _counts_via_scan(driver_ids: list[str], max_per_type: int) -> dict[tuple[str, str], int] | None:
    """Fallback client-side count. None on read failure.

    Used only when the RPC is unavailable (migration 273 not yet applied).
    The log holds rows written before the cap existed, so the row count is NOT
    bounded by max_per_type and a limit can truncate. PostgREST applies no
    ORDER BY, so truncation drops arbitrary rows and under-counts — which would
    push a capped driver again, the exact failure this cap exists to prevent.
    So truncation is detected and fails CLOSED: the whole page is reported at
    the cap, suppressing sends, rather than silently over-notifying.
    """
    limit = max(5000, len(driver_ids) * (max_per_type + 1) * 4)
    rows = await _get_rows(
        LOG_TABLE,
        {"driver_id": {"$in": driver_ids}},
        limit=limit,
        columns="driver_id,reminder_type",
    )
    if rows is None:
        return None
    if len(rows) >= limit:
        logger.error(
            "onboarding reminders: claim-log read hit the %s-row limit for %s drivers — "
            "counts would under-report, suppressing this page's reminders instead. "
            "Apply migration 273 so counts come from the %s RPC.",
            limit,
            len(driver_ids),
            COUNTS_RPC,
        )
        return {(did, kind): max_per_type for did in driver_ids for kind in (VEHICLE_DETAILS, VEHICLE_DOCUMENTS)}
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (str(row.get("driver_id")), str(row.get("reminder_type")))
        counts[key] = counts.get(key, 0) + 1
    return counts


async def _prior_reminder_counts(driver_ids: list[str], max_per_type: int) -> dict[tuple[str, str], int] | None:
    """{(driver_id, reminder_type): reminders already claimed}, or None on read failure.

    Skipped entirely when the cap is disabled. Prefers the DB-side aggregate;
    falls back to a truncation-guarded client-side count so the loop still
    works (conservatively) before migration 273 is applied.
    """
    if max_per_type <= 0 or not driver_ids:
        return {}
    counts = await _counts_via_rpc(driver_ids)
    if counts is not None:
        return counts
    return await _counts_via_scan(driver_ids, max_per_type)


async def _scan_pages(
    areas: dict[str, dict[str, Any]],
    global_reqs: list[dict[str, Any]],
    now: datetime,
    stats: dict[str, int],
    remindable_statuses: frozenset[str],
    max_per_type: int,
) -> bool:
    """Page through drivers, attempting claims + pushes.

    Returns scan_ok: False when any page read or per-row claim failed, so
    the send window stays incomplete and the next tick retries.
    ServiceUnavailableException propagates to the caller — systemic outage,
    abort instead of iterating the remaining drivers.
    """
    scan_ok = True
    offset = 0
    while True:
        drivers = await _get_rows(
            "drivers",
            {},
            limit=PAGE_SIZE,
            offset=offset,
            columns="id,user_id,status,deleted_at,service_area_id,vehicle_type_id,vehicle_make,vehicle_model,license_plate",
        )
        if drivers is None:
            scan_ok = False
            break
        if not drivers:
            break

        ids = [str(d["id"]) for d in drivers if d.get("id")]
        docs = await _get_rows(
            "driver_documents",
            {"driver_id": {"$in": ids}},
            limit=max(1000, len(ids) * 10),
            columns="id,driver_id,requirement_id,requirement_key,document_type,status",
        )
        if docs is None:
            # Without the docs we can't tell uploaded from missing; processing
            # the page anyway would push false "upload your documents" nags.
            scan_ok = False
            break
        docs_by_driver: dict[str, list[dict[str, Any]]] = {}
        for doc in docs:
            if doc.get("driver_id"):
                docs_by_driver.setdefault(str(doc["driver_id"]), []).append(doc)

        prior = await _prior_reminder_counts(ids, max_per_type)
        if prior is None:
            # Without the log we can't tell a first reminder from a 40th;
            # processing anyway would re-open the unbounded daily nag.
            scan_ok = False
            break

        for driver in drivers:
            stats["drivers_scanned"] += 1
            if should_skip_driver(driver, remindable_statuses):
                continue
            local_date = local_date_for_send_window(driver, areas, now)
            if not local_date:
                continue
            driver_id = str(driver["id"])
            if not _has_vehicle_details(driver):
                if reminder_cap_reached(prior.get((driver_id, VEHICLE_DETAILS), 0), max_per_type):
                    stats["capped_skips"] += 1
                else:
                    stats["claims_attempted"] += 1
                    sent = await _send(driver, VEHICLE_DETAILS, local_date, now)
                    if sent is None:
                        scan_ok = False  # claim insert failed — retry this window next tick
                    else:
                        stats["pushes_delivered"] += int(sent)
            if missing_required_document_uploads(driver, docs_by_driver.get(driver_id, []), areas, global_reqs):
                if reminder_cap_reached(prior.get((driver_id, VEHICLE_DOCUMENTS), 0), max_per_type):
                    stats["capped_skips"] += 1
                else:
                    stats["claims_attempted"] += 1
                    sent = await _send(driver, VEHICLE_DOCUMENTS, local_date, now)
                    if sent is None:
                        scan_ok = False
                    else:
                        stats["pushes_delivered"] += int(sent)

        if len(drivers) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return scan_ok


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
