"""PII retention purge — daily background loop (B-P1-6).

Calls the SECURITY DEFINER Postgres functions `purge_pii_retention()` and
`purge_trip_route_geometry()` once per day at ~03:00 UTC. The SQL functions
are naturally idempotent (anonymization gated by durable markers, deletes
filter on a moving time cutoff), so running on every replica is safe.
The Redis leader lock is belt-and-braces: it cuts the noise in the
audit_logs table (one row per day instead of N replicas worth) without
being a correctness requirement.

What this enforces:
    - rides.pickup/dropoff GPS scrubbed at 3 years (regulatory ceiling)
    - rides hard-deleted at 7 years (regulatory ceiling + PIPEDA)
    - DSAR-deleted accounts HARD-DELETED at 7 years, with their financial_events
      + driver footprint — NO earlier anonymization; records stay attributable
      until then (Uber/Lyft model, migration 216). Was: anonymize at 30 days.
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
import random
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

    def _call_trip_route_geometry() -> Any:
        return supabase.rpc("purge_trip_route_geometry", {"p_dry_run": dry_run}).execute()

    try:
        route_result = await run_sync(_call_trip_route_geometry)
    except Exception:
        logger.exception("retention_purge: rpc(purge_trip_route_geometry) failed")
        raise

    route_data = getattr(route_result, "data", None)
    if route_data is None and isinstance(route_result, dict):
        route_data = route_result.get("data")
    if not isinstance(route_data, dict):
        logger.error(
            "retention_purge: unexpected trip-route geometry response shape: %r",
            type(route_data).__name__,
        )
        raise RuntimeError("purge_trip_route_geometry returned an invalid response")

    skipped_fk = data.get("dsar_users_skipped_fk") or 0
    logger.info(
        "retention_purge complete dry_run=%s rides_anon=%s rides_del=%s "
        "loc_del=%s msgs_del=%s tokens_del=%s stripe_del=%s dsar_users=%s "
        "dsar_skipped_fk=%s",
        data.get("dry_run"),
        data.get("rides_anonymized"),
        data.get("rides_deleted"),
        data.get("driver_location_deleted"),
        data.get("ride_messages_deleted"),
        data.get("refresh_tokens_deleted"),
        data.get("stripe_events_deleted"),
        data.get("dsar_users_purged"),
        skipped_fk,
    )
    logger.info(
        "trip_route_geometry_purge complete dry_run=%s routes_anon=%s gap_events_del=%s",
        route_data.get("dry_run"),
        route_data.get("ride_routes_anonymized"),
        route_data.get("ride_location_gap_events_deleted"),
    )
    if skipped_fk:
        # A DSAR account past its 7y window could not be hard-deleted because an
        # unhandled RESTRICT FK still references it (Step H caught the violation
        # and left the tombstone). That is a retention gap that must be seen, not
        # buried in the Postgres log — surface it loudly for Compliance to fix by
        # adding the offending table to Step H.
        logger.error(
            "retention_purge: %s DSAR account(s) skipped on a residual FK — "
            "hard-delete blocked; add the offending table to purge_pii_retention Step H",
            skipped_fk,
        )
    # Preserve the established wrapper response contract for callers that use
    # the PII-purge counters. Route-geometry counts are logged independently.
    return data


INTERVAL_SECONDS = 86400  # 24 h nominal; actual sleep includes ±10% jitter


def _metric_gauge(name: str, value: float, labels: dict) -> None:
    pass  # stub — wire to real metrics system when Prometheus/OpenMetrics lands


def _metric_inc(name: str, labels: dict | None = None) -> None:
    pass  # stub


def _seconds_until_next(target_hour_utc: int) -> float:
    """How long to sleep until the next 03:00 UTC (or whatever hour is
    requested). Always returns a positive value."""
    now = datetime.now(timezone.utc)
    target = datetime.combine(now.date(), time(target_hour_utc, 0), tzinfo=timezone.utc)
    if target <= now:
        target = target + timedelta(days=1)
    return (target - now).total_seconds()


async def _tick() -> None:
    """One purge iteration: acquire the replica lock then call run_retention_purge_tick."""
    acquired = await redis_set_nx(_LOCK_KEY, _pod_id(), _LOCK_TTL_SECONDS)
    if acquired:
        await run_retention_purge_tick(dry_run=False)
    else:
        logger.info("retention_purge_loop: another replica holds the lock, skipping")


async def retention_purge_loop() -> None:
    """Daily retention-purge loop with ±10% jitter sleep.

    Runs every ~24 h (INTERVAL_SECONDS ± 10%). Idempotent across replicas via
    the Redis SET NX EX leader lock inside _tick.
    """
    while True:
        t0 = asyncio.get_event_loop().time()
        try:
            await _tick()
            _metric_gauge(
                "spinr_bgloop_duration_ms",
                (asyncio.get_event_loop().time() - t0) * 1000,
                {"loop": "retention_purge"},
            )
        except Exception:
            logger.exception("retention_purge_loop: tick raised")
            _metric_inc("spinr_bgloop_errors_total", {"loop": "retention_purge"})
        _record_heartbeat("retention_purge (24h)")
        await asyncio.sleep(INTERVAL_SECONDS * (0.9 + random.random() * 0.2))
