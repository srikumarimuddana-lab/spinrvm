# Path to A — Implementation Plan for an Executing Claude Code Session

**Date:** 2026-09-03 · **Source:** `docs/audit/2026-09-03-engineering-director-teardown-round2.md` (findings and grades) and `docs/audit/2026-09-01-path-to-a-grade.md` (the A bar per dimension) · **Predecessor plan:** `plans/2026-09-01-critical-topology-remediation-plan.md` (WS-A … WS-E; this plan supersedes its sequencing but reuses its subtask shapes where they still apply)
**Audience:** the Claude Code session (Opus-class model) that will execute this. It is written to be pasted as the opening brief of each session, one workstream at a time.
**Scope:** the nine moves the owner selected on 2026-09-03, plus one Blocker that sits inside the first move and must not be skipped. Nothing else. Do not widen.

---

## 0. How to use this document

1. **One workstream per session.** Each `WS-n` below is sized for one Claude Code session (or one day of one). Open the session with the kickoff prompt in §8, naming the workstream.
2. **Re-verify §1 before touching code.** Every `file:line` in this plan was true at commit `bf5d0ac` (2026-09-03). `main` moves ~10 commits a day. Run the §1 commands first; if an anchor moved, update the plan in the same PR (the plan is a living document, edits to it are allowed and expected).
3. **The repo's rules win over this plan.** `CLAUDE.md` (root), `backend/migrations/CLAUDE.md`, `AGENTS.md`, and the `/plan`, `/start`, `/pr`, `/review` commands under `.claude/commands/` define the mandatory process. This plan tells you *what* to build in *which order*; those files tell you *how a change is allowed to land*. Where this plan and `CLAUDE.md` conflict, `CLAUDE.md` wins; say so in the PR.
4. **Every subtask has a verify step.** Do not mark a subtask done until the verify step has actually run and its output is in the Change Impact log. "Tests pass" without the command and the count is not verification.
5. **Stop and ask (`AskUserQuestion`) at every 🛑 in this document.** Those are decisions or infra actions only a human with account access can make. Do everything that does not depend on the answer first.
6. **Never treat a dark launch as done.** The last three dark launches on this repo (H3, outbox, worker split) shipped unwired. Every workstream here ends with a *proof-of-wiring* step: a startup assertion, a metric that moves, a CI job that fails when the thing is missing.

---

## 1. Ground truth to re-verify before starting

Run from the repo root. If any output differs materially from the "expected" column, stop and reconcile before proceeding.

| Anchor | Command | Expected at `bf5d0ac` |
|---|---|---|
| Corporate kill-switch fails open | `grep -n 'proceeding as enabled' backend/services/payment_service.py` | `:942` inside `except Exception as settings_err` |
| Ledger settle flag fails to legacy | `grep -n 'assuming off' backend/services/payment_service.py` | `:1492` |
| PostgREST claim loop, no try/except | `grep -n 'claim_driver_atomic(driver\["id"\])' backend/routes/rides/matching.py` | `:1033`, inside `for driver, eta_sec, _ in ranked:` at `:1030` |
| Existing release pattern to mirror | `grep -n 'ride_offers insert failed' backend/routes/rides/matching.py` | `:1080`, followed by `for d, _ in claimed_drivers: set_driver_available(d["id"], True)` |
| Timeout revert with `id`-only filter | `grep -n '"status": RideStatus.SEARCHING' backend/routes/rides/matching.py` | `:1435` under `update_one("rides", {"id": ride_id}, …)` at `:1431` |
| Admin force-cancel state check | `sed -n 596,600p backend/routes/admin/rides.py` | `if ride.get("status") in ("completed", "cancelled")` only |
| Admin middleware decode-only | `sed -n 133,140p admin-dashboard/src/middleware.ts` | `isTokenValid` = `decodeJwtPayload` + `exp` check, no signature |
| Ride export has no audit call | `sed -n 281,312p backend/routes/admin/rides.py \| grep -c log_admin_action` | `0` |
| Audit helper name | `grep -n '^async def log_admin_action' backend/utils/audit_logger.py` | `:56` |
| LogRocket init without options | `grep -n "LogRocket.init('gfuign/spinr')" rider-app/app/_layout.tsx driver-app/app/_layout.tsx` | `rider :320`, `driver :372` |
| LogRocket kill flag | `grep -rn EXPO_PUBLIC_ENABLE_LOGROCKET rider-app/app/_layout.tsx shared/services/logRocketInstance.ts` | present, default on for iOS |
| Process role never read by API | `grep -c 'should_spawn_on_api\|SPINR_PROCESS_ROLE' backend/core/lifespan.py` | `0` |
| Registry helpers exist | `grep -n 'def resolve_process_role\|def should_spawn_on_api\|WORKER_WAVE1_LOOP_NAMES' backend/core/background_loop_registry.py` | `:24`, `:81`, `:91` |
| Fly has one process group | `grep -n 'processes' backend/fly.toml` | `:56  processes = ["app"]` |
| Deploy scales one group | `grep -n 'scale count' .github/workflows/deploy-fly.yml` | `:117  flyctl scale count 8 …` |
| Worker app exists, unreferenced by Dockerfile CMD | `grep -n '^CMD' backend/Dockerfile` | `uvicorn server:app …` only |
| H3 reconciler never spawned | `grep -rn 'h3_index_reconciler' backend/core/lifespan.py` | no output |
| No migrations in deploy | `grep -c run_migrations .github/workflows/deploy-fly.yml; grep -c release_command backend/fly.toml` | `0` and `0` |
| Health step id for rollback hook | `grep -n 'id: health' .github/workflows/deploy-fly.yml` | `:125` |
| Railway rollback pattern to mirror | `sed -n 105,112p .github/workflows/deploy-backend.yml` | `railway rollback --service spinr-backend \|\| true` |
| Staging workflow self-describes as unprovisioned | `sed -n 6,9p .github/workflows/deploy-backend-staging.yml` | "WILL FAIL UNTIL A HUMAN COMPLETES MANUAL SETUP" |
| Direct-pool flag default | `grep -n 'dispatch_direct_pool_enabled' backend/schemas.py` | `:695  … = False` |
| `service_areas` read per estimate | `sed -n 185p backend/routes/rides/estimates.py` | `get_rows("service_areas", {"is_active": True}, limit=500)` |
| Settings cache to copy | `grep -n '_SETTINGS_TTL\|^async def get_app_settings' backend/settings_loader.py` | `:17` and `:26` |
| Serial WS send in dispatch | `grep -n 'send_personal_message(dispatch_payload' backend/routes/rides/matching.py` | `:1270` inside a per-driver loop |
| Theatre tests | `grep -c '_SQL.index\|read_text()' backend/tests/test_payout_toctou.py backend/tests/test_wallet_apply_delta_contract.py` | both > 0 |
| Real-Postgres tier shape | `ls backend/tests/direct_pool` | `conftest.py test_claim_batch.py test_claim_batch_psycopg3.py test_fixture_smoke.py` |
| Wallet RPC definitions | `grep -l 'wallet_apply_delta' backend/migrations/*.sql` | includes `249_wallet_apply_delta.sql`; find the corporate variant with `grep -l corporate_wallet_apply_delta backend/migrations/*.sql` |
| No packaging | `ls backend/pyproject.toml` | missing; `backend/ruff.toml` exists |
| Dual-import count | `grep -rc 'except ImportError' backend --include=*.py \| awk -F: '{s+=$2} END{print s}'` | 1,012 |
| Lint budget | `grep -o 'max-warnings [0-9]*' admin-dashboard/package.json` | 1751 |

