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
    # Initialize database
    logger.info("Initializing database connection...")
    try:
        db = await init_database()
        app.state.db = db
        logger.info("Database initialized and attached to app state")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

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

    app.state.background_tasks = background_tasks

    # WebSocket pub/sub (audit P0-B3): before this, socket sends were
    # in-process only, so on >1 Fly machine the driver and the rider
    # regularly ended up on different VMs and dispatch events silently
    # disappeared. Starting the pub/sub attaches a Redis subscriber to
    # the shared ConnectionManager; every outbound send now fans out
    # across machines. In dev (no Redis URL configured) this is a
    # no-op and the manager stays in local-only mode.
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
