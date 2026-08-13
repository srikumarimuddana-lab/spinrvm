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

# Double-entry leg projection progress marker (see _check_leg_completeness).
# Holds the event id at the head of the projection's work queue as of the last
# daily run; an unchanged head 24 h later means the loop has stopped draining.
_QUEUE_HEAD_KEY = "spinr:ledger:projection:queue_head"
# 8 days: long enough to survive a missed run or two, short enough that a
# genuinely abandoned marker does not linger forever.
_QUEUE_HEAD_TTL_SECONDS = 8 * 24 * 60 * 60
# Matches the RPC's own LIMIT clamp (migration 287). Only the head row drives
# the alert; the rest is sampled purely to report queue depth.
_QUEUE_SAMPLE_LIMIT = 500


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
    """Alert when the double-entry leg projection has stopped making progress.

    The unbalanced-view check below can only see legs that EXIST; a header the
    projection loop never managed to decompose is invisible to it. This uses
    the projection's own work-queue RPC (migration 287) as the signal.

    It deliberately does NOT alert on queue depth or on absolute row age. Both
    look alarming during the intended initial backfill: the moment
    ``ledger_double_entry_enabled`` first turns on, EVERY historical header is
    leg-less and older than any age threshold, so a depth/age rule would fire
    at ERROR every night for the whole drain — training on-call to ignore the
    one alert that is supposed to mean "the projection is dead", during the
    exact window when the feature is newest.

    What actually distinguishes "backfilling" from "dead" is movement. The
    queue is drained oldest-first, so a live loop advances its head every tick
    (200 rows / 15 min). We store the head's event id between daily runs and
    alert only when it has not moved. Nothing can legitimately pin the head:
    an event that cannot be decomposed is booked DEGRADED rather than skipped,
    precisely so it leaves the queue (see utils/ledger_projection.py).

    Skips silently when double-entry is off (queue is expected to grow) or the
    RPC is absent (migration 287 not applied). Never raises.
    """
    # Lazy dual imports: a formatter hook rewrites this module's top-level
    # `except ImportError` list and strips additions (observed three times on
    # this branch), which would leave these names undefined under top-level
    # execution. Function-body imports are immune, and are already this
    # module's dominant idiom — see _check_entry_balance / _sum_financial_events.
    try:
        from services.ledger_service import double_entry_enabled  # type: ignore
    except ImportError:
        from ..services.ledger_service import double_entry_enabled  # type: ignore

    try:
        from db_supabase import rpc  # type: ignore
    except ImportError:
        from ..db_supabase import rpc  # type: ignore

    try:
        from utils.redis_client import redis_delete, redis_get, redis_set  # type: ignore
    except ImportError:
        from .redis_client import redis_delete, redis_get, redis_set  # type: ignore

    try:
        if not await double_entry_enabled():
            return
        rows = await rpc("financial_events_missing_legs", {"p_limit": _QUEUE_SAMPLE_LIMIT}) or []
    except Exception:
        logger.info("reconciliation: leg-completeness check unavailable (migration 287 not applied?)")
        return

    if not rows:
        await redis_delete(_QUEUE_HEAD_KEY)
        logger.info("reconciliation: double-entry leg projection current (work queue empty)")
        return

    # The RPC orders by created_at, so row 0 is the head of the drain.
    head = rows[0]
    head_id = str(head.get("id") or "")
    # Depth is capped by the RPC's own LIMIT, so report it as "at least".
    depth = f"{len(rows)}+" if len(rows) >= _QUEUE_SAMPLE_LIMIT else str(len(rows))

    previous = await redis_get(_QUEUE_HEAD_KEY)
    await redis_set(_QUEUE_HEAD_KEY, head_id, _QUEUE_HEAD_TTL_SECONDS)

    if previous is None:
        # First observation, or the marker expired / was lost with an
        # in-process Redis fallback across a restart. One run's blind spot;
        # hard loop death is separately covered by the lifespan watchdog
        # ("ledger_projection (15min)" in _WATCHDOG_LOOP_NAMES).
        logger.info(
            f"reconciliation: leg projection queue depth {depth}, head {head_id} "
            "— no prior marker, progress judged from the next run"
        )
        return

    if previous != head_id:
        logger.info(f"reconciliation: leg projection advancing (queue depth {depth}, head {previous} -> {head_id})")
        return

    logger.error(
        f"reconciliation ALERT: double-entry leg projection has made NO progress "
        f"in 24h — work-queue head still {head_id} (created {head.get('created_at')}), "
        f"queue depth {depth}. The loop is dead, wedged, or failing every leg write."
    )


async def _check_entry_balance(date) -> None:  # noqa: ANN001
    """Alert on any double-entry journal whose legs do not net to zero.

    An unbalanced entry means a leg builder produced a lopsided set or a partial
    leg write survived — an accounting defect, so it is logged at error even
    though no rider is affected. The expected result is always zero rows.

    Goes through the migration-293 RPC rather than filtering the
    financial_event_entries_unbalanced view (migration 286) directly. The view
    exposes ``MIN(created_at) AS created_at``, an AGGREGATE OUTPUT, and Postgres
    cannot push a predicate on one below the GROUP BY — so
    ``view WHERE created_at >= day`` re-aggregated the ENTIRE table every night
    just to discard everything outside the day (~20M rows within a year at the
    projected ~19k events/day × ~3 legs). The RPC applies the date bound inside
    the aggregate, where it can be pushed down onto migration 292's index.

    Never raises: this is an observability check bolted onto the end of the
    reconciliation run and must not mask the Stripe-vs-ledger result above. The
    function is absent until migration 293 is applied, which is not an error.
    """
    # Lazy dual imports — see _check_leg_completeness for why this module keeps
    # them in the function body rather than at module scope.
    try:
        from db_supabase import rpc  # type: ignore
    except ImportError:
        from ..db_supabase import rpc  # type: ignore

    day_start_dt = datetime(date.year, date.month, date.day, tzinfo=timezone.utc)
    day_start = day_start_dt.isoformat()
    day_end = (day_start_dt + timedelta(days=1)).isoformat()

    try:
        unbalanced = (
            await rpc(
                "financial_event_entries_unbalanced_between",
                {"p_start": day_start, "p_end": day_end},
            )
            or []
        )
    except Exception:
        logger.info(f"reconciliation: entry-balance check unavailable for {date} (migrations 286/292/293 not applied?)")
        return

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
