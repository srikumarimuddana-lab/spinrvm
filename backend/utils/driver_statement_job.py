"""Periodic driver earnings-statement emails — weekly + monthly (Uber-style).

Every 30 minutes each replica checks whether statements exist for the recently
COMPLETED periods (Mon–Sun weeks, calendar months, America/Regina wall clock)
and produces the missing ones: build the aggregates
(utils/driver_statement.py), render the branded PDF
(utils/driver_statement_pdf.py), and email it to the driver.

A period is only eligible once ``_GRACE_DAYS`` have passed since it closed, so
tips added after the last ride of the period still land on the statement (see
the constant's comment — without the grace such a tip appears on NO statement).
In practice weekly statements go out Wednesday for the week ending Sunday, and
monthly statements on the 3rd.

Replay-safety (background-loop contract, claim-flag via insert idempotency):
the ``driver_statements`` row is INSERTed with a deterministic id BEFORE any
build/send work. The migration-272 unique index on
(driver_id, period_type, period_start) makes a second replica's insert
violate — that replica skips, so a statement email can never be sent twice.
A crash after the claim leaves status='claimed', which is surfaced in the
admin listing and deliberately NEVER auto-retried (a retry could double-send
an email that did go out before the crash); the admin re-sends manually from
the dashboard if needed.

Statements for drivers with no activity in the period are recorded as
``skipped_inactive`` (no email — a $0.00 statement is spam) and drivers
without an email address as ``skipped_no_email``; both rows exist so the
period converges and is never rescanned for that driver.

There is no time-of-day gate: the claim set makes every tick cheap once a
period is complete, and the first tick after a deploy naturally catches up
the eligible periods.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_LOOP_INTERVAL_SECONDS = 30 * 60
_HEARTBEAT_NAME = "driver_statements (30min)"

# Wait this many days after a period closes before producing its statement.
#
# Not cosmetic: routes/rides/payments.add_tip accepts a tip on ANY completed
# ride with no time window, and a statement is keyed to ride_completed_at. A
# statement cut at midnight would miss a tip added the next morning for the
# last night's ride — and because the ledger row stops the period being
# revisited, and the NEXT period's window excludes that ride, the tip would
# never appear on any statement at all. The grace lets late tips land first.
# (Uber/Lyft weekly statements arrive mid-week for the same reason.)
_GRACE_DAYS = 2

# Also re-check the period before the latest one. The ledger claim makes a
# re-scan free, and without it a multi-day outage spanning a period boundary
# would skip that period's statements permanently.
_CATCHUP_PERIODS = 2

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:  # pragma: no cover

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from .. import db_supabase  # type: ignore
except ImportError:  # pragma: no cover
    import db_supabase  # type: ignore

try:
    from ..features import send_email  # type: ignore
except ImportError:  # pragma: no cover
    from features import send_email  # type: ignore

try:
    from .driver_statement import (
        MONTHLY,
        STATEMENT_TZ,
        WEEKLY,
        build_statement,
        latest_completed_monthly,
        latest_completed_weekly,
        period_bounds,
    )
    from .driver_statement_pdf import generate_driver_statement_pdf
except ImportError:  # pragma: no cover
    from utils.driver_statement import (  # type: ignore
        MONTHLY,
        STATEMENT_TZ,
        WEEKLY,
        build_statement,
        latest_completed_monthly,
        latest_completed_weekly,
        period_bounds,
    )
    from utils.driver_statement_pdf import generate_driver_statement_pdf  # type: ignore


def statement_id_for(period_type: str, period_start: date, driver_id: str) -> str:
    """Deterministic ledger id — the PK half of the insert-claim."""
    return f"stmt-{period_type}-{period_start.isoformat()}-{driver_id}"


def _grace_elapsed(period_end: date, today_local: date) -> bool:
    """True once ``_GRACE_DAYS`` full days have passed since the period closed."""
    return today_local >= period_end + timedelta(days=_GRACE_DAYS + 1)


def _prior_month(month_start: date) -> tuple[date, date]:
    end = month_start - timedelta(days=1)
    return end.replace(day=1), end


def _due_periods(today_local: date) -> list[tuple[str, date, date]]:
    """Periods that are closed AND past their grace window.

    Returns the latest completed weekly/monthly periods plus ``_CATCHUP_PERIODS
    - 1`` older ones, so an outage spanning a period boundary is recovered on
    the next tick rather than silently skipping that period forever.
    """
    due: list[tuple[str, date, date]] = []

    w_start, w_end = latest_completed_weekly(today_local)
    for i in range(_CATCHUP_PERIODS):
        start = w_start - timedelta(days=7 * i)
        end = w_end - timedelta(days=7 * i)
        if _grace_elapsed(end, today_local):
            due.append((WEEKLY, start, end))

    m_start, m_end = latest_completed_monthly(today_local)
    for _ in range(_CATCHUP_PERIODS):
        if _grace_elapsed(m_end, today_local):
            due.append((MONTHLY, m_start, m_end))
        m_start, m_end = _prior_month(m_start)

    return due


def _is_unique_violation(exc: Exception) -> bool:
    s = str(exc).lower()
    return "unique" in s or "duplicate" in s or "23505" in s


async def driver_statement_loop() -> None:
    """Entry point spawned by lifespan.py. Runs indefinitely."""
    while True:
        try:
            await _tick()
        except Exception:
            logger.error("driver_statement_job tick failed", exc_info=True)
        _record_heartbeat(_HEARTBEAT_NAME)
        await asyncio.sleep(_LOOP_INTERVAL_SECONDS)


async def _tick() -> None:
    today_local = datetime.now(STATEMENT_TZ).date()
    for period_type, start_d, end_d in _due_periods(today_local):
        try:
            await _ensure_period(period_type, start_d, end_d)
        except Exception:
            # DB read failure for the period scan — surface loudly and let the
            # next tick retry; individual drivers are guarded separately.
            logger.error(
                "driver_statement_job: period scan failed type=%s start=%s",
                period_type,
                start_d,
                exc_info=True,
            )


async def _ensure_period(period_type: str, start_d: date, end_d: date) -> None:
    """Produce statements for every driver that doesn't have one yet."""
    existing = (
        await db_supabase.get_rows(
            "driver_statements",
            {"period_type": period_type, "period_start": start_d.isoformat()},
            columns="driver_id",
            limit=20000,
        )
        or []
    )
    done = {r["driver_id"] for r in existing}
    drivers = await db_supabase.get_rows("drivers", {}, columns="id,user_id,name", limit=10000) or []

    todo = [d for d in drivers if d["id"] not in done]
    if not todo:
        return
    logger.info(
        "driver_statement_job: %d/%d drivers need a %s statement for %s",
        len(todo),
        len(drivers),
        period_type,
        start_d,
    )
    for driver in todo:
        try:
            await _process_driver(driver, period_type, start_d)
        except Exception:
            # Never let one driver's failure stall the batch; the claimed row
            # (if the claim landed) records the failure for the admin listing.
            logger.error(
                "driver_statement_job: statement failed driver=%s type=%s start=%s",
                driver.get("id"),
                period_type,
                start_d,
                exc_info=True,
            )


