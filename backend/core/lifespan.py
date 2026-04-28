from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

try:
    from core.config import settings
    from db_supabase import run_sync
except ImportError:  # pragma: no cover - import style varies by entrypoint
    from ..core.config import settings  # type: ignore
    from ..db_supabase import run_sync  # type: ignore

from supabase_client import supabase


# Global database reference accessible via app state
async def init_database():
    """Initialize database connection and verify it is reachable.

    The supabase-py client is synchronous; we route the health-check probe
    through run_sync() to avoid blocking the event loop. In production,
    any failure raises — Uvicorn will refuse to serve traffic. In development
    we log a warning so local work without Supabase still boots.
    """
    if not supabase:
        msg = "Supabase client not configured (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing)"
        if settings.ENV.lower() == "production":
            raise RuntimeError(msg)
        logger.warning(f"{msg} — continuing in {settings.ENV} mode")
        return None

    # Active health check — one trivial read against a table that always exists.
    # The `users` table is part of the core schema; a failure here means either
    # the service role key is invalid, the DB is unreachable, or the schema
    # has not been applied.
    try:
        await run_sync(lambda: supabase.table("users").select("id").limit(1).execute())
        logger.info("Supabase connection verified")
    except Exception as e:
        logger.error(f"Supabase health check failed: {e}")
        if settings.ENV.lower() == "production":
            raise
        logger.warning(f"Continuing in {settings.ENV} mode despite health-check failure")

    return supabase


