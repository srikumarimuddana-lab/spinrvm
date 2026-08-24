# Dev / test environment setup

> **Status: scaffolding only, not live.** Nothing described here has been
> provisioned. This runbook is the one-time manual procedure a human with
> real Fly.io and Supabase access must run. Design rationale lives in
> `docs/adr/011-environment-topology.md`; staging is a separate tier with its
> own runbook (`docs/runbooks/staging-environment.md`), and canary is a third
> (`docs/runbooks/canary-environment.md`).

## What this tier is

One Fly app and one Supabase project, shared by local development and CI/test
(ADR-011 §2). It is the tier you are allowed to break.

| Piece | Name | State |
|---|---|---|
| Fly app | `spinr-backend-dev` | Not created |
| Supabase project | `spinr-dev` (suggested) | Not created |
| Fly config | `backend/fly.dev.toml` | In repo, inert |
| Deploy workflow | `.github/workflows/deploy-backend-dev.yml` | In repo, fails fast until secrets exist |
| Mobile | EAS `test` profile / `test` channel | **Already working**, no setup needed |

`ENV=development` on this tier, which means `Settings.debug` is on, Firebase
App Check enforcement is off (`backend/core/middleware.py:890`), and HSTS is
off. That is deliberate — mobile dev and preview builds carry no App Check
attestation, so enforcement here would 403 every request. It also means this
tier must never hold real user data; see "What NOT to do".

## Steps

### 1. Create the Supabase project

Create a **new** project — never reuse the production one, not even read-only.

- Region: **`ca-central-1`** (or another Canadian region). PIPEDA data
  residency in `CLAUDE.md` applies here too; there is no "it's only dev"
  exception, and `backend/fly.dev.toml` already pins `SUPABASE_REGION`.
- Name it distinctly (e.g. `spinr-dev`) so it is never confused with
  production in the Supabase dashboard's project switcher.

Then apply the schema. The migration runner needs a **direct Postgres
connection string**, not the REST URL — multi-statement DDL needs a raw
psycopg session (`CLAUDE.md`, Database Migrations).

Copy that connection string from Supabase → Project Settings →
Database → Connection string → **URI** (the direct/session one, not the
transaction pooler), export it as `DATABASE_URL` in your shell, then:

```bash
cd backend
python -m backend.scripts.run_migrations --dry-run   # preview first
python -m backend.scripts.run_migrations
python -m backend.scripts.run_migrations --status    # confirm applied vs pending
```

Do not paste that connection string into any file in the repo — it carries
the database password, and the pre-commit secret scanner will (correctly)
block the commit.

Running the full set fresh against an empty database is itself useful: it is
the only place the ordered migration set gets exercised end to end.

### 2. Seed synthetic data

There is **no general-purpose synthetic seeder in this repo today** — this is
a real gap, not an omission from this runbook. What exists:

- `backend/scripts/seed_corporate_test_data.py` — corporate accounts only.
- `loadtest/README.md` — documents seeding rider/driver bot accounts for the
  Locust harness (`ACTION_ITEMS.md` E2).

Until a general seeder exists, seed by hand or via the two above. Fabricate
riders, drivers, vehicles, and rides. **Never** restore a production dump,
scrubbed or not.

### 3. Create the Fly app

```bash
fly apps create spinr-backend-dev --org <your-org>
fly tokens create deploy -a spinr-backend-dev
```

Use a **deploy-scoped** token, not a personal or org-wide one, so the token
cannot reach `spinr-backend-yyz` (production) or `spinr-backend-staging`.

Do not run `fly deploy` by hand from a local checkout — a stale local Fly
context is exactly how a dev config gets pushed to the wrong app. Deploy
through the workflow.

### 4. Register the GitHub secrets

GitHub → repo → Settings → Secrets and variables → Actions:

