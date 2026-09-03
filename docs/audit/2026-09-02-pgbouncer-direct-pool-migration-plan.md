# PgBouncer / Direct-Pool Migration — Gated Implementation Plan (rev 2)

**Date:** 2026-09-02 (rev 1) · **Revised:** 2026-09-02 (rev 2 — reviewed against the code; see §2 and §A)
**Status:** Proposed, gated — **Phase 0 (evidence) may start now.** Phases 1–3 are blocked on the Go/No-Go decision in G6 and must not start before it.
**Decision owner:** Kiran Kumar (`srikumarimuddana-lab`) · **Engineering owner:** unassigned
**Tracking:** `ACTION_ITEMS.md` C50
**Related:** `docs/audit/2026-08-26-db-query-optimization-recommendations.md` · `docs/adr/001-supabase-postgres.md` · `loadtest/README.md` · `docs/change-log/2026-08-27-p0-db-query-optimizations.md`, `…-p1-admin-query-optimization.md`, `…-p2-dispatch-loop-optimization.md` · `docs/change-log/2026-09-01-h3-dispatch-heatmap.md`, `…-transactional-outbox-worker.md`

---

## 0. Plain-English summary (read this first)

- **The question:** should the FastAPI backend stop going through PostgREST (`supabase-py`) for the dispatch hot path and talk to Supabase's pooler (Supavisor, PgBouncer-compatible) directly? Pooling already exists at the DB layer; the app path is 100% PostgREST today.
- **The answer stays "not yet, and maybe never."** The Aug 26 audit showed the measured slowness was row width and round-trip *count*, not the connection layer. The cheap fixes (indexes, projection, dispatch de-dup) are ~80% shipped. Whether anything is left for a direct pool to fix is an **evidence question**, and the evidence does not exist yet.
- **Rev 1 of this doc could not be executed as written.** Its gate needed a 500-driver load test (harness exists, never run, blocked on a staging environment that does not exist) and a timing breakdown that no instrumentation produces. Several technical claims were also wrong (§2).
- **What the team can start today (Phase 0, §5):** a retro dispatch review, additive per-phase timing metrics, standing up staging, running the existing Locust harness, and confirming pooler facts — then a recorded Go/No-Go (ADR-011).
- **What needs Kiran:** the six decisions in §8, three of which are infra actions only an account owner can do.

---

## 1. What this is and isn't (corrected)

This is **not** approval to start work. It is the scoped plan for a question that has come up repeatedly and has never been tracked: "should dispatch bypass PostgREST?"

**Provenance correction.** Rev 1 said this was "deferred as P3 in the Aug 26 audit." It was not. The audit's P3 is items 22–24 (rider polling → WebSocket, surge re-enable prep, `EXPLAIN` follow-up), and the only pooling item anywhere in it is P2 #21 — the *httpx* keepalive pool, already shipped (commit `3c82b2e`). This plan originates here and is tracked as `ACTION_ITEMS.md` C50.

**Today's direct-Postgres connections** (rev 1 said only one existed): `backend/scripts/run_migrations.py` (psycopg v3-preferred / psycopg2 fallback, `DATABASE_URL` = the Supavisor pooler URL per its docstring at `:19-23`), `backend/scripts/verify_restore.py`, `backend/scripts/audit_migration_drift.py`, and the real-Postgres test harness under `backend/tests/rls/`. None of these are app traffic; none change under this plan.

Pooling **already exists** at the DB layer (Supavisor — ADR-001). This plan is about whether the *dispatch claim path* bypasses PostgREST and talks to that pooler directly. Everything else stays on PostgREST (§9).

---

## 2. Corrections to rev 1 (what the code actually shows)

Kept in the doc so the team sees what changed and why. Every reference below was re-read this session.

