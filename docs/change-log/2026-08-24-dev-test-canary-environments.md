# Change Impact & Risk Log — dev/test, staging, and canary environment topology

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | srikumarimuddana@gmail.com (via Claude Code) |
| Surface(s) | backend (Sentry init + config), CI/CD workflows, docs |
| Domain (Sentry tag) | — (observability/infra; no ride, payment, or auth logic touched) |
| PR / commit link | branch `claude/test-dev-canary-setup-5h07cp` |
| Related issue or gap ID | `ACTION_ITEMS.md` E1 (staging), E2 (load testing, blocked on E1); ADR-011 |

## 1. Issue / gap identified

Deploys go `main` → 100% of production in one step, with no intermediate
environment. Three partial attempts existed in inconsistent states: staging was
inert scaffolding blocked on human provisioning, and
`.github/workflows/test-env.yml` was **dead code** — its only triggers were
`push`/`pull_request` on a `develop` branch that has never existed in this
repo, so it had never executed a single run.

## 2. Root cause

`test-env.yml` was authored against a GitFlow branching model
(`develop` → `staging` → `main`) that was never adopted. The repo has operated
trunk-based throughout (`main` plus ~1063 short-lived feature branches). The
workflow was committed and then silently never fired; nothing surfaced this
because a workflow with no matching trigger produces no runs and therefore no
failures.

## 3. Fix / remediation

Adopted an explicit four-tier topology (ADR-011) and replaced the dead workflow:

- **dev/test** — `backend/fly.dev.toml` + `.github/workflows/deploy-backend-dev.yml`
  (manual dispatch), one shared throwaway Supabase project.
- **staging** — unchanged; the existing E1 scaffolding remains as-is.
- **canary** — `backend/fly.canary.toml` + `.github/workflows/deploy-backend-canary.yml`,
  a separate Fly app on the **production** database taking ~5% of real traffic
  via Cloudflare weighted routing.
- **backend code change** — new optional `SENTRY_ENVIRONMENT` setting so the
  canary's Sentry events are separable from production's.

All infra files are inert until a human provisions the Fly apps, Supabase
projects, and secrets; every workflow fails fast at a secret-verification step
and none references a production secret name except the canary's own token.

## 4. Risk & impact on existing functionality

**Blast radius: isolated.**

*Backend code change (`SENTRY_ENVIRONMENT`).* Grepped every consumer of a
Sentry `environment=` in the repo:

| Consumer | Affected? |
|---|---|
| `backend/server.py:522` (`sentry_sdk.init`) | Yes — the only backend consumer, and the one changed |
| `admin-dashboard/sentry.client.config.ts:24` | No — reads `NODE_ENV`, independent |
| `admin-dashboard/sentry.edge.config.ts:19` | No — same |
| `admin-dashboard/sentry.server.config.ts:39` | No — same |

The new field defaults to `None`, and the expression falls back to the exact
prior value (`settings.ENV`) when unset. No tier sets it today except the
not-yet-created canary app, so every currently-running process is unaffected.
No ride state, money path, wallet delta, background loop, or auth path is
touched. The PIPEDA scrubbing options (`pipeda_sentry_options()`) are passed
after the changed line and are unmodified.

*Workflow change.* `test-env.yml` had never run, so deleting it removes no
executing coverage. Its backend and rider/driver TypeScript jobs duplicated
`ci.yml`'s `backend-test` / `rider-app-test` / `driver-app-test`, which remain
the sole owners of per-PR lint and test and are untouched. The new workflows
are `workflow_dispatch`-only and cannot fire on a merge.

*Standing risk introduced by the canary design.* The canary shares the
production database, so it **cannot** catch a bad migration — a canary machine
runs new code against an already-migrated production schema. Recorded in
ADR-011 and at the top of the canary runbook so a green canary is not misread
as migration confidence.

## 5. User-experience effect

**Nobody, today.** No user-facing behavior changes: the backend change is a
no-op while `SENTRY_ENVIRONMENT` is unset, and no new environment is
provisioned. Nothing is visible mid-session to a rider or driver. No copy or
notification changes.

Once a canary is provisioned and given a non-zero weight, ~5% of real riders
and drivers are served by it — which is why `fly.canary.toml` pins
`ENV="production"` and why the runbook requires session affinity.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `docs/adr/011-environment-topology.md` | New | Records the topology decision and the `ENV`-gating constraint |
| `docs/adr/README.md` | Added ADR-011 row | Index convention |
| `backend/core/config.py` | Added optional `SENTRY_ENVIRONMENT` field | Lets canary separate Sentry events while keeping `ENV="production"` |
| `backend/server.py` | `sentry_sdk.init(environment=…)` prefers `SENTRY_ENVIRONMENT`, falls back to `ENV` | Same; additive, default-unchanged |
| `backend/fly.dev.toml` | New | Dev/test Fly app config |
| `backend/fly.canary.toml` | New | Canary Fly app config, `ENV="production"` |
| `.github/workflows/test-env.yml` | **Deleted** | Dead — triggered only on a `develop` branch that never existed |
| `.github/workflows/deploy-backend-dev.yml` | New | Replaces it, trunk-based manual dispatch |
| `.github/workflows/deploy-backend-canary.yml` | New | Manual canary deploy, main-ancestor check, typed confirmation |
| `docs/runbooks/dev-test-environments.md` | New | One-time setup procedure |
| `docs/runbooks/canary-environment.md` | New | Setup, soak criteria, abort/promote |
| `ACTION_ITEMS.md` | Updated E1, added E1a/E1b | Backlog reflects the new provisioning steps |

