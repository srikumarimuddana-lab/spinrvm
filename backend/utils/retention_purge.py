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
    from ..db_supabase import delete_many, run_sync, supabase  # type: ignore
    from .redis_client import redis_set_nx  # type: ignore
except ImportError:
    from db_supabase import delete_many, run_sync, supabase  # type: ignore
    from utils.redis_client import redis_set_nx  # type: ignore


logger = logging.getLogger(__name__)

# 23 hours — leaves a 1h window between the lock expiring and the next
# scheduled tick, so a stuck pod can't hold the lock forever.
_LOCK_TTL_SECONDS = 23 * 60 * 60
_LOCK_KEY = "spinr:retention:purge:lock"
_PRIVATE_ROUTE_SNAPSHOT_BUCKET = "ride-route-snapshots"
_ROUTE_SNAPSHOT_PURGE_BATCH_SIZE = 100


def _pod_id() -> str:
    """Stable-ish identifier for the current replica, written into the
    leader-lock value so a debug session can see who held it."""
    return f"{socket.gethostname()}:{os.getpid()}"


async def _delete_expired_route_snapshot_objects() -> int:
    """Delete every private route image whose durable retention deadline passed.

    The append-only ledger retains every revision's object path, even after a
    late GPS batch clears the current route projection. If Storage removal
    fails, ``deleted_at`` remains NULL and the next daily run retries it.
    """
    if not supabase:
        raise RuntimeError("Supabase client not configured")

    def _pending_rows() -> list[dict]:
        response = (
            supabase.table("ride_route_snapshot_objects")
            .select("ride_id,storage_bucket,object_path")
            .is_("deleted_at", "null")
            .lte("retention_due_at", datetime.now(timezone.utc).isoformat())
            .limit(_ROUTE_SNAPSHOT_PURGE_BATCH_SIZE)
            .execute()
        )
        rows = getattr(response, "data", None)
        if not isinstance(rows, list):
            raise RuntimeError("route snapshot ledger query returned an invalid response")
        return [
            row
            for row in rows
            if isinstance(row, dict)
            and isinstance(row.get("storage_bucket"), str)
            and isinstance(row.get("object_path"), str)
            and row.get("storage_bucket") == _PRIVATE_ROUTE_SNAPSHOT_BUCKET
        ]

    try:
        pending = await run_sync(_pending_rows)
    except Exception:
        logger.exception("retention_purge: route snapshot ledger query failed")
        raise
    if not pending:
        return 0

    paths = [str(row["object_path"]) for row in pending]
    try:
        await run_sync(lambda: supabase.storage.from_(_PRIVATE_ROUTE_SNAPSHOT_BUCKET).remove(paths))
    except Exception:
        logger.exception("retention_purge: private route snapshot storage deletion failed")
        raise

    for row in pending:
        try:
            await run_sync(
                lambda row=row: (
                    supabase.table("ride_route_snapshot_objects")
                    .update({"deleted_at": datetime.now(timezone.utc).isoformat()})
                    .eq("storage_bucket", row["storage_bucket"])
                    .eq("object_path", row["object_path"])
                    .execute()
                )
            )
        except Exception:
            logger.exception("retention_purge: route snapshot ledger acknowledgement failed")
            raise
        if row.get("ride_id"):
            try:
                await run_sync(
                    lambda row=row: (
                        supabase.table("ride_routes")
                        .update({"snapshot_object_path": None, "snapshot_purge_pending_at": None})
                        .eq("ride_id", row["ride_id"])
                        .eq("snapshot_object_path", row["object_path"])
                        .execute()
                    )
                )
            except Exception:
                logger.exception("retention_purge: current route snapshot reference clear failed")
                raise
    return len(pending)


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
        # Raise, matching purge_trip_route_geometry's malformed-response
        # handling below and CLAUDE.md's "do not silently swallow errors"
        # rule — this is the daily regulatory PII/DSAR purge; a silent
        # `return None` here previously let it stop running indefinitely
        # (e.g. after a PostgREST/Supabase client upgrade changed the RPC
        # envelope shape) with only a single ERROR log line, no alarm, no
        # metric, and the calling loop seeing a normal completion.
        raise RuntimeError("purge_pii_retention returned an invalid response")

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

    deleted_snapshot_objects = 0
    if not dry_run:
        deleted_snapshot_objects = await _delete_expired_route_snapshot_objects()
        if deleted_snapshot_objects:
            # Mark rows anonymous only after their actual Storage objects were
            # deleted and their durable paths cleared above.
            try:
                route_result = await run_sync(_call_trip_route_geometry)
            except Exception:
                logger.exception("retention_purge: post-storage trip-route geometry purge failed")
                raise
            route_data = getattr(route_result, "data", None)
            if route_data is None and isinstance(route_result, dict):
                route_data = route_result.get("data")
            if not isinstance(route_data, dict):
                logger.error(
                    "retention_purge: unexpected post-storage trip-route geometry response shape: %r",
                    type(route_data).__name__,
                )
                raise RuntimeError("post-storage purge_trip_route_geometry returned an invalid response")

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
        "trip_route_geometry_purge complete dry_run=%s routes_anon=%s snapshots_deleted=%s gap_events_del=%s",
        route_data.get("dry_run"),
        route_data.get("ride_routes_anonymized"),
        deleted_snapshot_objects,
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