async def cleanup_database(db):
    """Cleanup database connections on shutdown."""
    try:
        # Add any cleanup logic here if needed
        logger.info("Database cleanup completed")
    except Exception as e:
        logger.error(f"Database cleanup error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan events"""
    # Size the default ThreadPoolExecutor that run_in_executor() uses.
    # Every Supabase call goes through run_sync() → run_in_executor(),
    # and each call pins one thread for its entire DB roundtrip (plus
    # any blocking retry is on asyncio.sleep — not pinning — so the
    # pool is only loaded during actual wire time).
    #
    # Python's default is min(32, os.cpu_count() + 4), which on a 2-vCPU
    # Railway instance works out to 6 threads. Under any realistic load
    # that's the bottleneck: at ~200ms average PostgREST latency, 6
    # threads cap us at 30 concurrent DB RPS. Bumping to 64 gives us
    # ~300 concurrent RPS headroom while still being well within the
    # memory budget (each thread is ~8 MB stack).
    #
    # Override with BACKEND_EXECUTOR_WORKERS env var if needed.
    import asyncio as _asyncio_lifespan
    import os as _os
    from concurrent.futures import ThreadPoolExecutor as _Executor

    executor_size = int(_os.environ.get("BACKEND_EXECUTOR_WORKERS", "64"))
    loop = _asyncio_lifespan.get_event_loop()
    loop.set_default_executor(_Executor(max_workers=executor_size, thread_name_prefix="spinr-db"))
    logger.info(f"Default executor sized to {executor_size} workers")

    # Initialize database
    logger.info("Initializing database connection...")
    try:
        db = await init_database()
        app.state.db = db
        logger.info("Database initialized and attached to app state")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

    # Warn operators if Redis is absent in production. Without Redis, OTP
    # lockout state and per-user rate-limit counters are in-process only and
    # are lost on every restart — brute-force protection degrades silently.
    if settings.ENV.lower() == "production" and not any(
        [settings.REDIS_URL, settings.RATE_LIMIT_REDIS_URL, settings.WS_REDIS_URL]
    ):
        logger.error(
            "No Redis URL configured in production. OTP lockout and rate-limit "
            "state are stored in-process and reset on every restart. "
            "Set REDIS_URL (or RATE_LIMIT_REDIS_URL + WS_REDIS_URL) before launch."
        )

    # Start background tasks
    import asyncio

    # Track task handles so we can cancel them cleanly on shutdown.
    background_tasks: list[asyncio.Task] = []

    def _spawn(name: str, coro_factory):
        try:
            task = asyncio.create_task(coro_factory(), name=name)
            background_tasks.append(task)
            logger.info(f"Started background task: {name}")
        except Exception as e:
            logger.warning(f"Failed to start background task {name}: {e}")

    # G5: Subscription expiry warning — checks every 6h for subscriptions
    # expiring within 24h and sends push notifications.
    try:
        from routes.drivers import check_expiring_subscriptions

        _spawn("subscription_expiry (6h)", check_expiring_subscriptions)
    except Exception as e:
        logger.warning(f"Failed to import subscription expiry checker: {e}")

    # Automated surge pricing — recalculates demand/supply ratio every 2 min
    # and updates service_areas.surge_multiplier for auto-managed areas.
    try:
        from utils.surge_engine import surge_recalculation_loop

        _spawn("surge_engine (2min)", surge_recalculation_loop)
    except Exception as e:
        logger.warning(f"Failed to import surge pricing engine: {e}")

    # Scheduled ride dispatcher — checks every 60s for rides due for dispatch
    # and sends 10-minute reminder notifications.
    try:
        from utils.scheduled_rides import scheduled_ride_dispatcher_loop

        _spawn("scheduled_dispatcher (60s)", scheduled_ride_dispatcher_loop)
    except Exception as e:
        logger.warning(f"Failed to import scheduled ride dispatcher: {e}")

    # Payment retry — retries failed Stripe payments every 5 minutes
    try:
        from utils.payment_retry import payment_retry_loop

        _spawn("payment_retry (5min)", payment_retry_loop)
    except Exception as e:
        logger.warning(f"Failed to import payment retry service: {e}")

    # Document expiry alerts — notifies drivers about expiring docs every 12h
    try:
        from utils.document_expiry import document_expiry_loop

        _spawn("document_expiry (12h)", document_expiry_loop)
    except Exception as e:
        logger.warning(f"Failed to import document expiry checker: {e}")

    # Corporate wallet auto-top-up — kicks off off-session Stripe charges
    # every 10 minutes for wallets that have dropped below their threshold.
    try:
        from utils.corporate_autotopup import corporate_autotopup_loop

        _spawn("corporate_autotopup (10min)", corporate_autotopup_loop)
    except Exception as e:
        logger.warning(f"Failed to import corporate autotopup loop: {e}")

    # Corporate wallet low-balance email — for accounts with auto-topup OFF,
    # sends a reminder once every 12h while the balance stays below threshold.
    try:
        from utils.corporate_low_balance import corporate_low_balance_loop

        _spawn("corporate_low_balance (1h)", corporate_low_balance_loop)
    except Exception as e:
        logger.warning(f"Failed to import corporate low-balance loop: {e}")

    # Monthly allowance reset — rolls fixed_recurring periods forward and
    # zeroes `used` for non-rollover employee allowances once per hour.
    try:
        from utils.allowance_reset import allowance_reset_loop

        _spawn("allowance_reset (1h)", allowance_reset_loop)
    except Exception as e:
        logger.warning(f"Failed to import allowance reset loop: {e}")

    # Driver presence sweeper — reconciles drivers.is_online against Redis
    # presence heartbeats every 60s, so ghost-online rows (app killed,
    # phone dead) flip offline without manual intervention. Dispatch
    # already filters on live presence; this keeps admin and analytics
    # tables honest.
    try:
        from utils.presence_sweeper import presence_sweeper_loop

        _spawn("presence_sweeper (60s)", presence_sweeper_loop)
    except Exception as e:
        logger.warning(f"Failed to import presence sweeper loop: {e}")

    # PII retention purge — daily SECURITY DEFINER call to anonymize
    # ride GPS at 3y, hard-delete rides at 7y, delete location history
    # / chat / stripe events at 90d, delete expired refresh tokens after
    # a 30d grace period. Closes audit B-P1-6 (Saskatchewan Transportation
    # Act + PIPEDA). The Postgres function is naturally idempotent; the
    # Redis leader lock inside the loop is belt-and-braces.
    try:
        from utils.retention_purge import retention_purge_loop

        _spawn("retention_purge (24h)", retention_purge_loop)
    except Exception as e:
        logger.warning(f"Failed to import retention purge loop: {e}")

    # PIPEDA right-to-erasure purge — daily sweep that permanently deletes
    # accounts whose 30-day grace period has expired (DV-8 / P2-46).
    # Replay-safe: gated on status='pending_deletion'; once deleted_at is
    # stamped the row no longer matches and other replicas skip it.
    try:
        from utils.account_purge import account_purge_loop

        _spawn("account_purge (24h)", account_purge_loop)
    except Exception as e:
        logger.warning(f"Failed to import account purge loop: {e}")

    app.state.background_tasks = background_tasks

    # WebSocket pub/sub (audit P0-B3): before this, socket sends were
    # in-process only, so on >1 replica the driver and the rider
    # regularly ended up on different containers and dispatch events
    # silently disappeared. Starting the pub/sub attaches a Redis
    # subscriber to the shared ConnectionManager; every outbound send
    # now fans out across replicas. In dev (no Redis URL configured)
    # this is a no-op and the manager stays in local-only mode.
    try:
        from socket_manager import manager as ws_manager
        from utils.ws_pubsub import pubsub as ws_pubsub
        from utils.ws_pubsub import resolve_ws_redis_url

        ws_redis_url = resolve_ws_redis_url(settings.WS_REDIS_URL, settings.RATE_LIMIT_REDIS_URL)
        ws_started = await ws_pubsub.start(ws_manager, ws_redis_url)
        app.state.ws_pubsub = ws_pubsub
        if not ws_started and settings.ENV.lower() == "production":
            # Production without distributed WS is a correctness
            # hazard, but not a boot-blocker — a single-machine prod
            # deploy is still coherent. Log at WARNING so the operator
            # sees it in the boot logs.
            logger.warning(
                "WS pub/sub did NOT start — WebSocket fan-out will be "
                "limited to the current machine. Set WS_REDIS_URL (or "
                "RATE_LIMIT_REDIS_URL, which will be reused) to enable "
                "cross-machine delivery."
            )
    except Exception as e:
        logger.warning(f"Failed to start WS pub/sub: {e}")

    # Perform startup checks
    logger.info(f"Spinr API startup complete ({len(background_tasks)} background tasks running)")

    yield

    # Cleanup on shutdown — cancel background tasks and await them.
    logger.info("Shutting down Spinr API...")
    # Stop WS pub/sub FIRST so in-flight publishes don't race against
    # a half-torn-down Redis client during the last ~millisecond of
    # shutdown (and so its consumer task isn't left as an orphan when
    # the event loop stops).
    try:
        ws_pubsub_ref = getattr(app.state, "ws_pubsub", None)
        if ws_pubsub_ref is not None:
            await ws_pubsub_ref.stop()
    except Exception as e:
        logger.warning(f"Error stopping WS pub/sub: {e}")

    for task in background_tasks:
        task.cancel()
    if background_tasks:
        await asyncio.gather(*background_tasks, return_exceptions=True)
        logger.info(f"Cancelled {len(background_tasks)} background tasks")

    # Cleanup database
    if hasattr(app.state, "db") and app.state.db:
        await cleanup_database(app.state.db)

    logger.info("Spinr API shutdown complete")
