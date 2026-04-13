# 03 — Backend / API / Database Audit

> **Read time:** ~25 min
> **Audience:** Backend engineers, DB owner

---

## Executive verdict

FastAPI layering is clean (`routes/*` → `dependencies` → `db`), request/response schemas are Pydantic, and error handling is well-structured. The backend's **real weaknesses are not in code quality but in DB discipline, payment idempotency, real-time fan-out, and lifecycle correctness.**

---

## P0 findings

### P0-B1 — Dead code in `core/lifespan.py` skips DB health-check on boot

**Evidence:** `backend/core/lifespan.py:19-25` — block sits after `return supabase` on line 16. The supabase ping / table-existence check never executes.

**Impact:** Backend boots green even if Supabase is unreachable, schema is stale, or the service-role key is invalid. First real user request 500s, looking like a user-side incident. Reduces MTTD.

**Fix (S):**
```python
# core/lifespan.py
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        # Active health check: one simple read
        await db.table("users").select("id").limit(1).execute()
        logger.info("Supabase connection verified")
    except Exception as e:
        logger.error(f"Supabase health check failed: {e}")
        raise  # fail fast — do NOT serve traffic

    tasks = [
        asyncio.create_task(surge_recalculation_loop()),
        asyncio.create_task(scheduled_dispatcher_loop()),
        asyncio.create_task(payment_retry_loop()),
        asyncio.create_task(document_expiry_loop()),
        asyncio.create_task(subscription_expiry_loop()),
    ]
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
```

---

### P0-B2 — Stripe webhook not idempotent

**Evidence:** `backend/routes/webhooks.py` verifies signature then processes inline. There is no `stripe_events` dedup table; no `SELECT … FOR UPDATE`-style lock; no check against `event.id`.

**Impact:** Stripe's retry behavior (every 1xx error, network blip, or response >20s) replays the same event. Result:
- `payment_intent.succeeded` → ride marked paid twice → double wallet credit.
- `checkout.session.completed` for a subscription → subscription activated N times; may double-charge.
- Push notifications fire multiple times.

**Fix (S):**
```sql
CREATE TABLE stripe_events (
  event_id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  payload JSONB NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  processed_at TIMESTAMPTZ
);
```
```python
# routes/webhooks.py
event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
try:
    await db.stripe_events.insert({
        "event_id": event.id,
        "event_type": event.type,
        "payload": event.to_dict_recursive(),
    })
except UniqueViolation:
    return {"status": "duplicate", "event_id": event.id}  # already processed

# …do real work…
await db.stripe_events.update(event.id, {"processed_at": "now()"})
```
Also: add nightly **reconciliation job** (query `stripe_events where processed_at is null and received_at < now() - interval '5 min'`).

---

### P0-B3 — WebSocket session is per-process

**Evidence:** `backend/socket_manager.py` holds a dict `{user_id: WebSocket}`. Dispatch in `rides.py` calls `socket_manager.broadcast_to_drivers(area_id)`.

**Impact:** Driver A is connected to machine A. Rider's request lands on machine B. Machine B broadcasts — **only drivers on machine B receive the offer.** Dispatch recall drops to 1/N. At 2 machines = 50% miss. At 4 = 75%.

**Fix (M):**
Option 1 (quick): Redis pub/sub. `socket_manager` publishes to a channel; every machine subscribes; local process delivers to its local sockets.
```python
# socket_manager.py
async def broadcast_to_drivers(area_id, payload):
    await redis.publish(f"drivers:{area_id}", json.dumps(payload))

async def on_message_from_redis(channel, payload):
    for ws in LOCAL_SOCKETS_BY_AREA.get(channel.split(":")[1], []):
        await ws.send_json(payload)
```
Option 2 (robust): Move to Ably / Pusher / managed WebSocket layer.
Option 3 (cheapest short-term): Single machine with `min_machines_running=1`, `max_machines_running=1`, until you need scale — explicitly cap.

---

### P0-B4 — Migration files have duplicate ordering prefixes

**Evidence:** `backend/migrations/` contains `10_disputes_table.sql` **and** `10_service_area_driver_matching.sql`. No `schema_migrations` tracking table; no up/down scripts; no tooling (Alembic/Atlas/supabase-cli).

