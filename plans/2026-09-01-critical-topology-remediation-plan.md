# Implementation Plan — Critical Topology Remediation

**Audience:** the Opus 5 session that will execute this. Read `CLAUDE.md` in full
first; every rule there (Decimal money, dual-import, `_require_ride_in_state()`,
insurance-period rows, ≤3 files per subtask, one logical change per commit,
Change Impact & Risk Log for anything on a live-tested surface) applies to every
step below and is not restated.

**Source:** `docs/audit/2026-09-01-engineering-director-teardown.md` §Critical
Issues C2, C3, C4, C5, C6.

**Branch:** work on the branch the session is assigned. Do not open a PR unless
asked. Commit after every subtask; never start the next subtask with an
uncommitted one.

---

## 0. Ground truth (verified 2026-09-01, re-verify before starting)

| Fact | Where |
|---|---|
| 40 named loops + `loop_watchdog` are spawned via `_spawn(name, coro_factory)` inside `lifespan()`; `_skip_background_loops = settings.ENV.lower() == "test"` is the only gate | `backend/core/lifespan.py:176-207` |
| `_WATCHDOG_LOOP_NAMES` list is checked against every `_spawn()` name by `tests/test_lifespan_watchdog_coverage.py` | `lifespan.py:654-700` |
| Loop heartbeats are an in-process dict; `/health` deliberately does not read them | `utils/loop_monitor.py:44`, `server.py:171-173` |
| Leader lock: `try_acquire_leader_lock()` returns **True on Redis error** (fails open); 30 loop modules call `redis_set_nx`/`try_acquire_leader_lock` | `utils/redis_client.py:433-475` |
| Fly runs one process group `app`, `UVICORN_WORKERS=2`, `min_machines_running=2`; deploy scales to 8 machines with `flyctl scale count 8` | `backend/fly.toml:36-75`, `.github/workflows/deploy-fly.yml:117` |
| Railway standby starts the same image with `uvicorn … --workers ${UVICORN_WORKERS:-4}` | `railway.json:8` |
| All DB I/O is `supabase-py` (sync, PostgREST) through `run_sync()` on a 64-thread `ThreadPoolExecutor`, with circuit breaker, deadline, retry policies and metrics | `backend/repositories/_base.py:150-372` |
| Only `psycopg2-binary` is in `requirements.in`; `DATABASE_URL` is read only by `scripts/run_migrations.py` and is documented as the Supabase pooler on port 6543 | `backend/requirements.in:20`, `scripts/run_migrations.py:19-23,217-223` |
| Dispatch candidate fetch: `get_rows("drivers", {...is_online,is_available,is_verified,status,vehicle_type_id,$and: dispatch_geo_bounds(...)}, limit=500)` then Python haversine in `filter_and_rank_drivers` | `routes/rides/matching.py:337-390`, `services/dispatch_service.py:85,180,417` |
| `drivers.location_geog geography(Point,4326)` is trigger-maintained from `lat`/`lng`, with partial GiST index `idx_drivers_location_geog_available WHERE is_online AND is_available`; `drivers_available_in_polygon()` is the template for a SECURITY DEFINER, service_role-only RPC | `backend/migrations/170_drivers_location_geog_surge.sql` |
| The older `find_nearby_drivers()` RPC reads the **unpopulated** `location` column and is bypassed by dispatch on purpose | `migrations/55_…sql:17-47`, `matching.py:313-316` |
| Existing precedent for an env-flagged spatial path with fallback: `SURGE_SPATIAL_COUNT` | `utils/surge_engine.py:51-52,118-130,205,409` |
| Flags are boolean columns on the single-row `settings` table (`id='app_settings'`), loaded by `get_app_settings()` with a 60 s in-process TTL; `get_cached_app_settings()` exists (2 callers); 117 modules call `get_app_settings()`; ~25 `*_enabled` keys | `backend/settings_loader.py:17-60`, `supabase_schema.sql:247` |
| A flag read failure on a money path logs a warning and "assumes off" | `services/payment_service.py:1491` |
| `http_exception_handler` sanitizes `detail` only when `status_code >= 500`; 4xx pass through verbatim by design | `utils/error_handling.py:44-60,711-745` |
| 33 `detail=str(e)` / `detail=f"…{e}"` sites: 30 in `routes/admin/*` import/backfill/appeal/LMS routes, 3 in `documents.py` (5xx, already sanitized by handler), plus `routes/drivers/{appeals,tax_exports,profile}.py` | list in §5.1 |
| Highest migration number today: `394_admin_audit_actor_stats_fn.sql` (re-check with `ls backend/migrations \| sort -V \| tail -1`) | — |

