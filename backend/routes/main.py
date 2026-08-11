"""
Main router aggregator

NOT CURRENTLY MOUNTED. ``server.py`` builds its router set by importing each
``routes.<module>`` directly (see the `from routes.<x> import api_router as
..._router` block plus `v1_api_router.include_router(...)` calls) — it never
imports `routes.main` or its `api_router`. The real liveness/readiness probe
Railway and the post-deploy smoke test hit is `server.py`'s own `@app.get
("/health")`, not the `health_check()` below. Keep this docstring accurate
if that changes — the previous version claimed this endpoint was what
Railway/the smoke test depend on, which was false and could mislead someone
debugging a health-check incident into looking at the wrong file.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# Create the main API router (sub-routers are assembled in server.py)
api_router = APIRouter()


# Health check and root endpoints
@api_router.get("/")
async def root():
    return {"message": "Spinr API", "version": "1.0.0"}


@api_router.get("/health")
async def health_check(request: Request = None):
    """DB + background-loop liveness/readiness probe.

    NOT the endpoint Railway health checks or the post-deploy smoke test
    hit — this module isn't mounted (see the module docstring above); the
    live one is `server.py`'s own `@app.get("/health")`. Kept here (and
    kept correct) in case this router is ever wired in, but do not assume
    it's exercised in production today.

    Also surfaces background loop liveness: any loop that has not ticked
    within 2× its expected interval appears as "stale" and flips the
    top-level status to "degraded".
    """
    # ── DB check ────────────────────────────────────────────────────────────
    db_ok = False
    db_info: dict = {}
    db_error: str = ""
    try:
        import db_supabase  # noqa: PLC0415

        db_info = await db_supabase.ping()
        db_ok = True
    except Exception:  # noqa: S110
        logger.warning("health_check: db_supabase absolute import failed", exc_info=True)
    if not db_ok:
        try:
            from .. import db_supabase as _db  # noqa: PLC0415

            db_info = await _db.ping()
            db_ok = True
        except Exception as exc:
            db_error = str(exc)
            if hasattr(exc, "details"):
                db_info = exc.details

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
        except Exception:  # noqa: S110
            logger.warning(
                "health_check: loop_monitor relative import failed; loops field omitted",
                exc_info=True,
            )
    except Exception:  # noqa: S110
        logger.warning(
            "health_check: loop_monitor import failed; health still reports DB status",
            exc_info=True,
        )

    # ── Aggregate ────────────────────────────────────────────────────────────
    overall_healthy = db_ok and loop_status.get("healthy", True)
    db_payload = {"status": "ok", **db_info} if db_ok else {"status": "error", "error": db_error, **db_info}
    payload = {
        "status": "healthy" if overall_healthy else "degraded",
        "db": db_payload,
        "loops": loop_status.get("loops", {}),
    }
    if not overall_healthy:
        return JSONResponse(status_code=503, content=payload)
    return payload
