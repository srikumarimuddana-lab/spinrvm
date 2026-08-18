# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | Claude (agent session, on behalf of vikas@ngitservices.com) |
| Surface(s) | infra / CI (no rider-app, driver-app, or admin-dashboard code touched) |
| Domain (Sentry tag) | n/a — no runtime code path changed |
| PR / commit link | see PR description |
| Related issue or gap ID | ACTION_ITEMS.md E1 |

## 1. Issue / gap identified

No staging environment exists. Every deploy goes `main` → production (Fly +
Railway) directly, with no intermediate environment to catch a bad
migration, dispatch change, or Stripe webhook regression before it reaches
real riders and drivers.

## 2. Root cause

Staging was never built — it's a backlog gap (E1), not a regression. This
change is scaffolding toward closing it, not a fix for broken behavior.

## 3. Fix / remediation

Added three new, inert files:

1. `backend/fly.staging.toml` — a Fly app config for a placeholder staging
   app (`spinr-backend-staging`), modeled on production `backend/fly.toml`
   but scaled down (1 machine, `auto_stop_machines = "stop"`,
   `min_machines_running = 0`, 512mb) and with `ENV = "staging"`.
2. `.github/workflows/deploy-backend-staging.yml` — a new GitHub Actions
   workflow that deploys `fly.staging.toml` on push to a `staging` branch
   or manual `workflow_dispatch` only. It references three secrets that do
   not exist yet (`FLY_API_TOKEN_STAGING`, `SUPABASE_STAGING_URL`,
   `SUPABASE_STAGING_SERVICE_ROLE_KEY`) and fails fast with an explanatory
   error at its first step until a human adds them.
3. `docs/runbooks/staging-environment.md` — documents why staging matters
   (prereq for E2 load testing, E4 synthetic monitoring, safe migration
   rehearsal) and the exact manual steps a human with real Fly/Supabase
   access must run once.

No existing file's runtime behavior changed. `ACTION_ITEMS.md`'s E1 entry
was updated to record this scaffolding as still-open/blocked, not closed.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `backend/fly.staging.toml` is a brand-new file
  with a distinct `app = "spinr-backend-staging"` name — it cannot be
  picked up by the existing `deploy-fly.yml` workflow, which hard-codes
  `--config fly.toml` and `FLY_APP: spinr-backend-yyz`. Grepped
  `.github/workflows/*.yml` and `backend/*.toml` for any reference to
  `fly.staging.toml` or `spinr-backend-staging` outside the two new files —
  none found. Production's `backend/fly.toml`, `deploy-fly.yml`, and
  `deploy-backend.yml` (Railway) are untouched.
- **New workflow trigger is deliberately narrow and additive**: `push:
  branches: [staging]` (a branch that does not exist yet) plus
  `workflow_dispatch`. It does not listen on `main`, `pull_request`, or any
  existing branch pattern, so it cannot fire as a side effect of any current
  merge activity. Also gated on `github.repository ==
  'srikumarimuddana-lab/spinrvm'` to prevent it firing from a fork.
- **No production secret is referenced anywhere in the new workflow** —
  grepped it for `FLY_API_TOKEN` (production spelling), `SUPABASE_URL`, and
  `SUPABASE_SERVICE_ROLE_KEY` and confirmed only the `_STAGING` suffixed
  names appear. Even if `FLY_API_TOKEN_STAGING` were mistakenly created as a
  copy of the production token, the workflow only ever deploys
  `fly.staging.toml` (a different `app =` name), so it would fail against
  Fly (wrong app for that config) rather than silently redeploying
  production.
- **`ACTION_ITEMS.md` edit is additive** — appended a status note to the
  existing E1 bullet, did not remove or reorder any other entry.
- Nothing here touches the ride state machine, wallet/money code paths, the
  16 background loops, or any migration.

## 5. User-experience effect

None. No rider, driver, corporate-admin, or internal-admin facing surface
changed. This is infra/CI scaffolding only, invisible to any app user, and
not deployed to anything reachable until a human completes the manual setup
in `docs/runbooks/staging-environment.md`.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/fly.staging.toml` | New file — inert Fly config for a not-yet-created staging app | E1 scaffolding |
| `.github/workflows/deploy-backend-staging.yml` | New file — CI workflow gated on missing secrets, triggers only on `staging` branch / manual dispatch | E1 scaffolding |
| `docs/runbooks/staging-environment.md` | New file — one-time manual setup runbook | E1 scaffolding |
| `ACTION_ITEMS.md` | Appended status note to the existing E1 bullet; checkbox stays `[ ]` | Record partial progress per project convention |

## 7. Before / after

Not applicable — every change here is purely additive (new files, plus an
appended note on an existing open backlog bullet). No existing behavior-
changing diff to show.

## 8. Rollback plan

`git revert` is sufficient and safe here: every file is new, and nothing in
this change has been applied to live data, live infra, or any deployed
system — no Fly app was created, no Supabase project was created, no
secrets were registered, no branch protection or repo settings changed. A
revert simply removes the three new files and the `ACTION_ITEMS.md` note;
no cleanup elsewhere is required.

## 9. Verification performed

- [x] Automated tests run: none apply — no application code changed (docs/
  CI/config only), per task instructions not to invent tests for these.
- [x] YAML syntax validated: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/deploy-backend-staging.yml'))"` parses cleanly.
- [x] Manual repro: confirmed by inspection that the workflow's secret names,
  branch trigger, and Fly app name do not collide with any existing
  production workflow, secret, or config file (grep as described in §4).
- [ ] Manual repro steps followed in staging: not applicable — no staging
  environment exists yet; this PR is what creates the scaffolding toward
  building one.
- [x] Blast-radius grep performed: `.github/workflows/*.yml`, `backend/*.toml`
  searched for `fly.staging.toml`, `spinr-backend-staging`,
  `FLY_API_TOKEN_STAGING`, `SUPABASE_STAGING_*` — only appear in the three
  new files.
- [x] Reviewed against relevant CLAUDE.md conventions: PIPEDA data
  residency (runbook requires `ca-central-1` for the staging Supabase
  project, same as production), "Settings in DB" convention noted as
  out of scope for this file (Fly secrets, not `app_settings`, since no
  backend process is running yet to read from a DB).
- [x] Reviewed by `spinr-cicd-infra-reviewer` subagent (Codex/Claude
  auto-PR-review is off per CLAUDE.md C7/C9, so this repo requires a manual
  subagent pass on CI/infra changes) — findings addressed before merge (see
  PR description / commit history for specifics).
- [ ] Feature-flagged: not applicable — nothing user-visible or runtime-
  reachable is being shipped; the workflow is dark by construction until a
  human adds the missing secrets.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no live
  data involved).
- [x] Blast radius is stated, not assumed: isolated, additive-only, no
  reference to or overlap with any production workflow/secret/app.
- [x] No silent behavior change to an already-shipped flow — nothing
  already-shipped is touched by this change.
