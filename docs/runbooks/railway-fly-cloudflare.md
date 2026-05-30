# Railway + Fly.io + Cloudflare Load Balancer

Use this runbook to run the backend on Railway Canada and Fly.io Toronto
behind one public API hostname.

Target architecture:

```text
api.spinr.ca
  -> Cloudflare Load Balancer
      -> Railway backend, primary
      -> Fly.io backend in yyz, failover or weighted secondary
```

Start with Railway primary and Fly failover. Move to weighted active-active
only after the smoke checks, logs, WebSockets, OTP, dispatch, and payments are
clean.

## Requirements

- Railway backend deployed in the Canadian region available to the account.
- Fly.io app deployed in `yyz` using `backend/fly.toml`.
- Supabase remains the shared durable store with `SUPABASE_REGION=ca-central-1`.
- One shared Redis-compatible service reachable from both providers for:
  `REDIS_URL`, `RATE_LIMIT_REDIS_URL`, and `WS_REDIS_URL`.
- Cloudflare Load Balancing enabled for the API domain.
- Same backend secrets on Railway and Fly. Do not generate separate secrets per
  provider.

## Fly.io Deployment

Install and authenticate `flyctl`, then create the app from the backend folder.
If `spinr-backend-yyz` is unavailable, choose a unique app name and update
`backend/fly.toml`.

```powershell
cd backend
fly auth login
fly apps create spinr-backend-yyz --org <fly-org>
```

Set Fly secrets to the exact same production values used by Railway:

```powershell
fly secrets set `
  SUPABASE_URL="<copy-from-railway>" `
  SUPABASE_SERVICE_ROLE_KEY="<copy-from-railway>" `
  JWT_SECRET="<copy-from-railway>" `
  ADMIN_PASSWORD="<copy-from-railway>" `
  FIREBASE_SERVICE_ACCOUNT_JSON="<copy-from-railway>" `
  FIREBASE_DRIVER_APP_ID="<copy-from-railway>" `
  FIREBASE_RIDER_APP_ID="<copy-from-railway>" `
  REDIS_URL="<copy-from-railway>" `
  RATE_LIMIT_REDIS_URL="<copy-from-railway>" `
  WS_REDIS_URL="<copy-from-railway>" `
  ALLOWED_ORIGINS="<production-origins>"
```

Deploy and keep two Machines warm in Toronto:

```powershell
fly deploy
fly scale count 2 --region yyz
fly status
fly checks list
```

Smoke-test Fly before putting it behind Cloudflare:

```powershell
Invoke-WebRequest https://spinr-backend-yyz.fly.dev/health
```

Expected response:

```json
{"status":"healthy"}
```

## Cloudflare Load Balancer

Create a monitor:

- Type: HTTPS
- Method: `GET`
- Path: `/health`
- Expected status: `200`
- Timeout: `5s`
- Interval: `60s`

Create two pools:

- `spinr-railway-ca`: Railway public backend domain.
- `spinr-fly-yyz`: Fly public backend domain.

Create or update the load balancer for `api.spinr.ca`:

- Default pool: `spinr-railway-ca`
- Fallback pool: `spinr-fly-yyz`
- Proxy status: proxied
- Session affinity: enabled
- Session affinity mode: Cloudflare cookie with client IP fallback

Cloudflare recommends session affinity when WebSocket origins sit behind a
Cloudflare Load Balancer. Keep it enabled even though Spinr also uses Redis
pub/sub for cross-replica WebSocket fan-out.

## Cutover Sequence

1. Deploy Fly and confirm `/health` is green.
2. Add Fly to Cloudflare as failover only.
3. Point mobile and admin production API URLs to `https://api.spinr.ca`.
4. Confirm auth, OTP, ride search, driver accept, WebSocket updates, and Stripe
   webhook processing through the load-balanced URL.
5. Watch Railway logs, Fly logs, Redis metrics, Supabase errors, and Sentry for
   at least one traffic peak.
6. If stable, test a small weighted split such as 90 percent Railway and
   10 percent Fly.

## Rollback

If Fly shows errors, remove `spinr-fly-yyz` from the load balancer or set its
weight to zero. Existing Railway traffic continues through the same
`api.spinr.ca` hostname.

If Cloudflare load balancing is the problem, point `api.spinr.ca` directly back
to the Railway backend domain and keep the Fly app deployed but out of traffic.

## Safety Checks

- `JWT_SECRET` must be identical across providers or users will be logged out
  randomly depending on which origin receives a request.
- Redis must be shared across providers or OTP lockouts, rate limits, driver
  presence, and WebSocket pub/sub degrade to per-machine behavior.
- Background loops run on every backend process. The current code uses atomic
  DB claims, flags, and idempotency keys; keep that contract for new loops.
- Stripe webhook URLs should target `https://api.spinr.ca/...`, not a provider
  domain, so failover does not require Stripe dashboard edits.
- Never put Railway or Fly provider URLs in mobile production builds after this
  cutover. Use only the load-balanced API hostname.
