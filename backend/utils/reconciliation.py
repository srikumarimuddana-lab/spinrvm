"""Daily Stripe ↔ DB ↔ wallet reconciliation loop.

Runs once per day (aligned to 02:00 UTC) comparing Stripe PaymentIntent
totals against financial_events rows and alerting finance on any discrepancy
> $0.01.

Replay-safety: the claim key is the ISO date string written to a Redis key
``spinr:reconciliation:last_run``. Only the first replica to successfully
acquire the key runs the tick for that calendar day.

Required env / settings: ``stripe_secret_key`` in app_settings table.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Run once per day; loop wakes every 60 s to stay aligned to 02:00 UTC.
_LOOP_POLL_SECONDS = 60
_DISCREPANCY_THRESHOLD_CENTS = 1  # $0.01 — alert on any cent-level drift


async def reconciliation_loop() -> None:
    """Entry point spawned by lifespan.py. Runs indefinitely."""
    while True:
        try:
            await _maybe_run_tick()
        except Exception:
            logger.error("reconciliation_loop tick failed", exc_info=True)
        await asyncio.sleep(_LOOP_POLL_SECONDS)


async def _maybe_run_tick() -> None:
    """Run a reconciliation tick only if it's after 02:00 UTC and hasn't run today."""
    now = datetime.now(timezone.utc)
    if now.hour < 2:
        return

    today_key = now.date().isoformat()

    # Redis leader-lock: only one replica runs the daily tick.
    try:
        from utils.redis_client import get_redis  # type: ignore

        redis = get_redis()
        lock_key = "spinr:reconciliation:last_run"
        last_run = await redis.get(lock_key)
        if last_run and last_run.decode() == today_key:
            return
        # Claim today's slot (TTL = 25 h so it survives the next 02:00 window)
        await redis.set(lock_key, today_key, ex=90000)
    except Exception:
        # Redis unavailable — fall back to always running (safe: reconciliation
        # is idempotent but may fire on every replica without Redis).
        logger.warning("reconciliation_loop: Redis unavailable; running without leader lock")

    yesterday = (now - timedelta(days=1)).date()
    await _run_reconciliation(yesterday)


async def _run_reconciliation(date) -> None:  # noqa: ANN001
    """Compare Stripe totals to financial_events for the given calendar date."""
    logger.info(f"reconciliation: starting for {date}")

    try:
        stripe_total_cents = await _sum_stripe_intents(date)
    except Exception:
        logger.error(f"reconciliation: failed to query Stripe for {date}", exc_info=True)
        return

    try:
        db_total_cents = await _sum_financial_events(date, "stripe_charge")
    except Exception:
        logger.error(f"reconciliation: failed to query financial_events for {date}", exc_info=True)
        return

    discrepancy = abs(stripe_total_cents - db_total_cents)
    logger.info(f"reconciliation: {date} stripe={stripe_total_cents}¢ db={db_total_cents}¢ discrepancy={discrepancy}¢")

    if discrepancy > _DISCREPANCY_THRESHOLD_CENTS:
        logger.error(
            f"reconciliation ALERT: {date} discrepancy {discrepancy}¢ "
            f"(stripe={stripe_total_cents}¢ db={db_total_cents}¢). "
            "Check financial_events for missing rows or Stripe for missed webhooks."
        )
        await _record_discrepancy(date, stripe_total_cents, db_total_cents, discrepancy)
    else:
        logger.info(f"reconciliation: {date} OK — within threshold")


async def _sum_stripe_intents(date) -> int:  # noqa: ANN001
    """Return total succeeded PaymentIntent amount in cents for the given UTC date."""
    import stripe  # type: ignore

    try:
        from settings_loader import get_app_settings  # type: ignore
    except ImportError:
        from ..settings_loader import get_app_settings  # type: ignore

    settings = await get_app_settings()
    stripe_key = settings.get("stripe_secret_key", "")
    if not stripe_key:
        raise RuntimeError("stripe_secret_key not configured")

    # Stripe list API uses Unix timestamps
    day_start = int(datetime(date.year, date.month, date.day, tzinfo=timezone.utc).timestamp())
    day_end = day_start + 86400

    total_cents = 0
    last_id = None
    while True:
        kwargs: dict = dict(
            created={"gte": day_start, "lt": day_end},
            limit=100,
            api_key=stripe_key,
        )
        if last_id:
            kwargs["starting_after"] = last_id

        intents = stripe.PaymentIntent.list(**kwargs)
        for pi in intents.data:
            if pi.status == "succeeded":
                total_cents += pi.amount

        if not intents.has_more:
            break
        last_id = intents.data[-1].id

    return total_cents


async def _sum_financial_events(date, event_type: str) -> int:  # noqa: ANN001
    """Return sum of delta_cents in financial_events for the given date and type."""
    try:
        from db_supabase import run_sync  # type: ignore
        from supabase_client import supabase  # type: ignore
    except ImportError:
        from ..db_supabase import run_sync  # type: ignore
        from ..supabase_client import supabase  # type: ignore

    day_start = datetime(date.year, date.month, date.day, tzinfo=timezone.utc).isoformat()
    day_end = datetime(date.year, date.month, date.day + 1, tzinfo=timezone.utc).isoformat()

    rows = await run_sync(
        lambda: (
            supabase.table("financial_events")
            .select("delta_cents")
            .eq("event_type", event_type)
            .gte("created_at", day_start)
            .lt("created_at", day_end)
            .execute()
        )
    )
    return sum(r["delta_cents"] for r in (rows.data or []) if r.get("delta_cents") is not None)


async def _record_discrepancy(date, stripe_cents: int, db_cents: int, delta: int) -> None:  # noqa: ANN001
    """Write a reconciliation_discrepancies row for ops follow-up."""
    try:
        from db_supabase import run_sync  # type: ignore
        from supabase_client import supabase  # type: ignore
    except ImportError:
        from ..db_supabase import run_sync  # type: ignore
        from ..supabase_client import supabase  # type: ignore

    try:
        await run_sync(
            lambda: (
                supabase.table("reconciliation_discrepancies")
                .insert(
                    {
                        "date": date.isoformat(),
                        "stripe_total_cents": stripe_cents,
                        "db_total_cents": db_cents,
                        "discrepancy_cents": delta,
                        "status": "open",
                    }
                )
                .execute()
            )
        )
    except Exception:
        # Non-fatal: the error log above is the primary alert mechanism.
        logger.error("reconciliation: failed to record discrepancy row", exc_info=True)
