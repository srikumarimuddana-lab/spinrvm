"""
Presence sweeper — reconciles ``drivers.is_online`` with Redis presence.

The Redis presence keys (``spinr:presence:driver:<id>``) are the source of
truth for "is this driver's app actually reachable". But the DB column
``drivers.is_online`` is what survives Redis restarts, what analytics
queries, and what admin staff-managed tools flip. The two can drift:

  - Driver's app crashes → presence key expires after 90 s
    but ``is_online`` stays True until someone calls the PUT status API.
  - Driver force-quits mid-shift → same as above.
  - Railway replica restarts with stale in-process fallback state.

This loop runs every 60 s and flips ``is_online=False`` on any driver row
where the DB says online but Redis presence has been gone longer than
the grace window (~15 min). The long grace is deliberate: normal driving
(elevators, tunnels, carrier handoffs, parking garages, brief OS-level
app suspensions) routinely produces multi-minute presence gaps, and the
cost of falsely flipping a driver offline — missed ride offers, having
to re-tap Go Online and re-request permissions — is high. Drivers on an
active ride are excluded from sweeps entirely; the ride state machine
owns their status until the trip ends.

Dispatch already filters on live presence (see
``DispatchService.find_candidate_drivers``), so this sweeper is belt-and-
braces for admin dashboards, analytics, and offline reporting. Without
it, the admin "online drivers" count would stay correct live (presence
filter) but historical exports would overcount.

Replay safety
-------------
Multi-replica safe: every replica runs this loop independently, but each
sweep only writes ``is_online=False`` when the current DB value is True
— so concurrent sweeps on the same driver reduce to one no-op write.
No idempotency key is needed. If a driver's presence briefly disappears
(momentary Redis blip), the worst case is one false flip — the driver
app's next heartbeat or status ping will refresh presence and the driver
reappears on the next sweep within a minute.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from .. import db_supabase
    from ..socket_manager import manager
    from .driver_presence import present_driver_ids
except ImportError:  # pragma: no cover
    import db_supabase  # type: ignore
    from socket_manager import manager  # type: ignore
    from utils.driver_presence import present_driver_ids  # type: ignore

logger = logging.getLogger(__name__)

# Sweep interval. Shorter than PRESENCE_TTL so ghosts rarely linger more
# than one interval past their TTL.
SWEEP_INTERVAL_SECONDS = 60


async def _sweep_once() -> int:
    """Run one reconciliation pass. Returns count of drivers flipped offline."""
    try:
        online_drivers = await db_supabase.get_rows(
            "drivers",
            {"is_online": True},
            limit=1000,
        )
    except Exception as exc:
        logger.error(f"[presence_sweeper] DB read failed, skipping tick: {exc}", exc_info=True)
        return 0

    if not online_drivers:
        return 0

    # Grace period — a driver who just flipped online needs time for the WS
    # to connect and mark_present to land, AND normal network blips
    # (elevator, tunnel, carrier handoff) shouldn't produce phantom
    # offline flips. Uber/Lyft are deliberately slow to flip a driver
    # offline on presence loss — minutes, not seconds — because the cost
    # of a false flip (driver pulls over, taps Go Online again, misses
    # trips in the interim) is high. 15 minutes is the Uber-ish default.
    now = datetime.now(timezone.utc)
    grace_seconds = 15 * 60

    def _within_grace(d: dict) -> bool:
        ts_str = d.get("last_status_changed_at")
        if not ts_str:
            return False
        try:
            ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return (now - ts).total_seconds() < grace_seconds
        except Exception:
            return False

    eligible = [d for d in online_drivers if not _within_grace(d)]
    if not eligible:
        return 0

    # Never flip a driver offline while they're on an active ride. A driver
    # completing a trip in a dead-zone may have no presence heartbeat for
    # many minutes; yanking is_online=False mid-trip would break dispatch
    # ETA calculation, admin monitoring, and post-trip settlement. The
    # ride lifecycle drives the status transition in that case.
    ACTIVE_RIDE_STATUSES = [
        "driver_assigned",
        "driver_accepted",
        "driver_arrived",
        "in_progress",
    ]
    active_driver_ids: set = set()
    try:
        active_rides = await db_supabase.get_rows(
            "rides",
            {"status": {"$in": ACTIVE_RIDE_STATUSES}},
            limit=1000,
        )
        active_driver_ids = {r["driver_id"] for r in active_rides if r.get("driver_id")}
    except Exception as exc:
        # Fail safe: if we can't confirm active-ride state, skip the
        # sweep rather than risk flipping a driver mid-trip.
        logger.error(f"[presence_sweeper] active-ride lookup failed, skipping tick: {exc}", exc_info=True)
        return 0

    eligible = [d for d in eligible if d["id"] not in active_driver_ids]
    if not eligible:
        return 0

    ids = [d["id"] for d in eligible if d.get("id")]
    try:
        present = await present_driver_ids(ids)
    except Exception as exc:
        # Redis outage — don't sweep everyone offline because we can't
        # tell who's present. Dispatch's own fallback handles routing
        # during the outage.
        logger.error(f"[presence_sweeper] presence lookup failed, skipping tick: {exc}", exc_info=True)
        return 0

    ghosts = [d for d in eligible if d["id"] not in present]
    if not ghosts:
        return 0

    now_iso = now.isoformat()
    flipped = 0
    for d in ghosts:
        try:
            await db_supabase.update_one(
                "drivers",
                {"id": d["id"], "is_online": True},
                {
                    "is_online": False,
                    "is_available": False,
                    "updated_at": now_iso,
                    # Bump last_status_changed_at so admin "offline since …"
                    # reflects the sweep time, not the last vehicle-edit.
                    "last_status_changed_at": now_iso,
                },
            )
            flipped += 1
            # Audit trail: record the system-initiated flip so operators
            # investigating "why is this driver offline" can see that the
            # app stopped heartbeating, not that the driver tapped off.
            try:
                await db_supabase.insert_one(
                    "driver_activity_log",
                    {
                        "id": str(uuid.uuid4()),
                        "driver_id": d["id"],
                        "event_type": "went_offline",
                        "title": "Went offline (presence timeout)",
                        "description": "System flipped driver offline — app heartbeat stopped within the presence TTL window.",
                        "metadata": {"reason": "presence_timeout", "source": "presence_sweeper"},
                        "actor": "system",
                        "created_at": now_iso,
                    },
                )
            except Exception as _log_exc:  # pragma: no cover - best effort
                logger.error(f"[presence_sweeper] activity log insert failed for {d['id']}: {_log_exc}", exc_info=True)
            # Notify admin live-monitoring clients so the badge updates
            # without a page reload.
            try:
                await manager.broadcast_to_admins(
                    {
                        "type": "driver_status_changed",
                        "driver_id": d["id"],
                        "is_online": False,
                    }
                )
            except Exception:  # pragma: no cover - best effort  # noqa: S110
                pass
        except Exception as exc:
            logger.error(f"[presence_sweeper] flip failed for {d['id']}: {exc}", exc_info=True)

    if flipped:
        logger.info(f"[presence_sweeper] flipped {flipped} ghost-online driver(s) offline")
    return flipped


async def presence_sweeper_loop() -> None:
    """Background loop: reconcile presence → DB every SWEEP_INTERVAL_SECONDS."""
    # Small initial jitter so replicas don't all sweep on the same tick.
    import random

    await asyncio.sleep(random.uniform(0, SWEEP_INTERVAL_SECONDS))
    while True:
        try:
            await _sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"[presence_sweeper] tick failed: {exc}", exc_info=True)
        _record_heartbeat("presence_sweeper (60s)")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
