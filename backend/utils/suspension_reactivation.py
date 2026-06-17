"""Auto-reactivation of expired temporary suspensions.

A temporary rider suspension stores ``suspended_until``. The booking gate
(routes/rides.py) already lets the rider book once that time passes, but the
stored ``status`` stays 'suspended' until something flips it — so the admin
Users list shows a stale "Suspended" badge. This loop closes that gap: it sets
status back to 'active' (clearing the moderation context) once suspended_until
has elapsed.

Replay-safety (CLAUDE.md, Background loops):
  - Redis leader lock (best-effort throttle across replicas).
  - The flip is an ATOMIC conditional update filtered on status='suspended', so
    if another replica already reactivated the row the update matches 0 rows —
    no double-write, no double audit (we only audit when the update sticks).
  - Indefinite suspensions (suspended_until IS NULL) never match the query and
    are left untouched — only an admin reactivates those.
"""

import asyncio
import logging
import os
import random
import socket
import uuid
from datetime import datetime, timezone

try:
    from ..db import db
    from .loop_monitor import record_heartbeat as _record_heartbeat
    from .redis_client import redis_set_nx
except ImportError:
    from db import db  # type: ignore
    from utils.redis_client import redis_set_nx  # type: ignore

    try:
        from utils.loop_monitor import record_heartbeat as _record_heartbeat  # type: ignore
    except ImportError:  # pragma: no cover

        def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
            pass


logger = logging.getLogger(__name__)

REACTIVATION_INTERVAL_SECONDS = 600  # 10 min — not time-critical (booking gate already lets them book)
_LOOP_NAME = "suspension_reactivation (10min)"


def _pod_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def _reactivate_tick() -> None:
    """Flip expired temporary suspensions back to active."""
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    try:
        # suspended_until <= now naturally excludes indefinite (NULL) suspensions.
        candidates = await db.get_rows(
            "users",
            {"status": "suspended", "suspended_until": {"$lte": now_iso}},
            limit=500,
            columns="id,suspended_until",
        )
    except Exception:
        logger.error("[suspension-reactivation] failed to fetch candidates", exc_info=True, extra={"domain": "admin"})
        return

    for u in candidates or []:
        uid = u.get("id")
        if not uid:
            continue
        try:
            # Atomic + idempotent: only flips if still 'suspended' (another
            # replica racing this loses the filter and writes nothing).
            updated = await db.update_one(
                "users",
                {"id": uid, "status": "suspended"},
                {
                    "status": "active",
                    "status_reason": None,
                    "suspended_until": None,
                    "status_changed_at": now_iso,
                    "status_changed_by": "system:auto_reactivation",
                    "updated_at": now_iso,
                },
            )
        except Exception:
            logger.error(
                "[suspension-reactivation] reactivate failed", exc_info=True, extra={"domain": "admin", "user_id": uid}
            )
            continue

        # Only audit when our update actually took effect (some db wrappers
        # return the row/list on success and None/[] when the filter matched
        # nothing). Treat a falsy result as "another replica won" and skip.
        if not updated:
            continue
        try:
            await db.insert_one(
                "audit_logs",
                {
                    "id": str(uuid.uuid4()),
                    "actor_id": "system",
                    "actor_role": "system",
                    "action": "user_status_change",
                    "entity_type": "user",
                    "entity_id": uid,
                    "details": {
                        "old_status": "suspended",
                        "new_status": "active",
                        "reason": "temporary suspension expired",
                        "suspended_until": u.get("suspended_until"),
                    },
                    "created_at": now_iso,
                },
            )
        except Exception:
            logger.warning(
                "[suspension-reactivation] audit insert failed", exc_info=True, extra={"domain": "admin", "user_id": uid}
            )


async def suspension_reactivation_loop() -> None:
    """Background loop: reactivate expired temporary suspensions every interval."""
    logger.info(f"Suspension reactivation loop started (interval={REACTIVATION_INTERVAL_SECONDS}s)")
    while True:
        lock_ttl = int(REACTIVATION_INTERVAL_SECONDS * 2)
        if not await redis_set_nx("spinr:users:suspension_reactivation:lock", _pod_id(), lock_ttl):
            _record_heartbeat(_LOOP_NAME)
            await asyncio.sleep(REACTIVATION_INTERVAL_SECONDS)
            continue
        try:
            await _reactivate_tick()
        except Exception:
            logger.error("Suspension reactivation loop error", exc_info=True)
        _record_heartbeat(_LOOP_NAME)
        delta = REACTIVATION_INTERVAL_SECONDS * 0.1
        await asyncio.sleep(REACTIVATION_INTERVAL_SECONDS + random.uniform(-delta, delta))
