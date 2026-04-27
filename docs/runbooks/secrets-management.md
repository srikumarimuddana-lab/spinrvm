# Secrets Management Runbook

## Architecture

Spinr uses a **platform-native secrets** model: no secret ever lives in the codebase.
Credentials are injected at runtime by the platform that runs each surface.

```
backend  → Railway Variables   (injected as env vars at container boot)
admin    → Vercel Variables     (injected at build + runtime)
mobile   → EAS Secrets          (injected at build time via eas.json env block)
local dev → .env files          (gitignored — never committed)
```

## Credential Inventory

| Secret | Surface | Where to get it | Rotation trigger |
|---|---|---|---|
| `SUPABASE_URL` | backend | Supabase → Settings → API | Never (URL is stable) |
| `SUPABASE_SERVICE_ROLE_KEY` | backend | Supabase → Settings → API → Roll key | On leak, on team member departure |
| `JWT_SECRET` | backend | Generate: `python3 -c "import secrets; print(secrets.token_hex(32))"` | On leak; rotates all active sessions |
| `ADMIN_EMAIL` | backend | Choose your admin email | Intentional only |
| `ADMIN_PASSWORD` | backend | Choose (≥20 chars in prod) | Every 90 days or on suspicion |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | backend | Firebase → Project Settings → Service Accounts → Generate key | On leak, on team departure |
| `FIREBASE_RIDER_APP_ID` | backend | `google-services.json` in repo | Never (public identifier) |
| `FIREBASE_DRIVER_APP_ID` | backend | `google-services.json` in repo | Never (public identifier) |
| `GOOGLE_MAPS_API_KEY` (server) | backend | GCP → Credentials | On leak; restrict to server IP first |
| `TWILIO_ACCOUNT_SID` | backend | Twilio console home | Never (not a secret) |
| `TWILIO_AUTH_TOKEN` | backend | Twilio console home | On leak, on team departure |
| `TWILIO_PHONE_NUMBER` | backend | Twilio console | Never |
| `STRIPE_SECRET_KEY` | backend | Stripe → Developers → API keys | On leak |
| `STRIPE_WEBHOOK_SECRET` | backend | Stripe → Webhooks → signing secret | When webhook endpoint changes |
| `REDIS_URL` | backend | Upstash console | On leak |
| `SENTRY_DSN` | backend + admin | Sentry → Project Settings | Never (low risk) |
| `EXPO_PUBLIC_BACKEND_URL` | rider + driver | Railway deployment URL | When backend URL changes |
| `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY` | rider + driver | GCP → Credentials | On leak; restrict to bundle ID |
| `EXPO_PUBLIC_STRIPE_PUBLISHABLE_KEY` | rider + driver | Stripe → Developers → API keys | When switching test↔live |
| `BACKEND_URL` | admin | Railway deployment URL | When backend URL changes |

## Local Dev Setup

Each developer needs to create local `.env` files from the examples:

```bash
cp backend/.env.example     backend/.env
cp rider-app/.env.example   rider-app/.env
cp driver-app/.env.example  driver-app/.env
cp admin-dashboard/.env.example admin-dashboard/.env.local
```

Then fill in real values. Share the current values with new team members via
a private channel — never by email or in a PR comment.

**The local `.env` files are gitignored and will never be committed.**

## Setting Variables in Railway (backend)

1. Go to [railway.app](https://railway.app) → your project → the backend service
2. Click **Variables** tab
3. Click **New Variable** for each entry in the inventory above
4. Deploy is triggered automatically after saving

## Setting Variables in Vercel (admin dashboard)

1. Go to Vercel → your project → **Settings → Environment Variables**
2. Add `BACKEND_URL` pointing to your Railway backend URL
3. Redeploy to apply (or Vercel redeploys automatically on next git push)

## Setting Secrets in EAS (mobile builds)

```bash
eas secret:create --scope project --name EXPO_PUBLIC_BACKEND_URL --value "https://your-backend.railway.app"
eas secret:create --scope project --name EXPO_PUBLIC_GOOGLE_MAPS_API_KEY --value "AIza..."
eas secret:create --scope project --name EXPO_PUBLIC_STRIPE_PUBLISHABLE_KEY --value "pk_test_..."
```

These are injected at build time and override anything in `eas.json`.

## Recommended: Doppler (when team grows)

[Doppler](https://doppler.com) is a secrets manager with native integrations for Railway, Vercel, and EAS.
It gives a single source of truth, team access controls, and an audit trail.

Setup:
1. Create a Doppler project `spinr` with configs `dev`, `stg`, `prd`
2. Migrate all secrets from the table above into Doppler
3. Connect Railway: Doppler Dashboard → Integrations → Railway
4. Connect Vercel: Doppler Dashboard → Integrations → Vercel
5. For EAS: `doppler secrets download --no-file --format env > rider-app/.env` in CI
6. For local dev: `doppler run -- yarn start` (no `.env` file needed locally)

## Rotation Procedure

1. Generate the new value (see "Where to get it" column above)
2. Update in the platform dashboard (Railway / Vercel / EAS)
3. Update in your local `.env` file
4. Announce the rotation in `#engineering` so teammates update their local files
5. Verify the old value no longer works if applicable (e.g. Supabase key: test with curl)
6. Record the rotation in your team's change log

## What Was Previously in Git History

A Supabase service-role key was committed in an early `.env.example` and has since
been redacted from HEAD (commit `eb58ec4`). The key must be rotated via Supabase
Dashboard → Settings → API → Roll service_role key before going to production.

See `reports/compliance/2026-04-26-supabase-service-role-key-breach-assessment.md`
for the full incident assessment and outstanding action items.
