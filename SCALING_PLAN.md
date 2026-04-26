# Spinr Scaling Plan — DB & Retry Layer

Branch: `claude/audit-driver-backend-EiiRL`
Date: 2026-04-23

This doc captures:

1. **What just shipped** — Redis row-level cache + proper circuit breaker.
2. **How Redis helps today** — diagrams and call-path analysis.
3. **How to extend Redis caching to more DB calls** safely (and where not to).
4. **What's next** — prioritized roadmap for write-safety, async I/O, retry budget, idempotency keys.

---

## 1. What shipped in this commit

### 1a. Redis row-level cache (`backend/db_supabase.py`)

Two helpers are now Redis-backed with a 30-second TTL:

| Function | What it caches | Hit path |
|---|---|---|
| `get_user_by_id(user_id)` | `users` row | `cache:user:{id}` |
| `get_driver_by_id(driver_id)` | `drivers` row | `cache:driver:{id}` |
| `get_driver_by_user_id_cached(user_id)` ← **new** | `drivers` row keyed by user | `cache:driver:by_user:{user_id}` |

**Writes to `users` / `drivers` auto-invalidate** via hooks in:

- `update_one("users", ...)` / `update_one("drivers", ...)`
- `delete_many("users", ...)` / `delete_many("drivers", ...)`
- `create_user(...)` (drops any negative-cache entry)
- `set_driver_available(...)`
- `claim_driver_atomic(...)` — critical for dispatch: if driver is claimed, the cache must not keep serving `is_available=true`.

This means **no route handler needs to remember to invalidate**. Any path that mutates a users/drivers row through these canonical helpers gets correct invalidation for free.

### 1b. Circuit breaker tightened (`_CircuitBreaker`)

Was already half-open correct, but previously allowed *every* call through in the half-open state. Now the half-open state releases **exactly one probe** until it resolves; all other concurrent callers see 503 until the probe tells us Supabase is healthy. This prevents a thundering-herd recovery where hundreds of queued requests all flood Supabase the moment it starts accepting connections.

States:
```
closed ──5 failures/30s──▶ open ──60s──▶ half-open
  ▲                                         │
  └───────first success on probe ───────────┘
                              │
                              └── probe fails ──▶ open
```

---

## 2. How Redis helps (high-level diagrams)

### 2a. Before the cache

Every single authenticated request fires **two** Supabase reads inside `get_current_user`:

```
          ┌──────────────┐
Rider app │  iPhone      │
  or      │  dashboard   │
 Driver   └──────┬───────┘
                 │ 1 req
                 ▼
          ┌──────────────┐        ┌──────────────┐
          │   FastAPI    │ ──r1──▶│              │
          │              │ ──r2──▶│   Supabase   │
          │              │ ◀──────│   (HTTP/2    │
          │              │ ◀──────│    via       │
          │              │        │   PostgREST) │
          └──────┬───────┘        └──────────────┘
                 │                r1 = users row
                 ▼                r2 = drivers row
            response
```

On a hot API (dashboard polling, location batch every 5 s, WS keep-alive HTTP probes), a single driver can fire 20 req/min × 2 DB reads = **40 Supabase reads per minute per user**. At 500 concurrent users that's 20 000 reads/min of which ~19 900 return **the same row**.

### 2b. After the cache

```
Rider app ─req─▶ FastAPI ─get_user_by_id(id)─▶ ┌────────────┐
                                                │   Redis    │
                                                │ (memory)   │
                                                │ TTL 30s    │
                                                └──┬─────┬───┘
                                                   │HIT  │MISS
                                                   │     │
                                    ◀──row──────── │     ▼
                                                         ┌──────────┐
                                                         │ Supabase │─┐
                                                         └──────────┘ │
                                                              │fill   │
                                                    ◀────row──┘       │
                                                              ▲       │
  writes (update_one/delete_many/…) ─ invalidate_user_cache(id)       │
                                                                      │
```

Now the **first** call in any 30-second window hits Supabase; the next ~N calls for the same user get a ~1 ms Redis read. For an auth-heavy endpoint like `/auth/me` polled every second, that's ~30× reduction in Supabase RPS for that user — which in turn reduces the likelihood of HTTP/2 GOAWAY bursts that caused the original incident.

### 2c. End-to-end request flow with cache + circuit breaker + retry