---

## 1. Order of work and why

```
WS-E  admin 4xx detail redaction        ~1 day    zero runtime risk, do first
WS-A  worker process split + strict lock ~3 days   topology only, no loop bodies change
WS-C  spatial dispatch RPC               ~2 days   flag-gated, independent of A
WS-D  typed feature-flag layer           ~4 days   needed before B so B can be canaried
WS-B  transactional hot-path data layer  ~3 weeks  largest, riskiest, last
```

E and C can run in parallel with A. B must wait for D (its rollout is a
per-flag canary). Each workstream ends with a Change Impact & Risk Log entry in
`docs/change-log/2026-09-DD-<slug>.md`.

Stop and use `AskUserQuestion` at the gates marked **⛔ GATE**. Do not proceed
past a gate on an assumption.

---

## 2. WS-E — Redact exception text in admin 4xx responses (C6)

**Goal.** No `str(e)` from an import/backfill/upstream service reaches a client,
while admins still learn *which row/field* failed.

**Design.** Two layers, defense in depth:
1. A `client_safe_detail(exc, *, fallback)` helper that returns the exception
   text with PII patterns redacted (SIN `\b\d{3}[- ]?\d{3}[- ]?\d{3}\b`, DOB-like
   dates, emails, phones, absolute file paths, JWT-shaped tokens) and truncated to
   300 chars. Reuse the regexes already in `utils/sentry_scrub.py` / `utils/pii.py`
   rather than writing new ones.
2. `http_exception_handler` applies the same redactor to **every** 4xx string
   detail (not replacement, redaction), so a future `detail=str(e)` cannot leak
   even if the helper is forgotten. Legit UX messages ("Invalid phone number")
   are untouched by redaction.

### Subtasks

| # | Files (≤3) | Change | Verify |
|---|---|---|---|
| E1 | `backend/utils/error_handling.py`, `backend/utils/pii.py`, `backend/tests/test_error_handling_4xx_redaction.py` | Add `redact_client_text()` (in `pii.py`) and `client_safe_detail()`; in `http_exception_handler` apply `redact_client_text` to 4xx string details; log the pre-redaction text at `warning` with `request_id` only when redaction changed it | New tests: SIN/email/path redacted on 422; "Invalid phone number" unchanged; 5xx behaviour unchanged (`pytest tests/test_error_handling*.py`) |
| E2 | `backend/tests/test_error_handling_guards.py` | Add a guard test that greps `backend/routes` + `backend/documents.py` for `detail=str(` and `detail=f"…{e}` and fails on any hit not on an allowlist that must be **empty** by E7 | Test fails now, passes after E3–E7 |
| E3 | `routes/admin/driver_import.py`, `routes/admin/legacy_driver_import.py`, `routes/admin/rider_import.py` | Replace each site with `client_safe_detail(e, fallback="CSV validation failed")`; keep status codes | `pytest tests -k "import" -q`; guard test count drops |
| E4 | `routes/admin/booking_import.py`, `routes/admin/wallet_import.py`, `routes/admin/stripe_import.py` | same | same |
| E5 | `routes/admin/legacy_sin_dob_backfill.py`, `routes/admin/legacy_saved_address_backfill.py`, `routes/admin/legacy_vehicle_history_backfill.py` | same; the SIN/DOB file is the highest-value target | same |
| E6 | `routes/admin/tax_id_import.py`, `routes/admin/data_transfer_import.py`, `routes/admin/driver_statements.py` | same | same |
| E7 | `routes/admin/drivers.py` (lines ~2602, ~3959), `routes/admin/export_approvals.py`, `routes/admin/driver_appeals.py` | `LMSUpstreamError` → fixed message "Upstream LMS error" + `details={"reason": redacted}`; `RequestAlreadyDecided` → fixed message; `ValueError` → `client_safe_detail` | same |
| E8 | `routes/drivers/appeals.py`, `routes/drivers/tax_exports.py`, `routes/drivers/profile.py` | same (these are driver-facing, so use fixed messages, not redacted text) | guard test passes with empty allowlist |

`documents.py` sites are 5xx and already sanitized by the handler; leave them
(surgical rule) but note in the change log.

**Rollback.** Pure code; `git revert` is sufficient (no data touched).

**Change log fields to fill.** UX effect: admins see redacted row errors
(`[REDACTED-SIN]`) instead of raw values. Not verified: no automated visual
tooling for admin-dashboard (standing gap B38).

