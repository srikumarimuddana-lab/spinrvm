# Runbook: One-time provisioning for the staging → canary → production pipeline

Everything here is created **outside the repo** (consoles/CLIs). Do the steps
in order; the pipeline stays dormant (workflows no-op) until the final step
flips the repo variables. Companion docs:
[ADR-008](../adr/008-staging-canary-production-pipeline.md),
[canary-deploy.md](canary-deploy.md).

## 1. Staging Supabase project

- [ ] Create a new Supabase project in **ca-central-1** (PIPEDA — non-`ca-`
      regions refuse to boot with ENV=staging).
- [ ] Apply the schema + all migrations:
      `PG_CONNECTION_STRING=<staging session-pooler DSN> python backend/scripts/migrate.py --env staging --yes`
      (use the Session pooler DSN — the direct `db.<ref>` host is IPv6-only).
- [ ] Seed the `app_settings` row (via the staging admin dashboard once it's
      up, or SQL) with **test-grade** credentials only:
      - Stripe **test mode** `sk_test_…` / `pk_test_…`
      - A Stripe test-mode webhook endpoint pointing at
        `https://api-staging.spinr.ca/api/v1/webhooks/stripe` → its own
        `whsec_…` signing secret (repeat for the Connect webhook if used)
      - Twilio **test credentials** + magic from-number
      - A Google Maps server key restricted to the staging backend IPs/domain
- [ ] Confirm RLS is active on user-data tables (migrations ship it, but
      verify with a quick anon-key probe).

## 2. Staging Redis

- [ ] Provision a small Redis reachable from Fly yyz (Upstash/Fly Redis).
- [ ] Note the URL for `REDIS_URL` (staging does not need separate
      RATE_LIMIT/WS URLs — they fall back to REDIS_URL).

## 3. Fly apps

- [ ] `fly apps create spinr-backend-staging` and `fly apps create
      spinr-backend-canary` (org/region yyz).
- [ ] Deploy-scoped tokens:
      `fly tokens create deploy -a spinr-backend-staging` → `STAGING_FLY_API_TOKEN`
      `fly tokens create deploy -a spinr-backend-canary` → `CANARY_FLY_API_TOKEN`
- [ ] **Staging secrets** (`fly secrets set -a spinr-backend-staging …`) —
      DISTINCT from production, strong values (staging runs prod guards):
      - `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (staging project)
      - fresh `JWT_SECRET` (≥32 chars), fresh `ADMIN_PASSWORD`, `ADMIN_EMAIL`
      - `FIREBASE_SERVICE_ACCOUNT_JSON`, `FIREBASE_DRIVER_APP_ID`,
        `FIREBASE_RIDER_APP_ID` (staging Firebase — see §7)
      - `REDIS_URL` (staging Redis)
      - `ALLOWED_ORIGINS=https://admin-staging.spinr.ca`
      - `COOKIE_DOMAIN=.spinr.ca`
      - `REVIEW_LOGIN_ACCOUNTS=<one staging smoke account phone:otp>`
      - optional: `METRICS_AUTH_TOKEN`
- [ ] **Canary secrets** — BYTE-IDENTICAL to `spinr-backend-yyz`, applied from
      the canonical secret store (Fly secrets are not readable; there is no
      copy command). Add canary to the secret-rotation checklist alongside
      stable + Railway.
- [ ] TLS: `fly certs add api-staging.spinr.ca -a spinr-backend-staging`.
      The canary needs no custom cert (reached via the LB origin hostname).

## 4. DNS + Cloudflare Load Balancer

- [ ] Plain CNAME (DNS-only): `api-staging.spinr.ca` →
      `spinr-backend-staging.fly.dev`.