| # | Rev 1 said | Reality | Reference |
|---|---|---|---|
| 1 | "Deferred as P3 in the Aug 26 audit" | No such item; P3 = 22/23/24. Untracked until C50 | audit §6, `:435-441` |
| 2 | "The only direct-Postgres connection is `run_migrations.py`" | Also `verify_restore.py`, `audit_migration_drift.py`, `tests/rls/` | `backend/tests/rls/conftest.py:86-95,185-278` |
| 3 | Gate: "P0 1–8, P1 9–14 shipped **and verified in production**" | P0 #7a/#7b/#7c not done (7a is a correctness bug — offboarding/suspension reads silently capped at 1,000, rides left uncancelled); P1 #10, #14 partial; P2 #17 partial, #18 untouched. Nothing was verified in production. The P2 change-log self-discloses that `spinr-dispatch-reviewer` was never run | the three `2026-08-27-*` change-logs |
| 4 | Gate: "P2 #15 must be shipped" | Shipped (P2 log B1–B3), plus #16 (claim returns the row) and #21. `matching.py` already batches quests via `$in` (`:961`), inserts `ride_offers` in one call (`:861`), reuses `_gate_subs` (`:580-592`) | `backend/routes/rides/matching.py` |
| 5 | Gate: "load test at 500 simulated drivers" | Harness exists — `loadtest/locustfile.py` asserts both SLAs and exits 1 on breach — but has **never run**: E2 is blocked on E1 (no staging), which is blocked on three human actions | `loadtest/README.md:118-122`; `ACTION_ITEMS.md:17249` (E1), `:17268` (E2) |
| 6 | Gate: "APM breakdown showing HTTP marshalling > 30% of P95" | No per-phase timing exists. Only the end-to-end `spinr_dispatch_offer_to_accept_duration_ms` histogram and thread-pool gauges. Must be built (T3) | `backend/routes/drivers/ride_flow.py:429` |
| 7 | "H3, outbox, PostgREST-monolith refactor already live in parallel Cursor sessions" | H3 and the outbox **landed dark on `main`** in `fc6f922` behind default-off flags (`dispatch_geo_provider=legacy`, `outbox_receipts_enabled=false`). No "PostgREST-monolith refactor" exists — no branch, commit, change-log, or backlog entry | the two `2026-09-01-*` change-logs |
| 8 | Target surface = `matching.py` + `dispatch_service.py` + `offer_expiry_reaper.py` | Only `matching.py` is a round-trip hotspot. `matching.py` calls `dispatch_service`'s *pure* functions only (`filter_and_rank_drivers`, `dispatch_geo_bounds`, `rank_by_eta_with_acceptance`); the reaper makes 3 DB calls per tick. The unmentioned relevant file is `services/dispatch_candidates.py` (dark H3/PostGIS candidate provider, zero callers) | `matching.py:172-1108`; `offer_expiry_reaper.py:76,98,120` |
| 9 | asyncpg "already implicitly evaluated via `run_migrations.py`" | asyncpg appears **nowhere** in the repo. `run_migrations.py` evaluates psycopg v3 vs psycopg2. Only `psycopg2-binary` is pinned; any async driver is a **new dependency** in `requirements.in`, `requirements.txt`, `requirements-win.txt`, and the hash-pinned `requirements-locked.txt` | `run_migrations.py:216-251`; `requirements.in:20` |
| 10 | Supavisor "confirmed available per Kiran's Railway/Redis note" | No repo referent. The only evidence is the `run_migrations.py` docstring citing `…pooler.supabase.com:6543`. Pooler mode, port, and pool size for the compute tier are unverified (G5) | `run_migrations.py:19-23` |
| 11 | "Mirror `supabase_client.py`'s fail-loud pattern" | `supabase_client.py` never raises — missing env → `supabase = None` silently; its only `except` logs and continues. The real fail-loud is `core/lifespan.init_database`, **production-gated** on `settings.ENV`. `cleanup_database` is an empty stub — a pool has nowhere to close today | `supabase_client.py:18-19,61-76`; `core/lifespan.py:17-75` |
| 12 | `claim_driver_atomic()` = "`UPDATE … WHERE status='searching' RETURNING *`" | Wrong predicate. It is `UPDATE drivers SET is_available=false, availability_claimed_at=now() WHERE id=$1 AND is_available=true`, returning the post-update row, with Redis cache invalidation on **both** sides. `status='searching'` is the *ride*-side guard in `claim_ride_atomic()` | `repositories/driver_repo.py:241-298` (driver), `:327` (ride) |
| 13 | (omitted) prior art | `match_and_claim_driver` (migrations 77/80) is already a `FOR UPDATE SKIP LOCKED` SQL atomic claim with a Python wrapper — **dead code, zero callers**. `record_insurance_period_transition` (migration 253) is an RPC callable from inside a SQL function | `driver_repo.py:216`; `migrations/77_…sql`, `80_…sql`, `253_…sql` |
| 14 | "Needs an equivalent fixture against a real or dockerized Postgres" | Exists: `tests/rls/conftest.py` (throwaway DB from `TEST_DATABASE_URL`, self-skips). CI already provisions `postgres:15` for `backend-test` but **no workflow runs the real-PG suite** | `.github/workflows/ci.yml:41-117` |
| 15 | Rollback flag = `DISPATCH_DIRECT_POOL` env var | Repo convention is an `app_settings` flag: typed field on `AppSettings` (`schemas.py:160`), `Optional[bool]` on the admin update model (`routes/admin/settings.py:187`), read via `get_app_settings()` (60 s cache, `settings_loader.py:17`). The DSN also cannot be called `DATABASE_URL` — `verify_restore.py:119-133` treats that name as production and refuses it | `CLAUDE.md` release gate 3 |
| 16 | Effort "M–L (3–5 days)" | Revised 8–11 eng days plus a 7-day validation window (§7) | — |

