"""PII retention purge — daily background loop (B-P1-6).

Calls the SECURITY DEFINER Postgres function `purge_pii_retention()`
(migration 50) once per day at ~03:00 UTC. The SQL function is naturally
idempotent (anonymization gated on `gps_anonymized_at IS NULL`, deletes
filter on a moving time cutoff), so running on every replica is safe.
The Redis leader lock is belt-and-braces: it cuts the noise in the
audit_logs table (one row per day instead of N replicas worth) without
being a correctness requirement.

What this enforces:
    - rides.pickup/dropoff GPS scrubbed at 3 years (regulatory ceiling)
    - rides hard-deleted at 7 years (regulatory ceiling + PIPEDA)
    - driver_location_history hard-deleted at 90 days
    - ride_messages hard-deleted at 90 days
    - refresh_tokens hard-deleted at expires_at + 30 days grace
    - stripe_events hard-deleted at 90 days

See docs/runbooks/data-retention.md for the policy + manual-run procedure.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from datetime import datetime, time, timedelta, timezone
from typing import Any, Optional

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from ..db_supabase import run_sync, supabase  # type: ignore
    from .redis_client import redis_set_nx  # type: ignore
except ImportError:
    from db_supabase import run_sync, supabase  # type: ignore
    from utils.redis_client import redis_set_nx  # type: ignore


logger = logging.getLogger(__name__)

# 23 hours — leaves a 1h window between the lock expiring and the next
# scheduled tick, so a stuck pod can't hold the lock forever.
_LOCK_TTL_SECONDS = 23 * 60 * 60
_LOCK_KEY = "spinr:retention:purge:lock"


def _pod_id() -> str:
    """Stable-ish identifier for the current replica, written into the
    leader-lock value so a debug session can see who held it."""
    return f"{socket.gethostname()}:{os.getpid()}"


async def run_retention_purge_tick(dry_run: bool = False) -> Optional[dict]:
    """Invoke purge_pii_retention(p_dry_run) once. Returns the JSONB
    result dict, or None if the DB client is not configured (dev/test
    without SUPABASE_URL).

    Errors are logged with full context and re-raised so the caller's
    loop wrapper records them; we never swallow DB errors silently
    (CLAUDE.md: "Do not silently swallow errors").
    """
    if not supabase:
        logger.info("retention_purge: supabase client not configured, skipping")
        return None

    def _call() -> Any:
        # rpc().execute() returns APIResponse(data=<jsonb>) for a JSONB-
        # returning function; .data is the dict directly, not a list.
        return supabase.rpc("purge_pii_retention", {"p_dry_run": dry_run}).execute()

    try:
        res = await run_sync(_call)
    except Exception:
        logger.exception("retention_purge: rpc(purge_pii_retention) failed")
        raise

    data = getattr(res, "data", None)
    if data is None and isinstance(res, dict):
        data = res.get("data")

    if not isinstance(data, dict):
        logger.error(
            "retention_purge: unexpected rpc response shape: %r",
            type(data).__name__,
        )
        return None

    logger.info(
        "retention_purge complete dry_run=%s rides_anon=%s rides_del=%s "
        "loc_del=%s msgs_del=%s tokens_del=%s stripe_del=%s dsar_users=%s",
        data.get("dry_run"),
        data.get("rides_anonymized"),
        data.get("rides_deleted"),
        data.get("driver_location_deleted"),
        data.get("ride_messages_deleted"),
        data.get("refresh_tokens_deleted"),
        data.get("stripe_events_deleted"),
        data.get("dsar_users_purged"),
    )
    return data


def _seconds_until_next(target_hour_utc: int) -> float:
    """How long to sleep until the next 03:00 UTC (or whatever hour is
    requested). Always returns a positive value."""
    now = datetime.now(timezone.utc)
    target = datetime.combine(now.date(), time(target_hour_utc, 0), tzinfo=timezone.utc)
    if target <= now:
        target = target + timedelta(days=1)
    return (target - now).total_seconds()


async def retention_purge_loop(
    interval_seconds: int = 86400,
    target_hour_utc: int = 3,
) -> None:
    """Daily retention-purge loop. Sleeps until the next `target_hour_utc`
    on first iteration, then runs every `interval_seconds` (24h).

    Idempotent across replicas via Redis SET NX EX. If a different replica
    already holds the lock for today's window, this replica skips silently;
    it does not retry until the next interval.
    """
    first_sleep = _seconds_until_next(target_hour_utc)
    logger.info(
        "retention_purge_loop: first run in %.0fs (target %02d:00 UTC)",
        first_sleep,
        target_hour_utc,
    )
    await asyncio.sleep(first_sleep)

    while True:
        try:
            acquired = await redis_set_nx(_LOCK_KEY, _pod_id(), _LOCK_TTL_SECONDS)
            if acquired:
                await run_retention_purge_tick(dry_run=False)
            else:
                logger.info("retention_purge_loop: another replica holds the lock, skipping")
        except Exception:
            logger.exception("retention_purge_loop: tick raised")
        _record_heartbeat("retention_purge (24h)")
        await asyncio.sleep(interval_seconds)