```
request ─▶ FastAPI handler
           │
           ▼
     get_current_user
           │
           ├─ get_user_by_id ───────────────┐
           │                                ▼
           │                       Redis: cache:user:{id}
           │                          ├ hit  → return row  (~1ms)
           │                          └ miss → db_supabase.run_sync ─▶ circuit breaker
           │                                                               │
           │                                                   ┌───────────┴───────────┐
           │                                                   │                       │
           │                                               closed/probe            open
           │                                                   │                       │
           │                                                   ▼                       ▼
           │                                             Supabase call           503 immediately
           │                                                   │
           │                                     ┌─────────────┴─────────────┐
           │                                     │                           │
           │                                  success                   transient error
           │                                     │                           │
           │                                     ▼                           ▼
           │                               write to cache              retry w/ backoff
           │                                                       (500ms, 1500ms)
           │                                                            │
           │                                                      success / 503
           │
           ├─ get_driver_by_user_id_cached (same pattern)
           │
           ▼
        handler body
```

---

## 3. Extending Redis to more DB calls — the rules

Not every `get_rows`/`get_*` call should be cached. The cache has three properties that dictate where it fits:

| Property | Implication |
|---|---|
| **Read-through**, 30s TTL | Staleness window is 30s. OK for rows edited at human timescale (users, drivers, settings, FAQs). **Not OK** for real-time state (ride status, driver_location, wallet balance) — those must be live. |
| **Auto-invalidated at `update_one`/`delete_many`/`create_*`** | Works only if *every* writer goes through those helpers. Direct `supabase.table(...).update(...)` calls bypass invalidation. Audit before caching a new table. |
| **Single-row, by primary key or unique index** | Multi-row queries (`get_rows("rides", {"status": "searching"})`) change every few seconds and have no stable cache key — don't cache them here. Use read-replicas or PostgREST materialized views instead. |

### 3a. What to cache next (safe wins)

| Call | Key | TTL | Invalidation path |
|---|---|---|---|
| `get_user_by_phone(phone)` | `cache:user:by_phone:{phone}` | 30 s | `update_one("users", …)` whenever the phone changes + `create_user` with phone |
| `get_app_settings()` (Stripe keys, Twilio, Google Maps) | `cache:settings:app` | 300 s | `update_one("settings", …)` from admin dashboard |
| `get_rows("vehicle_types", {"is_active": true})` | `cache:vehicle_types:active` | 600 s | Any admin vehicle_type write |
| `get_rows("service_areas", {"is_active": true})` | `cache:service_areas:active` | 300 s | Any admin service_area write (already partly done: `invalidate_fare_cache`) |
| `get_rows("fare_configs", {"service_area_id": X, "is_active": true})` | `cache:fare_configs:{area_id}` | 300 s | Admin fare config write |
| `get_rows("document_requirements")` | `cache:document_requirements` | 600 s | Admin document-requirement CRUD |
| Driver subscription lookup | `cache:driver_sub:{driver_id}` | 60 s | Subscription webhook + cancel endpoint |

Each of these has a low write rate and a high read rate — exactly the shape where a cache pays off.

### 3b. What **not** to cache in Redis row-cache

- Live ride state (`get_ride(ride_id)` mid-trip). Status transitions fire every few seconds and must be visible across replicas immediately.
- Wallet balances. Writing stale data here = billing bug.
- Dispatch queries (`find_nearby_drivers`, active-ride searches).
- Anything that returns a list whose membership changes minute-to-minute.
- Secrets, tokens, OTP codes — they have their own TTL logic and must not be long-cached.

### 3c. When a row changes via a path that doesn't go through `update_one`

Two options:

1. **Refactor to go through `update_one`** (preferred — one invalidation owner, one retry policy).
2. **Call `invalidate_user_cache(id)` / `invalidate_driver_cache(...)` explicitly** after the write. Document it with a comment at the call site.

Anti-pattern: directly calling `supabase.table("users").update(...).execute()` from a route. It bypasses the circuit breaker, bypasses the retry loop, and bypasses invalidation. Grep for these and funnel them through `db_supabase.update_one`.

---

## 4. Roadmap for the rest

### Tier 1 — this sprint (small, independent)

