"""
Main router aggregator
Import all route modules and combine them here
"""

from fastapi import APIRouter, Request
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
async def health_check(request: Request = None):
    """Liveness + readiness probe used by Railway health checks and the
    post-deploy smoke test. Verifies the DB connection is functional so
    Railway will not route traffic to a replica that cannot serve requests.

    Also surfaces background loop liveness: any loop that has not ticked
    within 2× its expected interval appears as "stale" and flips the
    top-level status to "degraded".
    """
    # ── DB check ────────────────────────────────────────────────────────────
    db_ok = False
    db_error: str = ""
    try:
        import db_supabase  # noqa: PLC0415

        await db_supabase.ping()
        db_ok = True
    except Exception:  # noqa: S110
        pass
    if not db_ok:
        try:
            from .. import db_supabase as _db  # noqa: PLC0415

            await _db.ping()
            db_ok = True
        except Exception as exc:
            db_error = str(exc)

    # ── Loop liveness ────────────────────────────────────────────────────────
    loop_status: dict = {"healthy": True, "loops": {}}
    try:
        from utils.loop_monitor import get_loop_status  # noqa: PLC0415

        registered: list = []
        if request is not None:
            app_state = getattr(request.app, "state", None)
            tasks = getattr(app_state, "background_tasks", None)
            if tasks is not None:
                registered = [t.get_name() for t in tasks if not t.done()]
        loop_status = get_loop_status(registered or None)
    except ImportError:
        try:
            from ..utils.loop_monitor import get_loop_status as _gls  # noqa: PLC0415

            loop_status = _gls(None)
        except Exception:
            pass
    except Exception:
        pass

    # ── Aggregate ────────────────────────────────────────────────────────────
    overall_healthy = db_ok and loop_status.get("healthy", True)
    payload = {
        "status": "healthy" if overall_healthy else "degraded",
        "db": "ok" if db_ok else db_error,
        "loops": loop_status.get("loops", {}),
    }
    if not overall_healthy:
        return JSONResponse(status_code=503, content=payload)
    return payload