Claims that **checked out:** the three file paths exist; ~25–35 PostgREST calls per dispatch attempt; the 64-thread `run_sync` executor (`_base.py:161`); `run_migrations.py` using the pooler URL; insurance-period writes on the dispatch path (`matching.py:886`); the 800–900 round-trips/ride derivation (audit §3.2).

---

## 3. Where the prerequisites actually stand

Per-item status against the Aug 26 audit, from the change-logs and the code. "Shipped" means merged to `main`; **nothing below has been verified against production load.**

### 3(a) Gate-relevant to this plan

| Audit item | Status | Evidence |
|---|---|---|
| P0 #4 — wrap the 3 blocking calls in `run_sync` | shipped | P0 log |
| P1 #9 — column projection on admin batch fetches | shipped | P1 log A1/A2 |
| P2 #15 — dispatch de-dup (one `service_areas` read, batched quests, unioned subscription projection) | shipped | P2 log B1–B3; `perf_rides_before.json` → `perf_rides_after.json` shows 17 → 10 DB calls per `POST /rides` |
| P2 #16 — claim returns the row, no follow-up read | shipped | P2 log B4; `driver_repo.py:244-258` |
| P2 #21 — httpx pool sized to the thread pool | shipped | commit `3c82b2e`; `supabase_client.py:53-57` |
| P2 #17 — leader locks on the 14 unlocked loops | **partial** — 4 of 14 locked; fail-open kept deliberately | P2 log B5/B6 |
| P2 #18 — stop the stale Railway standby running loops against prod | **open** — C5, no owner | `ACTION_ITEMS.md:12577` |
| `spinr-dispatch-reviewer` on the P2 dispatch changes | **never run** (self-disclosed) | P2 log "What was NOT verified" |

### 3(b) Open audit items that do NOT gate this plan

Listed so they are not forgotten, not because a direct pool depends on them: P0 #7a (correctness — do it regardless), #7b (PII projection on `admin/sgi_forms.py`), #7c; P1 #10 remainder (WS `get_drivers_snapshot` still unbounded) and #14 remainder (~26 sites deliberately excluded for staleness); the `_DEFAULT_ROW_LIMIT` 200-call-site sweep the P0 log says was "attempted, not completed" and which has no backlog entry.

---

## 4. Gate conditions (revised)

Each gate is measurable, has an owner, and records where it stands today. **All six must hold before Phase 1.**

