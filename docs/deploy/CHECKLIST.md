# Launch Checklist — Spinr Production

**Print this page.** Walk through every item top to bottom. No item may
be skipped. Items referencing a guide (e.g. `see 02-backend-fly.md`)
must be fully complete before ticking the box.

**Criterion for launch:** every unchecked box below is checked, in
order, and the final "smoke tests" section is green. If any item
surfaces an unexpected error, stop and diagnose — do not work around.

---

## Phase A — Pre-flight (do before touching any provider)

- [ ] **A.1** Company email domain exists and the operator has access
      to create provider accounts (e.g. `@spinr.app`).
- [ ] **A.2** Password manager or secrets vault is provisioned and
      ready to store ~25 values.
- [ ] **A.3** Two domain names are available (`api.<domain>` and
      `admin.<domain>`) and DNS is editable.
- [ ] **A.4** Apple Developer Program membership is active ($99/yr).
- [ ] **A.5** Google Play Console account is paid ($25 one-time).
- [ ] **A.6** Legal copy is final: Terms of Service + Privacy Policy.
      Drafts are signed off by a lawyer or legal reviewer.
- [ ] **A.7** You've read [`README.md`](./README.md) and understand
      the deploy sequence.
- [ ] **A.8** You've generated `JWT_SECRET`, `ADMIN_PASSWORD`, and
      `ADMIN_EMAIL` per [`SECRETS_INVENTORY.md § 1`](./SECRETS_INVENTORY.md).

---

## Phase B — Third-party services

Work through [`05-third-party-services.md`](./05-third-party-services.md)
section by section, then return and check off:

### Supabase ([`01-supabase.md`](./01-supabase.md))

- [ ] **B.1** Supabase Pro project created in a Canadian region
      (default `ca-central-1` / AWS Montreal).
- [ ] **B.2** `uuid-ossp` extension enabled.
- [ ] **B.3** `backend/supabase_schema.sql` applied (core tables).
- [ ] **B.4** `backend/sql/02_add_updated_at.sql` applied.
- [ ] **B.5** `backend/sql/03_features.sql` applied.
- [ ] **B.6** `backend/sql/04_rides_admin_overhaul.sql` applied.
- [ ] **B.7** `backend/migrations/*.sql` all applied via
      `backend/scripts/run_migrations.sh` (26 migrations as of 2026-04).
- [ ] **B.8** `backend/supabase_rls.sql` applied; every table in
      `public` schema has RLS enabled.
- [ ] **B.9** `settings` row exists with non-empty
      `terms_of_service_text` + `privacy_policy_text`.
- [ ] **B.10** Database PITR (point-in-time recovery) is enabled in
      Supabase → Database → Backups.
- [ ] **B.11** `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` stored in
      vault.

### Firebase ([`05-third-party-services.md § Firebase`](./05-third-party-services.md#firebase))

- [ ] **B.12** Firebase project created.
- [ ] **B.13** Phone Auth provider enabled; test phone numbers added
      for QA.
- [ ] **B.14** `google-services.json` downloaded for both rider and
      driver Android apps; placed in `rider-app/` and `driver-app/`.
- [ ] **B.15** `GoogleService-Info.plist` downloaded for both iOS
      apps; placed in respective directories.
- [ ] **B.16** APNs Auth Key (.p8) uploaded to Firebase → Cloud
      Messaging → Apple app config.
- [ ] **B.17** Service-account JSON generated, flattened, and stored
      as `FIREBASE_SERVICE_ACCOUNT_JSON` in vault.

### Stripe ([`05-third-party-services.md § Stripe`](./05-third-party-services.md#stripe))

- [ ] **B.18** Stripe account verified for **Live Mode**.
- [ ] **B.19** Business info, bank account, and tax forms submitted.
- [ ] **B.20** Live-mode API keys (`sk_live_…`, `pk_live_…`) stored
      in vault.
- [ ] **B.21** Webhook endpoint added at
      `https://api.<domain>/api/webhooks/stripe` with events
      `payment_intent.succeeded`, `payment_intent.payment_failed`,
      `charge.refunded`, `invoice.payment_succeeded`,
      `customer.subscription.updated`,
      `customer.subscription.deleted`.
- [ ] **B.22** `whsec_…` signing secret stored in vault.

### Twilio

- [ ] **B.23** Twilio project created.
- [ ] **B.24** SMS-capable phone number purchased for every country
      you operate in.
