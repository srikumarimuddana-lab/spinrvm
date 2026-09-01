# Spinr — Path from B- to A

Companion to `docs/audit/2026-09-01-engineering-director-teardown.md` (the
grades) and `plans/2026-09-01-critical-topology-remediation-plan.md` (the five
criticals, already decomposed). This document answers one question: what does
"A" mean for each graded dimension, measured, and what work closes the gap.

"A" here means: a two-city, ~500-driver operation can run for a quarter with
no manual deploy step, no unexplained reconciler repair, every SLA row
alerted, and an engineer new to the repo can ship a money-path change safely
inside a week. It does **not** mean Uber-scale: no Kubernetes, no Kafka, no
microservices, no H3 mesh. Those would lower the grade by adding surface the
team cannot staff.

---

## 1. The A bar, per dimension

| Dimension | Today | A bar (measurable exit criteria) |
|---|---|---|
| Correctness | B+ | Every ride/driver/insurance write that must agree is one transaction. Invariants enforced by Postgres constraints, not by reconcilers. Reconciler "repairs" metric = 0 for 30 consecutive days. |
| Security | B+ | Zero `detail=str(e)`. Secrets out of the `settings` table. Step-up auth on payout-destination changes. RLS role-level tests cover every policy on a PII table. Weekly DAST green. |
| Error handling & telemetry | B | Every SLA row has a dashboard panel and a burn-rate alert routed to PagerDuty. One dispatch is one trace. `except Exception → warning` = 0 under `routes/{rides,payments,wallet,webhooks}`, `services/{payment,dispatch}*`. `print()` = 0 in importable modules. |
| Performance | C+ | Load test at 5× current peak, nightly, all P95s inside the SLA table. Dispatch P95 < 1 s. Every admin list endpoint paginated server-side. |
| Architecture | C+ | Loops run in a worker tier with a lease scheduler. Routes contain no DB calls (enforced by import-linter). Zero dual-import blocks. Client types generated from OpenAPI with a drift gate. Flags are a typed layer with canary targeting. |
| Maintainability | C | No file > 800 lines. `utils/` ≤ 40 generic modules; jobs and domain code have homes. ESLint `--max-warnings 0`. Explicit `any` < 50 across all TS. One architecture doc that matches `package.json`. |
| Testing | B | Coverage 80% on money and dispatch, enforced per package. The 17-item edge-case matrix implemented. Real-Postgres tier for `wallet_apply_delta` and settlement. Maestro nightly + on PR label. Contract test backend ↔ clients. |
| Process | B+ | Migrations run inside deploy. Canary via flags + Fly `bluegreen`. CI refuses a money/auth/ride PR without a change-log file. Automated PR review restored. Standby either verified or removed from docs. |

---

## 2. What closes each gap

### 2.1 Correctness → A
Already planned: WS-B (transactional transitions), WS-C, WS-D.

Additional:
- **Invariants as constraints.** Add migrations for: `CHECK (NOT is_available OR is_online)` on `drivers`; a partial unique index on `rides(rider_id) WHERE status IN (active set)` so "one active ride per rider" is a database fact; `CHECK (period <> 3 OR ride_id IS NOT NULL)` on `driver_insurance_periods`. Each is additive, each needs a one-off data audit query first (the migration must not fail on existing bad rows; fix rows, then add the constraint `NOT VALID` then `VALIDATE`).
- **Reconcilers become detectors.** `stale_intent_reconciler`, `orphaned_hold_reconciler`, `stuck_ride_sweeper`, `stale_p3_closer`, `distance_reconciliation` keep running, but every repair they make increments `spinr_reconcile_repairs_total{loop}` and pages when non-zero for 3 days. A repair is a bug report against the transactional path, not routine.
- **Sequence numbers on WS events.** `broadcast_ride_status` stamps `seq` per ride (Redis `INCR ride:{id}:seq`); clients store the last seen `seq` and refetch on a gap. Turns "refetch on reconnect" into gap detection during a live connection.
- **Property-based state-machine tests.** `hypothesis` over random transition sequences against `_require_ride_in_state()` and the transition table in `CLAUDE.md`; any reachable illegal state fails the build.

### 2.2 Security → A
Already planned: WS-E.

