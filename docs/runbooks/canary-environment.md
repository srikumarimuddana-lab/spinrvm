# Canary environment

> **Status: scaffolding only, not live.** The Fly app, the secret, and the
> Cloudflare routing rule do not exist yet. Design rationale:
> `docs/adr/011-environment-topology.md` §3.

> **This tier serves real users and real data.** Unlike dev
> (`docs/runbooks/dev-test-environments.md`) and staging
> (`docs/runbooks/staging-environment.md`), the canary runs against the
> production Supabase project, production Redis, and production
> `app_settings`. It is a slice of production, not a test environment.

## What a canary is for here

`main` currently goes to 100% of production in one step. A canary takes a
commit that is already on `main`, runs it on a separate Fly app, routes a
small share of real traffic to it, and lets a human watch before the same
commit goes everywhere.

It catches what a feature flag cannot: a bad build, a dependency bump, a
startup regression in one of the 18 background loops in `core/lifespan.py`, a
memory leak, a slow query that only appears under real traffic shape.

**It does not catch a bad migration.** The canary shares the production
database, so a migration is already applied to production tables before any
canary machine reads them. Migration safety stays with staging rehearsal plus
`migration-check.yml`. Do not let a green canary be read as migration
confidence.

## Setup (one time)

### 1. Create the Fly app

```bash
fly apps create spinr-backend-canary --org <your-org>
fly tokens create deploy -a spinr-backend-canary
```

Register the token as `FLY_API_TOKEN_CANARY` in GitHub → Settings → Secrets
and variables → Actions. Optionally add `FLY_HEALTH_URL_CANARY`
(e.g. `https://spinr-backend-canary.fly.dev`) to enable the post-deploy probe.

### 2. Set production secrets on the canary app

The canary must run **byte-identical** secrets to production — same Supabase
project, same Redis, same Firebase, same Stripe — for the same reason
`backend/fly.toml` requires it of Railway: a canary running different
credentials is testing a configuration that will never ship.

```bash
fly secrets set -a spinr-backend-canary \
  SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... \
  JWT_SECRET=... ADMIN_PASSWORD=... \
  FIREBASE_SERVICE_ACCOUNT_JSON=... \
  REDIS_URL=... RATE_LIMIT_REDIS_URL=... WS_REDIS_URL=... \
  ALLOWED_ORIGINS=... SENTRY_DSN=...
```

Copy the values from the production app (`fly secrets list -a
spinr-backend-yyz` shows names; values come from your secret store, not from
Fly). **`JWT_SECRET` in particular must match production** — a rider whose
request lands on canary carries a token production issued, and a different
secret would reject it as invalid.

These are deliberately **not** staged through CI, unlike the dev and staging
workflows. Production credentials should not transit a workflow run.

### 3. Add Cloudflare weighted routing

`api-spinr.spinr.ca` is a Cloudflare CNAME today (ADR-007). Add a weighted
origin pool so a percentage of requests reach the canary:

| Origin | Weight (steady state) | Weight (canary soak) |
|---|---|---|
| `spinr-backend-yyz.fly.dev` | 100 | 95 |
| `spinr-backend-canary.fly.dev` | 0 | 5 |

Keep the canary at weight **0** as the resting state. A canary that is always
taking traffic is just an under-provisioned second production.

Session affinity should be **on**. Riders and drivers hold a long-lived
WebSocket (`fly.toml` counts concurrency in connections for this reason); a
client that flips between canary and production mid-ride gets inconsistent
behavior for no diagnostic benefit.

## Deploying a canary

1. Merge the change to `main` as normal. Let production deploy or not — the
   canary is a separate decision.
2. GitHub → Actions → **Deploy Backend to Fly.io (Canary)** → Run workflow.
   Select the SHA, type `CANARY` in the confirm box. The workflow refuses any
   ref that is not an ancestor of `origin/main`.
3. Raise the canary weight to 5%.
4. Soak.

## What to watch during a soak

Give it at least one full peak period. A quiet 20 minutes proves nothing about
a dispatch regression.

