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

- Fly.io app deployed in `yyz` from `backend/fly.toml`: a pool of 8 machines, of
  which 2 stay warm (`min_machines_running = 2`) and 6 sit suspended as burst
  capacity. Fly's proxy resumes suspended machines when running machines exceed
  `soft_limit` (750 connections). See `docs/runbooks/capacity-scaling.md`.
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
  the app (idempotent), verifies the required secrets are present on the Fly app,
  then deploys and scales. Needs a `FLY_API_TOKEN` with access to the org (a Fly
  personal token works; a per-app deploy token can't create the app).
  **Secrets are set directly in Fly, not GitHub** — set them with `fly secrets set`
  (next section) before running with `deploy=true`.
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

Set Fly secrets to the exact same production values used by Railway. Three things
to watch:

- `ADMIN_EMAIL` **must** be copied from Railway. `backend/fly.toml` sets
  `ENV=production`, and `core/middleware._validate_production_config()` refuses to
  start when `ADMIN_EMAIL` is left at its `admin@spinr.ca` default — the Machines
  would crash-loop before `/health` ever passes.
- The Redis URLs must keep their credentials. Only swap the **host** for the
  alias (e.g. `rediss://:<password>@redis.spinr.ca:6379`); a bare
  `rediss://redis.spinr.ca:6379` connects unauthenticated and silently drops
  shared rate-limit / OTP / WS / leader-lock state. (TLS caveat below.)
- `FIREBASE_SERVICE_ACCOUNT_JSON` is raw JSON full of double quotes, so it must
  **not** go in the double-quoted `fly secrets set KEY="..."` form — that breaks
  the command or stores a truncated value (the backend then silently drops every
  FCM push, while `/health` still passes). `core/security.init_firebase()` runs
  `json.loads()` on it, so it must stay **JSON, not base64**. Set it separately
  from a minified single-line value (see below).

```powershell
fly secrets set `
  SUPABASE_URL="<copy-from-railway>" `
  SUPABASE_SERVICE_ROLE_KEY="<copy-from-railway>" `
  JWT_SECRET="<copy-from-railway>" `
  ADMIN_PASSWORD=<paste-same-value-as-railway> `
  ADMIN_EMAIL="<copy-from-railway>" `
  FIREBASE_DRIVER_APP_ID="<copy-from-railway>" `
  FIREBASE_RIDER_APP_ID="<copy-from-railway>" `
  REDIS_URL="rediss://:<password>@redis.spinr.ca:6379" `
  RATE_LIMIT_REDIS_URL="rediss://:<password>@redis.spinr.ca:6379" `
  WS_REDIS_URL="rediss://:<password>@redis.spinr.ca:6379" `
  ALLOWED_ORIGINS="<production-origins>"
```

Set the Firebase service account separately. Save the Railway value to a file,
then pipe the raw contents so no shell quoting touches the embedded `"`:

```powershell
# firebase-sa.json = the exact JSON from Railway (single object; minified is fine)
fly secrets set "FIREBASE_SERVICE_ACCOUNT_JSON=$(Get-Content firebase-sa.json -Raw)" -a spinr-backend-yyz
# bash/zsh equivalent:
#   fly secrets set "FIREBASE_SERVICE_ACCOUNT_JSON=$(cat firebase-sa.json)" -a spinr-backend-yyz
```

(The `bootstrap-fly.yml` workflow handles this automatically via `fly secrets
import` — just store the minified single-line JSON as the `FIREBASE_SERVICE_ACCOUNT_JSON`
GitHub secret.)

Create a deploy token for CI and store it as the `FLY_API_TOKEN` GitHub secret:

```powershell
fly tokens create deploy -a spinr-backend-yyz
```

Deploy and provision the machine pool in Toronto. `scale count` sets the *pool*
size, not the running count — `fly.toml`'s `min_machines_running = 2` plus
`auto_stop_machines = "suspend"` means Fly keeps 2 warm and suspends the other 6
until a burst wakes them:

```powershell
fly deploy --config fly.toml
fly scale count 8 --region yyz
fly status
fly checks list
```

`fly status` after settling should show 2 machines `started` and 6 `suspended`.
Seeing all 8 `started` outside a burst means autostop is not taking effect —
check `auto_stop_machines` in `backend/fly.toml`.

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

> **TLS hostname caveat (read before using `rediss://` with the alias).** With
> `rediss://`, the Python clients (`utils.rate_limiter`, `utils.redis_client`,
> `utils.ws_pubsub`) verify the server certificate against the **hostname in the
> URL** — `redis.spinr.ca`. A managed provider (Upstash / Fly Redis) normally
> presents a cert for its *own* hostname, so a plain CNAME alias fails TLS
> verification and every shared-Redis connection breaks even though the URL is
> set. Pick one:
> - **Keep the DNS-swap benefit:** add `redis.spinr.ca` as a *custom domain* on
>   the Redis provider so the origin presents a cert valid for the alias. Then
>   `rediss://...@redis.spinr.ca:6379` verifies cleanly and fail-back is a DNS
>   change.
> - **Simplest:** put the provider's real TLS hostname in the URL (cert matches
>   out of the box). Fail-back then means editing the `REDIS_URL` secret on both
>   providers, not just repointing DNS.
> Do **not** disable certificate verification to paper over the mismatch.

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