async def _process_driver(driver: dict, period_type: str, start_d: date) -> None:
    driver_id = driver["id"]
    start_d, end_d = period_bounds(period_type, start_d)
    row_id = statement_id_for(period_type, start_d, driver_id)
    now_iso = datetime.now(timezone.utc).isoformat()

    # ── The claim: insert BEFORE any work (unique index = replay guard) ──
    try:
        await db_supabase.insert_one(
            "driver_statements",
            {
                "id": row_id,
                "driver_id": driver_id,
                "period_type": period_type,
                "period_start": start_d.isoformat(),
                "period_end": end_d.isoformat(),
                "status": "claimed",
                "created_at": now_iso,
            },
        )
    except Exception as exc:
        if _is_unique_violation(exc):
            return  # another replica owns this (driver, period)
        raise

    async def _finish(status: str, extra: dict | None = None) -> None:
        try:
            await db_supabase.update_one(
                "driver_statements",
                {"id": row_id},
                {"status": status, "updated_at": datetime.now(timezone.utc).isoformat(), **(extra or {})},
            )
        except Exception:
            logger.error("driver_statement_job: status write failed row=%s", row_id, exc_info=True)

    try:
        user = await db_supabase.get_user_by_id(driver.get("user_id")) if driver.get("user_id") else None
        name = (
            f"{(user or {}).get('first_name', '')} {(user or {}).get('last_name', '')}".strip()
            or driver.get("name")
            or "Driver"
        )
        stmt = await build_statement(driver, period_type, start_d, driver_name=name)
        totals = {"earnings": stmt["earnings"], "payouts_total": stmt["payouts_total"], "trips": stmt["trips"]}

        if not stmt["has_activity"]:
            await _finish("skipped_inactive", {"totals": totals})
            return

        email = ((user or {}).get("email") or "").strip()
        if "@" not in email:
            await _finish("skipped_no_email", {"totals": totals})
            return

        pdf_bytes = generate_driver_statement_pdf(stmt)
        filename = f"spinr-statement-{period_type}-{start_d.isoformat()}.pdf"
        kind = "monthly" if period_type == MONTHLY else "weekly"
        sent = await send_email(
            to=email,
            subject=f"Your Spinr {kind} earnings statement — {stmt['period_label']}",
            body=(
                "Hi,\n\n"
                f"Your Spinr {kind} earnings statement for {stmt['period_label']} is attached.\n\n"
                f"  Total earnings: ${stmt['earnings']['total']}\n"
                f"  Trips completed: {stmt['trips']}\n"
                f"  Paid out this period: ${stmt['payouts_total']}\n\n"
                "The attached PDF has the full breakdown. You keep 100% of every "
                "fare — Spinr takes no commission.\n\n"
                "Questions? Reply to support@spinr.ca.\n\n"
                "— The Spinr Team"
            ),
            attachments=[{"filename": filename, "content": pdf_bytes, "mime": "application/pdf"}],
            email_type="transactional",
            recipient_user_id=driver.get("user_id"),
            log_id="stmt",
        )
        if sent:
            await _finish("sent", {"totals": totals, "email_sent_at": datetime.now(timezone.utc).isoformat()})
        else:
            # Both providers rejected without raising — record it loudly.
            logger.error("driver_statement_job: email NOT sent (provider rejected) row=%s", row_id)
            await _finish("failed", {"totals": totals, "failure_reason": "email provider rejected"})
    except Exception as exc:
        logger.error("driver_statement_job: build/send failed row=%s", row_id, exc_info=True)
        await _finish("failed", {"failure_reason": str(exc)[:500]})