| G | Condition | How it is verified | Status today | Owner |
|---|---|---|---|---|
| **G1** | Dispatch-relevant audit items shipped (3a) **and** `spinr-dispatch-reviewer` run retroactively on the P2 claim/offer path | Reviewer findings filed and fixed; noted in the P2 change-log | Shipped; review not run (T2) | Eng |
| **G2** | Staging environment live (E1) | `deploy-backend-staging.yml` passes its "Verify required secrets" step; `LOADTEST_BASE_URL` answers | Blocked on three human actions: `fly apps create` for `backend/fly.staging.toml`, a throwaway `ca-central-1` Supabase project, three GitHub secrets | Kiran / ops |
| **G3** | Locust 500-driver run (E2) on current `main` in staging. Record P95 fare estimate, P95 offer→accept, `spinr_db_thread_pool_queue_depth`, and a read-only `pg_stat_statements` snapshot | Results row appended to the table in `loadtest/README.md`; snapshot saved under `docs/audit/` | Not run (T5) | Eng |
| **G4** | Per-phase dispatch timing (T3) shows PostgREST marshalling + `run_sync` queue wait as **> 30% of P95 offer→accept** at 500 drivers | Metrics export from the G3 run | Instrumentation does not exist (T3) | Eng |
| **G5** | Pooler facts confirmed on the real Supabase project: transaction vs session mode and port (6543 vs 5432), `pool_size`/max client connections for the compute tier, IPv4 reachability of the pooler hostname from Fly `yyz`, and where the service-role DSN will be stored | Checklist in T6 filled in | Unverified | Kiran (dashboard access) |
| **G6** | Go/No-Go recorded as **ADR-011** — Accepted *or* Rejected — signed by Kiran | ADR file + row in `docs/adr/README.md` | Pending G1–G5 (T7) | Kiran |

**If G3 shows both SLAs met at 500 drivers (< 2 s P95 offer→accept, < 300 ms P95 fare estimate), the decision is No-Go.** Write ADR-011 as *Rejected — not needed*, close C50, and keep this doc as the record. That is a success outcome, not a failure of the plan.

---

## 5. Work plan — phases and tasks

Task format follows `docs/LAUNCH_GATE_IMPLEMENTATION_PLAN.md`: **Files · Effort (S ≤ ½ day, M 1–3 days, L > 3) · Depends on · Verify · Rollback.** Subtasks stay ≤ 3 files each, one logical change per commit, per `CLAUDE.md`.

### Phase 0 — Evidence (can start now; no production behavior change)

**T1 — Register tracking.** `ACTION_ITEMS.md` C50 pointing at this doc. *Done in the same PR as rev 2.*

**T2 — Retro `spinr-dispatch-reviewer` pass.**
- **Files:** none changed; review target is `backend/routes/rides/matching.py:821-886` (claim loop → `ride_offers` insert → insurance loop) and `repositories/driver_repo.py:241-298`.
- **Effort:** S · **Depends on:** — · **Verify:** findings filed as backlog items or fixed in their own commits; the P2 change-log's "not verified" line updated. · **Rollback:** n/a.

**T3 — Per-phase timing instrumentation (additive metrics only).**
- **Files:** `backend/repositories/_base.py` (`run_sync`, `:274`), `backend/routes/rides/matching.py` (`_match_driver_to_ride_attempt`, `:172`), `backend/tests/test_dispatch_metrics.py`.
- **What:**
  - In `run_sync`: `spinr_db_run_sync_queue_wait_ms` (submit → thread start) and `spinr_db_run_sync_exec_ms` (thread start → return) via `utils/metrics.observe` (`metrics.py:71`); a `contextvars` counter so a caller can read "DB calls made in this task".
  - In the attempt: `spinr_dispatch_attempt_duration_ms{phase=candidate_read|rank|claim|offer_insert|insurance|notify}` via `utils/metrics.time_ms` (`metrics.py:98`), and `spinr_dispatch_attempt_db_calls` (histogram of the counter above, reset per attempt).
  - Keep `DEFAULT_MS_BUCKETS` — bucket layout is pinned by the first observation per metric name.
  - Optional: a panel in `metrics-agent/grafana/dashboard-panel.json` next to the existing offer→accept P95.
- **Effort:** S–M · **Depends on:** — · **Verify:** extend `test_dispatch_metrics.py` using its `_histogram_cell` before/after pattern (`:148`); `pytest -m unit`; `ruff check`. · **Rollback:** revert — pure additive, no behavior change. No Change Impact Log needed beyond the commit body.

**T4 — Stand up staging (E1).**
- **Files:** none in-repo beyond what already exists (`backend/fly.staging.toml`, `.github/workflows/deploy-backend-staging.yml`).
- **Human actions (Kiran/ops):** `fly apps create` for the staging app; create a throwaway Supabase project in `ca-central-1` (PIPEDA — never another region); set the three GitHub secrets the workflow's "Verify required secrets" step names.
- **Effort:** S eng + human time · **Depends on:** — · **Verify:** the staging deploy workflow runs green; `run_migrations.py --status` against the staging DB shows all migrations applied. · **Rollback:** delete the app/project.

