"""Daily Stripe ↔ DB ↔ wallet reconciliation loop.

Runs once per day (aligned to 02:00 UTC) comparing Stripe PaymentIntent
totals against financial_events rows and alerting finance on any discrepancy
> $0.01.

Replay-safety: a date-suffixed Redis key (``spinr:reconciliation:<YYYY-MM-DD>``)
is claimed via SET NX EX. Only the first replica to acquire it runs the tick
for that calendar day; the in-process fallback in `redis_client` makes this
behave correctly in single-replica dev as well.

Required env / settings: ``stripe_secret_key`` in app_settings table.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

try:
    from .redis_client import redis_set_nx  # type: ignore
except ImportError:
    from utils.redis_client import redis_set_nx  # type: ignore

logger = logging.getLogger(__name__)

# Run once per day; loop wakes every 60 s to stay aligned to 02:00 UTC.
_LOOP_POLL_SECONDS = 60
_DISCREPANCY_THRESHOLD_CENTS = 1  # $0.01 — alert on any cent-level drift
# 25 h: the date-suffixed lock key only needs to outlive the current calendar
# day's 02:00 UTC window. Tomorrow's tick uses a different key entirely, so
# the TTL is just defensive cleanup.
_LOCK_TTL_SECONDS = 25 * 60 * 60


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
    lock_key = f"spinr:reconciliation:{today_key}"

    # Redis leader-lock via SET NX EX: only the first replica to acquire the
    # date-suffixed key for today runs the tick. The redis_client wrapper
    # falls back to an in-process dict when REDIS_URL is unset (dev/test),
    # so this is also correct in single-replica mode.
    acquired = await redis_set_nx(lock_key, "1", _LOCK_TTL_SECONDS)
    if not acquired:
        return

    yesterday = (now - timedelta(days=1)).date()
    await _run_reconciliation(yesterday)


async def _run_reconciliation(date) -> None:  # noqa: ANN001
    """Compare Stripe totals to financial_events for the given calendar date."""
    logger.info(f"reconciliation: starting for {date}")

    try:
        stripe_total_cents = await _sum_stripe_intents(date)
    except RuntimeError as e:
        if str(e) == "stripe_secret_key not configured":
            logger.info(f"reconciliation: {e} — skipping for {date}")
            return
        logger.error(f"reconciliation: failed to query Stripe for {date}", exc_info=True)
        return
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

    await _check_entry_balance(date)
    await _check_leg_completeness()


async def _check_leg_completeness() -> None:
    """Alert when projectable headers have gone >24h without double-entry legs.

    The unbalanced-view check below can only see legs that EXIST; a header the
    projection loop never managed to decompose is invisible to it. This uses
    the projection's own work-queue RPC (migration 287) — anything still in
    that queue past 24h means the loop is dead, wedged, or consistently
    failing, which the 15-minute cadence should never allow.

    Skips silently when double-entry is off (queue is expected to grow) or the
    RPC is absent (migration 287 not applied). Never raises.
    """
    try:
        from services.ledger_service import double_entry_enabled  # type: ignore
    except ImportError:
        from ..services.ledger_service import double_entry_enabled  # type: ignore

    try:
        from db_supabase import rpc  # type: ignore
    except ImportError:
        from ..db_supabase import rpc  # type: ignore

    try:
        if not await double_entry_enabled():
            return
        rows = await rpc("financial_events_missing_legs", {"p_limit": 500}) or []
    except Exception:
        logger.info("reconciliation: leg-completeness check unavailable (migration 287 not applied?)")
        return

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    stale = []
    for r in rows:
        created = r.get("created_at")
        try:
            created_dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            stale.append(r)  # unparseable age — surface it rather than hide it
            continue
        if created_dt < cutoff:
            stale.append(r)

    if not stale:
        logger.info("reconciliation: double-entry leg projection current (queue <24h)")
        return

    logger.error(
        f"reconciliation ALERT: {len(stale)} financial_events headers >24h old "
        f"with no double-entry legs — projection loop dead or failing. "
        f"event_ids={[r.get('id') for r in stale[:10]]}"
    )


async def _check_entry_balance(date) -> None:  # noqa: ANN001
    """Alert on any double-entry journal whose legs do not net to zero.

    Reads the financial_event_entries_unbalanced view (migration 286), which is
    expected to be permanently empty. A row means a leg builder produced a
    lopsided entry or a partial leg write survived — an accounting defect, so
    it is logged at error even though no rider is affected.

    Never raises: this is an observability check bolted onto the end of the
    reconciliation run and must not mask the Stripe-vs-ledger result above. The
    table is absent until migration 286 is applied, which is not an error.
    """
    try:
        from db_supabase import run_sync  # type: ignore
        from supabase_client import supabase  # type: ignore
    except ImportError:
        from ..db_supabase import run_sync  # type: ignore
        from ..supabase_client import supabase  # type: ignore

    day_start = datetime(date.year, date.month, date.day, tzinfo=timezone.utc).isoformat()
    day_end = (datetime(date.year, date.month, date.day, tzinfo=timezone.utc) + timedelta(days=1)).isoformat()

    try:
        rows = await run_sync(
            lambda: (
                supabase.table("financial_event_entries_unbalanced")
                .select("event_id,debit_cents,credit_cents,imbalance_cents")
                .gte("created_at", day_start)
                .lt("created_at", day_end)
                .execute()
            )
        )
    except Exception:
        logger.info(f"reconciliation: entry-balance check unavailable for {date} (migration 286 not applied?)")
        return

    unbalanced = rows.data or []
    if not unbalanced:
        logger.info(f"reconciliation: {date} double-entry legs balanced")
        return

    logger.error(
        f"reconciliation ALERT: {date} {len(unbalanced)} unbalanced ledger "
        f"entries — event_ids={[r.get('event_id') for r in unbalanced[:10]]}"
    )


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

    # timedelta, not day + 1: datetime(y, m, 31 + 1) raises "day is out of
    # range for month" on the last day of every month, which killed the whole
    # reconciliation tick ~12 nights a year (same boundary handling as the
    # epoch+86400 form in _sum_stripe_intents).
    day_start_dt = datetime(date.year, date.month, date.day, tzinfo=timezone.utc)
    day_start = day_start_dt.isoformat()
    day_end = (day_start_dt + timedelta(days=1)).isoformat()

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