**Sentry** — filter `environment:canary`. This facet exists because the canary
runs `ENV=production` (see below), so `SENTRY_ENVIRONMENT` is what separates
it. A new issue class appearing only under `environment:canary` is the signal
this whole tier exists to produce.

**SLA table** (`CLAUDE.md`, Performance SLAs) — compare canary against
production over the same window, not against the published target:

| Path | Target P95 |
|---|---|
| Dispatch offer → driver notification | < 2 s |
| Fare estimate | < 300 ms (see the documented `_PRICING_ROUTE_WAIT_S` exception) |
| Fare settlement | < 1 s |
| WebSocket fan-out | < 100 ms |
| Stripe webhook processing | < 500 ms |

**KPI table** (`CLAUDE.md`) — match rate ≥ 85%, driver cancellation ≤ 3%,
payment success ≥ 99%. These move slowly; a 5% slice will be noisy. Treat a
sharp divergence as signal and a small one as noise.

**Fly** — `fly status -a spinr-backend-canary` and `fly logs`. Watch memory
across the soak; a leak is one of the few things only a canary surfaces.

## Abort

**Set the canary weight to 0 in Cloudflare.** Traffic drains immediately. No
redeploy, no code change, no Fly operation — the same DNS-level model as the
Fly/Railway failover in ADR-007.

Do this first, investigate second. The canary machine can be left running for
diagnosis once it is taking no traffic.

Abort — do not "wait and see" — on any of:

- A new error class in Sentry under `environment:canary` that is not present
  in production over the same window.
- Any SLA path materially worse on canary than on production.
- Any error touching money, ride state, or auth. `CLAUDE.md`'s "do not
  silently swallow errors" rule applies with more force here, not less: these
  are real riders' rides and real charges.
- Memory climbing steadily across the soak.

## Promote

Deploy the same SHA to production via `deploy-fly.yml`, then set the canary
weight back to 0. Production and canary now run the same code, and the canary
returns to its resting state ready for the next one.

## Why `ENV = "production"` on the canary

This is the single most important thing to understand before editing
`backend/fly.canary.toml`, and the mistake most likely to be made by someone
tidying it up.

`ENV` is not a label. About thirty code paths branch on the exact string
`"production"`, and four are single-gated — that string is the only thing
between the safe behavior and the unsafe one:

| Behavior | Call site | If `ENV != "production"` |
|---|---|---|
| Firebase App Check enforcement | `backend/core/middleware.py:890` | Disabled |
| HSTS response header | `backend/core/middleware.py:879` | Not sent |
| `secure` flag on auth cookies | `backend/utils/cookie_manager.py:37,64` | Cookies over plaintext |
| APNs endpoint | `backend/utils/apns_client.py:230` | **Sandbox** |

The APNs one is the sharpest: production device tokens are invalid against the
sandbox gateway, so an `ENV=canary` machine would silently fail every iOS push
for the users routed to it — no error the rider sees, just a notification that
never arrives.

So the canary is differentiated only by values nothing branches on:
`SENTRY_ENVIRONMENT=canary` (Sentry's environment facet, added to
`core/config.py` for exactly this) and `SPINR_DEPLOY_TIER=canary`
(descriptive; used in logs and metric labels only).

One `ENV`-gated behavior worth recording as *not* a risk, so it is not
re-litigated: the `"1234"` OTP bypass (`backend/routes/auth.py:434`, `:738`)
requires **both** `ENV != "production"` **and** Twilio unconfigured in
`app_settings`. The canary shares production `app_settings`, where Twilio is
configured, so it cannot activate regardless.

## What NOT to do

- **Never set `ENV` to anything but `"production"` in `fly.canary.toml`.** See
  above. It reads like a config tidy-up and is a live security and push-
  delivery regression.
- **Never point the canary at a non-production database** to "make it safer".
  A canary against staging data is a staging deploy with a production DNS
  entry — it proves nothing about production and the traffic hitting it is
  still real.
- **Never leave the canary at a non-zero weight as a resting state.**
- **Never use a green canary as migration confidence.** Shared database; see
  the top of this runbook.
- **Never promote from a soak that did not cover a peak period.**
- **Never skip the abort step before investigating.** Weight to 0 first.