# 90 days — same window purge_pii_retention()'s blanket driver_location_history
# delete enforces. The idle step below only acts when the operator configures a
# TIGHTER window than this via settings.idle_breadcrumb_retention_hours.
_IDLE_RETENTION_DEFAULT_HOURS = 2160


async def _purge_idle_breadcrumbs_below_default() -> None:
    """Enforce a tighter-than-default retention for Period-1 idle breadcrumbs.

    purge_pii_retention() already hard-deletes ALL driver_location_history rows
    at 90 days. This supplementary step only matters when
    settings.idle_breadcrumb_retention_hours is set BELOW 2160: online_idle
    rows (Period-1 roaming, no ride attached) then drop on the tighter
    schedule while ride-attached rows keep the full 90-day window. Best-effort:
    a failure here must not mask the main purge, so it logs and returns.
    """
    try:
        try:
            from ..settings_loader import get_app_settings  # type: ignore
        except ImportError:
            from settings_loader import get_app_settings  # type: ignore
        raw = ((await get_app_settings()) or {}).get("idle_breadcrumb_retention_hours", _IDLE_RETENTION_DEFAULT_HOURS)
        hours = int(raw)
    except Exception:
        logger.error(
            "retention_purge: idle retention setting read failed; skipping idle step",
            exc_info=True,
        )
        return
    if hours >= _IDLE_RETENTION_DEFAULT_HOURS:
        return  # blanket 90-day delete already covers (or is tighter than) this
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, hours))).isoformat()
    try:
        rows = await delete_many(
            "driver_location_history",
            {"timestamp": {"$lt": cutoff}, "tracking_phase": "online_idle"},
        )
        logger.info(
            "retention_purge: idle breadcrumbs purged rows=%s retention_hours=%s",
            len(rows) if isinstance(rows, list) else 0,
            hours,
        )
    except Exception:
        logger.exception("retention_purge: idle breadcrumb purge failed")


async def _tick() -> None:
    """One purge iteration: acquire the replica lock then call run_retention_purge_tick."""
    acquired = await redis_set_nx(_LOCK_KEY, _pod_id(), _LOCK_TTL_SECONDS)
    if acquired:
        await run_retention_purge_tick(dry_run=False)
        await _purge_idle_breadcrumbs_below_default()
    else:
        logger.info("retention_purge_loop: another replica holds the lock, skipping")


def _escalate_tick_failure(exc: BaseException) -> None:
    """CRITICAL log + Sentry `fatal` capture on a failed tick, mirroring
    ``retention_guard_monitor.py``'s ``_escalate`` pattern (same domain-tag
    rationale: "closest fit — this is a database-security/regulatory-posture
    control, not a product domain"). This is the daily regulatory PII/DSAR
    purge; a failed run must page, not just log, or it can silently stop
    running for days with nobody noticing (see docs/audit/2026-08-19-
    decision-writeups.md section 9).

    Message/context carry only the exception type and loop name — never
    row-level PII (CLAUDE.md: raw GPS/names/emails/etc. must never appear in
    logs or Sentry events). The preceding ``logger.exception`` call already
    carries the full traceback for on-call to pull up separately.
    """
    logger.critical(
        "retention_purge_loop: tick failed — daily PII/DSAR retention purge did not "
        "complete; regulatory retention windows may be missed if this repeats"
    )
    try:
        import sentry_sdk  # type: ignore

        sentry_sdk.capture_message(
            "RETENTION PURGE TICK FAILED",
            level="fatal",
            tags={"spinr_alert": "retention_purge_tick_failed", "domain": "admin", "surface": "backend"},
            contexts={"retention_purge": {"exception_type": type(exc).__name__}},
        )
    except Exception as sentry_err:  # pragma: no cover - telemetry must never break the loop
        logger.debug(f"retention_purge_loop: Sentry escalation unavailable: {sentry_err}")


async def retention_purge_loop() -> None:
    """Daily retention-purge loop with ±10% jitter sleep.

    Runs every ~24 h (INTERVAL_SECONDS ± 10%). Idempotent across replicas via
    the Redis SET NX EX leader lock inside _tick.

    The heartbeat only fires on a SUCCESSFUL tick (a leader-lock skip counts
    as success — another replica already ran). A failing tick must NOT
    refresh it, or the loop watchdog (core/lifespan.py -> loop_monitor.py)
    would see a healthy heartbeat every day even while the purge itself keeps
    failing — exactly the failure mode that let the original Step D/Step F
    bugs go undetected (see docs/audit/2026-08-19-decision-writeups.md
    section 9).
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
            _record_heartbeat("retention_purge (24h)")
        except Exception as exc:
            logger.exception("retention_purge_loop: tick raised")
            _metric_inc("spinr_bgloop_errors_total", {"loop": "retention_purge"})
            _escalate_tick_failure(exc)
        await asyncio.sleep(INTERVAL_SECONDS * (0.9 + random.random() * 0.2))
