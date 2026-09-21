# Change Impact & Risk Log — Railway service name fixed in `deploy-backend.yml` / `standby-parity-monitor.yml` (ACTION_ITEMS.md C5)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | Claude Code session (ACTION_ITEMS.md C5 follow-up, Railway MCP now available) |
| Surface(s) | backend (deployment config only — no application code) |
| Domain (Sentry tag) | admin (deployment/CI infra; not a runtime-request-path change) |
| PR / commit link | branch `fix/c5-railway-service-name` |
| Related issue or gap ID | ACTION_ITEMS.md C5 |

## 1. Issue / gap identified

`deploy-backend.yml` and `standby-parity-monitor.yml` both hardcode `RAILWAY_SERVICE: spinr-backend`, but no service by that exact name exists in the `cooperative-harmony` Railway project — every run of both workflows failed at the first Railway CLI call that names the service (`railway variables --service spinr-backend` / `railway up --service spinr-backend`) with `Service 'spinr-backend' not found`.

## 2. Root cause

The service name was hardcoded into these two workflow files without ever being verified directly against Railway's own service list. As documented across ACTION_ITEMS.md C5's prior updates (2026-09-04, 2026-09-14), earlier sessions correctly diagnosed the symptom (wrong service name) but had no Railway CLI/API/MCP access to confirm the actual name, so the fix was deliberately left as a human action ("escalate, don't silently ship" — CLAUDE.md gate 9) rather than guessed at. This session had Railway MCP access and could resolve it directly: `list-projects` → `list-services` → `describe-service` in `cooperative-harmony` shows the real service is named **`spinrvm`**, not `spinr-backend`. `describe-service` confirms it is in fact the backend service — it sources from `srikumarimuddana-lab/spinrvm` on branch `main` with `rootDirectory: /backend` — it was simply never named to match the workflow constant.

