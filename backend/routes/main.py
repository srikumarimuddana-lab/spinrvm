"""
Main router aggregator
Import all route modules and combine them here
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .admin import router as admin_router
from .auth import router as auth_router
from .corporate_accounts import router as corporate_accounts_router
from .drivers import router as drivers_router
from .rides import router as rides_router

# Create the main API router
api_router = APIRouter()

# Include all sub-routers
api_router.include_router(auth_router)
api_router.include_router(rides_router)
api_router.include_router(drivers_router)
api_router.include_router(admin_router)
api_router.include_router(corporate_accounts_router)


# Health check and root endpoints
@api_router.get("/")
async def root():
    return {"message": "Spinr API", "version": "1.0.0"}


@api_router.get("/health")
async def health_check():
    """Liveness + readiness probe used by Railway health checks and the
    post-deploy smoke test. Verifies the DB connection is functional so
    Railway will not route traffic to a replica that cannot serve requests.
    """
    try:
        import db_supabase  # noqa: PLC0415
        await db_supabase.ping()
        return {"status": "healthy", "db": "ok"}
    except Exception:
        pass
    try:
        from .. import db_supabase as _db  # noqa: PLC0415
        await _db.ping()
        return {"status": "healthy", "db": "ok"}
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "db": str(exc)},
        )
