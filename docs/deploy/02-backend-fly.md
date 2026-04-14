# 02 — Backend Deploy (Fly.io)

**Goal:** deploy `backend/` to Fly.io with every production secret
populated, backed by the Supabase project from
[`01-supabase.md`](./01-supabase.md) and the third-party services
from [`05-third-party-services.md`](./05-third-party-services.md).

**Time:** ~45 minutes.

**Pre-reqs:**

- Supabase project live; `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`
  in your vault.
- Redis URL (`rediss://…`) in your vault.
- `JWT_SECRET`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`,
  `FIREBASE_SERVICE_ACCOUNT_JSON` in your vault.
- `flyctl` installed locally (`curl -L https://fly.io/install.sh | sh`).

---

## Step 1 — Log in to Fly

```bash
fly auth login    # opens browser
fly auth whoami   # sanity check
```

---

## Step 2 — Create the app

From the repo root (NOT `backend/`):

```bash
fly apps create spinr-backend
```

If the name is taken, pick something else and update `app = "…"` in
`fly.toml` before deploying.

Verify `fly.toml` is correct:

```bash
cat fly.toml
```

Expected:

```toml
app = "spinr-backend"
primary_region = "yyz"   # edit if your users aren't near Toronto

[build]
  dockerfile = "backend/Dockerfile"

[http_service]
  internal_port = 8000
  force_https = true
  auto_stop_machines = false          # do NOT change
  auto_start_machines = true
  min_machines_running = 1            # do NOT drop to 0 — stops background loops
  processes = ["app"]
```

**Do not flip `min_machines_running = 0` or `auto_stop_machines = true`.**
The backend runs 5 critical background loops in-process. If the
machine stops, they stop. The P1 roadmap (Sprint 1 PR 1.1) splits
them into a dedicated worker; until that ships, min-1 is mandatory.

---

## Step 3 — Set every secret

**Copy this block, substitute real values from your vault, run it once.**

```bash
fly secrets set \
  SUPABASE_URL='https://<ref>.supabase.co' \
  SUPABASE_SERVICE_ROLE_KEY='eyJ...' \
  JWT_SECRET='<64-char-urlsafe>' \
  ADMIN_EMAIL='admin@yourdomain.app' \
  ADMIN_PASSWORD='<strong-12+-char-password>' \
  FIREBASE_SERVICE_ACCOUNT_JSON='{"type":"service_account","project_id":"..."}' \
  RATE_LIMIT_REDIS_URL='rediss://default:<pw>@<host>:6379' \
  ALLOWED_ORIGINS='https://admin.yourdomain.app,https://spinr.app' \
  SENTRY_DSN='https://<hash>@o<org>.ingest.sentry.io/<proj>' \
  ENV='production' \
  --app spinr-backend
```

Notes:

- **Single quotes** around values so shell variable expansion doesn't
  eat your `$` characters.
- `FIREBASE_SERVICE_ACCOUNT_JSON` must be one line (flatten with
  `cat sa.json | tr -d '\n'`).
- `ALLOWED_ORIGINS` must NOT contain `*` in production — the
  validator refuses to start otherwise.
- `ENV=production` is the trigger for the production-config
  validator in `backend/core/middleware.py`.
- `SENTRY_DSN` is currently optional but strongly recommended; the
  P1 roadmap makes it mandatory (Sprint 2 PR 2.1).

Verify:

```bash
fly secrets list --app spinr-backend
```

You should see all 10 names (values hidden).

---

## Step 4 — First deploy

```bash
fly deploy
```

Watch the build. On success Fly prints:

```
==> Monitoring deployment
v1 is being deployed
...
--> v1 deployed successfully
```

If the build fails, the error is in the Docker build step — usually
a dep install failure. Fix and retry.

---

## Step 5 — Verify the backend booted cleanly

```bash
fly logs --app spinr-backend
```

Look for (in this order):

```
Spinr API starting...
Initializing database connection...
Supabase connection verified
Middleware initialized: CORS, Security Headers (HSTS=on), Rate Limiting
Started background task: subscription_expiry (6h)
Started background task: surge_engine (2min)
Started background task: scheduled_dispatcher (60s)
Started background task: payment_retry (5min)
Started background task: document_expiry (12h)
WS pub/sub started (backend=rediss://…, channel=spinr:ws:dispatch)
Spinr API startup complete (5 background tasks running)
```

