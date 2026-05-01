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

    # PIPEDA data-residency check (Rider Phase E 22-2): Supabase must be in a
    # Canadian region (ca-central-1). The URL alone doesn't embed the region,
    # so we rely on the explicit SUPABASE_REGION env var. A missing or non-CA
    # value logs ERROR in production so this surfaces in SRE alerting.
    supabase_region = getattr(settings, "SUPABASE_REGION", "") or ""
    if settings.ENV.lower() == "production":
        if not supabase_region:
            logger.error(
                "PIPEDA 22-2: SUPABASE_REGION is not set. "
                "Set SUPABASE_REGION=ca-central-1 to confirm Canadian data residency."
            )
        elif supabase_region.lower() != "ca-central-1":
            logger.error(
                f"PIPEDA 22-2: SUPABASE_REGION={supabase_region!r} — expected 'ca-central-1'. "
                "Canadian data-residency compliance requires the Supabase project to be in ca-central-1."
            )
        else:
            logger.info(f"Supabase data residency confirmed: region={supabase_region}")
    else:
        logger.info(f"Supabase region: {supabase_region or 'unset (non-production)'}")

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

    async def _restartable(name: str, coro_factory):
        """Wrap a background loop so an uncaught crash auto-restarts after 5s."""
        while True:
            try:
                await coro_factory()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error(
                    f"Background task {name!r} crashed — restarting in 5s",
                    exc_info=True,
                )
                await asyncio.sleep(5)

    def _spawn(name: str, coro_factory):
        try:
            task = asyncio.create_task(_restartable(name, coro_factory), name=name)
            background_tasks.append(task)
            logger.info(f"Started background task: {name}")
        except Exception as e:
            logger.error(f"Failed to start background task {name}: {e}", exc_info=True)

    # G5: Subscription expiry warning — checks every 6h for subscriptions
    # expiring within 24h and sends push notifications.
    try:
        from routes.drivers import check_expiring_subscriptions

        _spawn("subscription_expiry (6h)", check_expiring_subscriptions)
    except Exception as e:
        logger.error(f"Failed to import subscription expiry checker: {e}", exc_info=True)

    # Automated surge pricing — recalculates demand/supply ratio every 2 min
    # and updates service_areas.surge_multiplier for auto-managed areas.
    try:
        from utils.surge_engine import surge_recalculation_loop

        _spawn("surge_engine (2min)", surge_recalculation_loop)
    except Exception as e:
        logger.error(f"Failed to import surge pricing engine: {e}", exc_info=True)

    # Scheduled ride dispatcher — checks every 60s for rides due for dispatch
    # and sends 10-minute reminder notifications.
    try:
        from utils.scheduled_rides import scheduled_ride_dispatcher_loop

        _spawn("scheduled_dispatcher (60s)", scheduled_ride_dispatcher_loop)
    except Exception as e:
        logger.error(f"Failed to import scheduled ride dispatcher: {e}", exc_info=True)

    # Payment retry — retries failed Stripe payments every 5 minutes
    try:
        from utils.payment_retry import payment_retry_loop

        _spawn("payment_retry (5min)", payment_retry_loop)
    except Exception as e:
        logger.error(f"Failed to import payment retry service: {e}", exc_info=True)

    # Document expiry alerts — notifies drivers about expiring docs every 12h
    try:
        from utils.document_expiry import document_expiry_loop

        _spawn("document_expiry (12h)", document_expiry_loop)
    except Exception as e:
        logger.error(f"Failed to import document expiry checker: {e}", exc_info=True)

    # Corporate wallet auto-top-up — kicks off off-session Stripe charges
    # every 10 minutes for wallets that have dropped below their threshold.
    try:
        from utils.corporate_autotopup import corporate_autotopup_loop

        _spawn("corporate_autotopup (10min)", corporate_autotopup_loop)
    except Exception as e:
        logger.error(f"Failed to import corporate autotopup loop: {e}", exc_info=True)

    # Corporate wallet low-balance email — for accounts with auto-topup OFF,
    # sends a reminder once every 12h while the balance stays below threshold.
    try:
        from utils.corporate_low_balance import corporate_low_balance_loop

        _spawn("corporate_low_balance (1h)", corporate_low_balance_loop)
    except Exception as e:
        logger.error(f"Failed to import corporate low-balance loop: {e}", exc_info=True)

    # Monthly allowance reset — rolls fixed_recurring periods forward and
    # zeroes `used` for non-rollover employee allowances once per hour.
    try:
        from utils.allowance_reset import allowance_reset_loop

        _spawn("allowance_reset (1h)", allowance_reset_loop)
    except Exception as e:
        logger.error(f"Failed to import allowance reset loop: {e}", exc_info=True)

    # Driver presence sweeper — reconciles drivers.is_online against Redis
    # presence heartbeats every 60s, so ghost-online rows (app killed,
    # phone dead) flip offline without manual intervention. Dispatch
    # already filters on live presence; this keeps admin and analytics
    # tables honest.
    try:
        from utils.presence_sweeper import presence_sweeper_loop

        _spawn("presence_sweeper (60s)", presence_sweeper_loop)
    except Exception as e:
        logger.error(f"Failed to import presence sweeper loop: {e}", exc_info=True)

    # Safety check-in — every 30s: sends a push to riders whose trip has been
    # in_progress for ≥ 20 minutes.  If the rider does not respond within 90s,
    # an open safety incident is created for the trust-and-safety team.
    try:
        from utils.safety_checkin_loop import safety_checkin_loop

        _spawn("safety_checkin (30s)", safety_checkin_loop)
    except Exception as e:
        logger.warning(f"Failed to import safety checkin loop: {e}")

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
        logger.error(f"Failed to import retention purge loop: {e}", exc_info=True)

    # Daily Stripe ↔ DB ↔ wallet reconciliation — 20-2 (PCI-DSS, SOC2 CC9.1,
    # CRA). Polls every 60 s, runs the actual reconciliation once per day at
    # 02:00 UTC. Alerts finance on discrepancies > $0.01.
    try:
        from utils.reconciliation import reconciliation_loop

        _spawn("reconciliation (daily 02:00 UTC)", reconciliation_loop)
    except Exception as e:
        logger.warning(f"Failed to import reconciliation loop: {e}")

    # Stripe ↔ DB daily reconciliation — runs at 02:00 UTC, one replica
    # via Redis leader lock. Flags paid rides with no matching Stripe
    # PaymentIntent, amount mismatches, and orphaned Stripe charges.
    # Discrepancies logged at ERROR so they reach Sentry + audit_logs.
    try:
        from utils.stripe_reconcile import stripe_reconcile_loop

        _spawn("stripe_reconcile (24h)", stripe_reconcile_loop)
    except Exception as e:
        logger.warning(f"Failed to import Stripe reconciliation loop: {e}")

    # Loop watchdog — scans heartbeats every 5 minutes and posts a
    # Slack-compatible alert when any loop has gone stale.  No-op when
    # ALERT_WEBHOOK_URL is unset.
    _WATCHDOG_LOOP_NAMES = list(
        [
            "subscription_expiry (6h)",
            "surge_engine (2min)",
            "scheduled_dispatcher (60s)",
            "payment_retry (5min)",
            "document_expiry (12h)",
            "corporate_autotopup (10min)",
            "corporate_low_balance (1h)",
            "allowance_reset (1h)",
            "presence_sweeper (60s)",
            "retention_purge (24h)",
            "stripe_reconcile (24h)",
        ]
    )

    async def _loop_watchdog():
        import asyncio as _asyncio

        try:
            from utils.loop_alert import check_and_alert
            from utils.loop_monitor import record_heartbeat
        except ImportError:
            from utils.loop_alert import check_and_alert  # type: ignore
            from utils.loop_monitor import record_heartbeat  # type: ignore

        while True:
            try:
                await check_and_alert(
                    registered_names=_WATCHDOG_LOOP_NAMES,
                    webhook_url=settings.ALERT_WEBHOOK_URL,
                )
                record_heartbeat("loop_watchdog (5min)")
            except Exception:
                logger.error("loop_watchdog tick failed", exc_info=True)
            await _asyncio.sleep(300)

    _spawn("loop_watchdog (5min)", _loop_watchdog)

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
        logger.error(f"Failed to start WS pub/sub: {e}", exc_info=True)

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
