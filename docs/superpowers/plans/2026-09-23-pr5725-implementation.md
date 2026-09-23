# PR 5725 Reliability Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans task by task. Use GPT-6 Luna for delegated investigation/review. Commit and publish each completed logical task to PR 5725.

**Goal:** Implement the verified first reliability wave from the September 22 plans and preserve explicit gates for subsequent production work.
**Architecture:** Extend the existing Redis client, payment loops, deployment gate and loop registry. Keep database claims and Stripe idempotency as the correctness authority; a TTL lock is only an additional gate and never proof of exclusive execution for an entire tick.
**Tech Stack:** Existing Python/FastAPI, redis-py, pytest, GitHub Actions and Fly.
**Spec:** `docs/superpowers/plans/2026-09-22-director-plans-assessment.md`, reconciled against `docs/audit/2026-09-22-consolidated-go-live-remediation.md` and `.claude/plans/2026-09-22-path-to-a.md`.

## Global constraints

- One logical change per commit, at most three files and approximately 200 changed lines. Include a change-impact record for runtime changes.
- Money stays Decimal; database conditional claims and Stripe idempotency keys stay intact.
- No production deployment, migration application, financial replay, credential rotation or topology activation in this implementation wave.
- Preserve dual imports, existing flags/kill switches, and default process role `all`.
- Source presence, test proof, live enablement and operational proof are separate states; never infer the latter from the former.
- Follow the director assessment where the older plans conflict: defer GEO/arq/packaging/secret relocation; no duplicate settings cache or WS sequence scheme.

## Baseline and decisions

Remote PR head on 2026-09-23: `101d3fdf2a5b498433124c0b496a028b92b18b60`. It contains three documentation files only. The PR description and review comment refer to unpublished code; that code is not considered implemented here.

Alternative considered: change all `redis_set_nx` calls globally to fail closed. Chosen: add a separate strict primitive and migrate named money loops individually, preserving deliberate local development/non-money fallback contracts.

Alternative considered: activate a dedicated worker immediately. Chosen: first align role-aware spawning and monitoring while leaving deployment topology unchanged; activation requires lease/replay and fleet proof.

Alternative considered: execute the entire A-grade plan verbatim. Rejected: its own prerequisites require staged/device evidence, measured bottlenecks and 14/30-day observation windows; the director assessment also identifies unsafe and duplicate work.

## Review focus

1. Missing/cold-failing Redis must never confer distributed ownership (Task 1).
2. Contention, outage, recovery and expiry must not skip all future money ticks or remove database replay protection (Tasks 3–7).
3. Failed/missing/foreign/stale CI and unconfigured production probes must not authorize deploy (Task 9).
4. API watchdog must not expect heartbeats from deliberately excluded worker loops (Task 10).
5. Validation exceptions must not expose arbitrary underlying exception strings (Task 11).

## Executable first wave

Each task: write the behavioral regression, observe failure, implement, run the named focused suite, inspect the actual diff with a reviewer, then commit + push. Record test evidence in the task's impact record. Independent investigations may run concurrently; commits stay isolated.

### Task 1: Strict Redis primitive
Files: `backend/utils/redis_client.py`, `backend/tests/test_redis_client_coverage.py`, `docs/change-log/2026-09-23-strict-redis-lock.md`.
Produces: `async redis_set_nx_strict(key: str, value: str, ttl: int) -> bool`; unavailable client/command raises; contention returns False; successful SET NX EX returns True. Never calls a local fallback.
- [ ] Test no URL, initialization failure, SET failure, contention, acquisition and expired ownership.
- [ ] Implement direct `_get_redis()` + `r.set(key, value, nx=True, ex=ttl)`; raise a fixed unavailable error when `r is None`.
- [ ] Run `python -m pytest tests/test_redis_client_coverage.py -q --no-cov` from backend, then commit `fix(redis): add fail-closed distributed lock primitive`.

### Task 2: Bounded Redis waits
Files: Redis client, its existing coverage test, `docs/change-log/2026-09-23-redis-timeouts.md`.
- [ ] Assert connection and command timeouts are passed to `from_url`, with timeout retries disabled.
- [ ] Configure bounded waits; preserve strict error propagation and sanitize initialization diagnostics.
- [ ] Run the Redis coverage suite; commit `fix(redis): bound connection and command waits`.

### Task 3: Payment retry gate
Files: `backend/utils/payment_retry.py`, `backend/tests/test_payment_retry.py`, `docs/change-log/2026-09-23-payment-retry-lock.md`.
Consumes Task 1. Preserve the module-local `redis_set_nx` name by importing the strict primitive with that alias, so existing test seams remain stable.
- [ ] Drive the real loop through failed acquisition then healthy acquisition; assert no financial tick on failure and normal recovery/heartbeat. Retain virtual-clock cadence and claim/idempotency tests.
- [ ] Change lock error to `got_lock = False`; record `spinr_loop_lock_unavailable_total{loop="payment_retry"}`.
- [ ] Run payment retry and payment retry coverage suites; commit `fix(payments): skip retry ticks without Redis ownership`.

### Task 4: Preauthorization capture gate
Files: `backend/utils/preauth_capture.py`, `backend/tests/test_preauth_capture_coverage.py`, `docs/change-log/2026-09-23-preauth-lock.md`.
- [ ] Test lock outage skips `_capture_tick`, contention skips, recovery resumes, and cancellation still propagates.
- [ ] Import strict primitive under existing local name, fail closed and emit lock-unavailable counter; retain DB claim and existing lease duration.
- [ ] Run both preauth capture suites; commit `fix(payments): fail closed on preauth lock outages`.