## 7. Before / after

The only behavior-changing backend diff:

```python
# Before — backend/server.py
environment=settings.ENV if hasattr(settings, "ENV") else "production",
```

```python
# After — backend/server.py
environment=(
    getattr(settings, "SENTRY_ENVIRONMENT", None)
    or (settings.ENV if hasattr(settings, "ENV") else "production")
),
```

With `SENTRY_ENVIRONMENT` unset (every tier today) the two expressions are
equivalent.

## 8. Rollback plan

- **Backend change:** set `SENTRY_ENVIRONMENT` to empty on any app where it was
  set — `fly secrets unset SENTRY_ENVIRONMENT -a <app>`, or remove the line from
  the tier's `fly.*.toml`. The fallback restores the previous `ENV`-derived
  value with no code change. Nothing is written to a database, so there is no
  live-data remediation to do.
- **Canary:** set the Cloudflare canary weight to **0**. Traffic drains
  immediately with no redeploy and no code change — the same DNS-level model as
  ADR-007's Fly/Railway failover. This is the documented abort step.
- **Workflows:** all new workflows are `workflow_dispatch`-only; not running
  them is a complete rollback. Restoring `test-env.yml` is possible via
  `git revert` but pointless — it never ran.

## 9. Verification performed

- [x] **Automated tests run (unit).** `pytest -m unit` → **3050 passed, 1
      skipped, 1 failed**. The failure
      (`test_scheduled_rides_coverage.py::TestCheckScheduledRides::test_lock_not_acquired_still_proceeds_to_fetch`)
      is **pre-existing**: it reproduces identically with these changes
      stashed. Targeted: `test_sentry_scrub.py` + `test_server_coverage.py`
      → 27 passed.
- [x] **Direct functional check** of the new setting: unset → resolves to
      `ENV` (unchanged); `SENTRY_ENVIRONMENT="canary"` with `ENV="production"`
      → Sentry environment `canary` while `ENV` stays `production`, so all four
      single-gated security behaviors still evaluate as production.
- [x] **Blast-radius grep performed.** Searched `environment=` across the
      backend and `environment:` across admin-dashboard/rider-app/driver-app
      Sentry configs, plus every `SENTRY_ENVIRONMENT` reference. Results in §4.
- [x] **Lint/format.** `ruff check` and `ruff format --check` clean on both
      changed Python files.
- [x] **Config validation.** All four `backend/fly.*.toml` parse and carry
      distinct app names; all new workflow YAML parses.
- [x] **Reviewed against `CLAUDE.md` conventions** — PIPEDA data residency
      (`ca-central-1` pinned on every non-prod tier, synthetic data only),
      observability (Sentry environment/tags), and the additive-over-destructive
      release gate.
- [x] **Additive rather than flagged.** The backend change defaults to the
      previous behavior, which is the same protection a flag would give here.

## 10. What was NOT verified

- **No real infrastructure exists.** No Fly app, Supabase project, Cloudflare
  routing rule, or GitHub secret has been created. Nothing here has been
  deployed or run against a real environment. Every workflow is unexecuted —
  they have been validated as YAML and reviewed, not run.
- **The canary has never taken traffic.** The soak criteria, weighted routing,
  and abort procedure in the runbook are reasoned from the existing Fly and
  Cloudflare setup, not exercised.
- **Sentry was not checked live.** That `environment=canary` renders as a
  separate facet in the Sentry UI is expected SDK behavior, verified at the
  value level in Python, not observed in Sentry itself. The `sentry` MCP
  connector in this session is unauthenticated, so no live query was possible.
- **No production build was run** for any frontend surface — this change
  touches no frontend code (`admin-dashboard` Sentry configs were read, not
  modified).
- **The full pytest suite was not run**, only `-m unit` (3050 tests). Integration
  and e2e tiers need a real Supabase and were not exercised.
- **The pre-existing unit-test failure was not fixed** — it is out of scope for
  this change and is noted above only to establish it is not a regression.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (unset one env var; set one DNS weight to 0)
- [x] Blast radius is stated, not assumed (every `environment=` consumer enumerated in §4)
- [x] No silent behavior change to an already-shipped flow — the backend diff is a
      no-op while `SENTRY_ENVIRONMENT` is unset, which is every tier today