---

## 2. The SDLC loop every workstream follows

Nine gates. A workstream is not done until all nine have evidence in the PR.

| Gate | What happens | Evidence required |
|---|---|---|
| **G1 Intake** | Read §1 anchors for this WS; read the cited audit finding; read every consumer of the function/table/component you will touch (`grep -rn`). Write the blast radius down before editing. | Blast-radius list in the Change Impact log |
| **G2 Decompose** | Run `/plan`. Subtasks of ≤ 3 files, each with a verify step. Register with `TodoWrite`. If > 8 subtasks, split the WS. | `/plan` output pasted in the PR description |
| **G3 Branch** | `/start fix/<slug>` or `/start feat/<slug>` per `.claude/commands/start.md`. One branch per WS. Never work on `main`. | Branch name in PR |
| **G4 Implement** | One subtask, one commit (`/commit`), ≤ ~200 lines. Match existing style. Dual-import pattern, `Decimal` money, `_require_*` guards, insurance-period rows, query-filter rules are all mandatory scaffolding. Never `logger.warning` and continue on DB/auth/payment errors. | Commit per subtask |
| **G5 Verify** | The subtask's verify command, plus the repo's fast checks: `cd backend && ruff check . && ruff format --check . && pytest -m unit` (or the targeted test file); admin: `npm run lint && npx tsc --noEmit && npm run build`; mobile: `npx tsc --noEmit && npm test -- <file>`. For a money/dispatch change, exercise against `mock_supabase_client` **and** state a concrete before/after scenario. | Commands and counts in the Change Impact log |
| **G6 Review** | Run the domain reviewer agents named in the WS via the `Agent` tool before opening the PR. Fix what they find or explain why not, in the PR. Automated PR review (Codex/Claude) is off on this repo; the agents are the review. | Agent verdicts summarised in PR |
| **G7 PR** | `/pr`. Fill every tier of `.github/pull_request_template.md`. Add `docs/change-log/YYYY-MM-DD-<slug>.md` from `docs/templates/CHANGE_IMPACT_LOG.md` for anything touching rides, dispatch, payments, auth, corporate, safety, deploy. Subscribe to the PR and drive it green. | Change-log file in the diff |
| **G8 Release** | User-visible or behaviour-changing work ships **dark** behind an `app_settings` flag (or the WS-7 flag service once it exists), then staging (WS-4), then a canary (one service area or one percent), then on. Infra changes ship with a documented rollback that does not need a second deploy. | Flag name, canary scope, rollback in the change-log |
| **G9 Observe and close** | Name the metric or assertion that proves the change is live and wired. Watch it for the stated window. Update `ACTION_ITEMS.md` (close or open items with IDs), write an ADR via `/adr` for any structural change, mark the WS done here. | Metric name + observed value; ACTION_ITEMS diff |

**Standing rules for all workstreams**
- Never delete or renumber a merged migration; next free number via `ls backend/migrations | sort -V | tail -1`.
- Never repurpose a column; add a new one.
- Never disable, skip, or quarantine a test to get green.
- A CI check red for a reason unrelated to your diff is a bug report against the gate: file a `[CR]` issue, do not force it green.
- Model identifiers never appear in commits, PR text, or code comments.

---

## 3. Human decision points (🛑 stop and ask)

| ID | Decision or action | Needed by | Who |
|---|---|---|---|
| H1 | Provision `spinr-backend-staging` on Fly, a staging Supabase project, and the three secrets `FLY_API_TOKEN_STAGING`, `SUPABASE_STAGING_URL`, `SUPABASE_STAGING_SERVICE_ROLE_KEY`, per `docs/runbooks/staging-environment.md` | WS-4 subtask 4, WS-5, WS-9 | Account owner |
| H2 | Approve `release_command` running migrations on every Fly deploy (this changes who applies schema and when) | WS-4 subtask 1 | Engineering owner |
| H3 | Choose admin auth hardening: `jose` signature verification in middleware **or** Cloudflare Access in front of the admin host (or both) | WS-2 subtask 1 | Engineering + infra owner |
| H4 | Choose LogRocket posture: redaction config **or** default-off on iOS | WS-2 subtask 3 | Product + privacy owner |
| H5 | Choose the flag vendor: Unleash (self-hosted), GrowthBook, or Flagsmith; or an in-repo typed layer as the `2026-09-01` plan's WS-D proposed | WS-7 | Engineering owner |
| H6 | Confirm the Fly worker machine size and count (`worker=1`, `shared-cpu-1x`, 1 GB proposed) | WS-3 subtask 3 | Account owner |
| H7 | Approve flipping `dispatch_direct_pool_enabled` on in production after the staging A/B shows P95 improvement and zero `claim_path=postgrest_fallback` errors for 24 h | WS-5 subtask 1 | Engineering owner |
| H8 | Choose the durable scheduler: arq (proposed) vs Temporal; and the live-position store: Redis GEO vs the existing H3 index | WS-9 | Engineering owner, by ADR |

