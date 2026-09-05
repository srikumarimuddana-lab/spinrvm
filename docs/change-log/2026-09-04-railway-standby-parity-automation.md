# Change Impact & Risk Log — Railway standby parity automation (C5)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code session (branch `claude/railways-backup-server-oyo1t2`) |
| Surface(s) | backend, CI/CD (deploy + monitor workflows), docs |
| Domain (Sentry tag) | admin (ops/infra) |
| PR / commit link | branch `claude/railways-backup-server-oyo1t2` |
| Related issue or gap ID | `ACTION_ITEMS.md` C5 (standby drift), C3 (env sweep), C1 (drill) |

## 1. Issue / gap identified

The Railway warm standby (ADR-007) has not been deployed from `main` for
weeks. `deploy-backend.yml` runs on every push and fails in ~30 s with
`Invalid RAILWAY_TOKEN` (runs #4196–#4203 on 2026-09-04 alone). Nothing
verified that Railway carried every required variable, that its secrets
matched Fly's, or what commit it was running — a fail-over today would land
on stale code with unknown config.

## 2. Root cause

- The `RAILWAY_TOKEN` GitHub secret is rejected by Railway (revoked/expired/
  wrong token type). C5 recorded an Environment-protection pause as the
  cause; the run logs show the token instead. The workflow's diagnostic step
  used `|| true`, so the real error was buried.
- Fly's non-secret config (`ENV`, `SUPABASE_REGION`, `UVICORN_WORKERS`) lives
  in `fly.toml [env]`; `railway.json` cannot carry env vars, so on Railway
  these are dashboard variables with no check that they exist. A Railway
  service missing `ENV=production` boots in development mode with every
  production guard skipped.
- No endpoint exposed the running commit or any secret-parity signal, so
  drift was invisible to automation by construction.

## 3. Fix / remediation

Detection and gating, not the fix itself (rotating the token is a human
dashboard action):

1. `GET /deploy-info` on the backend: `{provider, env, build, fingerprints}`.
   Build stamp comes from `backend/build_info.json`, written by both deploy
   workflows before `docker build`. Fingerprints are truncated HMAC-SHA256
   keyed by `JWT_SECRET` — never a value. Bearer `METRICS_AUTH_TOKEN`
   (header only), 503 fail-closed in every environment when unset.
2. `deploy-backend.yml`: fails early on an invalid token; refuses to deploy
   while any name in `deploy/backend-required-env.txt` is missing on the
   service (names only); stamps the build; verifies the served sha post-deploy;
   concurrency group. `deploy-fly.yml`: stamp + served-sha verification.
3. `standby-parity-monitor.yml` + `scripts/standby_parity.py`: daily
   Railway↔Fly comparison (token, names, one-sided vars, health, build sha,
   fingerprints), one tracked issue updated in place, auto-closed when green.
4. Runbook, ACTION_ITEMS C5/C3, DEPLOYMENT.md (`SUPABASE_KEY` → the real
   `SUPABASE_SERVICE_ROLE_KEY`; CLI v4 `--set` syntax).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `server.py` gains one new root route and two
  module-level helpers; no existing route, middleware, loop, table, or state
  field is touched. Grep for other consumers of `_metrics_token`: only
  `/metrics` (unchanged) and `worker.py`'s own copy (unchanged).
- `backend/build_info.json` is a new tracked placeholder read only by
  `utils/build_info.py` (no other importer). It is inside the Docker build
  context on both providers; `.dockerignore` does not exclude `*.json`.
  `railway up` honours `.gitignore`, which is why the file is committed
  rather than ignored — an ignored file would never reach Railway's image.
- Both deploy workflows now write that file in the runner checkout before
  building. Nothing downstream reads the working tree after that step.
- `deploy-backend.yml` now **fails** (instead of proceeding) when a required
  variable is missing on Railway. That is the intended behaviour change:
  today it fails anyway at the token step; once the token is rotated, a
  missing `ENV` would previously have deployed a dev-mode standby.
- The parity monitor is read-only against Railway/Fly and creates/edits/
  closes one labelled issue. `permissions: issues: write, contents: read`.
- The endpoint's threat model: an attacker holding `METRICS_AUTH_TOKEN`
  learns 16-hex-char HMACs keyed by a ≥32-char random secret — no preimage,
  no cross-provider oracle beyond "equal / not equal", which is the purpose.
  Low-entropy fields (`ADMIN_EMAIL`, `ALLOWED_ORIGINS`) are protected by the
  key, not by their own entropy. Values are never returned.
- Ride state machine, money paths, insurance periods, auth flows: untouched.

## 5. User-experience effect

None for riders, drivers, corporate admins, or internal admins. Backend
operators see a new unauthenticated-by-default (503) route and two new
Actions signals (a red Railway deploy with a real reason; a daily parity
issue). Not visible mid-session to anyone.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/build_info.py` | new: build-stamp loader + provider detection | expose running commit |
| `backend/build_info.json` | new: tracked null placeholder | reaches Railway image despite `.gitignore` handling |
| `backend/server.py` | new `GET /deploy-info`, `_PARITY_FIELDS`, `_config_fingerprints` | value parity without values |
| `backend/tests/test_deploy_info.py` | new: auth, body, fingerprint, loader tests | coverage of the new route |
| `deploy/backend-required-env.txt` | new: required names + scope | single source of truth |
| `.github/workflows/deploy-backend.yml` | token validity, names check, stamp, served-sha check, concurrency | fail loudly, deploy only a bootable standby |
| `.github/workflows/deploy-fly.yml` | stamp + served-sha check | symmetric evidence for the monitor |
| `.github/workflows/standby-parity-monitor.yml` | new daily monitor | make drift a tracked finding |
| `scripts/standby_parity.py` | new evaluator with `--self-test` | testable judgement logic |
| `docs/runbooks/railway-fly-failover.md` | corrected drift cause; automation + drill sections | operator guidance |
| `ACTION_ITEMS.md` | C5 root-cause correction, C3 pointer | keep the backlog truthful |
| `DEPLOYMENT.md` | `SUPABASE_KEY` → `SUPABASE_SERVICE_ROLE_KEY`; v4 syntax | the old snippet produced an unbootable Railway service |
| `docs/change-log/2026-09-04-railway-standby-parity-automation.md` | this file | mandatory log |

## 7. Before / after

```yaml
# Before (deploy-backend.yml) — token failure hidden, deploy attempted blind
- name: List Railway services (diagnostic)
  run: |
    railway status || true
    railway service list || true
- name: Deploy to Railway
  run: railway up --service spinr-backend
```

```yaml
# After — fail with the reason, gate on required names, prove the served sha
- name: Verify RAILWAY_TOKEN is valid (railway status)
  run: railway status --json  || { echo "::error title=Invalid RAILWAY_TOKEN::…"; exit 1; }
- name: Verify required Railway variables are set (names only)
  run: railway variables --service "$RAILWAY_SERVICE" --json | jq -r 'keys[]'   # vs deploy/backend-required-env.txt
- name: Stamp build info          # backend/build_info.json = {sha, ref, built_at, provider}
- name: Deploy to Railway
- name: Verify the deployed build SHA is serving   # GET /deploy-info .build.sha == github.sha
```

## 8. Rollback plan

- `/deploy-info` is off by default: it answers 503 unless `METRICS_AUTH_TOKEN`
  is set on that provider. To disable without a deploy, unset the variable
  on the provider (Fly: `fly secrets unset METRICS_AUTH_TOKEN`; note this
  also re-locks `/metrics` in production).
- The monitor can be disabled from the Actions UI ("Disable workflow") with
  no deploy; delete or close its issue.
- The deploy-gate steps: `git revert` of the workflow commit is a genuine
  rollback here — nothing they touch is live data.
- No migration, no data write, no ride/payment state involved.

## 9. Verification performed

- `scripts/standby_parity.py --self-test`: 21 scenarios pass. Two extra
  end-to-end fixture runs (today's real state; a stale-build + missing-ENV +
  Redis-drift state) produce the expected CRITICAL reports and
  `GITHUB_OUTPUT`.
- All three workflows parsed with PyYAML; every `run:` block passed
  `bash -n`.
- `backend/utils/build_info.py` executed standalone (placeholder → `None`,
  full stamp → 4 keys only, malformed → `None`, provider detection).
- The two new `server.py` functions executed by AST-extracting them against
  a stub `Settings`: 503/401 gates, no query-string token, body shape, no
  raw value in the serialized body, per-field independence, JWT-mismatch
  changes every set row, `REDIS_URL` vs `WS_REDIS_URL` with equal values
  differ (name-bound).
- flake8 (E9/F/E1/E7, line-length 120) clean on all new Python.
- No admin-dashboard / rider-app / driver-app change, so no visual-regression
  question arises.

## 10. What was NOT verified

- **`pytest` and `ruff` did not run.** PyPI is blocked by this sandbox's
  network policy (403), so FastAPI/pytest/ruff could not be installed. CI
  must run `backend/tests/test_deploy_info.py` and `ruff check` before
  merge; the extracted-function harness above is a substitute, not the
  suite.
- No live run of any workflow: no Railway or Fly token is available here.
  `railway status --json` field names in the informational echo are
  guarded (`|| true`) and never affect the check; `railway variables
  --json`'s `{NAME: value}` shape and `flyctl secrets list --json`'s
  `[{Name,…}]` shape are from CLI documentation, not observed.
- Whether `railway up`'s build context is the repo root or `backend/` — the
  stamp is written to `backend/build_info.json`, which is correct under
  either arrangement that lets the existing `COPY requirements-locked.txt`
  succeed.
- Current Railway variable contents, and whether `METRICS_AUTH_TOKEN` is set
  on Fly today. The monitor reports these as "NOT verified" until the
  one-time setup in the runbook is done.
- The token rotation itself and the C1 drill: human actions, still open.