A second, more important root-cause correction: prior updates on this item assumed (reasonably, given the workflow's own repeated failures) that the standby was actually stale/drifting from `main`. `describe-service`'s deployment history shows this was never true — `spinrvm` has been auto-deploying successfully on every push to `main` the whole time, via Railway's own native GitHub integration (a mechanism independent of `deploy-backend.yml`'s script; `deploy-backend.yml`'s own header comment documents this as a valid "Alternative (no tokens needed at all)" path). What was actually broken this entire time was only `deploy-backend.yml`'s (and `standby-parity-monitor.yml`'s) own extra verification layer on top of an already-working deploy path, not the deploy itself.

## 3. Fix / remediation

Changed `RAILWAY_SERVICE: spinr-backend` → `RAILWAY_SERVICE: spinrvm` in both `.github/workflows/deploy-backend.yml` (line ~64) and `.github/workflows/standby-parity-monitor.yml` (line ~49). No other files reference `RAILWAY_SERVICE` or a hardcoded Railway service name (grep below). This restores three checks in `deploy-backend.yml` that could never previously execute successfully because the service-name lookup failed before reaching them:
- **Env-completeness check** ("Verify required Railway variables are set") — fails the job if any name listed in `deploy/backend-required-env.txt` (scoped to `railway`/`both`) is missing from the Railway service's variable set, so a Railway deploy can never boot silently missing `ENV`/`SUPABASE_REGION`/`SENTRY_DSN` etc. the way Fly gets from `fly.toml`.
- **Build-sha stamping** ("Stamp build info") — writes `backend/build_info.json` with the pushed commit's sha/ref/timestamp into the Docker build context so the running replica can report it via `GET /deploy-info`.
- **Post-deploy serving verification** ("Verify the deployed build SHA is serving") — polls `/deploy-info` for up to 2 minutes after deploy and fails the job if the running sha doesn't match `GITHUB_SHA`, catching a deploy that "succeeded" without the new commit actually serving.

`standby-parity-monitor.yml`'s daily parity check (which also calls `railway variables --service "${RAILWAY_SERVICE}"`) is restored the same way.

**Alternative considered:** rename the Railway service itself from `spinrvm` to `spinr-backend` to match the workflow's existing hardcoded value, instead of changing the workflow. Rejected — renaming a live, currently-serving Railway service is a change to a resource with a live domain (`spinr-backend-production.up.railway.app`) and non-zero risk of an unexpected rename side effect (e.g. domain/URL implications), for zero functional benefit over just fixing two lines of workflow YAML that are the actual source of the mismatch. Editing the workflow files is strictly lower-risk and is also the option ACTION_ITEMS.md's own 2026-09-14 update already named as option (b).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to these two workflow files.** Grepped `.github/workflows/` and the repo root for `RAILWAY_SERVICE` and for other `spinr-backend`-shaped strings that could be a second hardcoded Railway service reference:
  - `RAILWAY_SERVICE` appears in exactly 4 files: `deploy-backend.yml` and `standby-parity-monitor.yml` (both fixed here), plus `ACTION_ITEMS.md` and `docs/change-log/2026-09-04-railway-standby-parity-automation.md` (both prose references, not executable config — not touched by this fix; `ACTION_ITEMS.md`'s C5 entry is updated separately, see below).
  - Every other `spinr-backend` string across `.github/workflows/` (`security-gates.yml`, `ci.yml`) is a **Docker image tag** (`spinr-backend:ci-<sha>`, `spinr-backend:sec-<sha>`, `ghcr.io/<repo>/spinr-backend:<tag>`) — an unrelated, correct naming convention for the container image itself, not the Railway service.
  - The Fly app name `spinr-backend-yyz` / `spinr-backend-staging` (in `deploy-fly.yml`, `bootstrap-fly.yml`, `deploy-fly-signed-image.yml`, `deploy-backend-staging.yml`, `standby-parity-monitor.yml`'s `FLY_APP`) is a separate, correct value for a different provider — explicitly out of scope per this fix's instructions, not touched.
  - No application code (`backend/`), no migration, no Supabase schema, no rider/driver/admin-facing behavior is touched by this change at all.
- **Nothing else reads `RAILWAY_SERVICE`** as an input from elsewhere (it's a workflow-local `env:` value only consumed within its own job's steps via `${RAILWAY_SERVICE}`/`${{ env.RAILWAY_SERVICE }}`), so there's no cross-workflow or cross-surface coupling to account for.
- **Interaction with background loops / ride state machine / money:** none — this is CI/CD deployment configuration, not runtime request-path code. It does not touch `backend/core/lifespan.py`, ride state, wallet deltas, or Stripe.
- **Regression risk of the fix itself:** effectively none on the happy path (a name string change that now matches reality). The only way this introduces a *new* failure is if `spinrvm` turns out not to have `railway up`/`railway variables` permissions the old (nonexistent) name implicitly avoided testing — but `describe-service` already confirms the service exists, is correctly configured, and already carries every required variable, so this is not expected.

## 5. User-experience effect

None. This is an internal deployment-tooling fix; no rider, driver, corporate-admin, or internal-admin-facing surface changes. Not visible mid-session to any user. (It does affect *ops/on-call* visibility: once this workflow runs green, the standby-parity monitor's daily check and `deploy-backend.yml`'s post-deploy verification become meaningfully informative again instead of failing at the same step every time regardless of actual drift.)

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/deploy-backend.yml` | `RAILWAY_SERVICE: spinr-backend` → `RAILWAY_SERVICE: spinrvm` | match the real Railway service name in `cooperative-harmony`, confirmed via Railway MCP |
| `.github/workflows/standby-parity-monitor.yml` | `RAILWAY_SERVICE: spinr-backend` → `RAILWAY_SERVICE: spinrvm` | same |
| `ACTION_ITEMS.md` | C5 entry marked resolved; added a dated 2026-09-21 Update block with the confirmed root cause, correction of the earlier "stale/drifting" language, and the fix | house rule: log resolution history in place, in the entry's own style |
| `docs/change-log/2026-09-21-c5-railway-service-name-fix.md` | this file | mandatory Change Impact Log entry per `CLAUDE.md` |

## 7. Before / after

```yaml
# Before (.github/workflows/deploy-backend.yml, .github/workflows/standby-parity-monitor.yml)
RAILWAY_SERVICE: spinr-backend
```

```yaml
# After
RAILWAY_SERVICE: spinrvm
```

Concrete scenario: on the next push to `main` that touches `backend/**`, `deploy-backend.yml`'s "Verify required Railway variables are set" step now runs `railway variables --service spinrvm --json` (previously `--service spinr-backend`, which immediately errored `Service 'spinr-backend' not found` and failed the job before the deploy step ever ran). With the corrected name, that lookup succeeds, the env-completeness check passes (all 14 required names already present per `describe-service`), `railway up --service spinrvm` runs, and the post-deploy sha-verification step can execute against the real, already-serving service instead of never being reached.

## 8. Rollback plan

Plain `git revert` of this commit. No live data, no migration, no Stripe charge, no wallet delta, no ride state, no feature flag — this is two YAML string values in CI workflow files. Reverting restores the previous (broken) `RAILWAY_SERVICE: spinr-backend` value, which returns the workflows to their prior (already-broken) failure mode; it does not touch the Railway service `spinrvm` itself or its already-running deployment, since Railway's native GitHub integration deploys independently of this workflow either way.

## 9. Verification performed

- [x] Confirmed the real Railway service name and its config directly via the Railway MCP (`list-projects` → `list-services` → `describe-service` in `cooperative-harmony`): service `spinrvm`, source `srikumarimuddana-lab/spinrvm@main`, `rootDirectory: /backend`, live domain `spinr-backend-production.up.railway.app`.
- [x] Confirmed via the same `describe-service` call that `spinrvm`'s live variable list already contains every name in `deploy/backend-required-env.txt` (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `JWT_SECRET`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `FIREBASE_SERVICE_ACCOUNT_JSON`, `FIREBASE_DRIVER_APP_ID`, `FIREBASE_RIDER_APP_ID`, `REDIS_URL`, `RATE_LIMIT_REDIS_URL`, `WS_REDIS_URL`, `ALLOWED_ORIGINS`, `SENTRY_DSN`, `ENV`, `SUPABASE_REGION`).
- [x] Confirmed via Railway's deployment history (through the MCP) that `spinrvm` has been auto-deploying successfully on every push to `main` via Railway's native GitHub integration — the standby was not actually stale, only this workflow's own extra verification layer was broken.
- [x] Blast-radius grep performed: `RAILWAY_SERVICE` across the whole repo (4 hits: the 2 fixed files + 2 prose-only docs); all other `spinr-backend`-shaped strings in `.github/workflows/` confirmed to be unrelated Docker-tag or Fly-app-name references, not a second hardcoded Railway service name.
- [x] Ran `spinr-cicd-infra-reviewer` against the diff before committing (see PR/commit for its verdict).
- [ ] **NOT verified: an actual green run of either workflow, or an actual Railway deploy triggered from this session.** This environment has no ability to dispatch a GitHub Actions workflow run or invoke `railway up` directly (write access to Railway wasn't exercised — only read/describe calls were made, deliberately, to avoid touching the live standby service). Verification performed here is config-correctness only: the service name and its variables were confirmed correct via the Railway MCP's read-only calls, not via watching `deploy-backend.yml` actually turn green. That only the next real push to `main` (or a manual `workflow_dispatch`) can prove.
- [ ] No `npm run build` / equivalent applicable — this change touches no `admin-dashboard`/`rider-app`/`driver-app` code, only backend deployment YAML.

## 10. What was NOT verified

- No real GitHub Actions run of `deploy-backend.yml` or `standby-parity-monitor.yml` was triggered or observed post-fix — this session cannot dispatch workflow runs. The next push to `main` (or a manual dispatch) is the actual proof.
- No real Railway deploy (`railway up`) was invoked from this session — verification relied entirely on Railway MCP read/describe calls (`list-services`, `describe-service`), not a live deploy trigger, to avoid any write-side risk to the already-correctly-deploying `spinrvm` service.
- C1's actual failover drill (cutting the `api-spinr.spinr.ca` Cloudflare CNAME to Railway and confirming real traffic is served correctly end to end) was not performed and is out of scope for this fix — restoring `deploy-backend.yml`'s checks is necessary but not sufficient evidence the failover path itself works.
