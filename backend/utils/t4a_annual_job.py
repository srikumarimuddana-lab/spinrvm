"""T4A annual issuance loop — CRA compliance (P4-7).

Runs daily at 08:00 UTC.  On the last day of February each year it identifies
all drivers who earned ≥ $500 in the prior calendar year, sends each a push
notification that their T4A slip is ready for download, and logs the batch to
``audit_logs``.

Replay-safety
-------------
A Redis key ``spinr:t4a:issued:{prior_year}`` is claimed via SET NX EX before
the batch runs.  Only the first replica to acquire the key processes the batch;
others no-op.  TTL is 400 days — safely beyond one calendar year so the key
cannot be claimed again in the same year but doesn't accumulate forever.

The in-process ``redis_client`` fallback (when ``REDIS_URL`` is unset) means
this also behaves correctly in single-replica dev/test mode.

CRA rule
--------
T4A (Box 048 – Fees for Services) is required for any person paid ≥ $500 from
a single payer in a calendar year.  The on-demand endpoint
``GET /api/v1/drivers/t4a/{year}/pdf`` already generates the PDF; this job
notifies drivers and records issuance so the audit trail is complete.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
from datetime import datetime, timezone
from decimal import Decimal

logger = logging.getLogger(__name__)

# Poll every 60 s so the loop stays responsive to restarts; actual work
# only happens once per year.
_LOOP_POLL_SECONDS = 60
# 400 days > 1 year — key expires well before the next issuance cycle.
_LOCK_TTL_SECONDS = 400 * 24 * 60 * 60
# CRA reporting threshold (CAD).
_T4A_THRESHOLD = Decimal("500.00")
# Run after 08:00 UTC so nightly retention jobs and reconciliation finish first.
_RUN_AFTER_HOUR_UTC = 8

try:
    from ..utils.redis_client import redis_set_nx  # type: ignore
except ImportError:
    from utils.redis_client import redis_set_nx  # type: ignore

try:
    from .. import db_supabase  # type: ignore
except ImportError:
    import db_supabase  # type: ignore

try:
    from ..features import send_push_notification  # type: ignore
except ImportError:
    from features import send_push_notification  # type: ignore

try:
    from ..utils.audit_logger import log_admin_action  # type: ignore
except ImportError:
    from utils.audit_logger import log_admin_action  # type: ignore

_SYSTEM_ACTOR = {"id": "system", "role": "system"}


async def t4a_annual_job_loop() -> None:
    """Entry point spawned by lifespan.py. Runs indefinitely."""
    while True:
        try:
            await _maybe_run_tick()
        except Exception:
            logger.error("t4a_annual_job tick failed", exc_info=True)
        await asyncio.sleep(_LOOP_POLL_SECONDS)


async def _maybe_run_tick() -> None:
    """Run the T4A batch only on the last day of February, after 08:00 UTC."""
    now = datetime.now(timezone.utc)
    if now.hour < _RUN_AFTER_HOUR_UTC:
        return

    # Last day of February: calendar.monthrange returns (weekday, days_in_month)
    _, days_in_feb = calendar.monthrange(now.year, 2)
    if not (now.month == 2 and now.day == days_in_feb):
        return

    prior_year = now.year - 1
    lock_key = f"spinr:t4a:issued:{prior_year}"
    acquired = await redis_set_nx(lock_key, "1", _LOCK_TTL_SECONDS)
    if not acquired:
        logger.info(f"t4a_annual_job: already issued for {prior_year}, skipping")
        return

    await _run_issuance(prior_year)


async def _run_issuance(year: int) -> None:
    """Identify eligible drivers and notify them that their T4A is ready."""
    logger.info(f"t4a_annual_job: starting issuance batch for tax year {year}")

    try:
        drivers = await db_supabase.get_rows("drivers", {}, limit=10000)
    except Exception:
        logger.error(f"t4a_annual_job: failed to fetch drivers for {year}", exc_info=True)
        return

    eligible: list[dict] = []
    for driver in drivers:
        try:
            earnings = await _driver_annual_earnings(driver["id"], year)
        except Exception:
            logger.error(f"t4a_annual_job: earnings query failed for driver {driver['id']}", exc_info=True)
            continue
        if earnings >= _T4A_THRESHOLD:
            eligible.append({"driver": driver, "earnings": earnings})

    logger.info(f"t4a_annual_job: {len(eligible)} of {len(drivers)} drivers eligible for {year} T4A")

    notified = 0
    for item in eligible:
        driver = item["driver"]
        earnings = item["earnings"]
        user_id = driver.get("user_id")
        if not user_id:
            continue
        try:
            await send_push_notification(
                user_id,
                title="Your T4A slip is ready",
                body=f"Your {year} T4A tax slip (${earnings:.2f} in earnings) is ready to download in the Spinr driver app.",
                data={"type": "t4a_ready", "year": str(year)},
            )
            notified += 1
        except Exception:
            logger.error(f"t4a_annual_job: push failed for user {user_id}", exc_info=True)

    # Audit log — one row per batch (not per driver to avoid table bloat).
    try:
        await log_admin_action(
            admin=_SYSTEM_ACTOR,
            action="t4a_annual_issuance",
            resource="drivers",
            resource_id=f"batch_{year}",
            details={
                "tax_year": year,
                "total_drivers": len(drivers),
                "eligible_count": len(eligible),
                "notified_count": notified,
            },
        )
    except Exception:
        logger.error("t4a_annual_job: audit log write failed", exc_info=True)

    logger.info(f"t4a_annual_job: issuance complete for {year} — {notified}/{len(eligible)} notified")


async def _driver_annual_earnings(driver_id: str, year: int) -> Decimal:
    """Sum completed driver_earnings for a driver in a calendar year, plus
    legacy-era income synced from Stripe transfer history.

    payout_type='stripe_sync' rows (services/stripe_payout_sync_service.py)
    are the only record of income the OLD app paid out — its rides were never
    imported — so the ≥$500 eligibility check and the notified amount must
    include them, attributed to the year of the transfer (CRA reports amounts
    PAID). App-native payouts are cash-outs of the ride earnings summed here,
    and 'legacy_import' offsets pair with imported rides — only the synced
    type is added, so nothing double-counts. Must stay consistent with
    routes/drivers/tax_exports.get_t4a_summary (the slip the driver downloads).
    """
    rides = await db_supabase.get_rows(
        "rides",
        {
            "driver_id": driver_id,
            "status": "completed",
            "ride_completed_at": {
                "$gte": f"{year}-01-01T00:00:00+00:00",
                "$lt": f"{year + 1}-01-01T00:00:00+00:00",
            },
        },
        limit=10000,
    )
    ride_total = sum(
        (Decimal(str(r.get("driver_earnings") or "0")) for r in rides),
        Decimal("0"),
    )
    synced_rows = await db_supabase.get_rows(
        "payouts",
        {
            "driver_id": driver_id,
            "payout_type": "stripe_sync",
            "created_at": {
                "$gte": f"{year}-01-01T00:00:00+00:00",
                "$lt": f"{year + 1}-01-01T00:00:00+00:00",
            },
        },
        limit=10000,
    )
    synced_total = sum(
        (Decimal(str(p.get("amount") or "0")) for p in synced_rows),
        Decimal("0"),
    )
    return ride_total + synced_total
