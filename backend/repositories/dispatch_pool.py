"""Direct-Postgres connection pool for the dispatch claim path (C50 Phase 1).

**Not wired to anything yet.** This module exists so `core/lifespan.py` has
something concrete to open/close behind the `dispatch_direct_pool_enabled`
flag (T10) — no caller in `routes/rides/matching.py` uses it until Phase 2
(T12/T13, explicitly out of scope for this change). See
`docs/audit/2026-09-02-pgbouncer-direct-pool-migration-plan.md`.

Why this exists
----------------
The dispatch claim path (`matching.py`'s claim loop → `ride_offers` insert →
insurance-period transition) currently goes through PostgREST
(`supabase-py`), which round-trips over HTTP for each logical DB operation.
This module is the plumbing for talking to Supabase's Supavisor pooler
*directly* over a real Postgres wire protocol, once Phase 2 wires it in.

Driver: `psycopg[binary,pool]` (v3), not asyncpg — decision D1 in the plan
doc. One driver family with `backend/scripts/run_migrations.py`,
`verify_restore.py`, and `audit_migration_drift.py`, which already prefer
psycopg v3 with a psycopg2 fallback. `AsyncConnectionPool` is async-native
(no thread-pool wrapping needed, unlike the PostgREST/`run_sync` path).

TRANSACTION-MODE POOLING DISCIPLINE — READ THIS BEFORE ADDING A CALL SITE
---------------------------------------------------------------------------
Supavisor's pooler mode for this DSN is (or must be configured as)
**transaction mode**, not session mode. In transaction mode, Supavisor hands
out an underlying server connection only for the duration of a single
transaction and may reassign that server connection to a *different* client
the moment the transaction ends. This is a hard constraint, not a style
preference — violating it does not error immediately, it silently corrupts
behavior under concurrent load:

  * **No `SET` (session-level GUCs).** A `SET search_path = ...` or
    `SET statement_timeout = ...` issued outside an explicit transaction can
    leak onto whichever client happens to get the same server connection
    next. Use `SET LOCAL` inside a transaction if a GUC override is ever
    needed, never bare `SET`.
  * **No advisory locks.** `pg_advisory_lock` (the non-`_xact` form) is tied
    to the *session*, not the transaction, and Supavisor may hand that
    "session" (really: a rotating server connection) to another client
    before the lock is released — the lock can silently outlive the caller
    that thinks it holds it, or never actually protect anything. Use
    `pg_advisory_xact_lock` (transaction-scoped, auto-released at COMMIT/
    ROLLBACK) if advisory locking is ever needed here.
  * **No server-side prepared statements.** This is why every connection
    from this pool is opened with `prepare_threshold=None` — see
    `_connect()` below. Without it, psycopg3 would, after a few uses of the
    same query shape, silently start issuing `PREPARE`/`EXECUTE` against
    whatever physical server connection Supavisor currently has assigned,
    and a later transaction-mode handoff to a different physical backend
    would either fail with "prepared statement does not exist" or, worse,
    execute a *different* previously-prepared statement that happens to
    share the same auto-generated name on that backend.
  * **One (short) transaction per logical call.** Every function in this
    module wraps its work in exactly one `async with conn.transaction():`
    block (or relies on the connection's own implicit transaction for a
    single statement) and does not hold a connection open across an `await`
    that waits on anything other than Postgres itself (no waiting on Redis,
    an HTTP call, or user input while holding a pooled connection).

None of the above is enforced by the driver — it is enforced by us reading
this docstring before writing a new call site. If Phase 2 (T13) ever needs
`SET`, an advisory lock, or a long-lived transaction, that is a discussion
with whoever owns Supavisor configuration, not a thing to add quietly here.

Flag + fail-loud semantics
---------------------------
`init_pool()` is a no-op unless BOTH `app_settings.dispatch_direct_pool_enabled`
(T10) is true AND `settings.DISPATCH_POOL_DSN` (T8) is non-empty — mirrors
`core/lifespan.init_database`'s existing production gate exactly (no new
gating mechanism introduced here). On open failure, this module raises in
production (`settings.ENV == "production"`) and logs+continues everywhere
else, same as `init_database`. Callers in `matching.py` do not exist yet
(Phase 2), so nothing today depends on this pool being open — flag OFF means
this module is inert.

THE FLAG IS READ ONCE, AT STARTUP — WHAT THAT OBLIGATES PHASE 2 TO DO
----------------------------------------------------------------------
`init_pool()` is called only from `core/lifespan.init_database`, so the flag
is sampled exactly once per process. Nothing re-reads it, and nothing closes
the pool when it flips. Two consequences:

  * Turning the flag ON against a running process does nothing — there is no
    open pool for a call site to use. Enabling is a restart.
  * Turning it OFF does NOT close the pool. Rollback works only because
    Phase 2 (T12/T13) call sites MUST re-read the flag per dispatch attempt
    (`settings_loader.get_app_settings`, ≤60s cache) and take the PostgREST
    path when it is false. A leftover open pool serving nobody is harmless.

That per-attempt re-read is the whole rollback story for C50. A Phase 2 that
instead decides "direct pool or PostgREST" once at startup would silently
turn the documented ≤60s no-redeploy rollback into a redeploy, so if that
design changes, the rollback procedure in `schemas.AppSettings` and in the
plan doc has to change with it.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from loguru import logger

try:
    from ..core.config import settings  # type: ignore
    from ..utils.deadline import deadline_exhausted as _deadline_exhausted  # type: ignore
    from ..utils.deadline import remaining_seconds as _remaining_seconds
    from ..utils.metrics import observe as _metric_observe  # type: ignore
    from ..utils.metrics import set_gauge as _metric_gauge
    from ._base import _redact_pg_error  # type: ignore
except ImportError:
    from core.config import settings  # type: ignore
    from repositories._base import _redact_pg_error  # type: ignore
    from utils.deadline import deadline_exhausted as _deadline_exhausted  # type: ignore
    from utils.deadline import remaining_seconds as _remaining_seconds
    from utils.metrics import observe as _metric_observe  # type: ignore
    from utils.metrics import set_gauge as _metric_gauge

try:
    import psycopg  # type: ignore
    from psycopg_pool import AsyncConnectionPool  # type: ignore

    _PSYCOPG_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when psycopg3 isn't installed
    psycopg = None  # type: ignore
    AsyncConnectionPool = None  # type: ignore
    _PSYCOPG_AVAILABLE = False


# Module-level handle. None whenever the pool has not been opened (flag off,
# DSN unset, or import-time failure) — every helper below treats that as
# "the direct pool is not available" rather than crashing.
_pool: Optional["AsyncConnectionPool"] = None


def is_open() -> bool:
    """True once `init_pool()` has successfully opened the pool."""
    return _pool is not None


def _in_use(pool: Optional["AsyncConnectionPool"]) -> float:
    """Connections currently checked out of `pool`.

    psycopg_pool reports `pool_size` (every connection the pool manages —
    idle ones included) and `pool_available` (the idle subset). In-use is the
    difference. Reading `pool_size` alone counts idle connections as busy, so
    a freshly-opened pool sitting at DISPATCH_POOL_MIN_SIZE=1 with nothing in
    flight would report 1 connection in use forever, and the gauge could
    never fall to 0 — which is exactly the signal Phase 2 needs it for
    (saturation against DISPATCH_POOL_MAX_SIZE).

    Falls back to `pool_size` if `pool_available` is absent so a stats-key
    change degrades to the previous behaviour rather than raising on the
    dispatch path, and clamps at 0 because the two keys are sampled under
    separate locks and can momentarily disagree under concurrency.
    """
    if pool is None:
        return 0.0
    stats = pool.get_stats() or {}
    size = float(stats.get("pool_size", 0) or 0)
    if "pool_available" not in stats:
        return size
    return max(size - float(stats.get("pool_available", 0) or 0), 0.0)


async def init_pool(dispatch_direct_pool_enabled: bool) -> Optional["AsyncConnectionPool"]:
    """Open the direct-pool `AsyncConnectionPool`, or do nothing.

    Called from `core.lifespan.init_database`. Mirrors that function's own
    `settings.ENV == "production"` fail-loud gate exactly — same pattern,
    not a new one. Returns the opened pool (also stored module-globally) or
    None when the pool was not opened (flag off / DSN unset / dependency
    missing / open failure in non-production).

    Idempotent: calling this twice without an intervening `close_pool()`
    returns the existing pool unchanged rather than opening a second one.
    """
    global _pool

    if _pool is not None:
        return _pool

    if not dispatch_direct_pool_enabled:
        logger.debug("[dispatch_pool] flag off — direct pool not opened")
        return None

    dsn = settings.DISPATCH_POOL_DSN
    if not dsn:
        logger.debug("[dispatch_pool] DISPATCH_POOL_DSN unset — direct pool not opened")
        return None

    if not _PSYCOPG_AVAILABLE:
        msg = (
            "[dispatch_pool] dispatch_direct_pool_enabled is on and DISPATCH_POOL_DSN "
            "is set, but psycopg[binary,pool] is not installed — direct pool cannot open"
        )
        logger.error(msg)
        if settings.ENV.lower() == "production":
            raise RuntimeError(msg)
        return None

    try:
        pool = AsyncConnectionPool(
            conninfo=dsn,
            min_size=settings.DISPATCH_POOL_MIN_SIZE,
            max_size=settings.DISPATCH_POOL_MAX_SIZE,
            # Transaction-mode discipline (see module docstring): a pooled
            # server connection can be handed to a different client between
            # transactions, so we must never let psycopg cache a server-side
            # PREPARE against it.
            kwargs={"prepare_threshold": None, "autocommit": False},
            open=False,
        )
        await pool.open(wait=True, timeout=10.0)
        _pool = pool
        logger.info(
            f"[dispatch_pool] direct pool opened (min={settings.DISPATCH_POOL_MIN_SIZE}, "
            f"max={settings.DISPATCH_POOL_MAX_SIZE})"
        )
        _metric_gauge("spinr_db_direct_pool_in_use", 0.0)
        return _pool
    except Exception as exc:
        redacted = _redact_pg_error(str(exc))
        logger.error(f"[dispatch_pool] failed to open direct pool: {redacted}")
        if settings.ENV.lower() == "production":
            raise RuntimeError(f"dispatch direct pool failed to open: {redacted}") from exc
        return None


async def close_pool() -> None:
    """Close the direct pool if open. Safe to call even if never opened.

    Called from `core.lifespan.cleanup_database` — the first real use of
    that previously-empty stub.
    """
    global _pool
    if _pool is None:
        return
    try:
        await _pool.close()
        logger.info("[dispatch_pool] direct pool closed")
    except Exception as exc:
        redacted = _redact_pg_error(str(exc))
        logger.error(f"[dispatch_pool] error closing direct pool: {redacted}")
    finally:
        _pool = None


@asynccontextmanager
async def acquire() -> AsyncIterator["psycopg.AsyncConnection"]:
    """Acquire a connection from the direct pool for exactly one transaction.

    Raises RuntimeError if the pool was never opened (flag off / DSN unset) —
    callers (Phase 2 only, today none exist) must check `is_open()` or catch
    this rather than silently falling back, per D2 in the plan doc (fail
    loud, no silent PostgREST fallback).

    Mirrors `repositories/_base.py`'s deadline-propagation pattern: rejects
    up front if the request's client deadline has already expired, and bounds
    the wait-for-a-free-connection step by whatever budget remains.
    """
    if _pool is None:
        raise RuntimeError(
            "[dispatch_pool] acquire() called but the direct pool is not open "
            "(dispatch_direct_pool_enabled is off, DISPATCH_POOL_DSN is unset, "
            "or pool initialization failed) — this is a Phase 2 (T12/T13) code "
            "path and should not be reachable while Phase 1 ships flag-off"
        )

    if _deadline_exhausted():
        remaining = _remaining_seconds()
        logger.warning(
            "[dispatch_pool] rejected before acquire: client deadline already expired",
            extra={"overdue_seconds": round(-(remaining or 0.0), 3)},
        )
        raise TimeoutError("dispatch direct pool: client deadline already expired")

    import time as _time

    _wait_start = _time.monotonic()
    remaining = _remaining_seconds()
    try:
        async with _pool.connection(timeout=remaining if remaining is not None else None) as conn:
            _metric_observe("spinr_db_direct_pool_wait_ms", (_time.monotonic() - _wait_start) * 1000.0)
            _metric_gauge("spinr_db_direct_pool_in_use", _in_use(_pool))
            yield conn
    finally:
        _metric_gauge("spinr_db_direct_pool_in_use", _in_use(_pool))


async def run_query(sql: str, params: tuple = (), *, fetch: str = "all") -> Any:
    """Run one SQL statement in its own transaction and return rows.

    `fetch`: "all" (fetchall), "one" (fetchone), or "none" (no result set,
    e.g. an UPDATE/INSERT without RETURNING). One transaction per call, per
    the transaction-mode discipline in the module docstring — do not call
    this repeatedly and expect state (a `SET`, a temp table, an advisory
    lock) to persist across calls.

    Not called from anywhere yet — Phase 2 (T13) is the first real caller.
    """
    import time as _time

    _t0 = _time.monotonic()
    async with acquire() as conn:
        try:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(sql, params)
                    if fetch == "all":
                        result = await cur.fetchall()
                    elif fetch == "one":
                        result = await cur.fetchone()
                    else:
                        result = None
            return result
        except Exception as exc:
            redacted = _redact_pg_error(str(exc))
            logger.error(f"[dispatch_pool] query failed: {redacted}")
            raise
        finally:
            _metric_observe("spinr_db_direct_query_duration_ms", (_time.monotonic() - _t0) * 1000.0)
