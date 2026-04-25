# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- After modifying code files in this session, run `python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"` to keep the graph current
- The package is published on PyPI as `graphifyy` (double-y), but imports as `graphify`. Install with `pip install graphifyy` if the rebuild command fails with `ModuleNotFoundError`.
- `graphify-out/cache/` is the per-file extraction cache and is gitignored (regenerated on rebuild). The tracked outputs are `graph.json`, `GRAPH_REPORT.md`, and `manifest.json`.

## Project Overview

Spinr is a Canadian ride-sharing platform (Saskatchewan-first, 0% driver commission). It consists of five surfaces:

| Surface | Tech | Purpose |
|---|---|---|
| `backend/` | Python 3.12 + FastAPI | Business logic, auth, dispatch, payments |
| `rider-app/` | React Native (Expo SDK 54) | Passenger booking, wallet, payments |
| `driver-app/` | React Native (Expo SDK 54) | Ride acceptance, navigation, earnings |
| `admin-dashboard/` | Next.js 16 | Fleet ops, analytics, corporate management |
| `shared/` | TypeScript | Shared API client, stores, types (`@spinr/shared`) |

## Commands

### Backend (Python/FastAPI)

```bash
cd backend
pip install -r requirements.txt
python3 -m backend.server          # run from repo root
pytest                             # full suite with coverage
pytest -m unit                     # unit tests only
pytest -m "not slow"               # skip slow tests
ruff check .                       # lint
ruff format .                      # format
```

### Rider App / Driver App

```bash
cd rider-app   # or driver-app
yarn install
yarn start          # Expo dev server
yarn test           # Jest
yarn test:coverage
yarn lint           # ESLint via expo lint
```

### Admin Dashboard

```bash
cd admin-dashboard
npm ci
npm run dev         # Next.js dev server
npm run build
npm test            # Vitest unit tests
npm run test:e2e    # Playwright E2E
npm run lint
```

### Database Migrations

```bash
cd backend
python migrate.py --env production   # ordered SQL runner over backend/migrations/
```

## Architecture

### System Topology

```
Rider App ──┐
Driver App ─┤── REST + WebSocket ──► FastAPI (Railway)
Admin ───────┘                            │
                             Supabase(Postgres+RLS)  Redis  Stripe
                             Firebase  Twilio  FCM
```

Backend is a single horizontally-scalable process. All durable state lives in Supabase; ephemeral cache/pub-sub lives in Redis. WebSocket fan-out across replicas uses the `spinr:ws:dispatch` Redis pub/sub channel.

### Key Backend Files

- `backend/server.py` — app factory; mounts ~25 routers
- `backend/core/config.py` — pydantic-settings `Settings`; fails fast in production on weak secrets
- `backend/core/lifespan.py` — startup/shutdown: DB health check + spawns 7 background asyncio loops (surge engine, scheduled dispatch, payment retry, document expiry, corporate auto-topup, low-balance nudge, allowance reset)
- `backend/core/middleware.py` — CORS, security headers, rate limiting (SlowAPI + Redis)
- `backend/db_supabase.py` — ~66 helper functions wrapping `supabase-py` via `run_sync()` (thread-pool with one retry on H2 GOAWAY)
- `backend/socket_manager.py` — `ConnectionManager` (in-process WS registry); delegates to Redis pub/sub when active
- `backend/routes/` — one file per domain (rides, drivers, auth, payments, wallet, corporate, fares, notifications, websocket, …)
- `backend/routes/admin/` — 15+ admin-only endpoints
- `backend/services/` — thin service layer for dispatch, fare, corporate wallet/membership/allowance
- `backend/utils/` — cross-cutting: `surge_engine.py`, `redis_client.py`, `ws_pubsub.py`, `rate_limiter.py`, `crypto.py`, `audit_logger.py`, `payment_retry.py`, `scheduled_rides.py`

## Critical Conventions

**Money arithmetic** — use Python `Decimal` only (never float). Helpers `_d()`, `_round()`, `_f()` must be used before any DB write or API response. A pre-commit hook blocks float arithmetic in fare code.

**Dual import pattern** — every backend module uses:
```python
try:
    from .routes.rides import ...
except ImportError:
    from routes.rides import ...
```
This is intentional (`python -m backend.server` vs top-level). Do not simplify away.

**Ride state machine** — always guard transitions with `_require_ride_in_state()`. `CANCELLED` is only valid before `TRIP_STARTED`. State changes must emit a WebSocket event.

**Race condition guard for ride acceptance** — the Supabase update filters on `{'status': 'searching'}`. Zero rows returned → ride already taken → send `ride_taken` WS event, return 409.

**JWT trust model** — admin JWTs are fully trusted (role+email+modules in claims). Rider/driver role is always re-read from the `users` table on every request; never trust the JWT role claim for non-admin tokens.

**Stripe idempotency** — call `claim_stripe_event(event_id)` in the `stripe_events` table before processing any webhook; silently skip if already claimed.

**OTP security** — OTPs are SHA-256 hashed at rest; 5 failures/hour triggers a 24-hour Redis lockout. Dev bypass `"1234"` only works when `ENV != production`.

