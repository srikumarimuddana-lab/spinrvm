# Railway + Fly.io failover (Cloudflare CNAME cutover)

Run the backend on **Fly.io (Toronto/yyz)** and **Railway (Canada)** at the same
time, both auto-deployed from `main`, behind one public hostname. Routing is a
**Cloudflare CNAME** — there is no load balancer. Fly is the intended primary;
Railway is the warm standby ("just in case"). Fail-over and fail-back are each a
single DNS change.

See [ADR-007](../adr/007-fly-primary-railway-standby.md) for the decision and
trade-offs.

```text
api-spinr.spinr.ca                 redis.spinr.ca
  -> Cloudflare CNAME (proxied)       -> Cloudflare CNAME (DNS-only / gray-cloud)
      -> Fly.io  (yyz)  PRIMARY           -> Redis on Fly  (primary)
      -> Railway (CA)   STANDBY           -> Redis on Railway (fail-back)
```

## Requirements

- Fly.io app deployed in `yyz` from `backend/fly.toml`, 2 warm machines.
- Railway backend deployed in the Canadian region (existing).
- Supabase remains the shared durable store with `SUPABASE_REGION=ca-central-1`.
- One Redis reachable from both providers via the `redis.spinr.ca` alias, used for
  `REDIS_URL`, `RATE_LIMIT_REDIS_URL`, and `WS_REDIS_URL`.
- **Identical** backend secrets on Railway and Fly. Do not generate separate
  secrets per provider — a `JWT_SECRET` mismatch logs users out at random.
- Cloudflare DNS for `api-spinr.spinr.ca` and `redis.spinr.ca`.

## One-time: deploy commands run automatically

Two workflows mean you never need flyctl locally:

