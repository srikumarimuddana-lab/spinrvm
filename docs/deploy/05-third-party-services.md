# 05 — Third-Party Service Setup

**Goal:** stand up every external service Spinr depends on, in the
order that minimizes blocked-on-approval time.

**Time:** 3-4 hours active; up to 2 weeks wall-clock if A2P 10DLC
or Stripe business verification is slow.

**Pre-reqs:** corporate email on `@<domain>`; company legal entity
info on hand (EIN / CRA BN, incorporation date, business address);
bank account for Stripe payouts.

---

## Sequencing rationale

Start the **slow-approval** items first so the clock runs while you
work on the fast ones:

1. **Apple Developer + Google Play Console** → 1-2 days to verify.
   Start this Day 0, even before Supabase. See § Apple / Google below.
2. **Stripe business verification + A2P 10DLC (Twilio)** → 2-14 days.
   Start Day 0-1.
3. **Firebase + APNs + Google Maps** → same-day.
4. **Upstash Redis + Sentry + SendGrid + Cloudinary** → 15-minute
   provisions.

---

## Firebase

**What it does:** Phone Auth verification, FCM push (Android),
APNs gateway for iOS push, Cloud Messaging.

### Create the project

1. https://console.firebase.google.com → **Add project**.
2. Name: `spinr-production`. Do NOT enable Google Analytics for
   Firebase unless you need it — it adds a mandatory Analytics
   project.
3. Region: match your Supabase region if possible. For Canada,
   `northamerica-northeast1` (Montreal).

### Enable Phone Auth

1. **Authentication** → **Sign-in method** → **Phone** → Enable.
2. **Phone numbers for testing** (development): add a dummy
   number and verification code for QA without spending SMS credits.

### Register 4 apps (rider + driver × iOS + Android)

For each:

- **iOS apps:** Project Settings → **Add app** → iOS.
  - Bundle ID: `com.spinr.rider` / `com.spinr.driver`.
  - Download `GoogleService-Info.plist`. Save into
    `rider-app/` / `driver-app/`.
- **Android apps:** similarly.
  - Package name: `com.spinr.rider` / `com.spinr.driver`.
  - SHA-1 fingerprint: get from EAS credentials after first build,
    then come back and add (Firebase → Android app → Add fingerprint).
  - Download `google-services.json`.

### Upload APNs auth key (iOS push)

1. https://developer.apple.com/account → **Keys** → `+`.
2. Name: `Spinr APNs`. Check **Apple Push Notifications service**.
3. Download the `.p8` once (you cannot re-download). Note the Key ID
   and your Team ID.
4. Firebase → **Cloud Messaging** → **Apple app configuration** →
   **Upload** the `.p8`. Paste Key ID and Team ID.

### Generate the service-account JSON (backend)

1. Firebase → **Project Settings** → **Service accounts** tab.
2. **Generate new private key**. Download the JSON.
3. Flatten:
   ```bash
   cat firebase-adminsdk-*.json | tr -d '\n' > firebase-sa-oneline.txt
   ```
4. Copy to vault as `FIREBASE_SERVICE_ACCOUNT_JSON`.

### Done when…

- [ ] 4 Firebase apps registered (iOS × 2, Android × 2).
- [ ] Two `google-services.json` and two `GoogleService-Info.plist`
      files saved into the app directories.
- [ ] APNs auth key uploaded to Firebase Cloud Messaging.
- [ ] Service-account JSON (one-line) in vault.

---

## Stripe

**What it does:** card payments, subscription billing, webhooks.

### Create account + verify business

1. https://dashboard.stripe.com → sign up.
2. **Live Mode** is gated until Business Settings is complete:
   - Business structure (corporation / sole proprietor).
   - Country, address, tax ID.
   - Bank account for payouts.
   - Owner identity verification (government ID upload).
3. Approval: usually ≤ 1 hour, occasionally 1-2 days if manual
   review is triggered. Check back periodically.

### Grab API keys (Live Mode)

1. **Developers** → **API keys**. Toggle **View live API keys**.
2. Copy **Secret key** (`sk_live_…`) to vault.
3. Copy **Publishable key** (`pk_live_…`) to vault.

### Configure webhook endpoint

1. **Developers** → **Webhooks** → **Add endpoint**.
2. **Endpoint URL:** `https://api.yourdomain.app/api/webhooks/stripe`
3. **Events to send:**
   - `payment_intent.succeeded`
   - `payment_intent.payment_failed`
   - `charge.refunded`
   - `invoice.payment_succeeded`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