**Redis transparency** — `utils/redis_client.py` falls back to an in-process dict when `REDIS_URL` is unset. Rate-limit and OTP lockout state are lost on restart in this mode.

**WebSocket auth** — first message must be `{"type": "auth", "token": "<jwt>"}`. Connection keys: `"driver_{user_id}"` / `"rider_{user_id}"`. 30-second ping heartbeat; 30 msg/s rate limit; 64 KB max message.

**Background task safety** — the 7 startup loops run on every replica concurrently. Dispatch uses an atomic DB claim; others use `reminder_sent` flags or idempotency keys. Any new loop must be replay-safe.

**Settings in DB** — Stripe keys, Twilio credentials, and Google Maps API keys live in the `app_settings` Supabase table (managed via admin dashboard), not in `.env`. This allows rotation without redeployment.

**Corporate billing layer** — sits on top of the consumer ride product without modifying ride/driver logic. Payment source selection (rider wallet / card / company allowance / master wallet fallback) happens at fare settlement. All wallet deltas go through the `corporate_wallet_apply_delta` Postgres function for row-level locking and idempotency.

**Token lifetimes** — access tokens: 15 min (rider/driver), 12 hr (admin). Refresh tokens: 30 days, stored as SHA-256 hash, rotated on every use. Mobile clients auto-retry 401s via Axios interceptor after token refresh.

**Do not silently swallow errors** — especially DB, auth, payment, and dispatch errors. These are crucial to the system and must surface loudly so the root cause can be fixed, not masked. Rules:
- Never replace a failing call with a generic fallback path that hides the symptom (e.g. don't fall through to "create new user" when `get_user_by_phone` raises — that produced duplicate accounts).
- Never `logger.warning(...)` and continue on a DB/auth/payment error. Use `logger.error(...)` with the full underlying exception (for `DatabaseError`, include `e.details["original"]` — `str(e)` alone gives only "Database operation failed").
- Return a clean `HTTPException` (usually 503 for DB, 502 for upstream) so the client retries, instead of handing back a half-valid response.
- Before silencing or softening any error during development, STOP and ask the user. "Soft-handling" is a trade-off they get to decide, not a default.

## Required Environment Variables

**Backend** (`backend/.env`):
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`
- `JWT_SECRET` — must be ≥32 chars; startup fails in production with weak value
- `FIREBASE_SERVICE_ACCOUNT_JSON` — stringified JSON
- `ADMIN_PASSWORD` — must not be `admin123` in production
- `REDIS_URL`, `RATE_LIMIT_REDIS_URL`, `WS_REDIS_URL` — optional in dev (in-memory fallback)

**Rider App** (`rider-app/.env`):
- `EXPO_PUBLIC_BACKEND_URL`
- `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY`

## Deployment

- **Backend**: Railway (auto-deploy from `main`; Render fallback)
- **Frontend/Admin**: Vercel
- **Mobile builds**: Expo EAS — only triggered when commit message contains `[build]`

## Agent Framework (`agents/`)

Python SDK for multi-agent development automation. **Not part of the production runtime** — used for code review, testing, documentation, and deployment orchestration during development.

| Module | Class | Role |
|--------|-------|------|
| `base_agent.py` | `BaseAgent` | Abstract base: task queue, message bus, knowledge entries |
| `orchestrator.py` | `OrchestratorAgent` | Top-level coordinator: decomposes tasks, assigns to specialists |
| `registry.py` | `AgentRegistry` | Single entry-point: initialise all agents, submit tasks |
| `code_reviewer.py` | `CodeReviewerAgent` | Static analysis and best-practice checks |
| `tester.py` | `TestingAgent` | Test generation and coverage analysis |
| `security_agent.py` | `SecurityAgent` | Vulnerability scanning |
| `backend_agent.py` | `BackendAgent` | FastAPI / Supabase domain specialist |
| `frontend_agent.py` | `FrontendAgent` | React Native / Expo domain specialist |
| `deployer.py` | `DeploymentAgent` | CI/CD and Railway/EAS deployment tasks |
| `documenter.py` | `DocumentationAgent` | Doc generation and CLAUDE.md maintenance |
| `knowledge_base.py` | `KnowledgeBaseAgent` | Shared knowledge store for all agents |
| `cli.py` | — | CLI entry-point (`python -m agents.cli`) |

**Graphify coverage** — `OrchestratorAgent` and `AgentRegistry` are high-centrality god nodes in the graphify graph (community 0). Read `graphify-out/GRAPH_REPORT.md` before making cross-agent changes.

## Claude-Adjacent Directories

These directories exist alongside `.claude/` but serve different tooling:

| Directory | Status | Purpose |
|-----------|--------|---------|
| `.kilo/` | Active | Kilo Code AI assistant config |
| `.emergent/` | Active | Emergent AI agent config |
| `.maestro/` | Active | Maestro orchestration config |
| `audit-framework/` | Active | Shared audit scripts for all AI assistants |
| `memory/` | Stale | Originally for agent memory; contains only `.gitkeep` — can be archived |
| `discovery/` | Stale | Early Expo sandbox; unreferenced by any surface — can be archived |