Additional:
- **Secrets.** Move Stripe/Twilio/Maps keys from the `settings` row to Fly secrets (and Railway variables), loaded at startup with a `POST /internal/reload-secrets` (super_admin + audit) so rotation still needs no deploy. Keep the admin UI field as a *write-through* to the secret store, not the source of truth. Reduces both blast radius (a DB read leak no longer yields the Stripe key) and settlement latency.
- **Step-up auth.** Changing a payout bank account or Connect destination requires a fresh OTP within 5 minutes (`routes/drivers/payouts.py`); the same for admin role grants in `routes/admin/staff.py`.
- **Refresh-token reuse detection test.** Rotation exists; add the test that a replayed old refresh token revokes the whole family and emits an audit row.
- **App Check enforcement.** `utils/device_attestation.py` verifies; make it *required* on rider/driver mutating endpoints behind a WS-D flag with `fail_mode="last_known_good"`, canary by app version.
- **RLS test tier growth.** From 5 policies to every policy on `users`, `drivers`, `rides`, `wallets`, `payouts`, `driver_insurance_periods`, `corporate_*`, `support_*`. Mechanical: one test per `CREATE POLICY` with allowed and denied paths, as `tests/rls/` already does.
- **Semgrep rules for house patterns.** `detail=str(e)`, float arithmetic in fare files, a router mounted without `require_module`, `logger.*` with `lat`/`lng`/`phone`/`email` in the format string. Wire into `security-gates.yml` as blocking.

### 2.3 Error handling & telemetry → A
- **Tracing.** `opentelemetry-instrumentation-{fastapi,httpx,redis,asyncpg}`, OTLP to Grafana Tempo through the existing Alloy agent in `metrics-agent/`. Propagate `traceparent` into WS fan-out payloads and mobile requests. The `X-Trace-ID` header already exists; make it the real trace id.
- **One log pipeline.** Route stdlib `logging` through a loguru intercept handler, JSON sink in production, ship to Loki via Alloy. Retention 30 days. Delete the second format.
- **SLO alerts.** `docs/slo.md` exists; turn each row into a Grafana Cloud alert rule with multi-window burn rate (1 h / 6 h), routed to PagerDuty per `docs/runbooks/on-call.md`. Also convert `monitoring/synthetic-checks.yaml` into actual Grafana Synthetic Monitoring checks (the file was written to make that a 1:1 translation).
- **Swallow census to zero in gated dirs.** A Ruff-compatible custom check (or Semgrep) that fails CI on `except Exception` followed by `logger.warning`/`pass`/`continue` in the money/dispatch/auth directories. Fix the 216 by classification: best-effort side effects (push, offer-card URL) may stay at warning *outside* gated dirs; inside them, they become `error` + metric or they raise.
- **`print()` → 0** in anything importable from `backend/` by moving one-off services under `backend/scripts/legacy/` (also a maintainability win).
- **Mobile SDK diet.** Keep Sentry + Crashlytics. Remove LogRocket, `expo-insights`, `expo-observe`, and the unused Firebase web SDK. Add a release gate on crash-free sessions ≥ 99.5% before OTA promotion.

### 2.4 Performance → A
Already planned: WS-B, WS-C.

Additional:
- **Live driver positions in Redis GEO.** Location marker writes go to `GEOADD drivers:live` plus the existing marker; dispatch candidates come from `GEOSEARCH` and are validated against Postgres only at claim time. This is the Uber pattern scaled down and it removes the DB from the dispatch read path entirely. Sequence: after WS-C proves the RPC path, flag-gated.
- **Cache the per-request constants.** `app_settings`, `vehicle_types`, `service_areas`, fare config: Redis with a 30 s TTL and pub/sub invalidation from the admin write path. Today these are re-read per estimate.
- **OSRM for estimates.** `deploy/osrm/` exists and is unused by the estimate path. Use it for the fare estimate and ranking ETAs; keep Google Directions for the quote-locked confirm. Removes most of the 3.5 s worst case and most of the Maps bill.
- **Admin pagination.** Every list endpoint in `routes/admin/*` takes `cursor` + `limit`; the dashboard uses React Query with cursor pagination. `_DEFAULT_ROW_LIMIT=1000` becomes a hard error on unpaginated callers, not a silent cap.
- **Load test in CI.** `loadtest/locustfile.py` runs nightly against staging at 5× peak; results written to `perf_*_baseline.json`; a regression > 20% on any SLA path fails the nightly and posts to Slack.

