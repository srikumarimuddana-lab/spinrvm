# PgBouncer / Direct-Pool Migration — Gated Implementation Plan

**Date:** 2026-09-02
**Status:** Proposed, gated — do NOT start without the trigger condition in §1 being met
**Related:** `docs/audit/2026-08-26-db-query-optimization-recommendations.md` (P0–P3), `docs/adr/001-supabase-postgres.md`

---

## 0. What this is and isn't

This is **not** approval to start work. It's the scoped plan for the question "should we move the FastAPI backend off PostgREST onto a direct pooled Postgres connection (via Supabase's Supavisor, PgBouncer-compatible)?" — deferred as P3 in the Aug 26 audit.

Today: 100% of live app traffic (rides, dispatch, payments, wallet, corporate) goes through PostgREST via `supabase-py`. The only direct-Postgres connection in the codebase is `backend/scripts/run_migrations.py`, which already uses the Supavisor pooler (`...pooler.supabase.com:6543`) — that's out of scope here, it's not changing.

Pooling **already exists** at the DB layer (Supavisor, PgBouncer-compatible, included with Supabase — see ADR-001). This plan is about whether the *app* bypasses PostgREST and talks to that pooler directly, not about adding pooling that doesn't exist.

---

## 1. Trigger condition — do not start until this is true

Per the 2026-08-26 audit's own findings: the measured 36–114ms "slow" queries were **not** a connection-layer or query-plan problem. `EXPLAIN (ANALYZE, BUFFERS)` on 2026-08-27 showed sub-millisecond index scans on every hot lookup. The cost was row width (`SELECT *` on PII-heavy rows) and round-trip count (~800–900 round-trips per ride search), both already being fixed via P0/P1/P2 items (indexes, column projection, dispatch dedup, blocking-call fixes).

**Do not start this migration until all of the following hold:**