4. **Add endpoint.** Click into it → reveal **Signing secret** →
   copy to vault as `stripe_webhook_secret`.

You may need to come back and update the URL from the Fly fallback
(`spinr-backend.fly.dev`) to your custom domain — that's fine.
Stripe webhooks can be edited at any time.

### Done when…

- [ ] Live Mode unlocked.
- [ ] `sk_live_…`, `pk_live_…`, `whsec_…` in vault.
- [ ] Webhook endpoint configured and `Test` button returns 200
      (you can click Test after the backend is deployed).

---

## Twilio

**What it does:** SMS OTP delivery.

### Create account + buy numbers

1. https://www.twilio.com → sign up.
2. Verify your own phone number.
3. **Phone Numbers** → **Buy a number**. Filter for SMS-capable
   and your target country. For Saskatchewan, any +1 number works.

### Register A2P 10DLC (US numbers only, but applies to US-routed SMS)

If you plan to send SMS to US phone numbers:

1. **Messaging** → **Regulatory Compliance** → **A2P 10DLC**.
2. Register a **Brand** with your business info.
3. Register a **Campaign** with use case "Account Notifications /
   2FA".
4. Attach your Twilio number to the campaign.
5. Approval: 1-14 days. Without A2P, US carriers aggressively
   filter your OTPs — a launch blocker.

Canadian numbers do NOT require A2P. If you only operate in Canada,
skip this step.

### Grab credentials

1. **Account** → **API keys & tokens** → **Live Credentials**.
2. Copy **Account SID** (`AC…`) → vault as `twilio_account_sid`.
3. Copy **Auth Token** → vault as `twilio_auth_token`. **Rotate this
   right after first use** for better security — create an API Key
   pair (Account → API keys) and use that pair instead.

### Done when…

- [ ] One or more SMS-capable phone numbers purchased.
- [ ] A2P 10DLC approved (if US-bound) or documented as skipped
      (if Canada-only).
- [ ] SID + token + from number in vault.

---

## Redis (Upstash)

**What it does:** shared rate limit counters across Fly machines +
WebSocket pub/sub fan-out.

### Provision

1. https://console.upstash.com → **Create Database**.
2. Type: **Redis** (not Kafka).
3. Name: `spinr-prod-redis`.
4. Region: nearest to your Fly region. For `yyz` (Toronto) Fly,
   pick `us-east-1` (N. Virginia) — latency to Fly-yyz is ~15ms.
5. **Global** if you expect multi-region Fly in future; **Regional**
   is cheaper for single-region.
6. **TLS:** ON (default). **Eviction:** `noeviction`. Other settings
   default.

### Grab the URL

1. Open the database dashboard.
2. Copy the **Redis Connect string** that starts with
   `rediss://` (TLS). The backend validator rejects plain `redis://`
   in production.
3. Save as `RATE_LIMIT_REDIS_URL` in vault.

### Done when…

- [ ] Upstash Redis DB provisioned with TLS.
- [ ] `rediss://` URL in vault.

---

## Google Maps Platform

**What it does:** Geocoding, map tiles, routing for rider and driver
apps.

### Enable APIs

1. https://console.cloud.google.com → create project `spinr-maps`.
2. Billing: attach a billing account (required even for free tier).
3. **APIs & Services** → **Library** → enable:
   - Maps SDK for Android
   - Maps SDK for iOS
   - Places API
   - Directions API
   - Geocoding API

### Generate restricted API key

1. **Credentials** → **+ Create credentials** → **API key**.
2. On the key detail page:
   - **Application restrictions:** pick the right one for each
     platform. Since one key is used by both iOS and Android you
     have two realistic options:
     - Create **two keys**: iOS-restricted key, Android-restricted
       key. Use each per platform's native config.
     - Or **single key with "None"** restriction and lean on **API
       restrictions** — simpler but weaker. For launch it's
       acceptable; tighten post-launch.
   - **API restrictions:** restrict to the 5 APIs enabled above.
3. Save as `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY` in vault.

### Done when…

- [ ] 5 APIs enabled and billing set.
- [ ] Key restricted and in vault.

---

## Sentry

**What it does:** error monitoring, crash reporting.