### 2.5 Architecture → A
Already planned: WS-A, WS-D.

Additional:
- **Lease scheduler for loops.** After WS-A isolates them, replace `_spawn()` loops with arq jobs (Redis-backed, cron syntax, at-least-once with leases) — one job per current loop body, bodies unchanged. Payout and settlement sagas move to Temporal only if arq's retry model proves insufficient; decide by ADR, not by default.
- **Packaging.** `backend/pyproject.toml`, `pip install -e .`, one import root. Then a single sweep removes all 984 dual-import blocks and the `CLAUDE.md` rule that protects them. Do it in one PR with no other changes so the diff is mechanical and reviewable.
- **Layering enforced.** `import-linter` contracts: `routes` may import `services` and `schemas`; only `services` and `repositories` may import `db_supabase`/`repositories`. Start as warning, ratchet to blocking per package as violations are fixed.
- **Contract-first clients.** Export `openapi.json` in CI from the FastAPI app, generate `shared/build-types` with `openapi-typescript`, fail the PR on drift. Mobile and admin then consume typed clients; the `any` count falls mechanically.
- **ADR per structural change.** 12 ADRs exist; every workstream above adds one via `/adr`.

### 2.6 Maintainability → A
- **Split the seven 2k+ backend files and four 2k+ pages.** `routes/admin/drivers.py` → `admin/drivers/{profile,documents,compliance,payouts,status}.py` using the existing sub-router pattern; same for `admin/rides.py`, `routes/auth.py`, `routes/webhooks.py` (one handler module per Stripe event family). Admin pages split into feature folders with one component per concern.
- **Re-home `utils/`.** `backend/jobs/` for loop bodies, `backend/domain/{rides,drivers,payments,corporate,safety}/` for domain helpers, `utils/` keeps crypto, metrics, redis, deadline, pii, money. Move with `git mv` in batches of ≤3 files; imports updated by the packaging change.
- **Retire legacy import code.** After the Oct 31 decommission: the 15 import/backfill/correction services, `diagnose_*.py`, `list_users.py`, `seed_vehicle_types.py` move to `backend/scripts/legacy/` and out of the mounted routers.
- **Lint ratchet.** `lint-staged` + `husky` pre-commit already has `commit-msg`; add `pre-commit` running `eslint --max-warnings 0` on staged files only, so the 1,751 budget can only go down. Same for `ruff`.
- **Docs.** Delete `frontend/`, root `.docx/.csv/.sql`; move review artifacts under `docs/audit/`; fix `ARCHITECTURE.md` (Expo 57, Fly primary); split `CLAUDE.md` into rules (kept short) and `docs/history/` (the incident narrative).

### 2.7 Testing → A
- **Edge-case matrix.** Implement the 17 cases from the teardown §Testing as named tests, most under `test_ride_state_machine.py`, `test_payout_toctou.py`, and a new `test_webhook_replay.py`.
- **Real-Postgres money tier.** Extend the `tests/rls` self-skipping pattern to `tests/pg/` covering `wallet_apply_delta`, `corporate_wallet_apply_delta`, `match_and_claim_driver`, and settlement end-to-end against a throwaway schema. CI already has a Postgres service; wire `TEST_DATABASE_URL` to it.
- **Coverage per package.** Run `pytest --cov` per package with the `CLAUDE.md` floors (`payments`/`fare_service`/`crypto` 90, `rides`/`dispatch` 80, admin 70) as separate CI steps; ratchet the global gate 60 → 70 → 80 on the schedule `pytest.ini` already promises.
- **Hollow-test audit.** One pass over money and dispatch tests: any test whose only assertion is "no exception" or that mocks the function under test gets rewritten or deleted.
- **Mutation sampling.** `mutmut` on `services/fare_service.py` and `utils/money.py` quarterly; surviving mutants become tests.
- **Mobile e2e.** Maestro on `run-maestro` label (workflow exists) and nightly on both apps; Playwright admin e2e already runs, seed the visual baselines so B38 closes.
- **Contract tests.** The OpenAPI drift gate above, plus a Pact-style check that the mobile client's error handling covers every `ErrorCode` the backend can emit.