- **`.github/workflows/bootstrap-fly.yml`** (manual `workflow_dispatch`) — creates
  the app, sets all secrets from GitHub Actions secrets, then deploys and scales.
  Run this once to launch (or again to rotate secrets). Needs an **org-scoped**
  `FLY_API_TOKEN` (`fly tokens create org`) plus the production values stored as
  GitHub secrets (`SUPABASE_URL`, `JWT_SECRET`, `ADMIN_EMAIL`, `REDIS_URL`, … —
  see that workflow's header for the full list).
- **`.github/workflows/deploy-fly.yml`** — deploys Fly on every push to `main`, in
  parallel with `.github/workflows/deploy-backend.yml` for Railway.

The manual flyctl commands below are equivalent, for a local bring-up or a drill.

## Fly.io app setup

Install and authenticate `flyctl`, then create the app from the backend folder.
If `spinr-backend-yyz` is unavailable, choose a unique app name and update
`backend/fly.toml`.

```powershell
cd backend
fly auth login
fly apps create spinr-backend-yyz --org <fly-org>
```

Set Fly secrets to the exact same production values used by Railway. Two things
to watch:

- `ADMIN_EMAIL` **must** be copied from Railway. `backend/fly.toml` sets
  `ENV=production`, and `core/middleware._validate_production_config()` refuses to
  start when `ADMIN_EMAIL` is left at its `admin@spinr.ca` default — the Machines
  would crash-loop before `/health` ever passes.
- The Redis URLs must keep their credentials. Only swap the **host** for the
  alias (e.g. `rediss://:<password>@redis.spinr.ca:6379`); a bare
  `rediss://redis.spinr.ca:6379` connects unauthenticated and silently drops
  shared rate-limit / OTP / WS / leader-lock state.

```powershell
fly secrets set `
  SUPABASE_URL="<copy-from-railway>" `
  SUPABASE_SERVICE_ROLE_KEY="<copy-from-railway>" `
  JWT_SECRET="<copy-from-railway>" `
  ADMIN_PASSWORD=<paste-same-value-as-railway> `
  ADMIN_EMAIL="<copy-from-railway>" `
  FIREBASE_SERVICE_ACCOUNT_JSON="<copy-from-railway>" `
  FIREBASE_DRIVER_APP_ID="<copy-from-railway>" `
  FIREBASE_RIDER_APP_ID="<copy-from-railway>" `
  REDIS_URL="rediss://:<password>@redis.spinr.ca:6379" `
  RATE_LIMIT_REDIS_URL="rediss://:<password>@redis.spinr.ca:6379" `
  WS_REDIS_URL="rediss://:<password>@redis.spinr.ca:6379" `
  ALLOWED_ORIGINS="<production-origins>"
```

Create a deploy token for CI and store it as the `FLY_API_TOKEN` GitHub secret:

```powershell
fly tokens create deploy -a spinr-backend-yyz
```

Deploy and keep two Machines warm in Toronto:

```powershell
fly deploy --config fly.toml
fly scale count 2 --region yyz
fly status
fly checks list
```

Smoke-test Fly directly before any DNS change:

```powershell
Invoke-WebRequest https://spinr-backend-yyz.fly.dev/health
```

Expected response:

```json
{"status":"healthy"}
```

## Shared Redis behind a DNS alias

OTP lockouts, rate limits, driver presence, WebSocket pub/sub, and loop leader
locks all live in Redis. Both providers must use the **same** Redis or these
degrade to per-machine behavior.

1. Provision Redis on Fly (Upstash-on-Fly or a Fly Redis machine).
2. Create a Cloudflare DNS record `redis.spinr.ca` → the Fly Redis host. **Set it
   to DNS-only (gray cloud), not proxied.** Cloudflare's orange-cloud proxy only
   handles HTTP/HTTPS; a proxied record on the Redis port (6379) routes clients to
   the Cloudflare edge instead of the Redis origin and breaks shared rate limits,
   OTP state, WebSocket pub/sub, and leader locks. (Proxying Redis would require
   Cloudflare Spectrum, which we are not using.)
3. Set `REDIS_URL` / `RATE_LIMIT_REDIS_URL` / `WS_REDIS_URL` to
   `rediss://:<password>@redis.spinr.ca:6379` on **both** Railway and Fly —
   keep the credentials, only the host is the alias.

> **Failure-domain caveat (read this).** If Redis lives only in Fly and the Fly
> region is what failed, repointing `api-spinr.spinr.ca` to Railway is not enough
> — Redis is down too. Provision a second Redis reachable from Railway and, during
> a Fly-region fail-back, repoint the `redis.spinr.ca` alias to it. An unaliased
> single Fly Redis makes fail-over hollow.

## Cut over to Fly-primary

1. Confirm `https://spinr-backend-yyz.fly.dev/health` is green and `fly checks
   list` shows both machines passing.
2. **Attach the custom hostname to Fly and provision TLS before touching DNS.**
   Fly only serves and issues a certificate for hostnames added to the app, so a
   green `.fly.dev` URL does not mean `api-spinr.spinr.ca` will work:
   ```powershell
   fly certs add api-spinr.spinr.ca -a spinr-backend-yyz
   fly certs show api-spinr.spinr.ca -a spinr-backend-yyz   # add any DNS records it asks for
   fly certs check api-spinr.spinr.ca -a spinr-backend-yyz  # wait until status is "Ready"
   ```
3. Confirm the `redis.spinr.ca` alias resolves and both providers connect to it.
4. Point the `api-spinr.spinr.ca` **CNAME** at the Fly app.
5. Validate through `https://api-spinr.spinr.ca`: auth, OTP issue/verify, ride
   search, driver accept, live WebSocket updates to rider + driver, and a Stripe
   test webhook (processes exactly once — idempotent).
6. Watch Fly logs, Railway logs, Redis metrics, Supabase errors, and Sentry for at
   least one traffic peak.

## Fail back to Railway

1. Point the `api-spinr.spinr.ca` CNAME back at the Railway backend domain.
2. If the fail-back was caused by a Fly-region outage that also took Redis down,
   repoint the `redis.spinr.ca` alias to the Railway-side Redis.
3. Confirm `/health` and one full ride flow through `api-spinr.spinr.ca`, then
   restore Fly when it recovers.

## Safety checks

- `JWT_SECRET` must be identical across providers or users are logged out at
  random depending on which origin served the request.
- `ADMIN_EMAIL` must be set to the real Railway value on Fly — the production
  config guard rejects the `admin@spinr.ca` default and the app will not boot.
- Redis must be shared (via the alias, **DNS-only**, credentials intact) across
  providers or OTP lockouts, rate limits, driver presence, WebSocket pub/sub, and
  loop leader locks degrade.
- Background loops run on every backend process; with both providers live they run
  twice over. This is safe only because both share the same Supabase and aliased
  Redis (atomic DB claims, idempotency keys, Redis leader locks). Confirm the
  shared Redis alias is live on both before both take traffic.
- Stripe webhook URLs must target `https://api-spinr.spinr.ca/...`, not a provider
  domain, so cutover needs no Stripe dashboard edits.
- `SUPABASE_REGION=ca-central-1` must stay set on Fly (it is, in `fly.toml`) —
  production refuses to boot otherwise (PIPEDA).
- Never put Railway or Fly provider URLs in mobile production builds. Use only the
  `api-spinr.spinr.ca` hostname.
