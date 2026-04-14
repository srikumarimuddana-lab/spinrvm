# Secrets Inventory — Pre-Flight

Generate or obtain every value below **before** starting the provider
setup guides. Store each one in your password manager tagged with its
`Name` from the first column. Never paste real values into this repo,
into chat, or into a text file on your laptop.

Each secret lists:
- **Source** — where you generate / obtain it.
- **Format** — what the value should structurally look like. The
  production validator checks these structural markers in
  `backend/core/middleware.py`.
- **Consumed by** — which component reads it.
- **Rotation** — which playbook rotates it post-launch.

---

## 1. Pre-generated (do these locally first, no provider needed)

### `JWT_SECRET`

- **Source:** generate with
  ```bash
  python3 -c "import secrets; print(secrets.token_urlsafe(64))"
  ```
- **Format:** 64+ URL-safe characters. NOT one of the known
  defaults (`your-strong-secret-key`,
  `spinr-dev-secret-key-NOT-FOR-PRODUCTION`,
  `replace-with-strong-random-secret`).
- **Consumed by:** backend — signs every access token.
- **Rotation:** [`SECRETS_ROTATION.md § JWT_SECRET`](../ops/SECRETS_ROTATION.md#jwt_secret)

### `ADMIN_PASSWORD`

- **Source:** your password manager's generator. Use 20+ chars,
  mixed case + digits + symbols.
- **Format:** ≥ 12 chars, NOT `admin123` / `changeme` / `password` /
  `replace-me`.
- **Consumed by:** backend — bootstrap super-admin login.
- **Rotation:** [`SECRETS_ROTATION.md § ADMIN_PASSWORD`](../ops/SECRETS_ROTATION.md#admin_password)

### `ADMIN_EMAIL`

- **Source:** a real monitored team inbox — e.g. `admin@spinr.app`.
  NOT `admin@example.com` or `admin@spinr.ca` (those are rejected
  defaults).
- **Format:** valid email.
- **Consumed by:** backend — super-admin identity.

---

## 2. Supabase (create project first per [`01-supabase.md`](./01-supabase.md))

### `SUPABASE_URL`

- **Source:** Supabase dashboard → Project Settings → API → Project URL.
- **Format:** `https://<project-ref>.supabase.co`. Validator rejects
  anything containing `your-project-ref`, `your-project`, or
  `example.supabase.co`.
- **Consumed by:** backend + mobile apps (as `EXPO_PUBLIC_SUPABASE_URL`
  if used).

### `SUPABASE_SERVICE_ROLE_KEY`

- **Source:** Supabase dashboard → Project Settings → API →
  service_role key. Click "Reveal".
- **Format:** a ~220-char JWT. MUST start with `eyJ`. Validator
  rejects anything that doesn't start with `eyJ` or is < 40 chars.
- **Consumed by:** backend only. Bypasses RLS — NEVER ship to clients.
- **Rotation:** [`SECRETS_ROTATION.md § SUPABASE_SERVICE_ROLE_KEY`](../ops/SECRETS_ROTATION.md#supabase_service_role_key--highest-priority)

### `SUPABASE_ANON_KEY` *(optional — only if using Supabase client-side auth)*

- **Source:** same dashboard, `anon` key.
- **Format:** JWT starting with `eyJ`, shorter than the service-role key.
- **Consumed by:** admin dashboard if you wire Supabase auth there;
  not used by the default stack.

### `DATABASE_URL` *(for migrations only)*

- **Source:** Supabase dashboard → Project Settings → Database →
  Connection string → URI (Transaction mode, port 6543 for pooled).
- **Format:** `postgres://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres`
- **Consumed by:** `backend/scripts/run_migrations.sh` only — not by
  the running backend.

---

## 3. Redis (Upstash or Fly Redis)

### `RATE_LIMIT_REDIS_URL`

- **Source:** Upstash → Create Database → copy "Redis URL" (TLS).
- **Format:** MUST start with `redis://` or `rediss://`. Validator
  rejects other schemes. Production must use `rediss://` (TLS).
- **Consumed by:** backend — SlowAPI rate limiter + WS pub/sub fallback.
- **Rotation:** [`SECRETS_ROTATION.md § Redis URLs`](../ops/SECRETS_ROTATION.md#redis-urls-rate_limit_redis_url-ws_redis_url)

### `WS_REDIS_URL` *(optional — only if you want WS on a separate Redis)*

- **Source:** second Upstash DB.
- **Format:** same as above.
- **Consumed by:** backend WS fan-out (audit P0-B3). If empty, falls
  back to `RATE_LIMIT_REDIS_URL`.

---

## 4. Firebase

### `FIREBASE_SERVICE_ACCOUNT_JSON`

- **Source:** Firebase console → Project Settings → Service accounts →
  Generate new private key. Download the JSON, then flatten:
  ```bash
  cat spinr-firebase-adminsdk-xxxxx.json | tr -d '\n'
  ```
- **Format:** one-line JSON; the `type` field MUST equal
  `service_account`; `project_id` MUST match your Firebase project ref.
- **Consumed by:** backend — verifies Firebase ID tokens + sends FCM.
- **Rotation:** [`SECRETS_ROTATION.md § FIREBASE_SERVICE_ACCOUNT_JSON`](../ops/SECRETS_ROTATION.md#firebase_service_account_json)

### `google-services.json` (Android mobile)

- **Source:** Firebase console → Project Settings → Android apps →
  Download. One per Android build (rider + driver).
- **Format:** binary JSON file committed to each mobile app directory.
- **Consumed by:** Expo build; native FCM delivery.

### `GoogleService-Info.plist` (iOS mobile)

- **Source:** Firebase console → Project Settings → iOS apps → Download.
- **Format:** Apple plist. One per iOS build.
- **Consumed by:** Expo build; native APNs delivery.

### APNs auth key

- **Source:** Apple Developer → Keys → `+` → enable Apple Push
  Notifications service → download the `.p8` file + note the Key ID
  and Team ID.
- **Format:** `AuthKey_<KeyID>.p8` (plaintext PEM).
- **Consumed by:** Firebase console → Cloud Messaging → Apple app
  config → upload the `.p8` once. Apple delivers pushes via APNs but
  Firebase is the one-way tower.

---

## 5. Stripe (Live Mode)

> **Stripe Live Mode is gated behind a business verification form.
> Fill it out early — approval is usually 1-2 hours but can take days.**

### `stripe_secret_key`

- **Source:** Stripe dashboard (Live Mode) → Developers → API keys
  → Secret key. Click "Reveal".
- **Format:** `sk_live_…` (never `sk_test_…` in production).
- **Consumed by:** backend — stored in Supabase `settings` table
  (NOT in env vars). Admin dashboard Settings page edits this.

### `stripe_publishable_key`

- **Source:** same page, publishable key.
- **Format:** `pk_live_…`.
- **Consumed by:** rider + driver apps at build-time.

### `stripe_webhook_secret`

- **Source:** Stripe dashboard → Developers → Webhooks →
  Add endpoint → copy "Signing secret".
- **Format:** `whsec_…`.
- **Consumed by:** backend `routes/webhooks.py` for signature
  verification. Stored in Supabase `settings` table.
- **Rotation:** [`SECRETS_ROTATION.md § Stripe`](../ops/SECRETS_ROTATION.md#stripe--twilio-supabase-settings-row)

---

## 6. Twilio

### `twilio_account_sid`

- **Source:** Twilio console → Account → API keys & tokens →
  Live credentials.
- **Format:** `AC…` (34 chars).
- **Consumed by:** backend — stored in Supabase `settings`.

### `twilio_auth_token`

- **Source:** same page. **Rotate this immediately after first use** —
  the primary token is high-blast-radius. Use an API key pair for
  production traffic.
- **Format:** 32-char hex.
- **Consumed by:** backend — stored in Supabase `settings`.

### `twilio_from_number`

- **Source:** Twilio console → Phone Numbers → Manage → Active numbers.
  Buy a local number in each country you operate in (+1-306 for
  Saskatchewan, etc.). SMS-capable.
- **Format:** E.164 (`+1306…`).
- **Consumed by:** backend — sends OTP from this number.

---

## 7. Google Maps Platform

### `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY`

- **Source:** Google Cloud Console → APIs & Services → Credentials →
  Create credentials → API key.
- **Format:** `AIzaSy…` (39 chars).
- **Consumed by:** rider + driver apps. EXPO_PUBLIC is embedded in
  the client bundle, so the key MUST be restricted:
  - Android: package name `com.spinr.rider` and `com.spinr.driver`
    with SHA-1 fingerprints from EAS.
  - iOS: bundle IDs `com.spinr.rider` and `com.spinr.driver`.
  - APIs: Maps SDK for Android, Maps SDK for iOS, Places API,
    Directions API, Geocoding API only.
- **Rotation:** rotate in Google Cloud Console → run
  `scripts/setup-eas-secrets.sh` → rebuild both apps.

---

## 8. Sentry

### `SENTRY_DSN` (backend)

- **Source:** Sentry → Settings → Projects → create `spinr-backend`
  → Client Keys (DSN).
- **Format:** `https://<hash>@o<org>.ingest.sentry.io/<project>`.
- **Consumed by:** backend `server.py`.

### `SENTRY_DSN` per mobile app (optional)

- **Source:** same, create `spinr-rider` + `spinr-driver` projects.
- **Consumed by:** rider + driver apps for crash reporting.

---

## 9. SendGrid

### `sendgrid_api_key`

- **Source:** SendGrid dashboard → Settings → API Keys → Create.
  Scope: **Mail Send only** (full access is unnecessary).
- **Format:** `SG.<hash>`.
- **Consumed by:** backend — stored in Supabase `settings`. Sends
  receipts.

### Verified sender

- **Source:** SendGrid → Settings → Sender Authentication → Verify a
  Single Sender (fast) or Authenticate a Domain (better deliverability,
  requires DNS).
- **Consumed by:** must match the "from" address in
  `utils/email_receipt.py`.

---

## 10. Cloudinary

### `cloudinary_cloud_name`, `cloudinary_api_key`, `cloudinary_api_secret`

- **Source:** Cloudinary dashboard → Dashboard card (shows cloud name
  + API key + API secret).
- **Format:** cloud_name is alphanumeric; api_key is numeric; api_secret
  is a 27-char base64 string.
- **Consumed by:** backend `utils/cloudinary.py`. Stored in Supabase
  `settings`.

---

## 11. Fly.io (deployment only)

### `FLY_API_TOKEN` (GitHub Actions only)

- **Source:** `flyctl auth token` (locally after `fly auth login`).
  Alternatively, create a deploy token scoped to the `spinr-backend`
  app: `flyctl tokens create deploy --app spinr-backend`.
- **Format:** `FlyV1 fm2_…`.
- **Consumed by:** `.github/workflows/deploy-backend.yml` (re-add per
  [`02-backend-fly.md`](./02-backend-fly.md) — this workflow was
  deleted during last refactor).

---

## 12. Vercel (deployment only)

### `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`

- **Source:** Vercel dashboard → Settings → Tokens → Create; Org ID
  and Project ID from the project URL.
- **Consumed by:** optional — only if you wire a GitHub Actions
  deploy for admin. Default Vercel git-push integration doesn't need
  these.

---

## 13. Apple + Google (mobile signing)

### `APPLE_ID`, `ASC_APP_ID`, `APPLE_TEAM_ID`

- **Source:** `eas credentials` will fetch them interactively after
  `eas login`. Apple Team ID is visible at
  https://developer.apple.com/account → Membership.
- **Consumed by:** EAS Build for iOS signing.

### Google Play service account JSON

- **Source:** Google Play Console → Setup → API access → Create
  service account in Google Cloud → grant release manager permission
  → download JSON.
- **Consumed by:** `eas submit` for Android store upload.

---

## Putting it all together

By the end of pre-flight, your vault should contain:

1. `JWT_SECRET`
2. `ADMIN_EMAIL`, `ADMIN_PASSWORD`
3. `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `DATABASE_URL`
4. `RATE_LIMIT_REDIS_URL`, optionally `WS_REDIS_URL`
5. `FIREBASE_SERVICE_ACCOUNT_JSON` (one-line), APNs `.p8` + Key ID +
   Team ID, `google-services.json` × 2, `GoogleService-Info.plist` × 2
6. `stripe_secret_key`, `stripe_publishable_key`, `stripe_webhook_secret`
7. `twilio_account_sid`, `twilio_auth_token`, `twilio_from_number`
8. `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY` (restricted)
9. `SENTRY_DSN` × up to 3
10. `sendgrid_api_key`
11. `cloudinary_cloud_name`, `cloudinary_api_key`, `cloudinary_api_secret`
12. `FLY_API_TOKEN`
13. Apple + Google mobile-signing credentials

That's **~25 distinct values** for a first launch. Plan for ~2 hours
of setup wall time to register accounts, verify domain ownership,
wait for Stripe approval, and get all values into your vault. After
that, each follow-up deploy guide becomes a 30-minute exercise.