### 2.8 Process → A
- **Migrations in deploy.** Fly `release_command = "python -m backend.scripts.run_migrations"` that fails the deploy on error and refuses `NEVER_APPLY` files. Expand/contract rule written into `backend/migrations/CLAUDE.md`.
- **Canary deploys.** Fly `strategy = "bluegreen"` for the app group; WS-D percentage flags for behaviour canaries.
- **Change-log gate.** CI check: a PR touching `routes/{rides,payments,wallet,webhooks,auth}`, `services/{payment,dispatch,corporate}*`, or `migrations/` must add or modify a `docs/change-log/*.md` file. Turns the mandatory template into an enforced one.
- **Automated review restored.** Either fix the silent Codex app or fund `claude-review.yml` for money/auth/migration paths only (cost-bounded by path filter). Until then the manual auditor pass stays mandatory, and it is not A.
- **Standby honesty.** Fix the Railway environment protection rule or remove the standby claim from `CLAUDE.md`, `ARCHITECTURE.md`, and the runbook. A documented standby that does not build is a false safety signal.
- **Postmortems in repo.** `docs/incidents/YYYY-MM-DD-<slug>.md` with the five-whys and the follow-up PR links; the teardown found the narrative scattered across `CLAUDE.md` and `ACTION_ITEMS.md`.

---

## 3. Sequencing (about 26 weeks, 2–3 engineers, or ~9 months with one engineer plus AI sessions)

| Phase | Weeks | Contents | Grade after |
|---|---|---|---|
| 0 — gates and hygiene | 1–2 | WS-E; migrations in deploy; `REDIS_URL` fail-fast; delete clutter; lint-staged ratchet; change-log CI gate; mobile SDK diet | B |
| 1 — topology and signal | 2–8 | WS-A, WS-C, WS-D; OTel + Loki + SLO alerts; synthetic checks live; secrets to Fly | B+ |
| 2 — data and speed | 8–14 | WS-B; DB constraints; reconcilers → detectors; per-request caches; admin pagination; nightly load test | A- on performance and correctness |
| 3 — structure and proof | 14–20 | Packaging + dual-import removal; import-linter; OpenAPI → generated types; god-file splits; `utils/` re-home; edge-case matrix; real-Postgres money tier; coverage 80 | A- overall |
| 4 — Uber-shaped where it pays | 20–26 | arq lease scheduler; Redis GEO live positions; OSRM estimates; WS sequence numbers; step-up auth; App Check enforced; bluegreen + canary; mutation sampling; regrade | A |

Every phase is independently valuable and independently revertible; none
requires the next to be worth shipping.

---

## 4. What A does not require

- A second backend language, microservices, or a service mesh.
- Kubernetes; Fly process groups plus a worker tier is enough at this scale.
- Kafka or a streaming platform; Redis Streams via arq covers the loop and
  event needs for years.
- H3/S2 geo-indexing; PostGIS KNN then Redis GEO covers a province.
- A vendor flag SaaS; the typed layer in WS-D with the rollout table is A-grade
  if it is the only way flags are read.
- Rewriting the mobile apps; they are structurally sound. They need fewer
  SDKs, generated types, and nightly e2e.

---

## 5. How to know you are there

Run these and expect the listed result:

```
grep -rn "detail=str(e)" backend/routes | wc -l            # 0
grep -rc "except ImportError" backend --include=*.py | awk -F: '{s+=$2} END {print s}'   # 0
find backend -name '*.py' -not -path '*/tests/*' | xargs wc -l | awk '$1>800' | wc -l   # 0
grep -rn ": any\b\|as any\b" admin-dashboard/src rider-app/app driver-app/app shared --include=*.ts --include=*.tsx | grep -v node_modules | wc -l   # < 50
grep -n "max-warnings" admin-dashboard/package.json        # --max-warnings 0
grep -n "cov-fail-under" backend/pytest.ini                # 80
grep -n "release_command" backend/fly.toml                 # present
ls backend/pyproject.toml                                   # present
```

Plus three things no grep can check: a quarter with zero reconciler repairs,
a quarter with every SEV paged from an alert rather than a user report, and a
new engineer's first money-path PR landing inside a week with the change-log
gate green.