1. P0 items 1–8 and P1 items 9–14 (audit doc) are shipped and verified in production.
2. P2 item 15 (dispatch de-duplication — service_areas re-read 5×/attempt, quest N+1, spinr_pass quota check) is shipped.
3. A load test at **500 simulated concurrent drivers** (Kiran's stated target) still shows PostgREST round-trip/HTTP overhead — not row width, not query plan, not thread-pool saturation — as the dominant cost. Confirm via:
   - `pg_stat_statements` mean times on hot queries after projection lands (expect these to drop toward the 0.1–0.8ms EXPLAIN numbers)
   - APM/timing breakdown showing HTTP marshalling (PostgREST JSON serialization + `run_sync` thread-pool queueing) as >30% of P95 dispatch latency, not DB execution time
4. Kiran explicitly signs off after seeing that evidence. This is the single highest-blast-radius change available in this codebase (every route, all ~66 `db_supabase.py` helpers) — it does not get greenlit on suspicion alone.

If the load test shows the P0–P2 fixes already hit the < 2s dispatch-offer SLA and < 300ms fare-estimate SLA at 500 drivers, **this plan does not get executed.** Report that instead.

---

## 2. Why scoped, not a full rewrite

ADR-001 already estimated a full move off Supabase/PostgREST at 2–3 weeks (rewriting all 66 helpers). A full migration:

- Touches every route file in `backend/routes/`
- Breaks the dual-import pattern and `run_sync` circuit-breaker/retry machinery that's tuned and battle-tested
- Is maximum blast radius while H3 spatial work, the outbox pattern, and the PostgREST-monolith refactor are already live in parallel Cursor sessions — landing this on top of that is how we got the lifespan.py/driver_repo.py/matching.py cross-contamination problem before, at 10x the file count.
- Is not what the evidence points to needing (see §1).

**Scope this to the dispatch hot path only** — the one place the audit quantified a real problem (800–900 round-trips/ride) that column projection and query dedup alone won't fully solve, because the underlying issue there is round-trip *count*, not row width.

---

## 3. Scoped plan (dispatch hot path only)

### 3.1 Target surface
- `backend/routes/rides/matching.py` (dispatch attempt loop)
- `backend/services/dispatch_service.py`
- `backend/utils/offer_expiry_reaper.py`

Everything else (auth, payments, wallet, corporate, admin, all CRUD routes) **stays on PostgREST**. No exceptions without a separate ADR.

### 3.2 Connection layer
- Use **Supavisor transaction-mode pooler** (already provisioned, zero new infra — confirmed available per Kiran's Railway/Redis note, same principle applies to Supabase's own pooler).
- Driver: `psycopg3` async (`psycopg[binary,pool]`) or `asyncpg`. Recommend `asyncpg` — already implicitly evaluated via `run_migrations.py`'s psycopg-v3-preferred/psycopg2-fallback pattern; asyncpg is faster and has better async-native pool semantics for a hot path.
- **Known gotcha to design around up front:** Supavisor transaction-mode pooling does not support session-level features — no prepared-statement caching across transactions, no `SET` session vars, no advisory locks tied to a connection. `asyncpg`'s statement cache must be disabled (`statement_cache_size=0`) or every query pays a re-parse under pooled transaction mode. This must be a documented decision in the code, not discovered in production.
- Separate, small connection pool sized independently from the existing 64-thread `run_sync` executor — this path is natively async, it does not use that executor at all.

### 3.3 What changes, mechanically
1. New module `backend/db_direct.py` (or `repositories/dispatch_pool.py`) — owns the asyncpg pool, mirrors `supabase_client.py`'s fail-loud pattern (no silent fallback to PostgREST on connection error — surface it per CLAUDE.md's error-handling rule).
2. Rewrite the dispatch attempt's read sequence as a small number of parameterized SQL queries (candidate driver fetch + claim + insurance-period write) instead of ~25–30 PostgREST calls. Target: collapse to single digits per attempt.
3. **The atomic claim stays atomic** — `claim_driver_atomic()`'s `UPDATE ... WHERE status='searching' RETURNING *` semantics must be preserved exactly (CLAUDE.md's race-condition guard). This is a straight SQL translation, not a redesign.
4. Money-adjacent code (none in dispatch itself, but fare snapshot writes are adjacent) stays on the existing `Decimal`-safe helpers — do not introduce float arithmetic in the new path.
5. RLS: this path uses a service-role-equivalent connection (same trust boundary as today's service-role PostgREST client) — bypasses RLS by design, same as now. No new RLS surface.

### 3.4 Testing gate (per CLAUDE.md)
- `spinr-dispatch-reviewer` pass — mandatory, this touches the live-tested ride-state-machine surface.
- `mock_supabase_client` dry run is not sufficient here since the new path doesn't go through `supabase-py` at all — needs an equivalent fixture against a real or dockerized Postgres for the new pool.
- Regression test proving identical dispatch outcomes (same driver claimed, same offer sequence, same insurance-period rows written) before/after on a fixed seed dataset.
- Load test: 500 concurrent simulated ride requests, compare P95 offer→accept latency against the < 2s SLA, before/after.

### 3.5 Rollback
- Feature-flagged: `DISPATCH_DIRECT_POOL` env var, default off. New code path is fully additive — old PostgREST-based dispatch path stays in place and callable until the new path has a full production cycle (at least one full week at 500-driver-equivalent load) with no regressions.
- Rollback is a flag flip, not a revert — keep both paths alive through the validation window.

### 3.6 Effort estimate
- **M–L (3–5 days)**, scoped to dispatch only, vs. the 2–3 week full-rewrite ADR-001 already ruled out.

---

## 4. Explicit non-goals

- Not touching payments, wallet, corporate billing, or admin routes — those stay on PostgREST indefinitely unless a future audit shows a specific bottleneck there.
- Not replacing `supabase-py` as the primary client. It remains the default for all new feature work.
- Not required for the "A-grade / 500 drivers, no new infra" goal by itself — P0/P1/P2 query-optimization work is very likely sufficient on its own. This plan exists so the option is scoped and ready *if* the load test in §1.3 proves it's needed, not because it's assumed to be needed.

---

## 5. Next action

No implementation starts today. Next step is finishing the in-flight P0–P2 work (query optimization audit items, currently split across the H3/outbox/PostgREST-monolith Cursor sessions) and running the 500-driver load test called for in §1.3. Revisit this doc with that evidence.