1. https://sentry.io → sign up (Developer free tier OK for launch).
2. Create organization: `spinr`.
3. Create three projects:
   - `spinr-backend` (platform: Python → FastAPI)
   - `spinr-rider` (platform: React Native)
   - `spinr-driver` (platform: React Native)
4. Each project shows its **DSN** on creation. Copy all three to
   vault.

### Done when…

- [ ] 3 Sentry projects created.
- [ ] 3 DSNs in vault.

---

## SendGrid

**What it does:** receipt emails + system notifications.

1. https://sendgrid.com → sign up.
2. **Settings** → **Sender Authentication** → **Authenticate a
   Domain** (preferred over Single Sender Verification for
   deliverability). Follow the DNS TXT / CNAME instructions for
   your domain.
3. Wait for verification (usually minutes).
4. **Settings** → **API Keys** → **Create API Key** → scope: **Mail
   Send only**.
5. Save the key to vault as `sendgrid_api_key`. You can't see it again.

### Done when…

- [ ] Domain authenticated (DKIM + SPF records live).
- [ ] API key in vault with Mail Send scope.

---

## Cloudinary

**What it does:** profile avatar upload + image transforms.

1. https://cloudinary.com → sign up (Free tier OK for launch).
2. **Dashboard** card shows:
   - **Cloud name**
   - **API Key**
   - **API Secret** (click Reveal)
3. Save all three to vault.

### Done when…

- [ ] 3 Cloudinary values in vault.

---

## Apple Developer Program

**What it does:** iOS distribution, APNs, TestFlight.

1. https://developer.apple.com/programs → **Enroll** ($99/yr).
2. **Identity verification** — for an organization, you need your
   DUNS number (free via Dun & Bradstreet, allow 2-3 days if you
   don't have one).
3. Once active, enable:
   - **Apple Push Notifications service** (automatic).
   - **App IDs:** create `com.spinr.rider` and `com.spinr.driver`
     under **Certificates, IDs & Profiles** → **Identifiers**.
4. Record your Team ID (top right of developer portal).

Without Apple Developer Program, you cannot submit iOS builds.
Start this on Day 0.

---

## Google Play Console

**What it does:** Android distribution.

1. https://play.google.com/console → sign up ($25 one-time).
2. Identity verification: requires a government ID photo. ~1 day.
3. Once active:
   - Create two apps: `Spinr Rider` and `Spinr Driver`.
   - Packages: `com.spinr.rider`, `com.spinr.driver`.
   - Fill out the mandatory **Data Safety**, **Content Rating**,
     **Target Audience**, and **Store Listing** sections.
4. **Setup** → **API access** → Create service account in Google
   Cloud; grant **Release Manager** permission. Download the JSON
   → save as `play-service-account.json` for `eas submit`.

---

## Cost sanity check

After all services are provisioned, run a quick audit:

| Service | Monthly est. at launch |
|---|---|
| Supabase Pro | $25 |
| Fly.io (2 machines @ 1GB) | $30 |
| Upstash Redis | $10 |
| Twilio (number + ~500 SMS) | $10 + usage |
| Firebase (FCM + Auth) | $0 |
| Google Maps | $0-100 (covered by $200 credit initially) |
| Sentry Developer | $0 |
| SendGrid | $0 (free tier) |
| Cloudinary | $0 (free tier) |
| Stripe | fee-based only |
| Vercel Hobby | $0 |
| Apple / Google stores | $99/yr + $25 one-time |

**Estimated month 1 cash:** ~$100, plus Stripe fees per transaction.
Usage-based services (Twilio, Google Maps) scale linearly with DAU.

---

## Done when…

- [ ] Every service in [`SECRETS_INVENTORY.md`](./SECRETS_INVENTORY.md)
      has a real value in your vault.
- [ ] Approval-gated services (Stripe Live Mode, Twilio A2P,
      Apple + Google store accounts) are confirmed approved —
      not "submitted and waiting".
- [ ] Downloaded config files (`google-services.json`,
      `GoogleService-Info.plist`, `play-service-account.json`)
      are placed into the correct repo directories.

You're now ready to return to [`02-backend-fly.md`](./02-backend-fly.md)
and deploy, then [`03-admin-vercel.md`](./03-admin-vercel.md), then
[`04-mobile-eas.md`](./04-mobile-eas.md). Finish with the
[`CHECKLIST.md`](./CHECKLIST.md) sign-off.
