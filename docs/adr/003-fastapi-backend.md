# ADR-003: FastAPI as the backend framework

**Date:** 2026-02-14
**Status:** Accepted

---

## Context

The backend must serve REST endpoints and persistent WebSocket connections simultaneously. It is the sole integration point for Supabase, Redis, Stripe, Twilio, Firebase Admin, and Google Maps. Requirements:

- Async I/O throughout — WebSocket fan-out and multiple concurrent Supabase calls per request
- Automatic OpenAPI schema generation (used by the admin dashboard and for the `@spinr/shared` API client)
- Pydantic-based request/response validation to catch contract violations at the boundary
- A clear path to horizontal scaling (Railway replicas + Redis pub/sub for WS fan-out)
- Python 3.12 — the team is more productive in Python for backend work than in Node/Go

Alternatives considered:

| Option | Rejected because |
|--------|-----------------|
| Django REST Framework | Synchronous by default; WebSocket support requires Channels + Daphne complexity |
| Flask + Flask-SocketIO | No native async; OpenAPI support is bolted on; less type-safety |
| Node.js (Express / Hono) | Would require the team to context-switch to a second runtime for backend work |
| Go (Gin / Fiber) | No team familiarity; longer ramp-up for initial prototype |
| Litestar | Solid async framework but smaller community; fewer Supabase/Stripe integration examples |

---

## Decision

Use **FastAPI 0.115** with **Uvicorn** (ASGI) as the backend framework, deployed with `--workers 4` on Railway.

Key implementation details:
- All ~25 routers are mounted in `backend/server.py` via `app.include_router()`.
- Pydantic v2 models are defined in `backend/schemas.py`; route handlers declare typed request bodies and response models, producing a complete `/openapi.json`.
- The `supabase-py` client is synchronous; all calls are offloaded to a thread pool via `run_sync()` in `db_supabase.py` to prevent blocking the async event loop.
- WebSocket connections are managed by `socket_manager.py` (`ConnectionManager`), with Redis pub/sub (`spinr:ws:dispatch` channel) used for cross-replica fan-out.
- Background tasks (surge engine, scheduled dispatch, payment retry, document expiry, corporate auto-topup, low-balance nudge, allowance reset) are spawned as `asyncio` loops in `backend/core/lifespan.py` using the FastAPI lifespan context manager.
- SlowAPI (Redis-backed) provides per-endpoint rate limiting; the `RequestIDMiddleware` injects `X-Request-ID` for distributed tracing.

---

## Consequences

**Positive:**
- Native async support handles hundreds of concurrent WebSocket connections without thread-per-connection overhead.
- OpenAPI schema is always in sync with the implementation; no separate schema maintenance.
- Pydantic v2 validation is ~5–10× faster than v1 and catches type errors before they reach the DB layer.
- `--workers 4` provides parallelism at the process level; each worker has its own thread pool for `run_sync()` calls.

**Negative / trade-offs:**
- The synchronous `supabase-py` client means DB calls occupy thread-pool threads. Under sustained load this can exhaust the default `ThreadPoolExecutor`. The current mitigation is `--workers 4` and observed sufficiency at Saskatchewan-scale load; a future async Supabase driver would eliminate this.
- FastAPI's background task model (lifespan loops) means every Railway replica runs all 7 background loops independently. Each loop must be replay-safe and use atomic DB claims or idempotency keys to avoid double-processing.
- Upgrading FastAPI or Pydantic major versions typically requires updating all schema models and route signatures — estimated 2–4 days for a v2→v3 Pydantic migration.