Everything else in this plan can be done by the executing session without asking.

---

## 4. Workstreams

Order of execution is §5. Each WS lists its own dependencies.

### WS-1 · Correctness: fail-closed kill-switch, claim-loop release, timeout-revert filter (+ admin force-cancel)

**Outcome:** Correctness B → A-. **Effort:** 1–2 days (+1 day for subtask A). **Depends on:** nothing. **Reviewers:** `spinr-money-auditor`, `spinr-dispatch-reviewer`, `spinr-insurance-period-auditor`, `spinr-test-coverage-reviewer`.

> Subtask A is not in the owner's nine-row list, but it is the #1 Blocker in the audit and lives in the same file family. It is included as recommended; drop it only with an explicit decision.

**Subtasks**

| # | Files (≤3) | Change | Verify |
|---|---|---|---|
| A | `backend/routes/admin/rides.py`, `backend/tests/test_admin_rides_cancel_state.py` (new), `docs/change-log/…-admin-cancel-state-guard.md` | In `admin_cancel_ride` (~`:596`): reject `in_progress` (allowed set = `searching`, `driver_assigned`, `driver_accepted`, `driver_arrived`, `scheduled`), mirroring `admin_complete_ride` at `~:757`. Replace `update_ride(ride_id, payload)` with a conditional update whose filter carries the status just read (the same optimistic pattern `routes/drivers/ride_flow.py:331` uses); 0 rows → 409 `ride_state_changed`. On release call `record_period_transition(driver_id, 1)` for the freed driver. Keep the migration-37/38 layered-payload fallback. | New test: `in_progress` → 400; `driver_arrived` → cancelled with Period-1 row written (assert the `record_period_transition` mock call); concurrent status change → 409. `pytest backend/tests/test_admin_rides_cancel_state.py backend/tests/test_ride_state_machine.py` |
| 1 | `backend/services/payment_service.py`, `backend/tests/test_corporate_kill_switch_fail_closed.py` (new) | At `:941-942`: replace warning-and-proceed with `logger.error(..., exc_info=True)` and `return PaymentResult(success=False, error="Corporate billing is temporarily unavailable", status_code=503)`. Add a counter `spinr_payment_settings_read_failed_total{flag="corporate_billing_enabled"}` via `utils/metrics`. | Test: patch `get_app_settings` to raise → 503 result, no wallet delta called, counter incremented. |
| 2 | `backend/services/payment_service.py`, same test file | At `:1491-1493`: keep the fall-back to the legacy path (a settle must not fail because a flag read failed) but log at `error` with `exc_info=True` and increment `spinr_payment_settings_read_failed_total{flag="ledger_atomic_settle_enabled"}`. Document the asymmetry (kill-switch closed, settle-path open) in the change-log and in an ADR (`/adr`, "flag read failure semantics on money paths"). | Test: patch to raise → returns `False`, counter incremented, `logger.error` called. |
| 3 | `backend/routes/rides/matching.py`, `backend/tests/test_dispatch_db_errors.py` | Wrap the PostgREST claim loop body (`:1030-1049`) in `try/except Exception`: on exception, release every driver in `claimed_drivers` via `set_driver_available(id, True)` (the exact block at `:1080-1083`), log at `error` with `exc_info`, then re-raise so the recovery shell re-arms. Do not touch the direct-pool branch. | Extend `test_dispatch_db_errors.py`: `claim_driver_atomic` succeeds twice then raises → both earlier drivers released, exception propagates. |
| 4 | `backend/routes/rides/matching.py`, `backend/tests/test_offer_timeout.py` | At `:1431-1442`: change the filter from `{"id": ride_id}` to `{"id": ride_id, "status": RideStatus.DRIVER_ASSIGNED, "driver_id": driver_id}`; if the update returns 0 rows, log at `info` ("accepted in the same window") and skip the rider/driver notifications. | New test: ride already `driver_accepted` → no revert, no `ride_search_resumed` WS event. |
| 5 | `docs/change-log/…-ws1-correctness.md`, `ACTION_ITEMS.md` | One Change Impact log for subtasks 1–4 (before/after code blocks required); close C54 in `ACTION_ITEMS.md`; note the new counter in the observability section. | `git diff --stat` shows only these files. |

**Flag:** none (bug fixes on live paths; behaviour change is strictly safer). **Rollback:** `git revert` is acceptable here because no data is written differently on the happy path; state it anyway. **Exit criteria:** all new tests green; `pytest -m unit` green; `spinr-dispatch-reviewer` finds no new race; C54 closed; ADR merged.

### WS-2 · Security: verified admin JWT, audited export, redacted replay

**Outcome:** Security B+ → A-. **Effort:** 2 days. **Depends on:** 🛑 H3, H4 for subtasks 1 and 3 (do subtask 2 first, it needs no decision). **Reviewers:** `spinr-security-auditor`, `spinr-admin-rbac-reviewer`, `spinr-regulatory-compliance-checker`.