- [ ] Cloudflare Load Balancer on `api-spinr.spinr.ca`:
      - Monitor: HTTPS GET `/health`, expect 200, interval 60s.
      - Pool `stable`: origin `spinr-backend-yyz.fly.dev`, weight **0.95**,
        Host header override `spinr-backend-yyz.fly.dev`.
      - Pool `canary`: origin `spinr-backend-canary.fly.dev`, weight **0.05**,
        Host header override `spinr-backend-canary.fly.dev`.
      - Pool `railway`: the Railway service hostname — configured as the
        **fallback pool** (receives traffic only when stable+canary are down).
      - Steering: random/weighted. Session affinity OFF (backend is stateless
        across replicas; WS reconnects are fleet-agnostic).
- [ ] **Cutover safely**: create the LB with weights 1.0/0.0 first (traffic
      unchanged vs today's CNAME), deploy the canary app with the SAME sha as
      prod, verify, then set 0.95/0.05.
- [ ] Verify no host-allowlist middleware rejects the `.fly.dev` Host
      override (CORS ALLOWED_ORIGINS is browser-only and unaffected).

## 5. Vercel (admin dashboard staging)

- [ ] New Vercel project (or preview environment) on `admin-staging.spinr.ca`,
      region `yul1`, env vars: `NEXT_PUBLIC_API_URL=https://api-staging.spinr.ca`,
      `BACKEND_URL=https://api-staging.spinr.ca` (build fails without them),
      staging Sentry DSN, staging Maps browser key.

## 6. EAS (mobile staging track)

- [ ] In EAS env vars for the `preview` (and `test`) environment:
      `EXPO_PUBLIC_BACKEND_URL=https://api-staging.spinr.ca` for rider-app and
      driver-app. Production env keeps `https://api-spinr.spinr.ca`.
- [ ] Note: push-to-main OTA publishes to the `preview` channel — internal
      testers on preview builds will now exercise staging data.

## 7. Firebase

Decision: separate staging Firebase project (cleaner push-token isolation,
own App Check registry) — reuse of the prod project is workable but mixes
device registries. Either way, set the three FIREBASE_* staging secrets in §3.

## 8. GitHub configuration

- [ ] Environments (repo → Settings → Environments):
      - `staging` — no reviewers. Secrets: `STAGING_FLY_API_TOKEN`,
        `STAGING_PG_CONNECTION_STRING`, `STAGING_SUPABASE_URL`,
        `STAGING_SENTRY_DSN` (optional), `STAGING_EXPECTED_PROJECT_REF`
        (optional).
      - `production-canary` — **required reviewers** (release approvers).
        Secrets: `CANARY_FLY_API_TOKEN`, `PROD_PG_CONNECTION_STRING`,
        `PROD_SUPABASE_URL`, `EXPECTED_PROD_PROJECT_REF`,
        `CANARY_METRICS_TOKEN` (optional).
      - `production` — **required reviewers**. Move the existing repo secrets
        here: `FLY_API_TOKEN`, `RAILWAY_TOKEN`, `SENTRY_DSN`,
        `FLY_HEALTH_URL`, `BACKEND_HEALTH_URL`.
- [ ] Repo variables (Settings → Secrets and variables → Variables):
      - `STAGING_ENABLED=true` once §1–§6 are done → next push to main runs
        the staging pipeline.
      - `CANARY_PIPELINE_ENABLED=true` once §4 is done and a same-sha canary
        is verified → promote-production becomes runnable.

## 9. Dress rehearsal, then retire direct prod deploys

- [ ] Run one full promote-production with a trivial change: approval #1 →
      no-op prod migration → canary → 20-min bake → approval #2 → stable +
      Railway.
- [ ] Only after a clean rehearsal: remove the `push:` triggers from
      `deploy-fly.yml` and `deploy-backend.yml` (keep `workflow_dispatch` as
      break-glass) in a follow-up PR. Until then, pushes to main still deploy
      prod directly — the pipeline runs alongside as a shadow.

## Standing costs (approximate)

| Item | ~CAD/month |
|---|---|
| Staging Supabase project | $0–35 (free tier may suffice initially) |
| Staging Redis | $0–15 |
| Fly staging machine (shared-cpu-1x/1gb, always on) | ~$8 |
| Fly canary machine (same) | ~$8 |
| Cloudflare Load Balancer | ~$7–14 |