### Task 5: Orphaned hold gate
Files: `backend/utils/orphaned_hold_reconciler.py`, `backend/tests/test_orphaned_hold_reconciler_loop_coverage.py`, `docs/change-log/2026-09-23-orphaned-hold-lock.md`.
- [ ] Test no reconciliation on unavailable/contended lock and recovery on next interval.
- [ ] Import strict primitive, fail closed and emit counter. Preserve atomic authorization-state claims and cancellation keys.
- [ ] Run orphaned-hold suites; commit `fix(payments): gate orphaned hold recovery on Redis`.

### Task 6: Automatic payout gate
Files: `backend/utils/auto_payout.py`, `backend/tests/test_auto_payout.py`, `docs/change-log/2026-09-23-auto-payout-lock.md`.
- [ ] Test a due Sunday cycle cannot transfer on lock error, but can resume when acquisition succeeds.
- [ ] Import strict primitive, fail closed and emit counter; retain payout claim/idempotency and payout settings.
- [ ] Run auto-payout tests; commit `fix(payouts): require distributed ownership for automatic runs`.

### Task 7: Corporate auto-topup gate
Files: `backend/utils/corporate_autotopup.py`, `backend/tests/test_corporate_autotopup.py`, `docs/change-log/2026-09-23-autotopup-lock.md`.
- [ ] Test contention/outage skips `run_autotopup_tick`, healthy next tick resumes; retain same-input Stripe idempotency tests.
- [ ] Add strict SET NX EX before each tick, TTL 510 seconds below the 540-second minimum jittered interval; preserve heartbeat/error metrics.
- [ ] Run corporate auto-topup tests; commit `fix(corporate): require Redis ownership for automatic topups`.

### Task 8: Production Redis configuration
Files: `backend/core/middleware.py`, `backend/tests/test_middleware_production_config_guard.py`, `docs/change-log/2026-09-23-production-redis-config.md`.
- [ ] Test missing, unsupported and malformed runtime `REDIS_URL` blocks production, valid redis/rediss succeeds and development remains usable.
- [ ] Validate the actual environment consumed by Redis utilities, separately from rate-limit storage; never echo credentials in errors.
- [ ] Run production-config tests; commit `fix(config): reject production without utility Redis`.

### Task 9: Authoritative Fly deployment gate
Files: existing deploy workflow, `scripts/test_deploy_gate.py`, one gate implementation/runbook file per commit as needed (split to preserve limits).
- [ ] Build/test a gate for exact SHA, repository, branch, CI completion/conclusion, required executed backend/security jobs, and current main. Missing evidence denies.
- [ ] Integrate before secret staging/build/deploy; re-read main after polling. Manual exceptions, if retained, must be explicit and audited; signed-image deploy stays manual.
- [ ] Require readiness/served-SHA configuration for production and retain existing probes.
- [ ] Run `python -m unittest discover -s scripts -p 'test_deploy_gate.py'`; commit each gate/integration slice independently.

### Task 10: API loop ownership and watchdog
Files: `backend/core/lifespan.py`, `backend/tests/test_lifespan_watchdog_coverage.py`, `docs/change-log/2026-09-23-loop-role-ownership.md`.
- [ ] Verify all-role inventory stays intact, API role excludes only registry-designated worker loops, watchdog sees active loops only, dormant loops stay dormant.
- [ ] Wire existing registry helpers into spawning/monitoring without activating a worker deployment.
- [ ] Run lifespan/watchdog/worker suites; commit `fix(workers): align API loop ownership and watchdog`.

### Task 11: Driver statement error boundary
Files: `backend/routes/drivers/tax_exports.py`, its existing statement-email tests, `docs/change-log/2026-09-23-statement-error-boundary.md`.
- [ ] Test invalid weekly/monthly anchors and arbitrary underlying ValueError cannot leak its text, status remains 422 and no email is sent.
- [ ] Replace exception interpolation with fixed actionable copy and redacted diagnostics.
- [ ] Run statement-email tests; commit `fix(drivers): sanitize statement period validation errors`.

## Subsequent waves and external gates

The original incident A–F source/test status is recorded in the final execution record after direct inspection. Existing fixes are verified, not rewritten.

- Wave 0 evidence: native Postgres using production TEXT IDs; applied migration/ACL status; required repository checks; actual flags and provider topology. Device and live evidence remains unknown until reproduced.
- Release recovery: immutable previous image/config capture and forced staging rollback proof before any automatic rollback implementation. Never undo financial data as a side effect of deployment rollback.
- Worker activation: shared worker production-config validation, fleet preflight/process health/metrics, single-loop move, worker death and overlapping-run tests before enabling a worker process.
- State correctness: extend existing transactional RPC and real Postgres acceptance/cancellation/wallet/webhook tests where coverage is actually missing. Keep Stripe HTTP outside DB transactions.
- Assurance: meaningful change-impact gate, executed DAST/Maestro artifacts, staging-origin allowlist for load tests, alert definitions/delivery tests and representative traffic proof.
- Measured optimization: benchmark before PostGIS/direct-claim canaries or OSRM; no GEO/arq/mass import/file-size project without measured need.
- Production go-live remains gated on controlled separate-account/device retest, 0 marker 42883/handler disconnects, timely first/second offers, reconciliation health and verified payment outcomes.
- No A-grade claim on merge: settle the rubric conflict (quarter versus 30 days), retain required soak windows, failover drill and onboarding proof.
