# ADR-011: Environment topology — dev/test, staging, canary

**Date:** 2026-08-24
**Status:** Accepted

---

## Context

Every deploy today goes straight from `main` to production, in parallel on
Fly.io (`spinr-backend-yyz`, primary) and Railway (warm standby) — see
`docs/adr/007-fly-primary-railway-standby.md`. There is no environment
between a merge and real riders and drivers. The product is in live app
testing with real users, which is exactly the situation `CLAUDE.md`'s
pre-merge release gates exist for, and those gates currently have nowhere to
run: gate 4 ("state-machine and money changes need a dry run") can only be
satisfied against `mock_supabase_client` fixtures, and gate 3 ("ship dark,
verify in staging/canary, then flip on") names a staging/canary that does
not exist.

Three partial attempts already exist in the repo, in three different states:

| Artifact | State |
|---|---|
| `backend/fly.staging.toml`, `.github/workflows/deploy-backend-staging.yml`, `docs/runbooks/staging-environment.md` | Scaffolding for `ACTION_ITEMS.md` E1. Inert by design — fails fast at a secret-verification step until a human provisions the Fly app, a throwaway Supabase project, and three GitHub secrets. |
| `.github/workflows/test-env.yml` | **Dead.** Its only triggers are `push`/`pull_request` on a `develop` branch. No `develop` branch has ever existed in this repo (`main` plus ~1063 feature branches). The workflow has never executed a single run. |
| `rider-app/eas.json`, `driver-app/eas.json` | **Already correct.** `development` / `test` / `preview` / `production` build profiles with matching EAS channels exist and work. Mobile needs no new environment scaffolding. |

So the gap is narrower than "we have no environments": staging is provisioned-blocked,
mobile is done, and the test tier is a workflow pointing at a branch that was
never created.

### The constraint that shapes the canary decision

`ENV` is a plain string on `Settings` (`backend/core/config.py:176`), and
roughly thirty code paths branch on `ENV == "production"`. Four of them are
**single-gated** — the only thing standing between the safe behavior and the
unsafe one is that string:

| Behavior | Call site | If `ENV != "production"` |
|---|---|---|
| Firebase App Check enforcement | `backend/core/middleware.py:890` | Disabled |
| HSTS response header | `backend/core/middleware.py:879` | Not sent |
| `secure` flag on auth cookies | `backend/utils/cookie_manager.py:37,64` | Cookies sent over plaintext |
| APNs endpoint selection | `backend/utils/apns_client.py:230` | **Sandbox** — push to real devices silently fails |

A canary serves real production traffic by definition. Running it as
`ENV=canary` (or `ENV=staging`) would therefore turn off App Check, drop the
secure-cookie flag, and route every iOS push for canary-served users to the
APNs sandbox, where production device tokens are not valid. That is a
user-visible outage on a slice of live traffic, caused by a config string.

Two `ENV`-gated behaviors are **double**-gated and are *not* a canary risk,
which is worth recording so nobody re-litigates them:

- The `"1234"` OTP bypass (`backend/routes/auth.py:434`, `:738`) requires
  **both** `ENV != "production"` **and** Twilio/email provider unconfigured in
  `app_settings`. A canary shares production `app_settings`, where Twilio is
  configured, so the bypass cannot activate regardless of `ENV`.
- Production secret-strength validation (`backend/core/config.py:280`) is
  `ENV`-gated, but `core/middleware.py`'s `_validate_production_config`
  re-checks `ADMIN_PASSWORD` independently.

## Decision

Adopt a four-tier topology. Code is promoted **trunk-based** — `main` stays the
only long-lived branch, and each tier is fed by an explicit, auditable action
rather than by a branch merge.

```
 local dev ──┐
             ├──► dev/test (shared Supabase, synthetic)  ── workflow_dispatch / PR
             │
             ├──► staging (own Supabase, schema parity)  ── workflow_dispatch on a main SHA
             │
             └──► canary  (PRODUCTION Supabase, ENV=production, ~5% traffic)
                       │
                       └──► production (Fly primary + Railway standby)
```

### 1. Trunk-based promotion, not GitFlow

`main` remains the only long-lived branch. Dev/test deploys run from any
feature branch via `workflow_dispatch`; staging and canary deploy a specific
`main` commit SHA passed as a workflow input.

Rejected: creating long-lived `develop` and `staging` branches to make
`test-env.yml` work as written. The repo has operated trunk-based for its
entire history; introducing two long-lived branches to satisfy one dead
workflow inverts the cost. `test-env.yml` is rewritten instead.

### 2. Dev and test share one Supabase project; staging gets its own

Two new Supabase projects, both `ca-central-1`, both synthetic-data-only:

- **`spinr-dev`** — serves both local development and CI/test. Freely
  resettable. CI may truncate and reseed it.
- **`spinr-staging`** — schema-parity target for migration rehearsal. Kept
  stable so `python -m backend.scripts.run_migrations` can be dry-run against
  a database whose schema actually matches production before a production
  migration window.

Sharing dev and test is the deliberate cost trade. The thing that makes
staging worth its own project — a schema that is *not* being reset out from
under you — is exactly what dev and test do not need.

Both are subject to the PIPEDA data-residency rule in `CLAUDE.md`
("Supabase project must be in a Canadian region"). There is no "it's only
dev" exception, and neither may ever be seeded from a production dump,
scrubbed or otherwise.

### 3. Canary is a separate Fly app on production data, running `ENV=production`

`spinr-backend-canary`: one machine, production Supabase, production Redis,
production `app_settings`, and **`ENV = "production"`** in `fly.canary.toml`,
for the reasons in the Context section above.

Canary is distinguished from production by things that carry no behavioral
gating:

- `SENTRY_ENVIRONMENT=canary` — separates canary errors in Sentry without
  touching any `ENV` branch.
- `SPINR_DEPLOY_TIER=canary` — a new, purely-descriptive variable for logs and
  metric labels. Nothing branches on it.
- Cloudflare weighted routing on `api-spinr.spinr.ca` sends ~5% of traffic.

Rollback is a weight change to 0 at the DNS layer — no redeploy, no code
change, consistent with the existing Fly/Railway failover model, which is
already "a single DNS change" (ADR-007).

Rejected: relying on `app_settings` feature flags alone. That pattern is
already in use and stays in use for *behavior* changes, but a flag cannot
canary a bad build, a dependency bump, a startup regression in one of the 18
`core/lifespan.py` background loops, or a memory leak. Those are precisely
the failures that reach production untested today.

## Consequences

**Positive**

- `CLAUDE.md` release gates 3 and 4 become executable rather than aspirational.
- `ACTION_ITEMS.md` E2 (Locust load testing) unblocks — `loadtest/locustfile.py`
  gets a target that is not production.
- Migration rehearsal gets a real database, addressing the standing fact that
  `run_migrations.py` has never been dry-run anywhere but production.
- A bad build is caught on ~5% of traffic with a DNS-level rollback, instead of
  on 100% with a redeploy.

**Negative / costs**

- Two new Supabase projects and two new Fly apps to pay for and keep patched.
- Nine new GitHub secrets across the three tiers.
- Canary shares the production database. It therefore **cannot** catch a bad
  migration — a canary machine running new code against a migrated production
  schema is still writing to production tables. Migration safety remains
  staging's job plus `migration-check.yml`, and this ADR does not change that.
- Canary doubles the surface on which a production incident can originate.
  The `SENTRY_ENVIRONMENT` split is what keeps that diagnosable.

**Deliberately out of scope**

- Stripe in staging/dev. Whether non-prod tiers exercise payment flows at all,
  and against which Stripe test-mode keys, is a separate decision. Until it is
  made, non-prod tiers should have Stripe credentials unset.
- Frontend (Vercel) preview environments. Vercel already builds per-PR previews;
  no change proposed.
- Mobile. EAS channels already cover it.
