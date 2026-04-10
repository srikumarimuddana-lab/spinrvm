# Environment Variables Reference

This document lists every environment variable used across all four Spinr services.
Variables are sourced from `backend/core/config.py`, each app's `.env.example`, CI workflow
secrets (`ci.yml`), and deployment manifests (`render.yaml`, `fly.toml`).

**Notation:**
- `Required` — the service will not start correctly or will behave insecurely without this.
- `Optional` — has a safe default or enables a non-core feature.
- Defaults shown in the table are the fallback used when the variable is absent.

---

## Backend (FastAPI — `backend/`)

Variables are loaded by `pydantic-settings` from a `.env` file in the `backend/` directory
(or from the system environment, which takes precedence).

| Variable | Required | Default | Description | How to obtain |
|----------|----------|---------|-------------|---------------|
| `SUPABASE_URL` | Required | `""` | Full URL of the Supabase project (e.g. `https://xxxx.supabase.co`) | Supabase Dashboard → Settings → API → Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Required | `""` | Service role JWT — bypasses RLS. Keep secret. | Supabase Dashboard → Settings → API → `service_role` key |
| `JWT_SECRET` | Required (prod) | `"your-strong-secret-key"` | HMAC secret used to sign Spinr-issued JWTs. The default is insecure; always override in production. In `render.yaml` this is auto-generated. | Generate with `openssl rand -hex 32` |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Optional | `null` | Full JSON content of a Firebase service account key file, serialised as a single-line string. Required for FCM push notifications and Firebase Auth verification. | Firebase Console → Project Settings → Service Accounts → Generate new private key |
| `ENV` | Optional | `"development"` | Runtime environment. Set to `"production"` in production; enables stricter behaviour (e.g. JWT warning on missing secret). | Hard-code per deployment |
| `DEBUG` | Optional | `false` | Enables FastAPI debug mode. Never set `true` in production. | Hard-code per deployment |
| `APP_NAME` | Optional | `"Spinr API"` | Display name returned by the API info endpoint. | Hard-code |
| `APP_VERSION` | Optional | `"1.0.0"` | API version string. | Hard-code or set in CI |
| `ALGORITHM` | Optional | `"HS256"` | JWT signing algorithm. | Hard-code; only change if rotating algorithms. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Optional | `30` | JWT expiry in minutes for short-lived tokens. Long-lived driver/rider tokens use 30 days (set in `dependencies.py`). | Hard-code |
| `ALLOWED_ORIGINS` | Optional | `"*"` | Comma-separated list of CORS origins. Use specific origins in production (e.g. `https://admin.spinr.ca`). | Hard-code per deployment |
| `ADMIN_EMAIL` | Optional | `"admin@spinr.ca"` | Email used for the built-in admin account login endpoint. Override in production. | Set to a real internal email |
| `ADMIN_PASSWORD` | Optional | `"admin123"` | Password for the built-in admin account. **Change this immediately in production.** | Generate a strong password |
| `RATE_LIMIT` | Optional | `"10/minute"` | Global API rate limit applied via `slowapi`. Format: `"<count>/<period>"`. | Hard-code per deployment |
| `STORAGE_BUCKET` | Optional | `"driver-documents"` | Supabase Storage bucket name used for driver document uploads. | Match the bucket name created in Supabase Storage |
| `USE_SUPABASE` | Optional | `true` | Feature flag — when `true`, the app uses Supabase as the primary database. Disabling routes queries to a legacy path. | Hard-code `true` for all new deployments |

### Backend — Secrets stored in `app_settings` (Supabase table, not env vars)

The following values are stored in the `app_settings` table in Supabase and managed via the
admin dashboard (Settings page). They are **not** set as environment variables.

| Setting key | Description | How to obtain |
|-------------|-------------|---------------|
| `stripe_secret_key` | Stripe secret API key (server-side). | Stripe Dashboard → Developers → API keys → Secret key |
| `stripe_webhook_secret` | Webhook signing secret for verifying Stripe event payloads. Starts with `whsec_`. | Stripe Dashboard → Webhooks → select endpoint → Signing secret |
| `twilio_account_sid` | Twilio account SID for SMS delivery. Starts with `AC`. | Twilio Console → Account Info |
| `twilio_auth_token` | Twilio auth token. | Twilio Console → Account Info |
| `twilio_from_number` | Twilio phone number to send OTP SMS from (E.164 format, e.g. `+15550001234`). | Twilio Console → Phone Numbers → Active Numbers |
| `google_maps_api_key` | Google Maps API key used by backend for geocoding / distance calculations. | Google Cloud Console → APIs & Services → Credentials |

### Backend — CI-only secrets (GitHub Actions)

These are only used in the CI/CD pipeline and are stored as GitHub repository secrets.

| Secret | Description |
|--------|-------------|
| `SUPABASE_URL` | Same as above; injected into test runs. |
| `SUPABASE_SERVICE_ROLE_KEY` | Same as above; injected into test runs. |
| `RENDER_API_KEY` | Render API key for triggering deployments. Render Dashboard → Account → API Keys |
| `RENDER_BACKEND_SERVICE_ID` | Render service ID for `spinr-backend`. Render Dashboard → service URL contains the ID. |
| `FLY_API_TOKEN` | Fly.io personal access token for alternative deploys. `fly tokens create deploy` |
| `SLACK_WEBHOOK` | Incoming webhook URL for CI failure notifications. Slack → App settings → Incoming Webhooks |

