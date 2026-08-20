# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Working Style

### Task decomposition (mandatory)
- Before starting any implementation, break it into subtasks of ≤ 3 files each.
- Use `TodoWrite` to track every subtask; mark done immediately after each commit.
- Never start the next subtask until the current one is committed.
- If a task touches > 5 files, use `/plan` to decompose it first.

### Context / token discipline
- Keep responses short; avoid re-reading files you already read this session.
- When context grows large (many tool calls in session), prefer targeted `grep`/`Read` with `offset`+`limit` over full file reads.
- If you hit a "prompt too long" error: stop, commit current work, summarize progress in one sentence, then continue in a fresh thought — do NOT retry the same giant prompt.

### Request timeouts
- For long-running bash commands (installs, full test suites), use `run_in_background: true`; never spin in a sleep loop waiting for output.
- Break large pip/npm installs into separate steps so a single timeout doesn't block everything.

### Batch size rule
- Limit each commit to one logical change. If a diff exceeds ~200 lines, split it.

## Context Imports

Sprint-scoped and domain-deep context is loaded on demand, not baked into this file. Reference these when the task enters the relevant area:

- `@.Codex/context/sprint-current.md` — active sprint goal, in-flight tickets, blockers
- `@.Codex/context/domain-dispatch.md` — dispatch algorithm, driver matching, offer timeout
- `@.Codex/context/domain-payments.md` — fare calc, surge, Stripe flows, corporate billing
- `@.Codex/context/domain-safety.md` — SOS, insurance periods, emergency flows
- `@.Codex/context/regulatory-sk.md` — Saskatchewan Transportation Act obligations

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
python -m backend.scripts.run_migrations   # ordered SQL runner over backend/migrations/;
                                            # requires DATABASE_URL. Add --dry-run to
                                            # preview, --status to show applied vs pending.
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
- `backend/core/lifespan.py` — startup/shutdown: DB health check + spawns 16 background asyncio loops (subscription expiry, surge engine, scheduled dispatch, payment retry, document expiry, corporate auto-topup, low-balance nudge, allowance reset, safety check-in, retention purge, reconciliation, Stripe reconcile, T4A annual job, stuck-ride sweeper, push retry, loop watchdog)
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

**Ride state machine** — always guard transitions with `_require_ride_in_state()`. `cancelled` is only valid before `in_progress`. State changes must emit a WebSocket event.

Valid states and transitions (source: `backend/routes/rides.py`):

```
                ┌─► cancelled (rider/driver/system, pre-trip only)
                │
scheduled ──► searching ──► driver_assigned ──► driver_accepted ──► driver_arrived ──► in_progress ──► completed
                │                │
                │                └─► searching (offer timeout, ~15s, releases driver)
                │
                └─► cancelled (auto, no drivers found after ~5min)
```

Invariants:
- `active_statuses = ["searching", "driver_assigned", "driver_accepted", "driver_arrived", "in_progress"]` — a rider may have at most one active ride at a time
- Transitions from `in_progress` are `completed` only. Never `cancelled` after trip start.
- `scheduled` rides skip `searching` until their dispatch time, then enter `searching` via the scheduled-dispatch background loop
- Race on acceptance: Supabase update filters `{'status': 'searching'}` — 0 rows → ride taken → 409 + `ride_taken` WS event
- Every state change must emit a WebSocket event keyed to both the rider and the driver connection (if assigned)

When writing code that reads `ride.status`, treat any value not in the set above as a contract violation — surface loudly.

**Race condition guard for ride acceptance** — the Supabase update filters on `{'status': 'searching'}`. Zero rows returned → ride already taken → send `ride_taken` WS event, return 409.

**JWT trust model** — admin JWTs are fully trusted (role+email+modules in claims). Rider/driver role is always re-read from the `users` table on every request; never trust the JWT role claim for non-admin tokens.