---

## 3. WS-A — Move the loops out of the API process; make money-loop locks fail closed (C2)

**Goal.** API replicas run zero batch loops. One `worker` machine runs all of
them. Loops whose correctness depends on single execution skip their tick when
the leader lock cannot be confirmed.

**Design.**
- New setting `PROCESS_ROLE` ∈ {`all`, `web`, `worker`}, default `all`
  (backward compatible: Railway standby, local dev, tests unchanged).
- `lifespan()` keeps recording every `_spawn()` name (so
  `test_lifespan_watchdog_coverage.py` still holds) but only creates tasks when
  the role permits. `web` spawns nothing except `redis_startup_diagnosis` and
  `capacity_watchdog` (it measures *this* process's thread pool; label its
  metrics with `role`). `worker` spawns everything except nothing-to-serve
  bits; `all` = today.
- Fly: two process groups on the same image. `app` keeps the HTTP service.
  `worker` runs uvicorn with 1 worker (reuses the whole startup path, gives Fly
  a TCP/HTTP check target) and `PROCESS_ROLE=worker` set in its command string
  (Fly `[env]` is app-wide, so role must be per-command).
- Strict lock: `try_acquire_leader_lock_strict()` returns **False** on Redis
  error, logs `error`, increments `spinr_loop_leader_lock_unavailable_total{loop}`.
  Applied only to loops that move money or external state.
- Production without `REDIS_URL` fails startup (extends the existing
  `core/config.py` production validators).

### Subtasks

| # | Files (≤3) | Change | Verify |
|---|---|---|---|
| A1 | `backend/core/config.py`, `backend/tests/test_config_process_role.py` | Add `PROCESS_ROLE: str = "all"` with validator (`all\|web\|worker`); in the production validator block, fail fast when `REDIS_URL` is empty and `PROCESS_ROLE != "all"` … **and** (⛔ **GATE A1**: ask whether production may fail fast on missing `REDIS_URL` for role `all` too — the review recommends yes; it changes a silent-degrade into a refused boot) | Unit tests for each role value and the production validator |
| A2 | `backend/core/lifespan.py`, `backend/tests/test_lifespan_process_role.py` | `_spawn()` consults `settings.PROCESS_ROLE`: role `web` records the name and returns (same branch as ENV=test but with an `info` log "skipped: role=web"); role `worker`/`all` spawns. Keep `capacity_watchdog` and `redis_startup_diagnosis` spawning in every role. `_WATCHDOG_LOOP_NAMES` untouched. Add `role` to the startup summary log line | `pytest tests/test_lifespan_watchdog_coverage.py tests/test_core_lifespan_coverage.py tests/test_lifespan_process_role.py` — the coverage test must still pass unmodified |
| A3 | `backend/utils/loop_monitor.py`, `backend/utils/metrics.py` (only if a label helper is missing), `backend/tests/test_loop_monitor_role.py` | `record_heartbeat()` and the exported gauges carry `role=`; the loop watchdog only alerts for loops the *current* role is expected to run (in `web` it has nothing to watch and says so once at startup) | Tests: `web` role → watchdog no-op; `worker` → unchanged behaviour |
| A4 | `backend/utils/redis_client.py`, `backend/tests/test_leader_lock_strict.py` | Add `try_acquire_leader_lock_strict(name, ttl_seconds)`: same TTL rule, but `except Exception → log.error(...) ; _metric_inc("spinr_loop_leader_lock_unavailable_total", {"loop": name}); return False`. Leave the existing fail-open helper for load-shedding loops | Tests: Redis raises → False + metric; NX lost → False; NX won → True |
| A5 | `backend/utils/auto_payout.py`, `backend/utils/payment_retry.py`, `backend/utils/corporate_autotopup.py` | Switch to the strict lock. Each tick that cannot get the lock logs once at `info` and sleeps its normal interval (no busy loop). Do **not** touch loop bodies | Existing tests for each loop + assert the strict helper is the one called |
| A6 | `backend/utils/preauth_capture.py`, `backend/utils/referral_payout.py`, `backend/utils/orphaned_hold_reconciler.py` | same | same |
| A7 | `backend/utils/stripe_reconcile.py`, `backend/utils/ledger_projection.py`, `backend/utils/scheduled_rides.py` (scheduled dispatch uses an atomic DB claim; keep fail-open there unless the claim is not atomic — read it and decide, document the decision in the change log) | strict lock for the first two | same |
| A8 | `backend/fly.toml`, `backend/fly.staging.toml` | Add `[processes] app = "sh -c 'uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${UVICORN_WORKERS:-2}'"` and `worker = "sh -c 'PROCESS_ROLE=worker uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1'"`; `[http_service] processes=["app"]` (already); add `[[vm]] processes=["worker"] size="shared-cpu-1x" memory="1gb"`; add a `[checks.worker_health]` HTTP check on `/health` for the worker group; set `[env] PROCESS_ROLE="web"` so `app` machines run web-only | `flyctl config validate -c backend/fly.toml` (available in CI image? if not, note as unverified) |
| A9 | `.github/workflows/deploy-fly.yml`, `docs/runbooks/capacity-scaling.md` | `flyctl scale count app=8 worker=1 --region yyz`; add rollback line `flyctl scale count worker=0` + revert `[env] PROCESS_ROLE`; update the runbook's burst-pool section | Workflow lints (`actionlint` if present in CI); runbook cross-links |
| A10 | `docs/change-log/2026-09-DD-worker-process-split.md`, `CLAUDE.md` (the "Background task safety" paragraph only), `ARCHITECTURE.md` (topology diagram only) | Change Impact & Risk Log; update the "40 startup loops run on every replica" sentence to describe roles | Docs cite real paths (pre-commit doc-path check) |

