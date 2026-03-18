"""
Database module - DEPRECATED

This module previously initialized the database at import time using asyncio.run(),
which is an antipattern that blocks the event loop.

Database initialization has been moved to the FastAPI lifespan context.
Use app.state.db to access the database connection in route handlers.

Example:
    @app.get("/users")
    async def get_users(app: FastAPI):
        db = app.state.db
        users = await db.users.find({})
        return users
"""

# Re-export for backward compatibility during transition
# TODO: Remove this file after all imports are updated
from supabase_client import supabase as SupabaseClient

def get_db(app_state):
    """Get database connection from app state.
    
    Args:
        app_state: The FastAPI app.state object
        
    Returns:
        SupabaseClient instance
        
    Raises:
        RuntimeError: If database is not initialized
    """
    if not hasattr(app_state, 'db') or app.state.db is None:
        raise RuntimeError("Database not initialized. Ensure lifespan is properly configured.")
    return app.state.db