**Driver online/available flags** — `is_online` is driver-toggled (a driver tapped "Go online"); `is_available` is system-computed (`is_online AND not on active ride AND not in offer-pending`). The invariant **`is_available ⇒ is_online`** must hold; the inverse does not. Dispatch reads `is_available`; admin filters read `is_online`. Never set `is_available = True` without `is_online = True`.

**Stripe idempotency** — call `claim_stripe_event(event_id)` in the `stripe_events` table before processing any webhook; silently skip if already claimed.

**OTP security** — OTPs are SHA-256 hashed at rest; 5 failures/hour triggers a 24-hour Redis lockout. Dev bypass `"1234"` only works when `ENV != production`.

**Redis transparency** — `utils/redis_client.py` falls back to an in-process dict when `REDIS_URL` is unset. Rate-limit and OTP lockout state are lost on restart in this mode.

**WebSocket auth** — first message must be `{"type": "auth", "token": "<jwt>"}`. Connection keys: `"driver_{user_id}"` / `"rider_{user_id}"`. 30-second ping heartbeat; 30 msg/s rate limit; 64 KB max message.

**Background task safety** — the 16 startup loops run on every replica concurrently. Dispatch uses an atomic DB claim; others use `reminder_sent` flags or idempotency keys. Any new loop must be replay-safe.

**Settings in DB** — Stripe keys, Twilio credentials, and Google Maps API keys live in the `app_settings` Supabase table (managed via admin dashboard), not in `.env`. This allows rotation without redeployment.

**Corporate billing layer** — sits on top of the consumer ride product without modifying ride/driver logic. Payment source selection (rider wallet / card / company allowance / master wallet fallback) happens at fare settlement. All wallet deltas go through the `corporate_wallet_apply_delta` Postgres function for row-level locking and idempotency.

**Surge pricing rules** (source: `backend/utils/surge_engine.py`):

Auto-mode tiers (demand / supply ratio → multiplier):
| Ratio | Multiplier |
|---|---|
| < 0.5 | 1.0× (normal) |
| 0.5 – 0.8 | 1.25× |
| 0.8 – 1.2 | 1.5× |
| 1.2 – 2.0 | 1.75× |
| 2.0 – 3.0 | 2.0× |
| ≥ 3.0 | 2.5× (HARD CAP) |

- `SURGE_CAP = 2.5` is the ceiling for auto mode. Never suggest raising it without explicit business + legal review.
- Surge engine runs every 2 minutes; updates only service areas where `surge_source == 'auto'`
- Admin manual override accepts 1.0–10.0 but any value > 2.5 requires documented justification (regulatory + reputational risk)
- Surge must be visible to the rider *before* booking — never apply retroactively
- Never apply surge to scheduled rides booked outside the surge window
- Surge does not apply to corporate account-paid rides (policy; verify in fare service)

**Token lifetimes** — access tokens: 15 min (rider/driver), 12 hr (admin). Refresh tokens: 30 days, stored as SHA-256 hash, rotated on every use. Mobile clients auto-retry 401s via Axios interceptor after token refresh.

**Insurance periods (TNC commercial insurance)** — every moment a driver spends in the app maps to one of four periods. Misclassification is a regulatory and insurance liability. Derive period from ride state, not from the driver UI:

| Period | Driver state | Ride state | Insurance layer |
|---|---|---|---|
| 0 | App off / offline | — | Personal auto only |
| 1 | App on, available | No assigned ride | TNC contingent liability |
| 2 | En route to pickup | `driver_assigned` or `driver_accepted` or `driver_arrived` | TNC primary commercial |
| 3 | Passenger aboard | `in_progress` | TNC primary commercial (full coverage) |

Rules:
- Every period transition is logged to `driver_insurance_periods` with `{driver_id, period, started_at, ended_at, ride_id?}` for regulatory audit
- Never delete or mutate period rows — append only
- Period 2 starts on `driver_assigned` (not `driver_accepted`) because the driver is already obligated to the ride
- A driver cannot be in Period 3 without a `ride_id` linking to an `in_progress` ride
- Document expiry (license, insurance, vehicle registration) blocks Period 1+ — checked on every `go_online` call