**Blast radius to state in the change log.** Every loop module (30 with
locks, 40 total), `loop_monitor`, `capacity_watchdog`, Grafana dashboards that
sum `spinr_*` loop metrics (now split by `role`), Railway standby (unchanged,
role `all`), `metrics-agent` discovery (scrapes every machine in the app — the
worker machine will now be scraped too; confirm `discover-targets.sh` does not
filter by process group).

**Rollback (no second deploy needed).** `flyctl scale count worker=0` then
`flyctl secrets set PROCESS_ROLE=all` (secrets override `[env]`) → every app
machine resumes running loops exactly as today. Strict-lock loops resume on
Redis recovery automatically.

**⛔ GATE A8/A9:** before editing `deploy-fly.yml`, confirm with the user that
adding a ninth Fly machine (cost) and changing the scale command is approved.

**Not covered here (later):** replacing the loops with a real queue (arq/Temporal).
This workstream only isolates them; loop bodies do not change.

---

## 4. WS-C — Use the spatial index for dispatch candidates (C4)

**Goal.** Candidate drivers come from one indexed `ST_DWithin` + KNN query
instead of a 500-row bounding-box fetch and Python distance filtering. The
Python ranking (`filter_and_rank_drivers`), presence filter, subscription gate
and service-area guard stay exactly where they are.

**Design.**
- New RPC `dispatch_candidate_drivers(p_lat, p_lng, p_radius_m, p_vehicle_type_id, p_requires_wav, p_area_ids text[], p_allow_unassigned_area, p_limit)` modelled on `drivers_available_in_polygon` (SECURITY DEFINER, `SET search_path = public, extensions`, REVOKE from PUBLIC/anon/authenticated, GRANT service_role). Returns exactly the projection `matching.py` requests today plus `distance_m`. `ORDER BY location_geog <-> point LIMIT p_limit`.
- Gate: env var `DISPATCH_SPATIAL_CANDIDATES` (default off), same shape as
  `SURGE_SPATIAL_COUNT`. Once WS-D lands, migrate the gate to a registry flag
  with `fail_mode="open"` (fallback to the bounding-box path is the existing,
  proven behaviour, so falling back is not a silent swallow — but it must log at
  `error` and increment `spinr_dispatch_spatial_fallback_total`).
- Do **not** change `find_nearby_drivers()`; instead grep its callers. If it has
  live callers, file a follow-up to point it at `location_geog`; if none, note
  it as dead code (do not delete in this workstream).

### Subtasks