> **Capacity asymmetry — read before failing over during a traffic peak.**
> Fly is provisioned for burst: 8 machines (2 warm + 6 suspended), 750/1000
> connections each, roughly 6,000 concurrent users. Railway runs
> `numReplicas: 1` (`railway.json`) with no autoscaling and no equivalent
> connection headroom. Failing over during a burst therefore lands
> burst-sized traffic on a single, much smaller instance.
>
> Worse today: Railway's `deploy-backend.yml` has been **failing on every push
> to `main`** with `Invalid RAILWAY_TOKEN` (confirmed 2026-09-04 from the run
> logs — not the Environment-protection pause C5 originally recorded), so the
> standby has been silently drifting from `main` (ACTION_ITEMS C5). Verify what
> commit Railway is actually running (`/deploy-info`, see "Standby readiness
> automation" below) before treating it as a viable target, and expect to scale
> Railway up manually as part of the cutover, not after it.

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

## Standby readiness automation

Added 2026-09-04 (ACTION_ITEMS C5). Until then nothing verified that Railway
was a standby at all: its deploy workflow had failed on every push for weeks
(`Invalid RAILWAY_TOKEN`) and nothing could even say which commit Railway was
running. Three pieces now keep it honest. None of them ever reads a secret
**value** — only variable names, HTTP status codes, and HMAC fingerprints.

| Piece | Where | What it guarantees |
|---|---|---|
| Required-variable list | `deploy/backend-required-env.txt` | Single source of truth for every name that must exist on a production deploy, with scope (`both` / `railway`-only) and the reason. Edit this, never a workflow's inline list. |
| Railway deploy gate | `.github/workflows/deploy-backend.yml` | Fails **before** building if the token is invalid, or if any required name is missing on the service. After deploy, confirms the served build sha is the commit just pushed. |
| Fly deploy gate | `.github/workflows/deploy-fly.yml` | Same served-sha confirmation. Both workflows stamp `backend/build_info.json` into the image. |
| `GET /deploy-info` | `backend/server.py` | Returns `{provider, env, build:{sha,ref,built_at,provider}, fingerprints:{…}}`. Fingerprints are truncated HMAC-SHA256 keyed by `JWT_SECRET` — identical on both providers ⇔ the value is identical. Gated by `Authorization: Bearer <METRICS_AUTH_TOKEN>`; answers 503 when that token is unset. |
| Daily parity monitor | `.github/workflows/standby-parity-monitor.yml` + `scripts/standby_parity.py` | Every day (and on demand): Railway token valid, required names present on both, one-sided variables, both `/health` green, both serve the same sha, Fly serves `main`, every fingerprint equal. Files one tracked issue (label `standby-parity`), updates it in place, auto-closes it when green; the run itself goes red on CRITICAL. |

### One-time setup a human must do

The automation is dark until these exist. Each is a dashboard action; none
can be done from the repo.

1. **Rotate `RAILWAY_TOKEN`.** Railway → project → Settings → Tokens → New
   Token, type **Project Token**. Put it in GitHub → Settings → Secrets →
   Actions → `RAILWAY_TOKEN`. Then re-run `deploy-backend.yml` via
   `workflow_dispatch`; its new "Verify RAILWAY_TOKEN is valid" step is the
   proof.
2. **Set the Railway-only variables** on the `spinr-backend` service:
   `ENV=production` and `SUPABASE_REGION=ca-central-1` (Fly gets these from
   `fly.toml [env]`; `railway.json` cannot carry them). `SENTRY_DSN` too —
   `deploy-fly.yml` stages it into Fly on every deploy, Railway has no such
   step. The deploy gate lists anything else missing by name.
3. **Set `METRICS_AUTH_TOKEN` on both providers** (same value, as a Fly
   secret and a Railway variable) and as the GitHub Actions secret of the
   same name. Without it `/deploy-info` answers 503 and the monitor reports
   build-sha and value parity as "NOT verified" (WARN) — it never fakes a
   pass. `/metrics` already wants this token in production, so it is likely
   set on Fly already; copy that value.
4. **Confirm `BACKEND_HEALTH_URL` and `FLY_HEALTH_URL`** GitHub secrets point
   at the provider hostnames (`https://<service>.up.railway.app`,
   `https://spinr-backend-yyz.fly.dev`) — not at `api-spinr.spinr.ca`, which
   would probe whichever provider the CNAME currently selects and hide the
   other.
5. Run the monitor once by hand (Actions → "Standby parity monitor" → Run
   workflow) and work the issue it opens down to green.

### Reading a finding

- **Railway variables → could not list … Invalid RAILWAY_TOKEN**: no deploy has
  landed since the token broke. Step 1 above.
- **missing on Railway: `ENV`, `SUPABASE_REGION`**: the standby would boot in
  *development* mode — dev OTP bypass `1234` live, every production secret
  guard skipped, wildcard CORS tolerated. Step 2. Treat as CRITICAL even
  though `/health` is green.
- **standby is on a different build**: Railway is serving an older commit.
  Re-run `deploy-backend.yml`; if it fails, its log names the step.
- **every fingerprint differs → `JWT_SECRET`**: the two providers mint
  mutually-invalid tokens; a fail-over logs everyone out at random. Copy the
  Fly value to Railway exactly (no trailing newline).
- **values DIFFER … `REDIS_URL`/`RATE_LIMIT_REDIS_URL`/`WS_REDIS_URL`**: the
  providers are on different Redis instances — rate limits, OTP lockouts,
  driver presence, WS pub/sub and loop leader locks are split. Re-point to
  the shared alias per "Shared Redis behind a DNS alias".
- **Fly only: `X`**: a feature configured on the primary is silently off on the
  standby. Decide per variable; add it to Railway or accept and note it here.

## Fail-over drill checklist (ACTION_ITEMS C1)

Run in a low-traffic window once the monitor is green. Record real timings
back into this file.

1. Monitor green on the day (`standby-parity` issue closed, latest run green).
2. `GET https://<railway>/deploy-info` and `GET https://spinr-backend-yyz.fly.dev/deploy-info`
   (bearer `METRICS_AUTH_TOKEN`) show the same `build.sha` and it equals
   `main`'s HEAD.
3. Railway → service → scale replicas up **before** the switch (Capacity
   asymmetry note above); confirm `/health` on the Railway host.
4. Cloudflare: point the `api-spinr.spinr.ca` CNAME at the Railway domain.
   Note the time. Watch `dig api-spinr.spinr.ca` until it resolves to Railway.
5. Through `https://api-spinr.spinr.ca`: `/health`, `/deploy-info` (provider
   must now read `railway`), rider login (OTP), driver go-online, one ride
   search → accept → live WebSocket updates on both apps, a Stripe test
   webhook (must process exactly once).
6. Watch Railway logs, Sentry, and the loop watchdog for 15 minutes. Both
   providers run every background loop; leader locks are only shared if the
   Redis alias is genuinely shared (fingerprints equal in step 2).
7. Fail back: CNAME → Fly. Repeat step 5 (provider must read `fly`).
8. Scale Railway back down. Write timings and surprises below.

Drill log: _(none yet)_