**Impact:** Order of application is non-deterministic. Re-running against a fresh DB may produce divergent schemas vs. the prod DB. Rollbacks are impossible. This has already happened — two files share prefix 10.

**Fix (M):**
1. Introduce **Alembic** (or supabase migration CLI). Every change → one revision file with `revision`, `down_revision`, `create_date`.
2. Bootstrap: generate a single "baseline" revision from the current prod schema; subsequent changes from that baseline.
3. CI step: `alembic upgrade head` against an ephemeral Postgres container, assert idempotent.
4. Document "no raw SQL in prod without a migration" in CONTRIBUTING.md.

---

## P1 findings

### P1-B5 — Database lacks FK cascade discipline

**Evidence:** `supabase_schema.sql` defines many tables; spot-checks show inconsistent `ON DELETE` clauses — some `CASCADE`, some `NO ACTION`, many omitted (defaulting to `NO ACTION`).

**Impact:**
- Deleting a user leaves orphaned `payments`, `notifications`, `wallet` rows.
- Deleting a driver while rides reference them causes either crash or ghost rows depending on which table.

**Fix (M):** Audit every FK. Decide per-table:
- PII-bearing tables: `ON DELETE CASCADE` (+ soft-delete with anonymization for audit).
- Financial tables: `ON DELETE RESTRICT` + explicit archive flow.
Write an Alembic revision enforcing the decisions.

---

### P1-B6 — N+1 risk in ride listing

**Evidence (probable):** `backend/routes/rides.py` fetches rides then, per ride, fetches `drivers`, `users`, `vehicle_types`, `fare_configs`, `ratings` in Python loops.

**Impact:** Admin dashboard ride table with 100 rows fires 500+ queries.

**Fix (S-M):**
- Use Supabase's PostgREST `select=*,driver:drivers(*),rider:users(*)` embeds.
- For admin-facing list endpoints, add a `rides_list_view` SQL view doing the join server-side.

---

### P1-B7 — No DB connection pool controls

**Evidence:** Supabase Python client used synchronously; no explicit pool settings observed.

**Impact:** Under burst, Supabase connection ceiling (max_connections = 60 on standard plan) trips. "Too many connections" errors cascade.