- **[#2] Retry budget + jitter (token bucket)**
  Location: `backend/db_supabase.py::run_sync`.
  Add a shared counter in Redis: `retries:window:{minute}`. Cap total retries to `0.1 × total_requests` per minute across the fleet. If the budget is exhausted, `run_sync` fails fast on the initial transient without attempting a retry. Jitter: multiply the sleep by `0.5 + random()` so 1000 clients don't retry at exactly the same 500 ms/1500 ms marks.

- **[#3 part a] Idempotency keys on write endpoints**
  Location: `backend/routes/rides.py::create_ride`, `routes/payments.py::create_intent`, `routes/wallet.py::top_up`, `routes/drivers.py::payouts`.
  Add an `Idempotency-Key` header (client-generated UUID per logical operation). Backend: hash(user_id + key) → Redis with 24h TTL. If a key is seen again, return the cached response. Lets clients retry safely on timeout without creating duplicate rides/charges. This is the Stripe pattern.

- **[#3 part b] Per-endpoint retry policy**
  In `run_sync` today, every call gets the same 3-attempt retry. Split it:
  - Reads (`SELECT`): retry aggressively, current policy.
  - Idempotent writes (`UPDATE ... WHERE id = ?`): retry once.
  - Non-idempotent writes (`INSERT` without idempotency key): **do not retry**. Fail fast, surface to the client, which decides via idempotency key whether to replay.
  Implement via a `retry_policy` kwarg on `run_sync`, default "reads", explicit for writes in the helpers.

- **[#8] Observability**
  Expose four Prometheus-style counters from `run_sync` and `_CircuitBreaker`:
  `spinr_db_retry_total{reason=transient|timeout}`,
  `spinr_db_circuit_state{state=closed|open|half_open}`,
  `spinr_cache_hit_total{key_prefix}`,
  `spinr_cache_miss_total{key_prefix}`.
  Plot retry rate alongside p99 latency in Grafana / whatever Railway gives us. Alert at `retry_rate > 5% for 2m`.

### Tier 2 — next sprint (medium, structural)

- **[#4] Async Supabase I/O** — the biggest structural win.
  Today every Supabase call runs in a thread pool (`run_in_executor`), which caps concurrency at ~40 threads and pins one thread per retry sleep. Move to:
  - `supabase-py v2` async API, **or**
  - Direct `httpx.AsyncClient` against PostgREST with our own auth header, **or**
  - `asyncpg` directly against Postgres (skips PostgREST entirely — fastest + no HTTP/2 GOAWAY problem).
  Any of these frees the worker thread during the DB roundtrip and during retry backoff. Throughput under load increases ~3–5×.

- **[#6] Deadline propagation**
  Client sends `X-Deadline-Ms: <epoch-ms>`; FastAPI middleware reads it and stores on `request.state.deadline`. `run_sync` checks remaining deadline before each retry and raises fast if the budget is exhausted. Axios's 15s timeout already gives us the upper bound — we just need to honour it server-side instead of doing 3.5 s of retries after the client has already given up.

### Tier 3 — next quarter (large, cross-cutting)

- **[#7] Direct Postgres via Supavisor** — bypass PostgREST for hot auth paths.
  PostgREST is the component dropping HTTP/2 streams; connecting directly via `asyncpg` + Supavisor (Supabase's pgbouncer) gives us 5–10 ms auth lookups and removes the GOAWAY failure mode entirely.
  Migration plan: start with read paths (`get_user_by_id`, `get_driver_by_user_id`, `get_app_settings`) on a separate pool, leave writes on PostgREST until v2 async is in. Fleet-gated with a feature flag so we can A/B latency.

- **Read replica routing**
  For pure reads (dashboard analytics, earnings, heatmap), route queries to a Supabase read-replica. Reduces load on the primary and insulates the auth path from slow analytics queries. Requires schema review to ensure no stale-read correctness bugs.

---

## 5. Summary — today vs next

| Today | After Tier 1 | After Tier 2 | After Tier 3 |
|---|---|---|---|
| 3 Supabase reads on every `/auth/me` | ~0.1 Supabase reads on hot path (30s cache) | Same + async, no thread-pool pinning | Direct Postgres, ~5ms vs ~60ms |
| Retries amplify outages | Retry budget caps amplification | Deadline-aware, no wasted retries | Rarely triggers (no GOAWAY) |
| Duplicate writes possible on retry | Idempotency keys → safe replay | | |
| Blind to retry rate | Full metrics + alerts | | |

Ship Tier 1 before any traffic growth. Tier 2 unlocks 3-5× throughput. Tier 3 removes the GOAWAY class of failure entirely.