| Secret | Value |
|---|---|
| `FLY_API_TOKEN_DEV` | The deploy token from step 3. **Not** the production `FLY_API_TOKEN`. |
| `SUPABASE_DEV_URL` | The dev project's URL. **Not** `SUPABASE_URL`. |
| `SUPABASE_DEV_SERVICE_ROLE_KEY` | The dev project's service-role key. **Not** `SUPABASE_SERVICE_ROLE_KEY`. |
| `FLY_HEALTH_URL_DEV` | Optional, e.g. `https://spinr-backend-dev.fly.dev`. Enables the post-deploy health probe; skipped safely if unset. |

Then set the dev-only runtime secrets directly on the Fly app — these are not
staged by the workflow:

```bash
gen() { python3 -c 'import secrets;print(secrets.token_urlsafe('"$1"'))'; }
jwt=$(gen 48)
apw=$(gen 24)
fly secrets set -a spinr-backend-dev \
  JWT_SECRET="$jwt" \
  ADMIN_PASSWORD="$apw" \
  FIREBASE_SERVICE_ACCOUNT_JSON="$(cat ./dev-firebase-service-account.json)"
```

Generate fresh values. Do not copy production's, even temporarily. Note that
`ENV=development` means `config.py`'s production strength validation does
**not** run here, so a weak secret will start successfully and silently — the
generators above are the guard, not the app.

Leave Stripe credentials **unset**. Whether non-prod tiers exercise payment
flows at all is an open decision (ADR-011, "Deliberately out of scope").

### 5. Deploy

GitHub → Actions → **Deploy Backend to Fly.io (Dev/Test)** → Run workflow,
picking the branch or SHA to deploy. Tick `publish_ota` to also push a JS-only
OTA update to the EAS `test` channel.

There is no automatic trigger by design (ADR-011 §1): `main` is the only
long-lived branch, so a dev deploy is an explicit act.

### 6. Point local development at it

`backend/.env` for a local backend against the dev database:

```
SUPABASE_URL=<dev project URL>
SUPABASE_SERVICE_ROLE_KEY=<dev service role key>
JWT_SECRET=<any 32+ char dev value>
ENV=development
```

Redis may be left unset locally — `utils/redis_client.py` transparently falls
back to an in-process dict (ADR-004). Rate-limit and OTP-lockout state is then
lost on restart, which is fine for dev and is not fine anywhere else.

For the mobile apps, point `EXPO_PUBLIC_BACKEND_URL` at
`https://spinr-backend-dev.fly.dev` (or your local backend).

## Resetting

This tier exists to be reset. The app scales to zero when idle, so a reset is
just: truncate the tables (or delete and recreate the Supabase project), re-run
`run_migrations.py`, re-seed.

Before resetting, tell anyone doing manual QA — dev and test share one project
by design (ADR-011 §2), so a CI reset lands on top of someone's session. If
that collision becomes routine, that is the signal to split test onto its own
project, which was the rejected option in the ADR.

## What NOT to do

- **Never point this tier at the production Supabase project or production
  Redis.** Not read-only, not "just to seed data". `ENV=development` disables
  App Check and HSTS; combined with production data that is a live PIPEDA
  exposure, not a dev convenience.
- **Never seed from a production export**, scrubbed or not.
- **Never copy production secrets** into `FLY_API_TOKEN_DEV`, the Supabase dev
  secrets, `JWT_SECRET`, `ADMIN_PASSWORD`, or Firebase credentials.
- **Never reuse the production or staging Fly app.**
- **Never add an automatic push trigger to `deploy-backend-dev.yml`.** The
  manual dispatch is the design (ADR-011 §1), not an unfinished TODO.
- **Do not add per-PR lint or test jobs here.** `ci.yml` owns those
  (`backend-test`, `rider-app-test`, `driver-app-test`). Duplicating them is
  what made the old `test-env.yml` wasteful as well as dead.

## Known gaps

- No general-purpose synthetic data seeder (step 2). Worth building before
  E2 load testing needs a populated database.
- `.github/workflows/ci.yml` still lists `develop` in its `push` and
  `pull_request` branch filters. Harmless — the branch does not exist — but
  it is a leftover from the same assumption that made `test-env.yml` dead.
