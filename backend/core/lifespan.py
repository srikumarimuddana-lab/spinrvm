from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from supabase_client import supabase


# Global database reference accessible via app state
async def init_database():
    """Initialize database connection properly within async context."""
    try:
        # Use the already initialized supabase client
        if not supabase:
            raise Exception("Supabase client not configured")
        return supabase

        # Verify connection with a simple query
        response = await supabase.table("test").select("*").limit(1).execute()
        if response.status_code == 200:
            logger.info("Supabase connection established successfully")
        else:
            logger.warning(f"Supabase connection test returned status: {response.status_code}")

        return supabase

    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise


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

    # G5: Subscription expiry warning — checks every 6h for subscriptions
    # expiring within 24h and sends push notifications.
    try:
        from routes.drivers import check_expiring_subscriptions
        asyncio.create_task(check_expiring_subscriptions())
        logger.info("Started subscription expiry checker (every 6h)")
    except Exception as e:
        logger.warning(f"Failed to start subscription expiry checker: {e}")

    # Automated surge pricing — recalculates demand/supply ratio every 2 min
    # and updates service_areas.surge_multiplier for auto-managed areas.
    try:
        from utils.surge_engine import surge_recalculation_loop
        asyncio.create_task(surge_recalculation_loop())
        logger.info("Started surge pricing engine (every 2min)")
    except Exception as e:
        logger.warning(f"Failed to start surge pricing engine: {e}")

    # Scheduled ride dispatcher — checks every 60s for rides due for dispatch
    # and sends 10-minute reminder notifications.
    try:
        from utils.scheduled_rides import scheduled_ride_dispatcher_loop
        asyncio.create_task(scheduled_ride_dispatcher_loop())
        logger.info("Started scheduled ride dispatcher (every 60s)")
    except Exception as e:
        logger.warning(f"Failed to start scheduled ride dispatcher: {e}")

    # Payment retry — retries failed Stripe payments every 5 minutes
    try:
        from utils.payment_retry import payment_retry_loop
        asyncio.create_task(payment_retry_loop())
        logger.info("Started payment retry service (every 5min)")
    except Exception as e:
        logger.warning(f"Failed to start payment retry service: {e}")

    # Document expiry alerts — notifies drivers about expiring docs every 12h
    try:
        from utils.document_expiry import document_expiry_loop
        asyncio.create_task(document_expiry_loop())
        logger.info("Started document expiry checker (every 12h)")
    except Exception as e:
        logger.warning(f"Failed to start document expiry checker: {e}")

    # Perform startup checks
    logger.info("Spinr API startup complete")

    yield

    # Cleanup on shutdown
    logger.info("Shutting down Spinr API...")
    # Note: Background tasks are disabled - no cleanup needed

    # Cleanup database
    if hasattr(app.state, "db") and app.state.db:
        await cleanup_database(app.state.db)

    logger.info("Spinr API shutdown complete")