- [ ] **B.25** A2P 10DLC registration submitted for US numbers (can
      take up to 2 weeks — start early).
- [ ] **B.26** `twilio_account_sid`, `twilio_auth_token`, and
      `twilio_from_number` in vault.

### Redis ([`05-third-party-services.md § Redis`](./05-third-party-services.md#redis))

- [ ] **B.27** Upstash Global DB provisioned (nearest region:
      `iad-1` or `sea-1`).
- [ ] **B.28** TLS `rediss://` URL stored as `RATE_LIMIT_REDIS_URL`
      in vault.

### Google Maps

- [ ] **B.29** Google Cloud project created; billing enabled.
- [ ] **B.30** Maps SDK for Android, Maps SDK for iOS, Places API,
      Directions API, Geocoding API enabled.
- [ ] **B.31** API key generated and restricted to:
      iOS bundle IDs `com.spinr.rider`, `com.spinr.driver`;
      Android package names `com.spinr.rider`, `com.spinr.driver`;
      APIs listed above.
- [ ] **B.32** Key stored as `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY` in
      vault.

### Sentry + SendGrid + Cloudinary

- [ ] **B.33** Sentry backend project created; `SENTRY_DSN` in vault.
- [ ] **B.34** SendGrid API key (Mail Send scope) + verified sender
      in vault.
- [ ] **B.35** Cloudinary credentials in vault.

---

## Phase C — Backend deploy ([`02-backend-fly.md`](./02-backend-fly.md))

- [ ] **C.1** `fly auth login` and `fly apps create spinr-backend`
      succeeded.
- [ ] **C.2** `fly.toml` `primary_region` matches your customer base
      (default `yyz` / Toronto for Canada).
- [ ] **C.3** All backend secrets set via `fly secrets set`:
      `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `JWT_SECRET`,
      `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `FIREBASE_SERVICE_ACCOUNT_JSON`,
      `RATE_LIMIT_REDIS_URL`, `ALLOWED_ORIGINS`, `SENTRY_DSN`,
      `ENV=production`.
- [ ] **C.4** `fly secrets list --app spinr-backend` shows every
      required secret.
- [ ] **C.5** `fly deploy` completed successfully; at least one
      machine shows `started` in `fly status`.
- [ ] **C.6** Startup logs show:
      "Supabase connection verified",
      "Middleware initialized",
      "WS pub/sub started",
      "Spinr API startup complete".
- [ ] **C.7** `curl -sSf https://spinr-backend.fly.dev/healthz` returns
      `200`.
- [ ] **C.8** Custom domain `api.<domain>` mapped:
      `fly certs create api.<domain>` + DNS CNAME/AAAA per Fly
      instructions; `fly certs show` reports `Issued`.
- [ ] **C.9** Stripe webhook endpoint updated to `https://api.<domain>/api/webhooks/stripe`
      and webhook secret re-set in Supabase `settings` table.

---

## Phase D — Admin dashboard ([`03-admin-vercel.md`](./03-admin-vercel.md))

- [ ] **D.1** Vercel project created from
      `ittalenthireca-sketch/spinr` with root directory
      `admin-dashboard/`.
- [ ] **D.2** `NEXT_PUBLIC_API_URL=https://api.<domain>` set in
      Vercel environment variables for Production.
- [ ] **D.3** First build succeeded.
- [ ] **D.4** Custom domain `admin.<domain>` mapped in Vercel.
- [ ] **D.5** Login page at `https://admin.<domain>/login` accepts
      the bootstrap `ADMIN_EMAIL` + `ADMIN_PASSWORD` and lands on
      `/dashboard`.
- [ ] **D.6** In admin → Settings, Stripe + Twilio + SendGrid +
      Cloudinary keys are saved. Verify by clicking a relevant test
      action (e.g. "Send test SMS" if exposed).

---

## Phase E — Mobile apps ([`04-mobile-eas.md`](./04-mobile-eas.md))

- [ ] **E.1** `eas login` and `eas init` in both `rider-app/` and
      `driver-app/`.
- [ ] **E.2** `scripts/setup-eas-secrets.sh` registered
      `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY` for both apps.
- [ ] **E.3** `EXPO_PUBLIC_BACKEND_URL` set to `https://api.<domain>`
      in each `.env` AND registered as EAS Secret for production
      builds.
- [ ] **E.4** `eas build --profile production --platform all` for
      both rider and driver. Both builds successful.