| # | Files (≤3) | Change | Verify |
|---|---|---|---|
| C1 | `backend/migrations/NNN_dispatch_candidate_drivers_rpc.sql` (pick `NNN` = next free number at execution time; CHECK B in `migration-check.yml` will fail a collision) | The RPC above. Predicates: `is_online AND is_available AND is_verified AND status='active' AND vehicle_type_id = p_vehicle_type_id AND location_geog IS NOT NULL AND (NOT p_requires_wav OR is_wav) AND (p_area_ids IS NULL OR service_area_id = ANY(p_area_ids) OR (p_allow_unassigned_area AND service_area_id IS NULL)) AND ST_DWithin(location_geog, pt, p_radius_m)`. Include the rollback `DROP FUNCTION` in the header comment and an `EXPLAIN ANALYZE` snippet for staging | Run `spinr-migration-reviewer` agent on the file; `python -m backend.scripts.run_migrations --dry-run` |
| C2 | `backend/repositories/driver_repo.py`, `backend/db_supabase.py` (re-export only), `backend/tests/test_driver_repo_spatial.py` | `async def dispatch_candidate_drivers(...)` wrapper calling `supabase.rpc(...)` through `run_sync` with `retry_policy="read"`; coerce ids to `str` (see migration 170's driver-id parity note) | Unit test with `mock_supabase_client`: params passed verbatim, rows returned, `DatabaseError` propagates |
| C3 | `backend/services/dispatch_service.py`, `backend/tests/test_dispatch_spatial_candidates.py` | In `find_candidate_drivers`: if flag on → call the RPC, on `Exception` log `error` + metric + fall through to the existing `get_rows` path; keep `filter_and_rank_drivers` unchanged | Tests: flag off → old query; flag on → RPC called with radius in metres and area ids; RPC raises → fallback + metric |
| C4 | `backend/routes/rides/matching.py`, `backend/tests/test_dispatch_match_attempt_branches.py` | Same flag branch at lines ~337-390; remove nothing; the "500-row cap" warning only fires on the fallback path | Existing dispatch tests + new branch tests; `pytest tests -k dispatch -q` |
| C5 | `docs/change-log/2026-09-DD-spatial-dispatch-candidates.md`, `.claude/context/domain-dispatch.md` | Change log with a before/after scenario (50 online drivers in Saskatoon, 12 km radius: 1 RPC vs 1 box query + Python loop) and the staging `EXPLAIN ANALYZE` output pasted in; update the dispatch context doc | Docs reviewed by `spinr-dispatch-reviewer` |

**Verification beyond tests.** In staging with the flag on: `EXPLAIN ANALYZE`
must show `idx_drivers_location_geog_available`; compare
`spinr_dispatch_offer_to_accept_duration_ms` P95 for 24 h flag-off vs 24 h
flag-on before asking to enable in production.

**Rollback.** Flag off (no deploy). Migration rollback: `DROP FUNCTION
dispatch_candidate_drivers(...)` (documented in the file header).

**⛔ GATE C5:** enabling `DISPATCH_SPATIAL_CANDIDATES` in production is the
user's call; present the staging numbers and ask.

---

## 5. WS-D — Typed feature-flag layer with last-known-good and canary targeting (C5)

**Goal.** One `flag_enabled(name, subject=…)` API with: a registry that
declares default + failure mode per flag, last-known-good on read failure, a
loud metric on every degraded read, and per-user/per-company/percentage
targeting. Storage stays the existing `settings` row (additive), plus one new
table for rollout rules. A vendor SDK (Unleash/GrowthBook) can be dropped in
later behind the same function; that is explicitly out of scope now.

**Design.**
```
backend/utils/flags.py
  FlagSpec(name, default: bool, fail_mode: Literal["last_known_good","default_off","default_on"],
           domain, owner, since, note)
  FLAGS: dict[str, FlagSpec]          # registry — every *_enabled key must be here
  FlagSubject(user_id, company_id, driver_id)
  async def flag_enabled(name, *, subject: FlagSubject | None = None) -> bool
  def flag_enabled_cached(name) -> bool | None   # sync, LKG only, for hot loops
```
- Read order: rollout rule (deny → allow → percentage bucket via
  `sha256(f"{name}:{subject_id}") % 100`) → `settings` value via
  `get_app_settings()` → registry default.
- On `get_app_settings()` failure: use `get_cached_app_settings()` (LKG). If
  none, apply `fail_mode`. Every degraded read logs `error` (not warning) and
  increments `spinr_flags_degraded_read_total{flag,mode}`. This replaces the
  "assume off" pattern at `payment_service.py:1491`.
- Money/regulatory flags get `fail_mode="last_known_good"` and a registry note
  explaining what "off" does; kill switches (`new_ride_requests_enabled`,
  `surge_engine_enabled`) get `default_on`/`default_off` as appropriate.
- Rollout rules table `feature_flag_rollouts(flag_name text PK, percentage int
  CHECK 0..100 DEFAULT 100, allow_user_ids text[], allow_company_ids text[],
  deny_user_ids text[], updated_by text, updated_at timestamptz)`; RLS enabled,
  service_role only; loaded with the same 60 s TTL. Empty table = today's
  behaviour.
- Admin: `routes/admin/settings.py` gains `GET/PUT /admin/feature-flags/{name}/rollout`
  (super_admin, `audit_logger` entry). Dashboard UI is a follow-up; the API is
  enough for a CLI canary.

### Subtasks

| # | Files (≤3) | Change | Verify |
|---|---|---|---|
| D1 | `backend/utils/flags.py`, `backend/tests/test_flags_registry.py` | Registry + `flag_enabled` without targeting; enumerate all `*_enabled` keys found by `grep -rhoE '\.get\("[a-z0-9_]+_enabled"' backend --include=*.py` and the `schemas.py` AppSettings fields; each entry needs `fail_mode` + `note` | Tests: default, settings override, LKG on failure, `fail_mode` branches, metric emitted, unknown flag raises `KeyError` at import time via a registry self-check |
| D2 | `backend/tests/test_flags_guard.py` | Guard test: every `*_enabled` key referenced anywhere in `backend/` (non-test) must exist in `FLAGS`; fails on drift | Passes after D1 |
| D3 | `backend/migrations/NNN_feature_flag_rollouts.sql`, `backend/repositories/_base.py` (only if a new re-export is needed), `backend/db_supabase.py` | Table + RLS + `service_role` grants; append-only conventions per `backend/migrations/CLAUDE.md` | `spinr-migration-reviewer`; `--dry-run` |
| D4 | `backend/utils/flags.py`, `backend/tests/test_flags_targeting.py` | Add `FlagSubject`, rollout loading + TTL, deny/allow/percentage evaluation | Tests: deny beats allow beats percentage; bucket is stable across calls and differs across users; 0% and 100% edges; table read failure → treat as no rules + degraded metric |
| D5 | `backend/routes/admin/settings.py`, `backend/schemas.py`, `backend/tests/test_admin_flag_rollout.py` | Rollout GET/PUT (super_admin, audit-logged, validates flag exists in registry) | Auth tests (non-super-admin 403), audit row asserted |
| D6 | `backend/services/payment_service.py`, `backend/routes/rides/booking.py`, `backend/routes/drivers/ride_complete.py` | Replace direct `settings.get("ledger_atomic_settle_enabled")` / `fare_lock_enabled` reads with `flag_enabled(...)`; delete the "assume off" warning branch | `pytest tests -k "settle or fare_lock or booking" -q`; `spinr-money-auditor` pass |
| D7 | `backend/utils/auto_payout.py`, `backend/utils/corporate_autotopup.py`, `backend/utils/surge_engine.py` | same for `auto_payout_enabled`, `corporate_billing_enabled` (where read), `surge_engine_enabled` | loop tests; `spinr-surge-auditor` for the surge file |
| D8 | `backend/utils/scheduled_rides.py`, `backend/routes/rides/queries.py`, `backend/utils/allowance_reset.py` | same | tests |
| D9..Dn | remaining files from the D2 guard output, 3 per commit | same; after the last batch, D2's guard flips to "every `*_enabled` read must go through `utils/flags`" (grep for `.get("…_enabled"` outside `flags.py` and `schemas.py` must be empty) | guard passes |
| D-final | `docs/change-log/2026-09-DD-feature-flag-layer.md`, `CLAUDE.md` ("Settings in DB" + release-gate 3 paragraphs), `docs/runbooks/feature-flags.md` (new) | Change log; document how to canary a flag by company; rollback = `DELETE FROM feature_flag_rollouts WHERE flag_name=…` or PUT percentage=100 | docs path check |

**Blast radius.** 117 `get_app_settings()` callers keep working untouched (the
loader is not modified). Only the boolean `*_enabled` reads move. Admin
Settings page continues to edit the same `settings` columns.

**Rollback.** Code revert restores direct reads; the rollout table is inert
when empty, so it can stay. No data mutation.

**⛔ GATE D1:** the `fail_mode` for each money flag is a product decision.
Propose values in a table and ask before committing D1.

---

## 6. WS-B — Transactional hot-path data layer on asyncpg (C3)

**Goal.** The four hottest, most invariant-sensitive write paths execute as
single Postgres transactions over a native async driver, with the same
breaker/deadline/metrics discipline `run_sync` has. Everything else stays on
supabase-py. No generic Mongo-filter→SQL compiler: each migrated path is a
named repository function with hand-written parameterized SQL.

**Design constraints (Supabase pooler on 6543 is PgBouncer transaction mode).**
- `asyncpg.create_pool(dsn, min_size=1, max_size=DB_POOL_MAX (default 4),
  statement_cache_size=0, command_timeout=…)`. No prepared statements, no
  session `SET`, no `LISTEN`, no advisory locks that outlive a transaction.
- Connection budget: 8 app machines × 2 uvicorn workers × 4 + worker 1 × 4 =
  68 pooled client connections. Confirm the Supabase plan's pooler limit before
  raising `DB_POOL_MAX`.
- The pool connects as the service role (same trust level as the REST key).
  RLS is bypassed exactly as today; document that.
- Deadline: honour `utils/deadline.py` remaining budget via `asyncio.wait_for`
  around each transaction, same as `run_sync`.
- Breaker: reuse `_breaker` from `_base.py` (one breaker for "the database"),
  record success/failure identically, `release_probe()` on deadline abort.
- Metrics: `spinr_pg_calls_total{op}`, `spinr_pg_call_duration_ms{op}`,
  `spinr_pg_pool_in_use`.
- Every migrated path is behind its own WS-D flag with
  `fail_mode="last_known_good"`; flag off = existing PostgREST code path,
  byte-for-byte unchanged.

### Subtasks

| # | Files (≤3) | Change | Verify |
|---|---|---|---|
| B1 | `backend/requirements.in`, `backend/core/config.py`, `backend/tests/test_config_pg_pool.py` | Add `asyncpg>=0.30,<1`; run the repo's pip-compile flow (`sync-pip-lockfile.yml` / `pip-compile-check.yml` expectations) to regenerate `requirements.txt`/`requirements-locked.txt`; settings `DB_POOL_URL: str = ""`, `DB_POOL_MIN=1`, `DB_POOL_MAX=4`, `DB_POOL_TIMEOUT_S=5`; production validator: if any `pg_tx_*` flag is on and `DB_POOL_URL` is empty → fail fast | lockfile CI job green; unit tests |
| B2 | `backend/repositories/_pg.py`, `backend/tests/test_pg_pool.py` | Pool lifecycle (`init_pool()`, `close_pool()`, `get_pool()`), `transaction()` async context manager with deadline + breaker + metrics, `fetchrow/fetch/execute` thin wrappers; when `DB_POOL_URL` is empty `init_pool()` logs `info` and every call raises `ServiceUnavailableException("database")` so a mis-flagged path fails loudly, never silently | Fake pool tests (breaker opens after N failures, deadline abort releases probe, metrics emitted) |
| B3 | `backend/core/lifespan.py`, `backend/tests/conftest.py`, `backend/tests/test_lifespan_pg_pool.py` | `init_pool()` at startup (after the DB health check), `close_pool()` at shutdown; conftest patches `repositories._pg` the same way it patches `_base.supabase` (document why in the same comment block) | Existing suite still green |
| B4 | `backend/repositories/ride_repo.py`, `backend/tests/test_ride_repo_transition_tx.py` | `transition_ride_status_tx(ride_id, *, from_states, to_state, ride_updates, driver_updates=None, insurance_period=None) -> dict \| None`: one transaction: `UPDATE rides … WHERE id=$1 AND status = ANY($2) RETURNING *` (0 rows → `None`, mirrors the optimistic filter), optional `UPDATE drivers`, optional `INSERT INTO driver_insurance_periods` (append-only), all parameterized; Decimal columns read back as `Decimal` | Unit tests with fake pool; integration test under a new `tests/pg/` tier that self-skips without `TEST_DATABASE_URL` (copy the `tests/rls/conftest.py` pattern) |
| B5 | `backend/routes/drivers/ride_flow.py` (arrived handler only), `backend/tests/test_ride_flow_arrived_tx.py` | Behind flag `pg_tx_ride_arrived_enabled`: call B4 with `from_states=["driver_accepted"]`, `to_state="driver_arrived"`; keep `_require_ride_in_state()`, the WS emit, and `spinr_rides_state_transition_total` exactly as they are | State-machine test added to `test_ride_state_machine.py`; `spinr-dispatch-reviewer` + `spinr-insurance-period-auditor` pass |
| B6 | `backend/routes/drivers/ride_flow.py` (start-trip handler), `backend/tests/test_ride_flow_start_tx.py` | Same for `driver_arrived → in_progress`, writing the Period 3 row in the same transaction | same auditors |
| B7 | `backend/repositories/driver_repo.py`, `backend/routes/drivers/location.py`, `backend/tests/test_location_marker_pg.py` | `update_driver_location_pg()` (single `UPDATE drivers SET lat,lng,heading,updated_at WHERE id=$1`; the trigger keeps `location_geog` in sync); used by the marker write behind `pg_location_marker_enabled`; `should_write_marker` gate untouched | Perf tests: assert one statement; `spinr-performance-sla-reviewer` |
| B8 | `backend/repositories/driver_repo.py`, `backend/routes/rides/matching.py`, `backend/tests/test_claim_pg.py` | `match_and_claim_driver` / `claim_ride_atomic` invoked as `SELECT * FROM match_and_claim_driver($1,…)` over the pool behind `pg_dispatch_claim_enabled` — same Postgres function, fewer hops; 409/`ride_taken` semantics unchanged | Existing acceptance-race tests run against both paths (parametrize the flag) |
| B9 | ⛔ **GATE B9** — settlement (`services/payment_service.py` `_finalize_card_settlement`, `utils/ride_settlement.py`) | Do **not** start until B5–B8 have run in production with flags on for ≥ 2 weeks with zero `spinr_pg_*` breaker opens. Present the dry-run scenario (mock fixtures: card ride, wallet ride, corporate allowance ride, Stripe failure mid-transaction) and ask | — |
| B-final | `docs/change-log/2026-09-DD-pg-hot-path.md`, `CLAUDE.md` ("Key Backend Files" + Testing patch-target paragraph), `docs/adr/0NN-asyncpg-hot-paths.md` | ADR (use `/adr`), change log with before/after for each migrated path, connection-budget math, rollback per flag | `spinr-migration-reviewer` is not needed (no schema), but `spinr-money-auditor` and `spinr-security-auditor` must pass |

**Rollback.** Per path: flag off (60 s TTL, no deploy). Global: unset
`DB_POOL_URL` → `init_pool()` skips and every pg path raises 503 loudly, so
flags must be off first; document that order in the runbook.

**What to measure.** For each migrated path, the existing histogram
(`spinr_rides_state_transition_total` rate, `spinr_dispatch_offer_to_accept_duration_ms`,
driver location write P95 from `spinr_db_call_duration` vs `spinr_pg_call_duration_ms`)
flag-off vs flag-on over 24 h in staging, then production canary by driver id
via WS-D targeting.

---

## 7. Cross-cutting execution rules for this plan

1. **Verification per commit:** `cd backend && ruff check . && ruff format --check .`
   then the targeted `pytest` files named in the subtask, then `pytest -m unit -q`.
   Full suite before each workstream's final commit.
2. **Migration numbers:** pick at execution time with
   `ls backend/migrations | sort -V | tail -1`; never reuse; never rename an
   applied file. Run the `spinr-migration-reviewer` agent on every new SQL file.
3. **Error-handling rule:** no new `except Exception: logger.warning(...)` on
   DB/payment/dispatch paths. Fallbacks in WS-C/WS-D/WS-B are allowed only
   because they route to the *existing* code path, and each one must log
   `error` and increment a named metric.
4. **Loops:** anything touched in WS-A must remain replay-safe; do not change
   loop bodies.
5. **Change Impact & Risk Log:** one file per workstream in `docs/change-log/`,
   using `docs/templates/CHANGE_IMPACT_LOG.md`, filled in before the final
   commit of the workstream, not after.
6. **Docs drift:** each workstream updates the one paragraph of `CLAUDE.md` it
   invalidates and nothing else in that file.
7. **Do not** add `[build]` to any commit message (it triggers EAS builds).
8. **Gates recap:** A1 (fail-fast on missing `REDIS_URL`), A8/A9 (ninth Fly
   machine + scale command), C5 (enable spatial dispatch in prod), D1 (money
   flag failure modes), B9 (settlement on asyncpg). Ask; do not assume.

## 8. Definition of done

- WS-E: guard test passes with an empty allowlist; no `detail=str(` in routes.
- WS-A: `PROCESS_ROLE=web` machines log zero "Started background task" lines;
  `worker` machine logs all 40 + watchdog; strict-lock loops emit
  `spinr_loop_leader_lock_unavailable_total` when Redis is down (verified in
  staging by pausing Redis for 60 s).
- WS-C: staging `EXPLAIN ANALYZE` shows the GiST index; dispatch P95 not worse
  than baseline; flag off in production until GATE C5.
- WS-D: every `*_enabled` read goes through `utils/flags.py`; the degraded-read
  metric exists; one flag has been canaried to a single company in staging.
- WS-B: B1–B8 merged, flags off in production, staging soak numbers attached to
  the change log; B9 not started without the gate.