**Do not silently swallow errors** — especially DB, auth, payment, and dispatch errors. These are crucial to the system and must surface loudly so the root cause can be fixed, not masked. Rules:
- Never replace a failing call with a generic fallback path that hides the symptom (e.g. don't fall through to "create new user" when `get_user_by_phone` raises — that produced duplicate accounts).
- Never `logger.warning(...)` and continue on a DB/auth/payment error. Use `logger.error(...)` with the full underlying exception (for `DatabaseError`, include `e.details["original"]` — `str(e)` alone gives only "Database operation failed").
- Return a clean `HTTPException` (usually 503 for DB, 502 for upstream) so the client retries, instead of handing back a half-valid response.
- Before silencing or softening any error during development, STOP and ask the user. "Soft-handling" is a trade-off they get to decide, not a default.

## Database & Migration Conventions

Migrations live in `backend/migrations/` and are applied in filename order by `backend/scripts/run_migrations.py` (a second runner, `backend/scripts/migrate.py`, targeted an older schema that was never actually applied to production — deleted; see `CLAUDE.md`'s Database Migrations section and `ACTION_ITEMS.md` A39).

Naming: `NN_short_description.sql` where `NN` is a zero-padded sequence number (currently highest applied is `101_users_add_is_rider.sql`; **next free slot is `102`**). Pick the next available number — never reuse or reorder existing numbers. If two PRs conflict on a number, the second one renames to the next free slot before merge. Note: the runner uses the full filename as the idempotency key, so already-applied migrations must never be renamed. (Pre-existing duplicate prefixes at 08, 28, 29, 48, 50, 51, 52, 54, 55, 56, 57, 58, 91, 92, 96 are handled by full-filename keying — do not introduce new duplicates; a CI prefix-uniqueness check blocks them.)

Migration rules:
- **Append-only**: never edit a merged migration. Schema changes go in a new file.
- **Forward-compatible**: every migration must be safe to run against production traffic in flight. Wrap long-running `ALTER TABLE` in batched updates.
- **Always reversible on paper**: put the rollback plan in a top comment, even if no down-migration file.
- **RLS first**: every new table that stores user data must ship with RLS policies in the same migration.
- **Indexes for new query patterns**: if you add a `WHERE foo = ?` or `ORDER BY foo`, add the index in the same migration.

Table naming:
- Lowercase, snake_case, plural (`rides`, `drivers`, `corporate_allowances`)
- Junction tables: `<a>_<b>` alphabetical (`corporate_member_rides`)
- Audit tables: `<entity>_audit` or `<entity>_events` (append-only, no updates)

RLS policy pattern:
- Every user-data table has `SELECT` restricted to `auth.uid() = user_id` or role-based equivalents
- `INSERT` / `UPDATE` / `DELETE` explicitly enumerated — never `FOR ALL` on user-writable tables
- Service role (backend) bypasses RLS by design; the frontend anon key must never touch user data directly

Postgres functions for mutating money or credits: call from backend only, never from client. All money-touching functions must be `SECURITY DEFINER` with explicit `search_path` pinning.

## Background Loop Recipe

The 16 startup loops in `core/lifespan.py` all run on every replica simultaneously. A new loop must satisfy the replay-safety contract or it will cause duplicate writes, charges, or notifications.

Template for a new loop:

```python
# backend/utils/my_loop.py
async def my_loop() -> None:
    """One-line purpose. Interval. What state it reads/writes."""
    while True:
        try:
            await _tick()
        except Exception:
            logger.error("my_loop tick failed", exc_info=True)
        await asyncio.sleep(INTERVAL_SECONDS)

async def _tick() -> None:
    # 1. Query candidates with a filter that excludes already-processed rows
    # 2. For each candidate, attempt an atomic claim (UPDATE ... WHERE reminder_sent = false RETURNING *)
    # 3. Only act on rows where the claim returned a row (other replicas got zero)
    # 4. Do the side-effect (notify, charge, dispatch)
    # 5. On failure, don't re-queue — idempotency key or claim flag prevents replay
    ...
```

Replay-safety options (pick one):
- **Claim flag column** (`reminder_sent`, `auto_approved_this_period`) — preferred for simple cases
- **Idempotency key** (`stripe_events.event_id`) — for external-system interactions
- **Atomic DB claim** (`UPDATE ... WHERE status='pending' RETURNING *`) — for dispatch-style work queues
- **Redis leader lock** (`SET NX EX`) — only for loops that genuinely must run on one replica

Forbidden: in-process locks, filesystem flags, "this pod is primary" environment logic.

## Observability Conventions

Logging:
- Python: `logger = logging.getLogger(__name__)` per module. Use structured context via `extra={...}`.
- Log levels: `error` for actionable failures, `warning` for recoverable anomalies, `info` for state transitions, `debug` gated behind env flag.
- Never `print()` in production code. Never `logger.warning(...)` and continue on a DB/auth/payment error.

Sentry tags (attach to every captured event):
- `domain`: one of `dispatch`, `payments`, `auth`, `corporate`, `safety`, `drivers`, `rides`, `admin`
- `surface`: one of `backend`, `rider-app`, `driver-app`, `admin`
- `ride_id`, `driver_id`, `rider_id` — only IDs, never PII
- `env`: `production` / `staging` / `development`

Metric naming (`spinr.<domain>.<metric>.<unit>`):
- `spinr.dispatch.offer_sent.count`
- `spinr.dispatch.offer_to_accept.duration_ms`
- `spinr.fare.calc.duration_ms`
- `spinr.payment.settlement.count{outcome=success|failed|retry}`
- `spinr.ws.fanout.duration_ms`

What to log vs metric vs Sentry:
- State transitions → info log + metric
- User-visible errors → Sentry (with domain tag) + error log
- Degraded-but-recovered → warning log + metric (never Sentry — noise)
- Security-relevant events (auth failures, RLS denials, admin actions) → audit table + info log

## Testing Conventions

Test files live in `backend/tests/` (Python) or `<app>/__tests__/` (RN/Next.js).

Python:
- Use `@pytest.mark.anyio` for async tests (loaded explicitly in `conftest.py`)
- Mock Supabase via the `mock_supabase_client` fixture in `conftest.py` — don't hit the real DB in unit tests
- Data factories live in `backend/tests/_factories.py` (kept out of `conftest.py` because pytest loads conftest by file path, which breaks `from tests.conftest import ...`)
- Patch target for DB is always `backend.db_supabase.supabase` — match that path exactly
- Use `pytest -m unit` for fast local loop; `pytest -m "not slow"` in pre-push; full suite in CI

Test tiers:
- **Unit**: single function, all deps mocked. Target: < 100 ms per test.
- **Integration**: real Supabase against a throwaway test schema. Target: < 2 s per test.
- **E2E (ride lifecycle)**: full searching → completed flow with mock payments. Keep in `test_e2e_*.py`.
- **Performance (perf_baseline.py)**: benchmark critical paths; compare against `perf_*_before.json` baselines to detect regressions.

Coverage minimums (per domain):
- `routes/payments.py`, `services/fare_service.py`, `utils/crypto.py`: ≥ 90%
- `routes/rides.py`, `services/dispatch_service.py`: ≥ 80%
- Admin routes, utilities: ≥ 70%

What must have a test:
- Every new state transition (add a case to `test_ride_state_machine.py`)
- Every fare calculation branch (tiers, surge, corporate, promo)
- Every auth/RLS policy (both allowed and denied paths)
- Every Stripe webhook type before hitting production

## Performance SLAs

Target P95 latencies per critical path. Code that risks breaching them should be flagged in review:

| Path | Target P95 | Failure impact |
|---|---|---|
| Dispatch offer → driver phone notification | < 2 s | Ride abandonment |
| Fare estimate (rider tap → price shown) | < 300 ms | Booking friction |
| Fare settlement (trip end → receipt) | < 1 s | Rider wait on arrival |
| WebSocket event fan-out (backend → client) | < 100 ms | Missed state updates |
| Driver location update (write) | < 150 ms | Stale ETA |
| Auth token refresh | < 200 ms | UX stutter |
| Stripe webhook processing | < 500 ms | Payment backlog |
| Migration apply (prod window) | < 30 s | Deploy stall |

Anti-patterns that reliably breach SLAs:
- N+1 Supabase reads in a loop (batch via `.in_()` instead)
- Awaiting Twilio/Stripe inline in a request handler (queue via `asyncio.create_task` or background worker)
- Reading full ride list on dashboards without pagination
- WebSocket broadcast to all connections instead of targeted fan-out

## Saskatchewan Regulatory

Spinr operates under Saskatchewan Government Insurance (SGI) and the province's ride-share regulations. The detailed checklist lives in `@.Codex/context/regulatory-sk.md`; the non-negotiables appear below.

Driver eligibility (enforced at onboarding + every `go_online`):
- Valid Class 5 driver's license (standard) — Class 1-4 drivers need separate approval
- Minimum 3 years licensed driving experience
- Clean abstract: no major violations in past 3 years, no Criminal Code driving offences
- Vehicle < 10 years old; passes annual inspection
- Ride-share endorsement on insurance (SGI Auto Fund)
- Criminal Record Check + Vulnerable Sector Check on file, renewed annually

Trip log retention (regulatory, cannot be overridden by PIPEDA deletion requests):
- Trip record: 7 years (financial/tax)
- Driver/vehicle linkage at trip time: 7 years
- GPS trace at pickup and dropoff (not entire route): 3 years
- Insurance period transitions for commercial coverage audit: 7 years

Tax:
- Rider receipts must show GST (5%) and PST (6% where applicable) as separate line items
- Driver earnings summary must be T4A-compatible at year end (annual threshold applies)
- Fare floor subject to municipal minimum (if any); surge cap is provincial (ours is tighter at 2.5×)

Accessibility:
- Wheelchair-accessible vehicle (WAV) requests must be supported if a WAV driver is online in the service area
- Service animal accommodation is mandatory; drivers cannot refuse
- App must meet WCAG 2.1 AA for customer-facing surfaces

Driver classification:
- Drivers are independent contractors, not employees. Language in onboarding, training, and app copy must reflect this. Any control-of-work language (mandatory shifts, required uniforms, employee benefits) triggers re-classification risk and must be reviewed before shipping.

## Compliance (PIPEDA)

Spinr operates under Canada's Personal Information Protection and Electronic Documents Act. Every data flow must respect these rules:

Data minimization:
- Collect only what is needed to provide the ride. Tie every new field to a stated purpose.
- Driver's full address: needed for background check → stored encrypted in `drivers`. Not shared with riders.
- Rider's home/work addresses: stored only if saved as a favorite by the user. Never inferred.

What can NEVER appear in logs, Sentry events, or analytics payloads:
- Raw GPS coordinates (lat/lng) — log geohashed area at most
- Full phone numbers — use last-4 if you must (`phone_last4`)
- Full names — use user_id
- Email addresses — use user_id
- Payment card numbers — Stripe handles; never log even masked PANs
- Government IDs, SIN, driver license numbers
- Exact pickup/dropoff addresses — log city/area only

Data residency:
- Supabase project must be in a Canadian region (ca-central-1 or equivalent). Changing regions is a compliance event — never do without legal sign-off.
- All primary storage (Stripe customer data, Firebase) must be region-matched or justify exception.

User rights:
- **Access**: rider/driver can request full data export via Support → backend generates JSON dump
- **Correction**: profile fields are self-serve; non-trivial corrections go through Support
- **Deletion**: right-to-delete retains only what the Saskatchewan Transportation Act requires (see regulatory section below). All other PII is scrubbed within 30 days. Ride records become anonymized (user_id nulled, coordinates rounded to city).
- **Consent**: consent language version is stored on signup. Material changes require re-consent.

Breach protocol:
- Any suspected PII exposure (wrong user saw another user's data, leaked logs, RLS bypass) is a P0 incident
- Within 24h: scope assessment, log capture, preserve evidence
- Within 72h: Privacy Commissioner notification if the breach poses "real risk of significant harm"
- See `docs/runbooks/data-breach.md` (to be created) for the full procedure

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

## What Spinr Is NOT

Guardrails against accidentally turning Spinr into a generic Uber clone. Suggestions that violate these should be flagged, not implemented:

- **Not** a commission-taking marketplace. Driver keeps 100% of the fare. Monetization is SaaS corporate accounts + premium rider features + partner referrals — never per-trip cuts on consumer rides.
- **Not** a surge-first product. Hard cap of 2.5× auto surge. Never introduce "dynamic pricing" that behaves like unbounded surge. Never hide surge before booking confirmation.
- **Not** a driver-control platform. Drivers are contractors; we don't dictate shifts, mandate uniforms, or penalize offline time. Features that nudge toward control-of-work patterns require legal review.
- **Not** a data-harvesting product. Analytics exists to improve matching and safety — not to build advertising profiles. Never add third-party ad SDKs or behavioral retargeting.
- **Not** a hidden-fee operator. Every charge on the receipt maps to a disclosed line item: base fare, distance, time, booking fee, surge, tax, tip. Never add a "service fee" to mask the monetization model.
- **Not** a country-agnostic product. Canadian regulatory context (PIPEDA, Saskatchewan Transportation Act, SGI insurance) is baked into the design. Features that don't work in that context aren't features.
- **Not** a 911 replacement. SOS notifies emergency contacts and our safety team and *offers* one-tap 911; it never auto-dials and never claims to replace calling emergency services.

## KPI Targets

Production health is measured against these targets. Code that risks breaching them should be flagged in review. Pull current values via `/kpi`.

| Metric | Target | Below-target signal |
|---|---|---|
| Match rate (rides requested → driver accepted) | ≥ 85% | Dispatch radius too tight or driver supply gap |
| Rider cancellation rate | ≤ 8% | Long wait or wrong ETA |
| Driver cancellation rate | ≤ 3% | Offer quality or fare transparency issue |
| Driver utilization (on-trip time / online time) | ≥ 55% | Supply-demand mismatch; surge engine should activate |
| P95 dispatch latency (offer → accept) | < 2 s | Matching engine or WS latency |
| P95 fare calc latency | < 300 ms | Upstream (Google Maps) or logic bloat |
| Payment success rate | ≥ 99% | Stripe config, card decline, 3DS flow |
| Weekly active driver retention (week-over-week) | ≥ 80% | Earnings, UX, or support issue |
| Safety incident rate | < 1 / 10k rides | Investigate every incident individually |
| Support ticket response (P1) | < 2 h | Staffing or playbook gap |

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
| `documenter.py` | `DocumentationAgent` | Doc generation and AGENTS.md maintenance |
| `knowledge_base.py` | `KnowledgeBaseAgent` | Shared knowledge store for all agents |
| `cli.py` | — | CLI entry-point (`python -m agents.cli`) |

**Graphify coverage** — `OrchestratorAgent` and `AgentRegistry` are high-centrality god nodes in the graphify graph (community 0). Read `graphify-out/GRAPH_REPORT.md` before making cross-agent changes.

## Codex-Adjacent Directories

These directories exist alongside `.Codex/` but serve different tooling:

| Directory | Status | Purpose |
|-----------|--------|---------|
| `.kilo/` | Active | Kilo Code AI assistant config |
| `.emergent/` | Active | Emergent AI agent config |
| `.maestro/` | Active | Maestro orchestration config |
| `audit-framework/` | Active | Shared audit scripts for all AI assistants |
| `memory/` | Archived | Originally for agent memory; contained only `.gitkeep`. Deleted 2026-05-05 in commit `223ec89b0` (PR #451). |
| `discovery/` | Archived | Early Expo sandbox; was unreferenced by any surface. Deleted 2026-05-05 in commit `223ec89b0` (PR #451). |