**T5 — Run the Locust harness (E2).**
- **Files:** `loadtest/README.md` (append a results row).
- **What:** `locust -f loadtest/locustfile.py --headless -u 600 -r 4 -t 30m` against `LOADTEST_BASE_URL=<staging>` on current `main` (the T3 build). Capture: the harness's own SLA verdict; P95s; `spinr_db_thread_pool_queue_depth`; the T3 phase histograms; and a **read-only** `pg_stat_statements` snapshot before and after (`SELECT calls, mean_exec_time, total_exec_time, query FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 30`). Save the snapshot as `docs/audit/YYYY-MM-DD-500-driver-loadtest.md`.
- **Never point this at production** — it books real rides (`loadtest/README.md:9`).
- **Effort:** S · **Depends on:** T3, T4 · **Verify:** results row present; both G3 and G4 numbers recorded. · **Rollback:** n/a.

**T6 — Pooler facts checklist (G5).** For Kiran, in the Supabase dashboard: (1) pooler mode and port the app would use — transaction mode (6543) is what a per-request pool needs; (2) `pool_size` / max client connections for the current compute tier, and whether it can hold `DISPATCH_POOL_MAX_SIZE × replicas` on top of PostgREST's own connections; (3) the pooler hostname resolves over IPv4 from a Fly `yyz` machine (the direct host is IPv6-only — see the migration-263 note at `ACTION_ITEMS.md:4938`); (4) where the service-role DSN will live (Fly secret, not `app_settings` — it is a credential, and `app_settings` is admin-visible). **Effort:** S · **Depends on:** — · **Verify:** answers written into this doc under G5.

**T7 — Go/No-Go.**
- **Files:** `docs/adr/011-dispatch-direct-pool.md` (new, via `/adr`; use the 3-digit on-disk numbering, not the 4-digit one in `.claude/commands/adr.md`), `docs/adr/README.md` (row), this doc (Status line).
- **What:** ADR-011 records the G3/G4 numbers and the decision. **Accepted** → Phase 1 opens, D1–D3 resolved in the ADR. **Rejected** → C50 closed, this doc stays as the record.
- **Effort:** S · **Depends on:** T2, T5, T6 · **Verify:** ADR row visible in the index; Kiran named as decider in the ADR header.

### Phase 1 — Foundation (after Go; flag off; nothing user-visible)