**Fix (M):**
- Use PgBouncer / Supavisor (Supabase's pooled endpoint `…pooler.supabase.com:6543`).
- Set `pool_mode=transaction` for short queries.
- Expose `DB_POOL_MIN`, `DB_POOL_MAX`, `DB_POOL_TIMEOUT` env vars.

---

### P1-B8 — Background tasks share the same process as API

**Evidence:** `lifespan.py` launches 5 loops in the request-serving process.

**Impact:** Bursty surge-recalc (SQL-heavy) competes with real user requests. One misbehaving task can starve Uvicorn's event loop.

**Fix (M):** Split into **worker process**:
- `backend/worker.py` entrypoint running only the background loops.
- Fly.io `processes = { app = "uvicorn …", worker = "python -m backend.worker" }`.
- `min_machines_running=1` for both.
- Remove background tasks from `lifespan.py`.

---

### P1-B9 — Health endpoint is shallow

**Evidence:** `/health` returns `{ok: true}` without checking DB, Redis, Stripe reachability.

**Impact:** Load balancers mark the instance healthy while it's functionally broken. Page alerts fire late.

**Fix (S):**
```python
@app.get("/health")
async def health():
    checks = {}
    try: await db.table("users").select("id").limit(1).execute(); checks["db"] = "ok"
    except: checks["db"] = "fail"
    try: await redis.ping(); checks["redis"] = "ok"
    except: checks["redis"] = "fail"
    status = 200 if all(v=="ok" for v in checks.values()) else 503
    return JSONResponse({"checks": checks, "version": APP_VERSION}, status_code=status)
```
Expose `/health/liveness` (shallow, for k8s-style liveness) and `/health/readiness` (deep).

---

### P1-B10 — Surge engine: integer division and polygon test cost

**Evidence:** `backend/utils/surge_engine.py:103` `ratio = demand / max(supply, 1)` — safe; good. But `_count_supply_in_area` runs a point-in-polygon check **in Python** for every online driver for every area every 2 minutes.

**Impact:** O(drivers × areas) per tick. At 1000 drivers × 30 areas = 30k polygon checks / 2 min. Manageable now, but scales poorly, and PostGIS can do this natively far faster.

**Fix (M):** Replace Python loop with a single SQL using PostGIS `ST_Contains`:
```sql
SELECT sa.id, COUNT(*) FROM drivers d
JOIN service_areas sa ON ST_Contains(sa.polygon, ST_MakePoint(d.lng, d.lat)::geography)
WHERE d.is_online AND d.is_available
GROUP BY sa.id;
```

---

### P1-B11 — `datetime.utcnow()` is deprecated and timezone-naive

**Evidence:** `surge_engine.py:54` and many other files use `datetime.utcnow()`.

**Impact:** Deprecation in Python 3.12; comparisons with timezone-aware DB timestamps silently buggy.

**Fix (S):** Global replace with `datetime.now(timezone.utc)` via a small codemod.

---

### P1-B12 — WebSocket auth happens after connection accepted

**Evidence:** `routes/websocket.py` accepts the connection, then waits for the first message to carry a token.

**Impact:** Unauthenticated sockets briefly hold server resources. DoS vector: open N sockets, never send auth.

**Fix (S):** Reject the `accept()` unless `?token=` query param is present and validates. Close with 4401 (custom "unauthorized").

---

### P1-B13 — API versioning is dual (`/api` + `/api/v1`) with no deprecation plan

**Evidence:** `server.py` mounts routers at both paths.

**Impact:** Maintaining two copies drifts silently; clients lock onto whichever path works. No visibility into which mobile app version uses which.

**Fix (S):** Log `X-API-Path` and `User-Agent` to correlate. Announce deprecation of `/api` (unversioned) → 6-month sunset → 410 Gone.

---

## P2 findings

### P2-B14 — Pydantic v2 warning noise

Many schemas may use v1-style `Config` classes; mixing causes warnings. Standardize on v2 `model_config`.

### P2-B15 — `SCHEMA.md` may drift from `supabase_schema.sql`

Docs are maintained by hand; add CI step: `pg_dump --schema-only | diff - docs/SCHEMA.md.expected`.

### P2-B16 — Admin "make_admin.py" CLI is unaudited

`backend/make_admin.py` promotes users; no trace in `admin_activity_log`. Wrap with audit log insert.

### P2-B17 — Payments routes lack explicit currency validation

`payments.py` accepts amount; validate `currency in {'CAD'}` (jurisdiction lock). Defends against client sending USD at 10×.

### P2-B18 — `features.py` flag loads per-request

Feature flags loaded from DB per request path add latency. Cache in Redis with 60s TTL.

### P2-B19 — No DB read-replica usage for analytics

Admin analytics hit primary. For large aggregates, route to a read-replica.

### P2-B20 — Scheduled rides loop has fixed 60s tick

60s precision can delay a scheduled ride 59s. Acceptable but note in SLA; tighten to 15s when scale demands.

---

## P3 findings

- **B21** — `discovery/` and `agents/` folders at repo root are unclear; audit and delete if dead.
- **B22** — Legacy `*.md` reports at repo root (`CODE_REVIEW_REPORT.md`, `ANALYSIS_REPORT.md`, etc.) duplicate this audit; move to `docs/archive/`.
- **B23** — `backend/uploads/` checked in with a `.gitignore` but risk of accidental commit — add pre-commit hook.

---

## Priority summary

| ID | Severity | Effort |
|---|---|---|
| B1 lifespan dead code | P0 | S |
| B2 webhook idempotency | P0 | S |
| B3 WS fan-out | P0 | M |
| B4 migrations | P0 | M |
| B5 FK cascades | P1 | M |
| B6 N+1 | P1 | S |
| B7 pool | P1 | M |
| B8 worker split | P1 | M |
| B9 health | P1 | S |
| B10 PostGIS surge | P1 | M |
| B11 utcnow | P1 | S |
| B12 WS auth | P1 | S |
| B13 API versions | P1 | S |
| B14–B20 | P2 | S–M |
| B21–B23 | P3 | S |

---

*Continue to → [04_FRONTEND_AUDIT.md](./04_FRONTEND_AUDIT.md)*
