# Staging environment setup (ACTION_ITEMS.md E1)

> **Status: scaffolding only, not live.** This runbook documents the
> one-time manual steps a human with real Fly.io and Supabase access must
> run before a staging environment actually exists. Nothing described here
> has been provisioned yet — see `ACTION_ITEMS.md` E1 for current status.

## Why staging matters

Today every deploy goes straight from `main` to production, on **both**
Fly.io and Railway in parallel (`docs/adr/007-fly-primary-railway-standby.md`).
There is no intermediate environment to catch a bad migration, a broken
dispatch change, or a Stripe webhook regression before it reaches real
riders and drivers. This is a hard prerequisite for:

- **E2 — Marketplace load/simulation testing**: the Locust harness
  (`loadtest/locustfile.py`, branch `claude/eager-franklin-69ta0w`) needs a
  target that isn't production — running rider/driver bots and real
  dispatch matchmaking against the live database would corrupt production
  ride state and pollute KPI dashboards.
- **E4 — Synthetic monitoring + SLO alerting**: synthetic probes need a
  safe environment to validate against before pointing any probe at
  production endpoints that touch real money or real rides.
- **Safe migration rehearsal**: `backend/scripts/run_migrations.py` has
  never been dry-run against a database that isn't production. A staging
  Supabase project with the same schema lets a migration be applied and
  observed before the production migration window (target: `< 30s` per
  `CLAUDE.md`'s Performance SLA table).

## What has been built (scaffolding, inert)

Three files exist in the repo already, all inert until the manual steps
below are completed by a human with real access:

| File | Purpose |
|---|---|
| `backend/fly.staging.toml` | Fly app config for a `spinr-backend-staging` app — scaled down (1 machine, scale-to-zero) vs. production's 8-machine burst pool |
| `.github/workflows/deploy-backend-staging.yml` | Deploys `backend/fly.staging.toml` on push to a `staging` branch or manual `workflow_dispatch` — never on `main` |
| `docs/runbooks/staging-environment.md` | This file |

The workflow fails fast and loud (not silently, not by falling through to
production) at its "Verify required secrets" step until the secrets below
are added. It never reads a production secret name, so there is no path by
which a misconfigured staging deploy can reach the production Fly app or
production Supabase project.

## Manual steps a human must run once

These require real Fly.io and Supabase account access that this scaffolding
task deliberately does not have and must not simulate.

### 1. Create the Fly app

```bash
fly apps create spinr-backend-staging --org <your-org>
```

Do **not** reuse `spinr-backend-yyz` (the production app name) or attempt to
rename/repurpose it. Staging must be a fully separate Fly app so a staging
deploy can never affect production machines, and so `fly deploy` cannot
accidentally target the wrong app from a stale local context.

### 2. Create a throwaway Supabase project (Canadian region, synthetic data only)

Per `CLAUDE.md`'s Compliance (PIPEDA) section: *"Supabase project must be in
a Canadian region (ca-central-1 or equivalent). Changing regions is a
compliance event — never do without legal sign-off."* This applies to the
staging project too — there is no PIPEDA exception for "it's just staging."

- Create a **new, separate** Supabase project in `ca-central-1`.
- Run the full migration set against it fresh
  (`python -m backend.scripts.run_migrations`) to confirm schema parity with
  production and to rehearse the migration path itself.
- Seed it with **synthetic data only** — fabricated riders, drivers,
  vehicles, and rides. Never copy or restore a production data dump into
  it, even scrubbed. PIPEDA data-minimization and the "never real user
  data outside production" boundary both apply here.
- Rotate a staging-only `JWT_SECRET`, `ADMIN_PASSWORD` (not `admin123`,
  same production rule applies), and any other backend secret listed in
  `CLAUDE.md`'s Required Environment Variables section. None of these may be
  copied from production.

### 3. Register the two new GitHub secrets

In GitHub → repo → Settings → Secrets and variables → Actions, add:

| Secret | Value |
|---|---|
| `FLY_API_TOKEN_STAGING` | A deploy-scoped token for `spinr-backend-staging` only: `fly tokens create deploy -a spinr-backend-staging`. Not the production `FLY_API_TOKEN`. |
| `SUPABASE_STAGING_URL` | The staging Supabase project's URL. Not the production `SUPABASE_URL`. |
| `SUPABASE_STAGING_SERVICE_ROLE_KEY` | The staging Supabase project's service-role key. Not the production `SUPABASE_SERVICE_ROLE_KEY`. |

Optionally, `FLY_HEALTH_URL_STAGING` (e.g. `https://spinr-backend-staging.fly.dev`)
enables the post-deploy health probe in the workflow; it is skipped safely
if left unset.

### 4. Create the `staging` branch and push

Once the app and secrets exist, `.github/workflows/deploy-backend-staging.yml`
takes over automatically: pushing `backend/**` changes to a `staging` branch
(or running the workflow manually via `workflow_dispatch`) deploys
`backend/fly.staging.toml` to `spinr-backend-staging`, staging the two
Supabase secrets into Fly first. No further workflow changes should be
needed — verify the health check passes, then the environment is live for
E2/E4 work.

## What NOT to do

- **Never point staging at the production Supabase project.** Not even
  read-only, not even temporarily "to save time" seeding data. A shared
  project means a staging bug (bad migration test, a load-test bot writing
  garbage rides) can corrupt production data instantly.
- **Never reuse the production Fly app** (`spinr-backend-yyz`) for staging,
  and never point `FLY_API_TOKEN_STAGING` at a token scoped to that app.
- **Never copy production secrets verbatim into staging** — including
  `JWT_SECRET`, `ADMIN_PASSWORD`, Stripe keys, or Firebase credentials.
  Staging needs its own values (test-mode Stripe keys if Stripe flows are
  exercised at all in staging — that scope is not covered by this runbook
  and should be decided separately before E2/E6 touch payments).
- **Never seed staging with a production data export**, scrubbed or not.
  Use synthetic data generated for the purpose.
- **Never wire the `staging` branch or this workflow to trigger on `main`.**
  The trigger is deliberately `push: branches: [staging]` plus manual
  `workflow_dispatch` so a routine `main` merge can never accidentally
  deploy to (or overwrite) staging as a side effect.
