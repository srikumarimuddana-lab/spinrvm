"""Orphaned card-hold reconciler.

Finds rides that are finished but still carry an open booking-time authorization, and
releases the rider's money.

Two distinct populations, which is why this is a recurring loop and not a one-off
script:

  1. **The historical backlog.** Until T2b, ``utils/stuck_ride_sweeper`` auto-cancelled
     no-drivers-found rides without releasing the hold placed at booking. Every ride
     it swept left ``grand_total + RIDE_AUTH_BUFFER_CAD`` reserved on the rider's card
     until Stripe expired the authorization (~7 days). Those rows are still in the
     table with ``status='cancelled'`` and ``auth_status`` open. Fixing the sweeper
     stopped new ones; it returned nothing to riders already affected.

  2. **Ongoing failures.** ``release_open_hold`` can return ``failed`` — Stripe was
     down, the API call timed out, the pod died between the cancel and the
     bookkeeping write. Neither the sweeper nor the interactive cancel path retries.
     Without this loop, a single transient Stripe error converts a 5-minute
     inconvenience into a 7-day hold on a real person's card, permanently and
     silently.

Deliberately conservative about what it will touch. It only considers rides in a
**terminal, non-billable** state (``cancelled``), so it can never race settlement for
a ride that is still going to be charged. Completed rides with open holds are
``utils/preauth_capture``'s job (it *captures* them — the money is owed), and stealing
that work would refund fares the driver has earned. See ``_TERMINAL_STATES``.

Replay-safety contract (CLAUDE.md, Background loops) — runs on every replica:
  1. **Redis leader lock** (``SET NX``) — a best-effort throttle so replicas do not
     all scan at once. NOT the safety net: ``utils/redis_client`` falls back to an
     in-process dict when ``REDIS_URL`` is unset, in which case every replica runs.
  2. **Atomic DB claim** — a compare-and-swap on ``updated_at``, asserting
     ``auth_status`` is still open. Only the replica whose UPDATE returns a row acts.
     This is the real guard and holds with Redis down. ``updated_at`` is used as the
     version column because migration 156 pins ``auth_status`` to four values with a
     CHECK constraint, so there is no intermediate "claiming" state to flip to
     without a schema change.
  3. **Stripe idempotency key** on the cancel (``ride-cancelauth-{ride}-{pi}``) — even
     if two replicas both won a claim race, Stripe dedupes.

A crash between claim and release is safe: ``auth_status`` is still open, so the next
tick re-finds the ride and retries. Nothing is permanently lost by dying mid-flight.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import socket
from datetime import datetime, timezone

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from .. import db_supabase as db
    from .card_hold_release import OPEN_AUTH_STATES, RELEASED, RELEASED_UNMARKED, has_open_hold, release_open_hold
    from .metrics import inc as _metric_inc
    from .redis_client import redis_set_nx
except ImportError:  # pragma: no cover - dual import
    import db_supabase as db  # type: ignore
    from utils.card_hold_release import (  # type: ignore
        OPEN_AUTH_STATES,
        RELEASED,
        RELEASED_UNMARKED,
        has_open_hold,
        release_open_hold,
    )
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.redis_client import redis_set_nx  # type: ignore

logger = logging.getLogger(__name__)

_LOOP_NAME = "orphaned_hold_reconciler (15m)"
RECONCILE_INTERVAL_SECONDS = 15 * 60

# Ride states that are terminal AND non-billable, so an open hold on one is
# unambiguously orphaned money.
#
# 'completed' is deliberately excluded: a completed ride's open hold is owed to the
# driver and belongs to utils/preauth_capture, which CAPTURES it. Releasing those
# would refund fares on rides that actually happened — on a 0%-commission product the
# driver eats that loss directly.
_TERMINAL_STATES = ("cancelled",)

# Bounded batch so a large backlog cannot make one tick run for minutes holding the
# leader lock. The backlog drains over successive ticks.
_BATCH_LIMIT = 50


async def find_orphaned_holds(limit: int = _BATCH_LIMIT) -> list[dict]:
    """Rides in a terminal non-billable state that still hold an open authorization.

    Covered by ``idx_rides_open_authorization`` (partial index on the open
    ``auth_status`` values from migration 156), so this stays cheap on a hot table.
    """
    rides = await db.get_rows(
        "rides",
        {
            "status": {"$in": list(_TERMINAL_STATES)},
            "auth_status": {"$in": list(OPEN_AUTH_STATES)},
        },
        limit=limit,
        order="updated_at",
        columns="id,rider_id,status,auth_status,payment_intent_id,authorized_amount,grand_total,updated_at",
    )
    # payment_intent_id IS NOT NULL is filtered here rather than in the query: the
    # Mongo-shaped filter layer compiles to PostgREST and a null-check predicate is
    # not expressible across both the $or and non-$or paths (see CLAUDE.md, "Query
    # filters"). Cheap to do in Python on a bounded batch.
    return [r for r in (rides or []) if has_open_hold(r)]


async def _claim(ride: dict) -> bool:
    """Compare-and-swap on ``updated_at`` so exactly one replica releases this hold.

    Returns True if this replica won. See the module docstring for why ``updated_at``
    is the version column rather than an intermediate ``auth_status`` value.
    """
    observed = ride.get("updated_at")
    if not observed:
        # No version to compare against. Skip rather than release unguarded — a
        # duplicate release is harmless via Stripe idempotency, but an unbounded
        # claim-free path invites one replica to re-release the same rows every tick.
        logger.warning(f"[orphaned_hold_reconciler] ride {ride.get('id')} has no updated_at — skipping claim")
        return False
    claimed = await db.update_one(
        "rides",
        {"id": ride.get("id"), "auth_status": {"$in": list(OPEN_AUTH_STATES)}, "updated_at": observed},
        {"updated_at": datetime.now(timezone.utc).isoformat()},
    )
    return claimed is not None


async def reconcile_tick(*, dry_run: bool = False, limit: int = _BATCH_LIMIT) -> dict:
    """One reconciliation pass. Returns a summary suitable for logging or a CLI.

    ``dry_run=True`` finds and reports without claiming, cancelling, or writing —
    which is how an operator sizes the backlog before touching real money.
    """
    summary: dict = {
        "found": 0,
        "released": 0,
        # RELEASED_UNMARKED is tracked separately from `failed` on purpose. It means
        # the Stripe cancel SUCCEEDED (money is freed) but the auth_status bookkeeping
        # write did not land. Folding it into `failed` would make the operator CLI
        # report freed money as "may still be held — investigate" and exit non-zero,
        # which is exactly the confusion card_hold_release's distinct outcomes exist
        # to prevent. No rider impact; the 15-min loop re-marks it when the DB recovers.
        "released_unmarked": 0,
        "skipped": 0,
        "not_claimed": 0,
        "failed": 0,
        "dry_run": dry_run,
    }

    try:
        orphans = await find_orphaned_holds(limit=limit)
    except Exception as exc:
        logger.error(f"[orphaned_hold_reconciler] candidate query failed: {exc}", exc_info=True)
        summary["error"] = str(exc)
        return summary

    summary["found"] = len(orphans)
    if not orphans:
        return summary

    if dry_run:
        # Log IDs and amounts only — never rider identity. PIPEDA: no names, phones,
        # emails or coordinates in logs.
        for r in orphans:
            logger.info(
                f"[orphaned_hold_reconciler] DRY RUN would release ride_id={r.get('id')} "
                f"auth_status={r.get('auth_status')} authorized_amount={r.get('authorized_amount')}"
            )
        summary["skipped"] = len(orphans)
        return summary

    logger.warning(f"[orphaned_hold_reconciler] releasing {len(orphans)} orphaned card hold(s)")

    for ride in orphans:
        try:
            if not await _claim(ride):
                summary["not_claimed"] += 1
                continue
            outcome = await release_open_hold(ride, source="reconciler")
            if outcome == RELEASED:
                summary["released"] += 1
            elif outcome == RELEASED_UNMARKED:
                # Money freed, bookkeeping write deferred to the next tick. Not a failure.
                summary["released_unmarked"] += 1
            else:
                # FAILED (Stripe cancel returned False → money may still be held) or
                # ERROR (unexpected). Both warrant operator attention.
                summary["failed"] += 1
        except Exception as exc:
            # One rider's failure must not strand the next rider's funds.
            logger.error(
                f"[orphaned_hold_reconciler] release failed for ride {ride.get('id')}: {exc}",
                exc_info=True,
            )
            summary["failed"] += 1

    if summary["released"]:
        _metric_inc("spinr_orphaned_hold_reconciled_total", {"outcome": "released"}, by=summary["released"])
    if summary["released_unmarked"]:
        _metric_inc(
            "spinr_orphaned_hold_reconciled_total",
            {"outcome": "released_unmarked"},
            by=summary["released_unmarked"],
        )
    if summary["failed"]:
        _metric_inc("spinr_orphaned_hold_reconciled_total", {"outcome": "failed"}, by=summary["failed"])
    return summary


def _pod_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


async def orphaned_hold_reconciler_loop() -> None:
    """Background loop: release orphaned card holds every 15 minutes."""
    logger.info(f"Orphaned card-hold reconciler started (interval={RECONCILE_INTERVAL_SECONDS}s)")
    # Stagger against the other startup loops so a cold start does not fire 17 scans
    # in the same second.
    await asyncio.sleep(random.uniform(0, 60))
    while True:
        # TTL must be SHORTER than the minimum possible sleep below (interval *
        # 0.9, worst-case jitter), or the pod that ran the last tick wakes to
        # find its OWN key still alive, fails SET NX, and sleeps another full
        # interval — halving the documented cadence. The old `interval * 2`
        # ("outlives a slow tick") doesn't (2x > 0.9x) — the atomic DB claim is
        # still the real correctness guard if a second replica races in; this
        # lock is only ever a throttle. Matches ledger_projection.py's
        # `_LOCK_TTL_SECONDS` formula (ACTION_ITEMS B21): 0.05 headroom under
        # the 0.9 floor.
        lock_ttl = int(RECONCILE_INTERVAL_SECONDS * 0.85)
        if not await redis_set_nx("spinr:orphaned_hold:reconcile:lock", _pod_id(), lock_ttl):
            _record_heartbeat(_LOOP_NAME)
            await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)
            continue
        try:
            summary = await reconcile_tick()
            if summary.get("found"):
                logger.info(f"[orphaned_hold_reconciler] tick summary: {summary}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"[orphaned_hold_reconciler] tick failed: {exc}", exc_info=True)
            _metric_inc("spinr_bgloop_errors_total", {"loop": "orphaned_hold_reconciler"})
        _record_heartbeat(_LOOP_NAME)
        delta = RECONCILE_INTERVAL_SECONDS * 0.1
        await asyncio.sleep(RECONCILE_INTERVAL_SECONDS + random.uniform(-delta, delta))
