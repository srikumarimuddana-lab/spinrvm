"""Dedicated worker FastAPI app — outbox poller plus worker-wave-1 loops."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from loguru import logger

_log = logger.bind(domain="admin", surface="backend")

try:
    from core.background_loop_registry import WORKER_WAVE1_LOOP_NAMES
    from core.config import settings
    from core.lifespan import init_database
    from core.security import init_firebase
    from utils.metrics import render_prometheus, set_gauge
    from utils.outbox_worker import run_outbox_worker
    from utils.sentry_runtime import init_backend_sentry
except ImportError:
    from core.background_loop_registry import WORKER_WAVE1_LOOP_NAMES  # type: ignore
    from core.config import settings  # type: ignore
    from core.lifespan import init_database  # type: ignore
    from core.security import init_firebase  # type: ignore
    from utils.metrics import render_prometheus, set_gauge  # type: ignore
    from utils.outbox_worker import run_outbox_worker  # type: ignore
    from utils.sentry_runtime import init_backend_sentry  # type: ignore

OUTBOX_LOOP_NAME = "outbox_poller (1-10s)"
WORKER_LOOP_NAMES: tuple[str, ...] = (OUTBOX_LOOP_NAME, *WORKER_WAVE1_LOOP_NAMES)

# Prometheus label values — fixed registry, never request-derived.
WORKER_TASK_LABELS: Dict[str, str] = {
    OUTBOX_LOOP_NAME: "outbox_poller",
    "push_retry (30s)": "push_retry",
    "zoho_desk_sync (10min)": "zoho_desk_sync",
    "driver_onboarding_reminders (15min)": "driver_onboarding_reminders",
}

STARTUP_GRACE_S = 60.0
_METRICS_QUERY_WARN_INTERVAL_S = 60.0
_metrics_query_warn_mono = 0.0
_metrics_log = logging.getLogger("spinr.metrics")


def _metrics_token() -> str:
    return os.getenv("METRICS_AUTH_TOKEN", "").strip()


async def _restartable(name: str, factory: Callable[[], Awaitable[None]]) -> None:
    while True:
        try:
            await factory()
            _log.error("worker task {} returned unexpectedly — restarting in 5s", name)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.opt(exception=True).error("worker task {} crashed — restarting in 5s", name)
        await asyncio.sleep(5)


def _outbox_factory(stop_event: asyncio.Event) -> Callable[[], Awaitable[None]]:
    async def _run() -> None:
        await run_outbox_worker(stop_event)

    return _run


def _wave1_factories() -> Dict[str, Callable[[], Awaitable[None]]]:
    try:
        from utils.driver_onboarding_reminders import driver_onboarding_reminder_loop
        from utils.push_retry import push_retry_loop
        from utils.zoho_desk_sync import zoho_desk_sync_loop
    except ImportError:
        from utils.driver_onboarding_reminders import driver_onboarding_reminder_loop  # type: ignore
        from utils.push_retry import push_retry_loop  # type: ignore
        from utils.zoho_desk_sync import zoho_desk_sync_loop  # type: ignore
    return {
        "push_retry (30s)": push_retry_loop,
        "zoho_desk_sync (10min)": zoho_desk_sync_loop,
        "driver_onboarding_reminders (15min)": driver_onboarding_reminder_loop,
    }


def _task_running(task: Optional[asyncio.Task]) -> bool:
    return task is not None and not task.done()


def _refresh_task_gauges(tasks: Dict[str, asyncio.Task]) -> Dict[str, Any]:
    details: Dict[str, Any] = {}
    for name in WORKER_LOOP_NAMES:
        task = tasks.get(name)
        running = _task_running(task)
        label = WORKER_TASK_LABELS[name]
        set_gauge("spinr_worker_task_healthy", 1.0 if running else 0.0, {"task": label})
        details[name] = {
            "running": running,
            "done": bool(task is None or task.done()),
        }
    return details


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_backend_sentry(process_name="spinr worker")
    init_firebase()
    await init_database()
    stop_event = asyncio.Event()
    tasks: Dict[str, asyncio.Task] = {}
    factories: Dict[str, Callable[[], Awaitable[None]]] = {
        OUTBOX_LOOP_NAME: _outbox_factory(stop_event),
        **_wave1_factories(),
    }
    for name in WORKER_LOOP_NAMES:
        factory = factories[name]
        tasks[name] = asyncio.create_task(_restartable(name, factory), name=name)
        logger.info("Started worker task: {}", name)
    app.state.worker_tasks = tasks
    app.state.worker_stop = stop_event
    app.state.worker_started_mono = time.monotonic()
    try:
        yield
    finally:
        stop_event.set()
        for _name, task in list(tasks.items()):
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks.values(), return_exceptions=True)
        logger.info("worker tasks cancelled")


app = FastAPI(title="Spinr Worker", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get("/health")
async def health():
    """Unauthenticated liveness for Fly `[[checks]]`. Do not expose this port publicly."""
    tasks: Dict[str, asyncio.Task] = getattr(app.state, "worker_tasks", {})
    details = _refresh_task_gauges(tasks)
    started = getattr(app.state, "worker_started_mono", time.monotonic())
    in_grace = (time.monotonic() - started) < STARTUP_GRACE_S
    healthy = all(item["running"] for item in details.values())
    if healthy and not in_grace:
        try:
            from utils.loop_monitor import get_loop_status
        except ImportError:
            from utils.loop_monitor import get_loop_status  # type: ignore
        status = get_loop_status(registered_names=list(WORKER_LOOP_NAMES))
        loops = status.get("loops") or {}
        for name, info in loops.items():
            # never_ticked is expected for 10–15 min loops after a 60s grace
            # window. Health is task-liveness plus actual staleness.
            item = details.get(name)
            if isinstance(info, dict) and info.get("status") == "stale" and isinstance(item, dict):
                healthy = False
                item["heartbeat"] = "stale"
    body = {"status": "healthy" if healthy else "unhealthy", "tasks": details}
    if not healthy:
        return JSONResponse(status_code=503, content=body)
    return body


@app.get("/metrics")
async def metrics(request: Request) -> Response:
    global _metrics_query_warn_mono
    token = _metrics_token()
    if request.query_params.get("token") is not None:
        now = time.monotonic()
        if now - _metrics_query_warn_mono >= _METRICS_QUERY_WARN_INTERVAL_S:
            _metrics_query_warn_mono = now
            _metrics_log.warning("/metrics query-string token rejected; use Authorization: Bearer")
    if token:
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Unauthorized")
        presented = auth[7:].strip()
        if not hmac.compare_digest(presented, token):
            raise HTTPException(status_code=401, detail="Unauthorized")
    elif settings.ENV.lower() == "production":
        _metrics_log.error(
            "/metrics requested in production without METRICS_AUTH_TOKEN set — refusing. "
            "Set METRICS_AUTH_TOKEN to enable scraping."
        )
        raise HTTPException(status_code=503, detail="Metrics endpoint not configured")

    tasks: Dict[str, asyncio.Task] = getattr(app.state, "worker_tasks", {})
    _refresh_task_gauges(tasks)
    return Response(content=render_prometheus(), media_type="text/plain; version=0.0.4")