---

## Rider App (Expo — `rider-app/`)

Expo variables must be prefixed with `EXPO_PUBLIC_` to be accessible in client-side code.
Set them in `rider-app/.env` (local) or as EAS build secrets for production builds.

| Variable | Required | Default | Description | How to obtain |
|----------|----------|---------|-------------|---------------|
| `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY` | Required | none | Google Maps API key for in-app maps, search, and directions. Restrict this key to your app's bundle ID in Google Cloud Console. | Google Cloud Console → APIs & Services → Credentials → Create API key |
| `EXPO_PUBLIC_BACKEND_URL` | Optional (prod) | Auto-detected in dev | Base URL of the FastAPI backend (e.g. `https://spinr-api.fly.dev`). Must be set for production / EAS builds; Expo dev client auto-detects the local server. | Your deployment URL (Fly.io or Render) |
| `EXPO_PUBLIC_STRIPE_PUBLISHABLE_KEY` | Optional | none | Stripe publishable key for client-side payment sheet initialisation. Starts with `pk_`. Required if payments are enabled. | Stripe Dashboard → Developers → API keys → Publishable key |

### Rider App — EAS / CI secrets

| Secret | Description |
|--------|-------------|
| `EXPO_TOKEN` | EAS build service account token. Expo account → Access Tokens |
| `EXPO_PUBLIC_API_URL` | Production API URL injected by CI during `npm run build`. Same value as `EXPO_PUBLIC_BACKEND_URL`. |

---

## Driver App (Expo — `driver-app/`)

Identical structure to the rider app. Set in `driver-app/.env` (local) or as EAS build
secrets for production.

| Variable | Required | Default | Description | How to obtain |
|----------|----------|---------|-------------|---------------|
| `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY` | Required | none | Google Maps API key for navigation and map display. Restrict this key to the driver app bundle ID separately from the rider app key. | Google Cloud Console → APIs & Services → Credentials → Create API key |
| `EXPO_PUBLIC_BACKEND_URL` | Optional (prod) | Auto-detected in dev | Base URL of the FastAPI backend. Required for production / EAS builds. | Your deployment URL |
| `EXPO_PUBLIC_STRIPE_PUBLISHABLE_KEY` | Optional | none | Stripe publishable key — used if the driver app displays payout information via Stripe Connect. | Stripe Dashboard → Developers → API keys → Publishable key |

---

## Admin Dashboard (Next.js — `admin-dashboard/`)

The admin dashboard does not have a committed `.env.example`. Variables are set in
`.env.local` (local development) or as Vercel environment variables (production).

| Variable | Required | Default | Description | How to obtain |
|----------|----------|---------|-------------|---------------|
| `NEXT_PUBLIC_API_URL` | Required | none | Base URL of the FastAPI backend used for all admin API calls (e.g. `https://spinr-api.fly.dev`). | Your deployment URL |
| `NEXT_PUBLIC_SUPABASE_URL` | Optional | none | Supabase project URL — used if the dashboard reads from Supabase directly (realtime, auth). | Supabase Dashboard → Settings → API → Project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Optional | none | Supabase anon/public key for client-side Supabase queries. Safe to expose publicly; RLS protects data. | Supabase Dashboard → Settings → API → `anon` key |

### Admin Dashboard — CI/CD secrets (GitHub Actions → Vercel)

| Secret | Description |
|--------|-------------|
| `VERCEL_TOKEN` | Vercel personal access token for deployment. Vercel → Account Settings → Tokens |
| `VERCEL_ORG_ID` | Vercel team/org ID. Found in Vercel project settings or `vercel.json`. |
| `VERCEL_ADMIN_PROJECT_ID` | Vercel project ID for the admin dashboard. Vercel project settings → General. |

---

## Quick-Start Minimum for Local Development

To run all services locally with the minimum required configuration:

**`backend/.env`:**
```dotenv
SUPABASE_URL=https://<your-project-ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<service_role_key>
JWT_SECRET=local-dev-secret-change-in-prod
ENV=development
```

**`rider-app/.env`:**
```dotenv
EXPO_PUBLIC_GOOGLE_MAPS_API_KEY=<your_maps_key>
# EXPO_PUBLIC_BACKEND_URL is auto-detected by Expo in dev mode
```

**`driver-app/.env`:**
```dotenv
EXPO_PUBLIC_GOOGLE_MAPS_API_KEY=<your_maps_key>
# EXPO_PUBLIC_BACKEND_URL is auto-detected by Expo in dev mode
```

**`admin-dashboard/.env.local`:**
```dotenv
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Twilio, Stripe, Firebase, and Sentry are all optional for local development. The backend
falls back to console-logging OTPs when Twilio is not configured, and uses a hardcoded OTP
of `1234` for development convenience.