- [ ] **E.5** iOS builds submitted to TestFlight via
      `eas submit --platform ios`.
- [ ] **E.6** Android builds uploaded to Play Console internal track
      via `eas submit --platform android`.
- [ ] **E.7** Both apps installed on a test device pointing at
      production backend.
- [ ] **E.8** Phone auth (OTP) login works on Android and iOS.
- [ ] **E.9** Rider can request a ride; driver receives it; full
      lifecycle (accept → arrive → OTP → start → complete → payment)
      runs end to end.

---

## Phase F — DNS + TLS

- [ ] **F.1** `api.<domain>` DNS record propagated; TLS cert issued
      by Fly; `curl https://api.<domain>/healthz` returns 200.
- [ ] **F.2** `admin.<domain>` DNS record propagated; Vercel cert
      auto-issued.
- [ ] **F.3** HSTS header appears in responses from both domains
      (`curl -I` check).
- [ ] **F.4** Every mobile app + admin dashboard build points at
      `api.<domain>`, NOT `spinr-backend.fly.dev` (the Fly domain is
      for internal fallback only).

---

## Phase G — Operational readiness

- [ ] **G.1** `fly logs` streams to at least one operator terminal
      during first-hour launch monitoring.
- [ ] **G.2** Sentry backend project receives a test exception:
      trigger a 5xx deliberately and verify it appears in the
      Sentry UI. Then remove the triggering code.
- [ ] **G.3** Stripe webhook test event fires (Stripe dashboard →
      Webhooks → Send test event → `payment_intent.succeeded`);
      backend logs show event received; `stripe_events` table has
      a row with `processed=true`.
- [ ] **G.4** Twilio SMS delivered to a real phone via
      `/auth/send-otp`.
- [ ] **G.5** FCM push delivered to a driver app in background on a
      physical Android device.
- [ ] **G.6** APNs push delivered to a driver app in background on a
      physical iOS device.
- [ ] **G.7** Backup drill: Supabase PITR restore test to a
      scratch project. Document RTO in `docs/ops/INCIDENT_RESPONSE.md`.

---

## Phase H — Compliance + business

- [ ] **H.1** `settings.terms_of_service_text` and
      `privacy_policy_text` contain real legal content (not
      placeholders).
- [ ] **H.2** Privacy Officer contact email is listed in the
      privacy policy.
- [ ] **H.3** Commercial auto / TNC insurance policy is active and
      certificate is on file.
- [ ] **H.4** Provincial / municipal ride-share license filings
      complete (for Canadian TNCs: SGI license in Saskatchewan,
      etc.).
- [ ] **H.5** Driver background-check provider contract signed;
      first check flows integrated OR documented manual process.
- [ ] **H.6** Support inbox (`support@<domain>`) is monitored; first
      3 training tickets resolved end-to-end.

---

## Phase I — Smoke tests (run immediately after launch)

Do NOT declare launch until every test below passes.

- [ ] **I.1** Rider sign-up → OTP → profile → request ride → driver
      accepts → arrive → start → complete → receipt email received.
- [ ] **I.2** Driver earnings export CSV downloads from tax documents
      screen.
- [ ] **I.3** Admin dashboard dispute resolution flow: rider files
      dispute → admin sees it → admin resolves → rider/driver both
      get notified.
- [ ] **I.4** Stripe payment intent succeeds for a $1 test charge
      against a real card (use Apple Pay test card if available).
- [ ] **I.5** Rate limit kicks in: hammer `/auth/send-otp` > 5×/min
      from one IP, expect `429`.
- [ ] **I.6** RLS sanity: using a rider's JWT, `GET /api/v1/rides`
      returns only that rider's rides (never another rider's).

---

## Go / No-go decision

**GO** if every box A through I is checked AND there are no open
errors in Sentry > 1 minute old.

**NO-GO** if any box is unchecked. Fix the blocking item, then
re-run the affected smoke tests.

After GO, hold launch monitoring for the first 2 hours:

- Watch `fly logs` for 500s, 401s, and WS disconnect storms.
- Watch Sentry for error budget burn.
- Watch Stripe dashboard for failed payment intents.
- Keep the first-deployment runbook close — the first 24 hours are
  the highest-risk window.

---

## Sign-off

| Role | Name | Timestamp | Initials |
|---|---|---|---|
| Deploy operator | | | |
| Engineering lead | | | |
| Security reviewer | | | |
| Legal / privacy | | | |
| Business / GM | | | |