**T8 — Dependency and config.**
- **Files:** `backend/requirements.in` (+ regenerated `requirements.txt`, `requirements-win.txt`, `requirements-locked.txt` — use the repo's pin/lock tooling, never edit the hash-pinned file by hand), `backend/core/config.py`, `docs/ENVIRONMENT_VARIABLES.md`.
- **Driver:** recommend `psycopg[binary,pool]` (v3) — one driver family with the scripts, async-native `AsyncConnectionPool`, `prepare_threshold=None` for transaction-mode pooling. asyncpg is the alternative (faster, but a second driver family and `statement_cache_size=0` semantics to carry). **Decision D1.**
- **Blast radius to state in the Change Impact Log:** installing psycopg v3 flips `run_migrations.py`, `verify_restore.py`, and `audit_migration_drift.py` from their psycopg2 fallback onto their v3 branch. Run `python -m backend.scripts.run_migrations --status` and `--dry-run` against staging before merge.
- **Settings:** add `DISPATCH_POOL_DSN: str = ""`, `DISPATCH_POOL_MIN_SIZE: int = 1`, `DISPATCH_POOL_MAX_SIZE: int = 8` to `Settings` (`config.py:35-41` is the DB block; these are the first pool settings there). **Do not name it `DATABASE_URL`** (reserved by `verify_restore.py`).
- **Effort:** S–M · **Depends on:** T7 · **Verify:** `pip install -r requirements.txt` clean; migration runner `--status` unchanged in staging; env-var table rows added. · **Rollback:** revert the dependency commit (nothing reads the new settings yet).

**T9 — Pool module.**
- **Files:** `backend/repositories/dispatch_pool.py` (new), `backend/core/lifespan.py`, `backend/tests/test_dispatch_pool.py` (new).
- **What:** an `AsyncConnectionPool` opened in `lifespan.init_database` **only when** the flag (T10) is on and `DISPATCH_POOL_DSN` is set — fail loud in production on open failure using the same `settings.ENV == "production"` gate `init_database` already uses; closed in `lifespan.cleanup_database` (currently an empty stub at `:69-75`). Transaction-mode discipline documented in the module docstring: no `SET`, no advisory locks, no server-side prepared statements, one transaction per call. Reuse `_base._redact_pg_error` (`:220-238`) and mirror `_base`'s deadline propagation (`:336-356`). Metrics: `spinr_db_direct_pool_in_use` (gauge), `spinr_db_direct_pool_wait_ms`, `spinr_db_direct_query_duration_ms`. Dual-import pattern.
- **Effort:** M · **Depends on:** T8 · **Verify:** unit tests with the pool mocked; startup with flag off is byte-identical to today (no pool opened). · **Rollback:** flag off (T10) — pool is never opened.

**T10 — Feature flag.**
- **Files:** `backend/schemas.py` (`AppSettings`, add `dispatch_direct_pool_enabled: bool = False` with a comment naming it the rollback switch), `backend/routes/admin/settings.py` (admin update model), `docs/ENVIRONMENT_VARIABLES.md` (`app_settings` table row).
- **Effort:** S · **Depends on:** — · **Verify:** admin PUT round-trip test in the existing settings test module; default off. · **Rollback:** it *is* the rollback — flip off, ≤ 60 s propagation, no redeploy.
- **Asymmetry — binds T12/T13:** `lifespan.init_database` reads this flag once, at startup (T9), so the switch is not symmetric. Flipping it **on** against a running process opens no pool; enabling is a restart. Flipping it **off** closes no pool either — the ≤ 60 s no-redeploy rollback above holds *only if* the T12/T13 claim-path call sites re-read the flag per dispatch attempt (`settings_loader.get_app_settings`) and fall back to PostgREST when false. A T12/T13 that resolves "direct pool vs PostgREST" once at startup would silently downgrade this rollback to a redeploy. Treat the per-attempt re-read as a requirement of T13, not an implementation detail.

**T11 — Real-Postgres test harness + CI step.**
- **Files:** `backend/tests/direct_pool/conftest.py` (new; copy the `tests/rls/conftest.py` pattern — `TEST_DATABASE_URL`, throwaway `CREATE DATABASE`, self-skip), `.github/workflows/ci.yml` (`backend-test` job).
- **What:** the fixture applies `backend/supabase_schema.sql` plus migrations 77, 80, 131, 143 (both), 224, 253, 354, and T12's file. Add a CI step running `pytest tests/direct_pool -c /dev/null --confcutdir=tests/direct_pool` with `TEST_DATABASE_URL` pointed at the job's existing `postgres:15` service — today that service is provisioned but no real-PG suite runs against it.
- **Effort:** M · **Depends on:** T8 · **Verify:** CI job green with the new step executing (not skipping — check the log for the skip reason string). · **Rollback:** revert the CI step.

### Phase 2 — Dispatch claim on the pool (flag off by default)

**T12 — Migration: `dispatch_claim_batch`.**
- **Files:** `backend/migrations/NNN_dispatch_claim_batch.sql` — pick `NNN` with `ls backend/migrations | sort -V | tail -1` at PR time (399 is current; CHECK B in `migration-check.yml` hard-fails collisions).
- **Signature:** `dispatch_claim_batch(p_ride_id uuid, p_driver_ids uuid[], p_max_offers int, p_offered_at timestamptz, p_expires_at timestamptz)` returning the claimed `drivers` rows plus the inserted `ride_offers` ids.
- **Semantics (a translation, not a redesign):** walk `p_driver_ids` **in the given order** — Python ranking stays authoritative, SQL does not re-rank; per driver, `UPDATE drivers SET is_available = false, availability_claimed_at = now() WHERE id = $id AND is_available = true RETURNING *` (the exact predicate at `driver_repo.py:271-290`); revalidate `is_online AND is_verified AND status = 'active'` on the returned row (the check at `matching.py:839`) else release with `is_available = true`; stop at `p_max_offers`; insert the `ride_offers` rows mirroring `_build_offer_rows` (`matching.py:115`); call `record_insurance_period_transition(driver_id, 2, ride_id)` per claimed driver **in the same transaction** (period 2 opens at claim time — `CLAUDE.md` insurance rules); return.
- **Conventions:** `SECURITY DEFINER` with pinned `search_path`; grants per migration 354; rollback `DROP FUNCTION` in the header comment; supersedes `match_and_claim_driver` (77/80) — do not edit those files (append-only).
- **Effort:** M · **Depends on:** T11 · **Verify:** `spinr-migration-reviewer`; T14's real-PG tests. · **Rollback:** `DROP FUNCTION` — nothing calls it while the flag is off.

**T13 — `matching.py` behind the flag.**
- **Files:** `backend/routes/rides/matching.py`, `backend/repositories/dispatch_pool.py`.
- **What:** when `dispatch_direct_pool_enabled` is true, replace the block at `:821-886` (claim loop, `ride_offers` insert, insurance loop) with one `dispatch_pool.claim_batch(...)`. Keep on the Python side: `invalidate_driver_cache` for every driver attempted (Redis side effect, not in SQL); `set_driver_available(…, True)` release on any failure after a partial claim; `_metric_inc("spinr_dispatch_offer_sent_total")`; add `spinr_dispatch_claim_path_total{path=postgrest|direct}`. Candidate read (`:373`), ranking, notify, quests, incentives **stay on PostgREST** in this phase.
- **Error policy:** pool error → `logger.error(..., exc_info=True)` and **raise**, so the recovery shell re-arms the retry chain exactly as the existing offer-insert failure path does (`:864-871`). No silent fallback to PostgREST unless **D2** decides otherwise.
- **Effort:** M · **Depends on:** T9, T10, T12 · **Verify:** T14; `spinr-dispatch-reviewer`. · **Rollback:** flag off.

**T14 — Parity and race tests.**
- **Files:** `backend/tests/test_dispatch_claim_parity.py` (new, `mock_supabase_client` side), `backend/tests/direct_pool/test_claim_batch.py` (new, real-PG side).
- **What:** fixed seed → both paths produce the identical claimed-driver set and order, identical `ride_offers` rows, identical `driver_insurance_periods` rows. Real-PG: two concurrent `claim_batch` calls on the same driver → exactly one claim; a driver suspended between candidate read and claim is released, not offered.
- **Effort:** M · **Depends on:** T13 · **Verify:** both suites green in CI. · **Rollback:** n/a.

**T15 — Reviews and Change Impact Log.**
- Reviewers (all mandatory — automated PR review is currently silent, `ACTION_ITEMS.md` C7/C9): `spinr-dispatch-reviewer`, `spinr-insurance-period-auditor`, `spinr-migration-reviewer`, `spinr-realtime-reliability-reviewer`.
- Change Impact Log: `docs/change-log/YYYY-MM-DD-dispatch-direct-pool.md` from `docs/templates/CHANGE_IMPACT_LOG.md`, with the before/after claim-path snippet and the flag named as the rollback.
- **Effort:** S · **Depends on:** T14.

### Phase 3 — Rollout

**T16 — Staging validation.** Flag on in staging; re-run T5; compare against the T5 baseline row; watch `spinr_db_direct_pool_*` and the `pg_stat_statements` delta. Pass = offer→accept P95 improves and no pool-wait saturation. **Effort:** S · **Depends on:** T15.

**T17 — Production enable.** Flip `dispatch_direct_pool_enabled` via the admin settings screen in a low-traffic window with on-call watching the existing Grafana P1 alert (`metrics-agent/grafana/alert-rules.yaml:68` — offer→accept P95 > 2 s for 5 min). **Rollback = flag off**, ≤ 60 s, no deploy. Validation window: **7 days** at normal traffic with no regression in match rate, cancellation rate, or the P95. Note: `app_settings` is global — there is no per-replica canary without a Fly env override (**D3**). **Effort:** S + 7 days · **Depends on:** T16.

**T18 — Close-out.** Record outcomes in ADR-011's Consequences; add a one-line pointer in `CLAUDE.md` (Key Backend Files: `repositories/dispatch_pool.py`); close C50; file a separate backlog item for removing the PostgREST claim path once the direct path has a full production cycle. **Effort:** S.

---

## 6. Sequencing

```
Phase 0 (now)          T1 → T2 ─┐
                       T3 ──────┼→ T5 ─┐
                       T4 ──────┘      ├→ T7  Go / No-Go  (ADR-011)
                       T6 ─────────────┘
                                          │  No-Go → close C50, stop
                                          ▼  Go
Phase 1                T8 → T9 ─┐
                       T10 ─────┼→ T13
                       T8 → T11 → T12 ┘
Phase 2                T13 → T14 → T15
Phase 3                T15 → T16 → T17 (7-day window) → T18
```

Critical path to a decision: **T3 → T4 → T5 → T7**. T4 is the only step on it that needs a human outside engineering.
Rule: nothing in Phase 1 or later starts before T7 is recorded.

---

## 7. Effort estimate (revised)

| Phase | Engineering | Other |
|---|---|---|
| 0 — Evidence | 2–3 days | Kiran/ops: staging infra actions (T4), dashboard checks (T6), decision (T7) |
| 1 — Foundation | 2–3 days | — |
| 2 — Claim on the pool | 3–4 days | four reviewer passes |
| 3 — Rollout | 1 day | 7-day validation window |
| **Total** | **8–11 days** (rev 1 said 3–5) | |

---

## 8. Open decisions — need Kiran's answers

| D | Decision | Recommendation | Needed by |
|---|---|---|---|
| **D1** | Driver: `psycopg[binary,pool]` (v3) or asyncpg | psycopg v3 — one driver family with the scripts; accept the `run_migrations.py` branch flip as a verified blast-radius item | T8 |
| **D2** | Pool failure semantics: fail loud and let the retry chain re-arm, or auto-fall back to the PostgREST claim path | Fail loud. `CLAUDE.md` says soft-handling is the owner's call, so recorded here rather than assumed | T13 |
| **D3** | Canary mechanism: global `app_settings` flip only, or a Fly per-machine env override for a single-replica canary | Global flip in a low-traffic window is enough for a flag with a 60 s rollback | T17 |
| **D4** | The three E1 infra actions (Fly app, throwaway `ca-central-1` Supabase project, GitHub secrets) | Do them — E1/E2 block far more than this plan | T4 |
| **D5** | Whether to also gate on P0 #7a/#7b/#7c | No — they are unrelated to dispatch latency. Do #7a anyway (correctness) | T7 |
| **D6** | Who runs the load test and owns the results doc | Whoever takes T3 | T5 |

---

## 9. Explicit non-goals

- Payments, wallet, corporate billing, admin, auth, and every CRUD route stay on PostgREST **indefinitely** unless a future audit shows a specific bottleneck there. No exceptions without a separate ADR.
- `supabase-py` remains the default client for all new feature work.
- Not a replacement for finishing the audit's open items (3b) — those are worth doing regardless of G6.
- Not required for the "500 drivers, no new infra" goal by itself. The P0–P2 work may already be sufficient; G3 exists to find out.
- Not touching `services/dispatch_candidates.py` / H3 activation — that is its own dark-shipped rollout (`docs/change-log/2026-09-01-h3-dispatch-heatmap.md`).

---

## 10. What this review verified and did not verify

**Verified (this session, read-only):** every `file:line` in §2 and §5 by reading the code; the status of every P0/P1/P2 audit item against the 08-27 and 09-01 change-logs and the code; the load-test harness and its blocking items; the flag, ADR, metrics, and real-PG test conventions cited.

**Not verified:** anything in production (no DB access this session — no `pg_stat_statements`, no pooler configuration); Fly → Supabase network path; that migrations 77/80's function is still present in production (it is dead code either way).

---

## A. Revision history

| Rev | Date | Change |
|---|---|---|
| 1 | 2026-09-02 | Initial gated plan (commit `b110994`) |
| 2 | 2026-09-02 | Reviewed against the code. Corrected 16 claims (§2); replaced the unexecutable gate with six measurable gates (§4); added the phased task plan with files, verification, and rollback (§5); recorded the decisions needed (§8); registered as C50 |
