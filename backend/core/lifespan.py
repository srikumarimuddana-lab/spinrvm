import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from loguru import logger

from features import check_scheduled_rides
from supabase_client import supabase
from core.config import settings


# Global database reference accessible via app state
async def init_database():
    """Initialize database connection properly within async context."""
    try:
        # Use the already initialized supabase client
        if not supabase:
            raise Exception("Supabase client not configured")
        return supabase
        
        # Verify connection with a simple query
        response = await supabase.table('test').select('*').limit(1).execute()
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
    logger.info("Starting scheduled rides checker...")
    # Note: scheduler_task is disabled - scheduled rides feature needs Supabase migration
    
    # Perform startup checks
    logger.info("Spinr API startup complete")
    
    yield
    
    # Cleanup on shutdown
    logger.info("Shutting down Spinr API...")
    # Note: scheduler_task is disabled - no background tasks to cancel
    
    # Cleanup database
    if hasattr(app.state, 'db') and app.state.db:
        await cleanup_database(app.state.db)
    
    logger.info("Spinr API shutdown complete")