| # | Files (≤3) | Change | Verify |
|---|---|---|---|
| 1 | `admin-dashboard/src/middleware.ts`, `admin-dashboard/package.json`, `admin-dashboard/e2e/auth-forged-cookie.spec.ts` (new) | Add `jose`; in `isTokenValid` call `jwtVerify(token, new TextEncoder().encode(process.env.JWT_SECRET), { algorithms: ["HS256"] })` (confirm the backend's algorithm in `backend/utils/*jwt*` first) with the existing 30 s leeway; on failure redirect to `/login`. `JWT_SECRET` must be set as a Vercel server env (not `NEXT_PUBLIC_`). Update the stale comment at `:25-26`. If H3 chooses Cloudflare Access instead, keep the code change as defence in depth and add the Access setup to `docs/runbooks/`. | Playwright: forged `admin_token` with future `exp` and bad signature → `/login`; valid token → dashboard. `npm run build`, `npm run check:middleware`. |
| 2 | `backend/routes/admin/rides.py`, `backend/tests/test_admin_rides_export_audit.py` (new) | In `admin_export_filtered_rides` (`:281`): replace `_: dict = Depends(get_admin_user)` with a named `admin_user`, call `await log_admin_action(admin_user, action="rides.export", target_type="rides", details={filters…, "row_count": n})` **before** returning (and before the query if the helper supports a two-phase pattern; mirror `reveal-sin` at `routes/admin/drivers.py:~3858`). Above 1,000 rows route through the existing `export_approvals` flow (read `routes/admin/export_approvals.py` first). | Test: export → `log_admin_action` awaited once with `rides.export` and the filter dict; > 1,000 rows → approval path. |
| 3 | `rider-app/app/_layout.tsx`, `driver-app/app/_layout.tsx`, `shared/services/logRocketInstance.ts` | Per H4: either pass `{ network: { requestSanitizer, responseSanitizer }, redactionTags: ["data-private"], shouldCaptureIP: false }` into `LogRocket.init` and tag map/address/phone views with `data-private`, **or** flip the `EXPO_PUBLIC_ENABLE_LOGROCKET` default to off for both platforms. Either way add `useLogRocketPrivacyScreen()` to `ride-status`, `ride-in-progress`, `ride-completed`, and driver `(tabs)/index`. | Jest: init called with the sanitizer options (or not called when flag off); `npx tsc --noEmit` in both apps. On-device capture cannot be verified in CI: say so in the change-log. |
| 4 | `docs/change-log/…-ws2-security.md`, `ACTION_ITEMS.md`, `docs/adr/013-admin-session-verification.md` | Change Impact log with PIPEDA note; ADR for the auth choice. | Files present; `/legal-check` passes if `docs/legal` references replay. |

**Flag:** LogRocket already has one; middleware change ships directly (fails closed to `/login`, which is the safe direction). **Rollback:** Vercel instant rollback for the admin build; `EXPO_PUBLIC_ENABLE_LOGROCKET` is a build-time flag (OTA needed to revert, state it). **Exit criteria:** forged-cookie e2e green; audit row visible in `audit_logs` on staging export; `spinr-security-auditor` verdict SAFE.

### WS-3 · Wire the worker tier and spawn the H3 reconciler

**Outcome:** Architecture C+ → B. **Effort:** 2–3 days. **Depends on:** 🛑 H6 for subtask 3. **Reviewers:** `spinr-realtime-reliability-reviewer`, `spinr-cicd-infra-reviewer`, `spinr-background-loop` skill (read it first).

| # | Files (≤3) | Change | Verify |
|---|---|---|---|
| 1 | `backend/core/lifespan.py`, `backend/tests/test_lifespan_watchdog_coverage.py` | Resolve the role once at startup: `role = resolve_process_role(os.environ.get("SPINR_PROCESS_ROLE"), env=settings.ENV)`. In `_spawn` (`:261`) skip when `not should_spawn_on_api(name, role)` and log the skip at `info`. Add a startup assertion: `set(LOOP_CATALOG names) == set(spawned) ∪ set(WORKER_WAVE1_LOOP_NAMES) ∪ {"loop_watchdog (5min)"}`; raise in production, error-log elsewhere. | Test: role `api` → the three wave-1 names are not spawned; role `all` → all spawned; a catalog name missing from both sets fails the assertion. |
| 2 | `backend/core/lifespan.py`, `backend/core/background_loop_registry.py`, `backend/tests/test_lifespan_watchdog_coverage.py` | Spawn `h3_index_reconciler_loop` (`utils/h3_index_reconciler.py:74`) with the catalog's name and placement; since it is placement `deferred`, decide in this subtask whether it belongs to `api`, `worker_wave1`, or a new `worker_wave2` and record it in the registry. Add it to `_WATCHDOG_LOOP_NAMES`. | Watchdog-coverage test passes with the new name; `grep -c h3_index_reconciler backend/core/lifespan.py` ≥ 2. |
| 3 | `backend/fly.toml`, `.github/workflows/deploy-fly.yml`, `.github/workflows/bootstrap-fly.yml` | Add `[processes] app = "uvicorn server:app …" worker = "uvicorn worker:app --workers 1 …"`; set `SPINR_PROCESS_ROLE=api` under `[env]` for `app` via `[processes]`-scoped env if Fly supports it, else pass through the command; change `flyctl scale count 8` to `flyctl scale count app=8 worker=1`. Health check for the worker uses its own `/health`. **Never** run a bare `scale count 8` again (it would create 8 workers). | `flyctl config validate` in CI (add the step); after deploy, `flyctl status` shows both groups; `spinr_loop_heartbeat` (or the existing loop-heartbeat gauge) for `push_retry` reports from the worker machine only. |
| 4 | `docs/change-log/…-ws3-worker-tier.md`, `ACTION_ITEMS.md`, `docs/adr/014-worker-process-group.md` | Close C53 items that this wires; document the `SPINR_PROCESS_ROLE` contract and the "never bare scale count" rule in the runbook. | Files present. |

**Flag:** `SPINR_PROCESS_ROLE` defaults to `all`, so a machine with no role env behaves exactly as today; the worker group is additive. **Rollback:** `flyctl scale count worker=0` and unset the role env on `app`; no code revert needed. **Exit criteria:** the three wave-1 loops run in exactly one place; the outbox poller is live on the worker (enable `outbox_receipts_enabled` only after WS-4's staging exists); startup assertion in production log; `spinr-realtime-reliability-reviewer` finds no double-spawn.

### WS-4 · Migrations in deploy, rollback on health failure, staging

**Outcome:** Process B → A-. **Effort:** 1 week (subtask 4 is mostly waiting on H1). **Depends on:** 🛑 H2 (subtask 1), 🛑 H1 (subtask 4). **Reviewers:** `spinr-cicd-infra-reviewer`, `spinr-migration-reviewer`.

| # | Files (≤3) | Change | Verify |
|---|---|---|---|
| 1 | `backend/fly.toml`, `backend/scripts/run_migrations.py`, `backend/tests/test_run_migrations_release_guard.py` (new) | Add `[deploy] release_command = "python -m scripts.run_migrations --release"` (confirm the import root the Fly image uses: `CMD` runs `uvicorn server:app`, so top-level). Add a `--release` mode to `run_migrations.py` that: refuses to run if any pending file is in `NEVER_APPLY` (exit 2), refuses if `DATABASE_URL` is unset (exit 2), prints `--status` first, applies pending, exits non-zero on any error so Fly aborts the deploy. Set the `DATABASE_URL` secret on Fly (🛑 owner). | Test: pending `NEVER_APPLY` file → exit 2; happy path applies in order; error → non-zero. Dry run locally against the CI Postgres service. |
| 2 | `backend/migrations/CLAUDE.md`, `docs/runbooks/deploy-migrations.md` (new) | Write the expand/contract rule: a column is added in deploy N, read in deploy N+1, dropped only after a full release cycle; every migration must be safe to run before the code that uses it ships. | Doc review by `spinr-migration-reviewer`. |
| 3 | `.github/workflows/deploy-fly.yml` | After the `health` step (`:125`), add `Rollback on health check failure` with `if: failure() && steps.health.outcome == 'failure'` running `flyctl releases rollback -a "$FLY_APP" --yes` (check the exact subcommand for the pinned flyctl version) and `::error::`. Mirror `deploy-backend.yml:105-112`. | Workflow lint (`actionlint` if present, else YAML parse); one deliberate failing health probe on staging (subtask 4) proves the branch. |
| 4 | `.github/workflows/deploy-backend-staging.yml`, `backend/fly.staging.toml`, `docs/runbooks/staging-environment.md` | After H1: remove the "WILL FAIL" banner, create the `staging` branch, add `release_command` to the staging toml too, and add a nightly `workflow_dispatch`/`schedule` that deploys `main` to staging. Point `dast-zap-baseline.yml`'s `STAGING_URL` at it. | First staging deploy green; `/health` on staging returns the deployed SHA; ZAP baseline runs for the first time. |
| 5 | `docs/change-log/…-ws4-deploy-pipeline.md`, `ACTION_ITEMS.md`, `docs/adr/015-migrations-in-deploy.md` | Close C1/C44 lineage items and E1; open a follow-up for canary/bluegreen. | Files present. |

**Flag:** none; each step is additive and reversible by config. **Rollback:** remove `release_command` (one-line toml change, no code); delete the rollback step. **Exit criteria:** a no-op migration deploys through the pipeline with `schema_migrations` updated by the release command; a deliberately broken staging deploy rolls back automatically; staging exists and is on a nightly cadence.

### WS-5 · Performance: flip the direct pool after a staging A/B, cache `service_areas`, gather WS sends

**Outcome:** Performance B- → A-. **Effort:** 1 week plus the staging run. **Depends on:** WS-4 subtask 4 (staging) and 🛑 H7 for subtask 1; subtasks 2–3 have no dependency and can go first. **Reviewers:** `spinr-performance-sla-reviewer`, `spinr-dispatch-reviewer`.

| # | Files (≤3) | Change | Verify |
|---|---|---|---|
| 1 | `loadtest/results/…`, `docs/audit/2026-xx-xx-t16-direct-pool-ab.md`, `ACTION_ITEMS.md` | Deploy `main` to staging; run the harness twice per `loadtest/README.md:51-61` (`preauth_bots.py` then `locust … -u 600`), once with `dispatch_direct_pool_enabled=false`, once `true` (admin PUT, `routes/admin/settings.py`). Compare `spinr_dispatch_attempt_duration_ms{phase}` and `market:offer-to-accept` P95, and `spinr_dispatch_claim_path_total{path}`. Then 🛑 H7 → flip in production for one service area first, then all. | A/B table in the audit doc; production `claim_path=direct_pool` count rising, `…fallback` at 0 for 24 h. |
| 2 | `backend/settings_loader.py` (or a new `backend/utils/service_area_cache.py`), `backend/routes/rides/estimates.py`, `backend/tests/test_service_area_cache.py` (new) | Add `get_active_service_areas()` with the same 60 s in-process TTL as `get_app_settings` (`:17-54`), invalidated from the admin service-area write path (grep `service_areas` in `routes/admin/service_areas.py`). Replace the read at `estimates.py:185`. Keep the `limit=500`. | Test: two calls within TTL → one `get_rows`; admin write → next call refetches. Before/after `perf_rides_before.json` numbers. |
| 3 | `backend/routes/rides/matching.py`, `backend/tests/test_dispatch_notify_gather.py` (new) | Collect the per-driver `send_personal_message` coroutines (`:1270`) and run them with `asyncio.gather(..., return_exceptions=True)`; log each failure at `error` with `driver_id`; keep the FCM `spawn` as is. Do the same for the per-driver `record_period_transition` loop at `~:1112` only if `spinr-insurance-period-auditor` agrees ordering does not matter (it writes independent rows). | Test: three drivers → three sends started before any completes; one send failing does not block the others. `spinr_dispatch_attempt_duration_ms{phase="notify"}` drops on staging. |
| 4 | `docs/change-log/…-ws5-performance.md` | Change Impact log with the A/B table and the SLA row before/after. | File present. |

**Flag:** `dispatch_direct_pool_enabled` (exists); the cache is additive. **Rollback:** flag off (documented as not symmetric in `docs/change-log/2026-09-02-…` — read it); cache has a `SERVICE_AREA_CACHE_TTL=0` escape hatch. **Exit criteria:** dispatch P95 inside the 2 s SLA on staging at 600 users; estimate path shows one fewer DB call in `perf_rides_*`; production flag on for all areas with zero fallbacks for a week.

### WS-6 · Testing: real-Postgres wallet RPC tests, executed replacements for the theatre tests, missing edge cases

**Outcome:** Testing B → A-. **Effort:** 1 week. **Depends on:** WS-1 subtask A (for the admin-cancel test). **Reviewers:** `spinr-test-coverage-reviewer`, `spinr-money-auditor`.

| # | Files (≤3) | Change | Verify |
|---|---|---|---|
| 1 | `backend/tests/direct_pool/test_wallet_apply_delta.py` (new), `backend/tests/direct_pool/conftest.py` | Using the existing psycopg fixture, apply `249_wallet_apply_delta.sql` (and its later amendments: `grep -l wallet_apply_delta backend/migrations/*.sql`) into the throwaway DB, then test: idempotent replay with the same key returns the same balance; concurrent deltas from two connections serialise (row lock); floor clamp rejects a negative balance; `_f`/`_round` boundaries at cents. | `TEST_DATABASE_URL=… pytest tests/direct_pool -c /dev/null --confcutdir=tests/direct_pool`; runs in `ci.yml:172`. |
| 2 | `backend/tests/direct_pool/test_corporate_wallet_apply_delta.py` (new) | Same shape for `corporate_wallet_apply_delta` (locate its migration first). Include the allowance-cap and master-wallet-fallback branches. | As above. |
| 3 | `backend/tests/test_payout_toctou.py`, `backend/tests/test_wallet_apply_delta_contract.py`, `backend/tests/test_payout_request_conflict.py` (new) | Keep the two source-text tests but rename them `test_*_source_tripwire.py` so nobody mistakes them for behaviour tests; add an executed test that calls `request_instant_payout` with the mocked Supabase client raising the partial-unique-index violation and asserts the 409 path. | New test fails when the 409 branch is removed. |
| 4 | `backend/tests/test_admin_rides_cancel_state.py` (from WS-1 A), `backend/tests/test_webhook_ordering.py` (new) | Webhook ordering: `payment_intent.succeeded` arrives while the ride is still `in_progress` → handler defers or records without marking paid twice; second delivery after completion settles exactly once. Read `routes/webhooks.py` handlers for `payment_intent.succeeded` first. | Tests fail on a deliberately broken ordering guard. |
| 5 | `.github/workflows/ci.yml`, `backend/pytest.ini` | Raise `--cov-fail-under` 60 → 65 (the ratchet `pytest.ini` promised); ensure the new `direct_pool` files are collected in the real-Postgres step. | CI green at the new floor. |

**Flag:** none. **Rollback:** none needed. **Exit criteria:** both wallet RPCs exercised on real Postgres in CI; zero tests whose only assertions are on source text without a "tripwire" name; coverage gate 65.

### WS-7 · Feature-flag service with last-known-good defaults

**Outcome:** Architecture B → A-. **Effort:** 1 week. **Depends on:** 🛑 H5; WS-4 staging for the canary proof. **Reviewers:** `spinr-security-auditor` (SDK keys), `spinr-money-auditor` (kill-switch migration), `spinr-edge-case-reviewer`.

| # | Files (≤3) | Change | Verify |
|---|---|---|---|
| 1 | `docs/adr/016-feature-flags.md`, `backend/utils/flags.py` (new), `backend/tests/test_flags.py` (new) | Per H5. Typed accessor `flags.get_bool(name, *, default, ctx)` with: vendor SDK if configured, else `app_settings` (the existing 60 s cache), else the compiled-in default; every read records `spinr_flag_read_total{name, source}`; read failure returns **last-known-good** (cached) then default, never raises on the hot path; explicit `fail_mode="closed"` option for kill-switches. | Unit tests for every fallback tier. |
| 2 | `backend/services/payment_service.py`, `backend/routes/rides/matching.py`, `backend/utils/outbox_worker.py` (or wherever `outbox_receipts_enabled` is read) | Migrate `corporate_billing_enabled` (fail closed), `ledger_atomic_settle_enabled`, `dispatch_direct_pool_enabled`, `outbox_receipts_enabled` to the accessor. Do not change semantics decided in WS-1. | Existing tests pass unchanged; `grep -rn 'get_app_settings().get("<flag>")'` for these four → 0. |
| 3 | `backend/utils/flags.py`, `backend/routes/admin/settings.py`, `admin-dashboard/src/app/dashboard/settings/page.tsx` | Add percentage and per-service-area targeting (`ctx={"service_area_id": …, "user_id": …}`) using a stable hash; expose the flag list read-only in admin with source and last-known-good timestamp. | Test: 5 % bucket is stable per id; admin page renders the list. |
| 4 | `shared/config/flags.ts` (new), `rider-app`, `driver-app` layout files | Client accessor with the same fallback order (vendor → `/settings/public` → bundled default) for the two mobile flags that exist today (`EXPO_PUBLIC_ENABLE_LOGROCKET`, min-app-version). | Jest tests; `npx tsc --noEmit`. |
| 5 | `docs/change-log/…-ws7-flags.md`, `ACTION_ITEMS.md` | Close C5 lineage; record the vendor and its key rotation runbook. | Files present. |

**Flag:** the accessor itself is guarded by `FLAGS_PROVIDER` env (`settings` default = today's behaviour). **Rollback:** `FLAGS_PROVIDER=settings`. **Exit criteria:** one real canary on staging (direct pool at 5 % of dispatches by service area) observed via `spinr_flag_read_total`; the four money/dispatch flags read through the accessor; vendor outage test shows last-known-good behaviour.

### WS-8 · Maintainability: package the backend, remove the dual imports, enforce layering, split the god files, ratchet lint

**Outcome:** Maintainability C → B+. **Effort:** 3–4 weeks, mechanical, best done in a quiet window after WS-1…WS-3 merge. **Depends on:** WS-1, WS-2, WS-3 merged (to avoid conflicts in `matching.py`, `admin/rides.py`, `lifespan.py`). **Reviewers:** `spinr-test-coverage-reviewer` (no coverage loss), `spinr-admin-rbac-reviewer` (router splits keep gating), `spinr-cicd-infra-reviewer`.

| # | Files | Change | Verify |
|---|---|---|---|
| 1 | `backend/pyproject.toml` (new), `backend/Dockerfile`, `.github/workflows/ci.yml` | Declare `backend` as a package (`[project] name = "spinr-backend"`, `[tool.setuptools] packages = find:`), `pip install -e .` in the Dockerfile and CI, keep `ruff.toml` as is. Do not remove any import yet. | Image builds; full suite green; `python -m backend.server` and the Dockerfile `CMD` both start. |
| 2 | one mechanical PR, no other changes | Script the removal of all 1,012 `try: from .x import … except ImportError: from x import …` blocks to the single package-relative form. Review the script, not the 1,012 hunks. Retire the "dual import pattern" rule from `CLAUDE.md` in the same PR. | `grep -rc 'except ImportError' backend --include=*.py` → ~0 (leave genuine optional-dependency guards, list them); full suite green; both entrypoints start. |
| 3 | `backend/.importlinter` (new), `.github/workflows/ci.yml` | Contracts: `routes` may import `services`, `schemas`, `utils`; only `services` and `repositories` may import `db_supabase`/`repositories`. Start as a non-blocking CI step that prints violations (~100 route files); ratchet to blocking per package as each is fixed. | Violation count published in the CI log; number goes down each PR. |
| 4 | per file, one PR each: `routes/admin/drivers.py`, `routes/admin/rides.py`, `services/driver_import_service.py`, `routes/webhooks.py`, `services/payment_service.py`, `routes/auth.py`, `routes/drivers/subscriptions.py` | Split by sub-resource using the existing sub-router pattern (`routes/admin/__init__.py` mounts). Every moved handler keeps its `require_module` dependency (assert with a test that lists mounted paths before and after). No behaviour change. | Route table identical before/after (`app.routes` snapshot test); coverage unchanged. |
| 5 | `admin-dashboard/package.json`, `.husky/pre-commit`, `rider-app`/`driver-app` `package.json` | `lint-staged` with `eslint --max-warnings 0` on staged files (admin) and `expo lint --max-warnings 0` on staged files (mobile); lower the admin budget by 100 per PR from 1751 as files are touched. Add `no-explicit-any` as `warn` in admin so it appears in the trend. | Pre-commit blocks a new warning; budget number in `package.json` decreases monotonically. |
| 6 | `ARCHITECTURE.md`, `CLAUDE.md`, root clutter | Fix Expo 57/Fly primary; move `SPINR_CODE_REVIEW.md` and the root `.docx/.csv/.sql` under `docs/audit/` (or delete), delete `frontend/`; split `CLAUDE.md` into rules and `docs/history/` narrative. | `ls` shows no review artifacts at root; `ARCHITECTURE.md` matches `package.json`. |

**Flag:** none; every step is refactor-only with route-table and coverage snapshots as the safety net. **Rollback:** revert the single PR. **Exit criteria:** no file > 2,000 lines in `backend/routes` or `services`; dual imports gone; import-linter blocking for `services` and `repositories`; lint budget < 1,000 and falling; docs match reality.

### WS-9 · Uber-shaped where it pays: lease scheduler, live-position read path, OSRM estimates

**Outcome:** Architecture and Performance → A. **Effort:** 6–8 weeks. **Depends on:** WS-3 (worker tier), WS-4 (staging), WS-7 (flags), 🛑 H8. **Reviewers:** `spinr-realtime-reliability-reviewer`, `spinr-dispatch-reviewer`, `spinr-performance-sla-reviewer`, `spinr-surge-auditor` (OSRM changes distance inputs to fares).

| # | Files | Change | Verify |
|---|---|---|---|
| 1 | `docs/adr/017-durable-scheduler.md`, `backend/jobs/` (new package), `backend/worker.py` | Per H8: introduce arq (Redis-backed) on the worker process group. Move loop bodies unchanged into `backend/jobs/<name>.py` as arq cron functions with `unique=True` leases; keep `_spawn` for the API-role loops only. Migrate in waves of ≤ 5 loops per PR, starting with the read-only/alert loops, then reconcilers, then money loops last. Every job keeps its atomic DB claim; the lease only removes duplicate scans. | Per wave: the loop's heartbeat gauge reports from exactly one place; `spinr_reconcile_repairs_total` unchanged or lower; leader-lock fail-open code paths deleted for migrated loops. |
| 2 | `backend/utils/h3_location_index.py`, `backend/services/dispatch_candidates.py`, `backend/routes/rides/matching.py` | Finish C53's four unwired pieces; run `dispatch_geo_provider=shadow` for a week (WS-7 targeting by service area); compare in-radius parity; flip to `h3`. Postgres remains the validator at claim time. If H8 picks Redis GEO instead, implement it behind the same provider interface. | `shadow_skipped` = 0; parity ≥ 99 % over the week; dispatch `phase="candidates"` P95 falls; DB calls per dispatch drop by the candidate query. |
| 3 | `deploy/osrm/`, `backend/utils/route_distance.py`, `backend/routes/rides/estimates.py`, `backend/services/fare_service.py` | Deploy OSRM (the Dockerfile and smoke test already exist) as a Fly app; use it for the estimate distance and the ranking ETAs; keep Google Directions for the quote-locked confirm. Fare parity test: OSRM vs Google distance within 2 % on a fixed set of Saskatoon/Regina routes, or the estimate falls back to Google. | Parity test in CI against a recorded fixture; `_PRICING_ROUTE_WAIT_S` worst case no longer on the estimate path; Maps bill down. `spinr-surge-auditor` confirms no surge or corporate-exemption change. |
| 4 | `docs/change-log/…-ws9-*.md`, `ACTION_ITEMS.md`, ADRs | One change-log per wave. | Files present. |

**Flags:** `dispatch_geo_provider`, a new `estimates_router_provider` (`google` default), and per-job `JOB_<name>_ENABLED`. **Rollback:** flags; arq jobs can be disabled individually while `_spawn` fallbacks remain for one release. **Exit criteria:** zero loops with a fail-open leader lock; dispatch read path off Postgres; estimate P95 inside 300 ms for OSRM-served quotes; regrade to A on both dimensions with the metrics attached.

---

## 5. Sequencing and parallelism

Weeks are calendar weeks with one executing session per workstream; two sessions can run in parallel where the arrows allow.

```
Week 1   WS-1 ──────┐            WS-2 (subtask 2 now; 1 and 3 after H3/H4)
Week 2   WS-3 ──────┼── merge ── WS-6 (subtasks 1-3, 5)
Week 3   WS-4 (1-3) │            WS-6 (subtask 4, after WS-1 A)
Week 4   WS-4 (4, after H1) ─────── WS-5 (2-3 now; 1 after staging + H7)
Week 5-6 WS-7 (after H5) ───────── WS-8 (1-3, quiet window)
Week 7-9 WS-8 (4-6)
Week 10+ WS-9 (after WS-3, WS-4, WS-7, H8), in waves
```

Rules:
- Do not start WS-8 subtask 2 (the 1,012-block removal) while any other backend PR is open; it touches every file.
- WS-5 subtask 1 and WS-9 cannot be verified without staging (WS-4 subtask 4). If H1 stalls, do everything else and say so in `ACTION_ITEMS.md` rather than waiting silently.
- Merge order within a week: correctness before performance, infra before flags, mechanical refactors last.

---

## 6. Risk register

| Risk | Where | Mitigation |
|---|---|---|
| A refactor PR silently drops a route's `require_module` gate | WS-8 subtask 4 | Route-table snapshot test asserting path → dependency pairs before and after |
| `release_command` runs a half-written migration and blocks every deploy | WS-4 subtask 1 | `--release` refuses `NEVER_APPLY`; migration-check CI stays; staging deploys first |
| Bare `flyctl scale count 8` creates 8 workers | WS-3 subtask 3 | Runbook rule + a CI grep that fails the workflow if `scale count` lacks a group name |
| Flag vendor outage flips a money flag | WS-7 | Last-known-good then compiled default; kill-switches `fail_mode="closed"`; chaos test in WS-7 exit criteria |
| Direct-pool flag on causes Supavisor pool exhaustion | WS-5 subtask 1 | Staging A/B at 600 users first; `spinr_db_direct_pool_wait_ms` alert; flag rollback is documented as asymmetric, read the 2026-09-02 change-log |
| OSRM distance drifts from Google and under-prices fares | WS-9 subtask 3 | 2 % parity gate with fallback to Google; `spinr-surge-auditor` review |
| Squash-merge drops a late commit (happened twice, see C57/C62) | all | Watch CI on the actual last-pushed SHA before merging; never push after approval without re-checking the PR head |
| Session rate limits interrupt a multi-agent review | G6 | Run reviewer agents one at a time when a limit was hit in the last hour |

---

## 7. Definition of done for "A"

Regrade only when every row has evidence.

| Dimension | Exit criteria (measurable) | Proven by |
|---|---|---|
| Correctness | No ride, driver, or insurance write that must agree happens outside one transaction or one conditional update; admin overrides obey the state machine; reconciler repairs = 0 for 30 days | WS-1, WS-5, WS-9; `spinr_reconcile_repairs_total` |
| Security | Admin sessions signature-verified; every PII export audited; no third-party replay without redaction; kill-switches fail closed | WS-2, WS-1, WS-7 |
| Error handling / telemetry | Money-path flag reads never silent; every fallback counted; loops restart or page (from the round-2 plan's Phase 2, not in this document's scope but required for the letter grade) | WS-1, WS-7 + Phase 2 items |
| Performance | Dispatch P95 < 2 s at 600 users on staging; estimate path one DB call lighter; direct pool on in production with zero fallbacks for a week | WS-5, WS-9 |
| Architecture | Loops run in a worker tier with leases; flags are a typed layer with canary targeting; dispatch reads do not hit Postgres | WS-3, WS-7, WS-9 |
| Maintainability | No file > 2,000 lines in routes/services; zero dual-import blocks; import-linter blocking; lint budget < 1,000 and falling | WS-8 |
| Testing | Wallet RPCs on real Postgres in CI; no source-text-only test without a tripwire name; coverage gate ≥ 65 and ratcheting | WS-6 |
| Process | Migrations in deploy; rollback on health failure; staging on a nightly cadence | WS-4 |

---

## 8. Kickoff prompt for the executing session

Paste this, replacing `WS-n`:

> You are executing **WS-n** of `plans/2026-09-03-path-to-a-implementation-plan.md` in the Spinr repository. Read that file's §0, §1, §2, §3, and the WS-n section in full before doing anything else, then read `CLAUDE.md`. Re-run every §1 command that WS-n depends on and reconcile any moved anchor in the plan itself. Run `/plan` to decompose WS-n into subtasks of ≤ 3 files with a verify step each, register them with `TodoWrite`, then `/start` a branch named for the workstream. Implement one subtask per commit, run its verify step and the repo's fast checks before each commit, and stop at every 🛑 with `AskUserQuestion` after finishing everything that does not depend on it. Before opening the PR, run the reviewer agents named in the WS section and address their findings. Open the PR with `/pr`, include the Change Impact log file the WS names, subscribe to the PR, and drive it green. Finish by updating `ACTION_ITEMS.md`, writing the ADR the WS names via `/adr`, and stating in the PR the metric or assertion that proves the change is wired, with its observed value. Do not widen scope beyond WS-n; if you find something outside it, add an `ACTION_ITEMS.md` entry and move on.