If you see:

- `RuntimeError: Refusing to start: production configuration has N problem(s)` —
  a required secret is wrong. Read the error, fix the secret, redeploy.
- `Supabase health check failed` — `SUPABASE_URL` or
  `SUPABASE_SERVICE_ROLE_KEY` is wrong, or the schema was never
  applied. Re-run `01-supabase.md` § 4.
- `WS pub/sub: could not connect to Redis` — `RATE_LIMIT_REDIS_URL`
  is wrong. Upstash URLs must be `rediss://` (TLS) with the password.

---

## Step 6 — Smoke-test

```bash
curl -sSf https://spinr-backend.fly.dev/healthz
# Expected: {"status":"ok"} or similar
```

Try a real endpoint:

```bash
curl -sSf -X POST https://spinr-backend.fly.dev/api/v1/auth/send-otp \
  -H 'Content-Type: application/json' \
  -d '{"phone":"+15555550123"}'
# Expected: 200 with {"message":"OTP sent"} — or 429 if rate limit already hit.
# Real Twilio delivery requires a verified number.
```

---

## Step 7 — Map your custom domain

```bash
fly certs create api.yourdomain.app --app spinr-backend
```

Fly prints DNS records to add at your DNS provider. Most commonly:

- An `A` record pointing `api.yourdomain.app` to a Fly-provided IPv4.
- An `AAAA` record pointing to a Fly-provided IPv6.
- An `_acme-challenge.api.yourdomain.app` CNAME for the TLS cert
  issuance.

Add them at your DNS provider (Cloudflare, Route53, Namecheap…).
**If using Cloudflare**, disable the proxy (orange cloud OFF) until
the cert issues — Fly needs direct access to validate.

Wait 1-10 minutes, then:

```bash
fly certs show api.yourdomain.app --app spinr-backend
```

Look for `Issued`. Once issued:

```bash
curl -sSf https://api.yourdomain.app/healthz
```

Now update:

- **Stripe** webhook endpoint → `https://api.yourdomain.app/api/webhooks/stripe`
- **Admin dashboard** `NEXT_PUBLIC_API_URL` → `https://api.yourdomain.app`
- **Mobile apps** `EXPO_PUBLIC_BACKEND_URL` → `https://api.yourdomain.app`

---

## Step 8 — Set up the GitHub Actions deploy workflow

The prior `deploy-backend.yml` was removed while this runbook was
being built. Restore it now:

```yaml
# .github/workflows/deploy-backend.yml
name: Deploy Backend to Fly.io

on:
  push:
    branches: [main]
    paths:
      - 'backend/**'
      - 'fly.toml'

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: superfly/flyctl-actions/setup-flyctl@master
      - run: flyctl deploy --remote-only
        env:
          FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}
```

Add the token to GitHub:

```bash
flyctl tokens create deploy --app spinr-backend
# Copy the token, then:
# GitHub → Settings → Secrets and variables → Actions → New repository secret
# Name: FLY_API_TOKEN
# Value: FlyV1 fm2_...
```

Push the workflow file; next `backend/**` change auto-deploys.

---

## Step 9 — Scale up (post-launch, not now)

Launch at 2 machines once traffic arrives:

```bash
fly scale count 2 --app spinr-backend --region yyz
```

Upgrade CPU / memory if p95 latency climbs:

```bash
fly scale vm shared-cpu-2x --memory 2048 --app spinr-backend
```

---

## Done when…

- [ ] `fly status --app spinr-backend` shows ≥1 machine as `started`.
- [ ] Logs show all expected startup banners.
- [ ] `curl https://api.yourdomain.app/healthz` returns 200.
- [ ] `fly certs show api.yourdomain.app` reports `Issued`.
- [ ] Stripe webhook updated to the production URL.
- [ ] `FLY_API_TOKEN` is in GitHub Actions secrets for auto-deploy.

Next: [`03-admin-vercel.md`](./03-admin-vercel.md) to deploy the admin
dashboard.
