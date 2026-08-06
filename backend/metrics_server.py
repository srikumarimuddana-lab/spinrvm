"""Private, unauthenticated Prometheus listener on a second port.

Why this exists
---------------
Fly's built-in Prometheus scrapes an app's own metrics when ``fly.toml``
declares a ``[metrics]`` block — but **the scraper cannot send an
``Authorization`` header**. The public ``/metrics`` on the app port is
token-gated and fail-closed in production, so Fly would receive 401 (token
set) or 503 (token unset) and collect nothing.

Fly's documented answer is port isolation rather than authentication: expose
metrics on a port that is **absent from ``[http_service]``**. fly-proxy only
routes ports declared there, so an undeclared port is unreachable from the
internet and visible only on the private 6PN WireGuard mesh — which is exactly
where the scraper connects from. The endpoint is unauthenticated because it is
unroutable, not because the data stopped being sensitive.

**The public port-8000 ``/metrics`` is unchanged and stays token-gated.** This
is additive: a second door on a private hallway, not a wider public one.

Opt-in
------
Starts only when ``METRICS_PORT`` is set, so local development, tests, and the
Railway deploy are byte-identical to before unless someone opts in.

Single-process assumption
-------------------------
Only one process may bind the port. This is safe under ``UVICORN_WORKERS=1``
(set in ``fly.toml`` so that one scrape target equals one counter set — see the
change log on worker collapse). If workers > 1, the first worker binds and the
rest log the conflict and continue serving normally; the app must never fail to
boot because a metrics port was taken.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("spinr.metrics")

DEFAULT_METRICS_PORT = 9091


def metrics_port() -> int:
    """Resolve METRICS_PORT. 0/unset/invalid ⇒ disabled (returns 0)."""
    raw = (os.getenv("METRICS_PORT") or "").strip()
    if not raw:
        return 0
    try:
        port = int(raw)
    except ValueError:
        logger.error("METRICS_PORT=%r is not an integer — private metrics listener disabled", raw)
        return 0
    if not (1 <= port <= 65535):
        logger.error("METRICS_PORT=%d out of range — private metrics listener disabled", port)
        return 0
    return port


def build_metrics_app():
    """A minimal ASGI app exposing only GET /metrics.

    Deliberately not the main FastAPI app: binding the whole API to a second
    port would expose every authenticated route on an unauthenticated listener.
    Only the metrics route exists here, so there is nothing else to reach.
    """
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    async def _metrics(_request):
        from utils.metrics import render_prometheus
        from utils.scrape_gauges import refresh_all

        # Same refresh the public endpoint runs, so both report identical
        # freshness. Never raises by contract.
        await refresh_all()
        return PlainTextResponse(
            render_prometheus(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return Starlette(routes=[Route("/metrics", _metrics, methods=["GET"])])


async def serve_metrics(port: int) -> None:
    """Run the private metrics listener until cancelled.

    Binds 0.0.0.0 so Fly's scraper can reach it over 6PN. That is not a public
    exposure: fly-proxy routes only ports declared in [http_service], and this
    port is deliberately not one of them.

    **This coroutine never returns normally.** ``lifespan._restartable`` wraps
    background tasks in ``while True: await coro_factory()``, so returning would
    immediately re-invoke us — a tight loop re-attempting a bind that is already
    known to fail, flooding the logs. On an unrecoverable bind error we park
    instead. A genuine crash is still re-raised so ``_restartable`` can apply
    its 5 s backoff and retry, which is the behaviour we do want there.
    """
    import asyncio

    import uvicorn

    config = uvicorn.Config(
        app=build_metrics_app(),
        host="0.0.0.0",  # noqa: S104 - private port, see docstring
        port=port,
        log_level="warning",
        access_log=False,  # a 15s scrape would otherwise flood the log drain
    )
    server = uvicorn.Server(config)

    try:
        logger.info("Private metrics listener starting on :%d/metrics", port)
        await server.serve()
    except asyncio.CancelledError:
        raise
    except OSError:
        # Almost always "address already in use" from a second uvicorn worker.
        # Retrying cannot help while another process holds the port, so park
        # rather than spin. Logged at error, never fatal: a missing metrics
        # listener must not take the API down.
        logger.error(
            "Private metrics listener could not bind :%d; app continues without it. "
            "With UVICORN_WORKERS>1 only the first worker can bind.",
            port,
            exc_info=True,
        )
    except Exception:
        # Unexpected — let _restartable log, back off 5s, and try again.
        logger.error("Private metrics listener crashed on :%d", port, exc_info=True)
        raise

    # Reached after a bind failure or a clean serve() exit. Park until the task
    # is cancelled at shutdown, so _restartable cannot busy-loop on us.
    await asyncio.Event().wait()
