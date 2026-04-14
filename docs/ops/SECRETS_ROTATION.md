# Secrets Rotation Playbook

This document is the operational procedure for rotating every
production secret Spinr uses. Each section is structured as:

* **What it is** — a 1-line definition.
* **Blast radius on leak** — what an attacker can do with a stolen copy.
* **Rotation procedure** — exact sequence of commands, ordered to
  avoid downtime.
* **After rotation** — how to verify the rotation landed and nothing
  broke.

Every rotation procedure is designed to be zero-downtime:

1. Add the new secret to the environment.
2. Deploy.
3. Revoke the old secret.

If that ordering isn't possible for a particular secret, it's called
out explicitly. Treat every one of these as a change that goes through
the normal deploy pipeline — do not edit secrets by hand on a running
machine.

---

## Inventory

| Secret | Where it lives | Who uses it |
| --- | --- | --- |
| `SUPABASE_SERVICE_ROLE_KEY` | Fly secret (backend) | Backend → all DB writes |
| `SUPABASE_URL` | Fly secret (backend) + client `.env` (public) | Backend + mobile apps |
| `JWT_SECRET` | Fly secret (backend) | Signs every access token (rider / driver / admin) |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Fly secret (backend) | Bootstraps first admin login |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Fly secret (backend) | Firebase auth verify + FCM push |
| `RATE_LIMIT_REDIS_URL` | Fly secret (backend) | SlowAPI + WS pub/sub |
| `WS_REDIS_URL` | Fly secret (backend; optional) | WebSocket cross-machine fan-out |
| `SENTRY_DSN` | Fly secret (backend; optional) | Error monitoring |
| Stripe keys (`stripe_secret_key`, `stripe_webhook_secret`) | Supabase `settings` table | Backend payments |
| Twilio credentials (`twilio_account_sid`, `twilio_auth_token`, `twilio_from_number`) | Supabase `settings` table | SMS OTP delivery |
| Google Maps API key (`EXPO_PUBLIC_GOOGLE_MAPS_API_KEY`) | Mobile `.env` | Rider + driver map tiles |

Fly secrets are set via:

```bash
fly secrets set KEY=value --app spinr-backend
```

Supabase `settings` row secrets are edited from the **admin dashboard →
Settings** page. They do not require a backend redeploy.

---

## `SUPABASE_SERVICE_ROLE_KEY` — highest priority

* **What it is** — a ~220-char JWT starting with `eyJ`, minted by
  Supabase. Bypasses Row Level Security; equivalent to full DB access.
* **Blast radius on leak** — complete read/write of every row in
  every table. An attacker can read rider PII, issue ride refunds,
  impersonate drivers, promote themselves to super-admin, and
  exfiltrate the entire DB. Assume a leak is terminal — rotate first,
  post-mortem second.

### Rotation procedure

1. **Generate a new service-role key in Supabase.**
   Dashboard → Project Settings → API → **"Regenerate service_role
   key"**. Supabase issues a new JWT but does NOT yet revoke the old
   one — you can run both in parallel for the rotation window.
   *Copy the new key somewhere safe for the next step.*

2. **Push the new key to Fly.** From a workstation you trust:

   ```bash
   fly secrets set SUPABASE_SERVICE_ROLE_KEY='eyJ...new...' --app spinr-backend
   ```

   Fly will roll the machines automatically as the secret is applied.
   Watch `fly logs` for the startup banner and verify the health check
   passes.

3. **Smoke-test one DB-backed endpoint.**

   ```bash
   curl -sSf https://api.spinr.app/healthz | jq
   ```

   The `users` table health check in `backend/core/lifespan.py` runs
   against the service-role key; if it passes, the new key is live.

4. **Revoke the old key in Supabase.**
   Dashboard → Project Settings → API → **"Revoke old service_role
   key"**. Only do this after step 3 is green. Once revoked, any Fly
   machine still on the old key 401s — we want to be certain none are.

### After rotation

* Check `fly status --app spinr-backend` — every machine should be
  running the new release.
* Check Sentry for spikes in 401 from Supabase calls. A clean rotation
  produces none.
* Note in the ops log: who rotated, when, why (scheduled / suspected
  leak / post-incident).

### Emergency rotation (suspected leak)

Skip the parallel-running window: revoke the old key FIRST, then set
the new one. The backend will return 500s for the ~30s between revoke
and Fly roll — that's acceptable when the alternative is a continuing
exfiltration.

---

## `JWT_SECRET`

* **What it is** — the HMAC-SHA256 key used to sign rider/driver and
  admin access tokens.
* **Blast radius on leak** — an attacker can forge access tokens
  claiming to be any user. `token_version` gating (audit P0-S3) adds
  a second check — bumping `users.token_version` invalidates a forged
  token on its next request — but the window between forge and bump
  is the compromise window.

### Rotation procedure

Rotating `JWT_SECRET` invalidates **every outstanding access token**.
Refresh tokens are opaque and stored in `refresh_tokens` — they
continue to work, so clients that hold a refresh token re-auth
silently. Users without a refresh token (older mobile builds) get
kicked to login.

1. Generate a new secret:

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(64))"
   ```

2. Push to Fly:

   ```bash
   fly secrets set JWT_SECRET='<new-secret>' --app spinr-backend
   ```

3. Fly rolls machines. Existing user sessions go to 401 on next
   request; the mobile apps detect this and POST to `/auth/refresh`
   using their stored refresh token, which returns a fresh
   access token signed with the new secret.

### After rotation

* Check `/metrics` (or Sentry) for the expected spike in 401s followed
  by `/auth/refresh` traffic. The refresh rate should recover within
  a few minutes as clients reconnect.
* Any admin users are forced to re-enter their password — admin
  refresh tokens go through `/admin/auth/refresh`, but the super-admin
  login path uses `ADMIN_PASSWORD` directly and re-authenticates from
  scratch.

---

## `ADMIN_PASSWORD`

* **What it is** — the bootstrap super-admin password compared
  directly in `routes/admin/auth.py`.
* **Blast radius on leak** — full admin dashboard access. Admin users
  can read every rider/driver record, suspend accounts, refund
  payments, and adjust Supabase `settings`.

### Rotation procedure

1. Choose a new password (≥ 12 chars; the validator rejects
   ≤ 11-char passwords in production).
2. Push to Fly:

   ```bash
   fly secrets set ADMIN_PASSWORD='<new-password>' --app spinr-backend
   ```
3. Fly rolls machines. The super-admin login path now accepts only
   the new password.
4. Call `POST /admin/auth/logout-all` as a separate admin user (any
   non-super admin) to invalidate existing admin sessions. Super-admin
   logout-all is deliberately rejected — the advice in the error
   message is to rotate `ADMIN_PASSWORD`, which you've just done.

### After rotation

* Log in via the dashboard with the new credentials. If the login
  screen accepts the *old* password, the secret didn't propagate;
  check `fly secrets list` and redeploy.

---

## `FIREBASE_SERVICE_ACCOUNT_JSON`

* **What it is** — the Firebase Admin SDK service account, used to
  verify Firebase-issued ID tokens (phone auth) and to deliver FCM
  push notifications.
* **Blast radius on leak** — an attacker can mint ID tokens for any
  Firebase user in this project and send push notifications claiming
  to come from Spinr. Cannot directly read DB state but can use a
  minted ID token to reach `get_current_user` and hit the full
  authenticated API surface.

### Rotation procedure

1. Firebase Console → Project Settings → Service accounts →
   **"Generate new private key"**. Firebase keeps the old key valid —
   both run in parallel during the rotation window.
2. Flatten the JSON to a single line:

   ```bash
   cat new-service-account.json | tr -d '\n'
   ```
3. Push to Fly:

   ```bash
   fly secrets set FIREBASE_SERVICE_ACCOUNT_JSON='{...}' --app spinr-backend
   ```
4. Wait for Fly roll + smoke test one Firebase-authed endpoint.
5. Firebase Console → Project Settings → Service accounts → locate the
   old key by ID → **Delete**.

### After rotation

* Check that phone-auth logins still work.
* Send a test push notification from the admin dashboard.

---

## Redis URLs (`RATE_LIMIT_REDIS_URL`, `WS_REDIS_URL`)

* **What it is** — the shared Redis store backing rate limiting (audit
  P0-S2) and WebSocket cross-machine fan-out (audit P0-B3).
* **Blast radius on leak** — an attacker with this URL can flush rate
  limit counters (enabling OTP / login brute force) and inject /
  observe every WebSocket message the backend publishes (rides,
  locations, chat). Less severe than the DB key but still sensitive.

### Rotation procedure

Upstash / Fly Redis support rotating the password without touching
the hostname:

1. Provider console → Databases → **"Rotate password"** / **"Reset
   credentials"**.
2. Copy the new `rediss://default:<new-password>@host:port` URL.
3. Push to Fly:

   ```bash
   fly secrets set RATE_LIMIT_REDIS_URL='rediss://...' --app spinr-backend
   # Only if you configured a separate WS Redis:
   fly secrets set WS_REDIS_URL='rediss://...' --app spinr-backend
   ```
4. Fly rolls. On startup you'll see the rate-limiter and
   `WS pub/sub started` banners; if they're missing, the new URL is
   wrong — revert with `fly secrets unset`.

### After rotation

* Check `fly logs` for the startup banners.
* Confirm rate limiting by hammering `/auth/send-otp` more than 5x
  and expecting a 429.
* Confirm WS fan-out by connecting two sockets on different machines
  (via `curl` against the WS endpoint) and sending a message routed
  through `manager.send_personal_message`.

---

## Stripe + Twilio (Supabase `settings` row)

Rotated from the admin dashboard (no backend redeploy):

1. Rotate the key in Stripe / Twilio console.
2. Admin dashboard → Settings → paste new value → Save.
3. The backend reads `settings.twilio_*` / `settings.stripe_*` on
   every relevant request, so the new value is live immediately.
4. Revoke the old key in Stripe / Twilio console.

For Stripe specifically, webhook secret rotation requires updating
both the webhook endpoint in Stripe (which generates a new secret on
save) AND the `stripe_webhook_secret` setting in Supabase. Mismatched
secrets cause the webhook handler to 400 every event — the idempotency
fix in audit P0-B2 ensures we don't lose events, but Stripe retries
exponentially, so fix the mismatch within an hour to stay inside the
retry window.

---

## Incident response

If a secret is KNOWN-leaked (pushed to a public repo, emailed by
mistake, captured in a Sentry payload):

1. **Rotate immediately** — don't wait for a maintenance window. Every
   minute the old secret stays valid is a minute of exposure.
2. **Rotate every derived secret too** — if `SUPABASE_SERVICE_ROLE_KEY`
   leaked, assume the attacker has read the `settings` table and rotate
   Stripe + Twilio keys in the same incident.
3. **Review audit logs** — Supabase logs every service-role query in
   the project dashboard (Settings → Logs). Look for queries from
   unexpected IPs in the leak window.
4. **File an incident** in `docs/ops/incidents/` with a timeline, scope
   of compromise, and list of rotated secrets.

---

## Pre-flight checklist (before every production deploy)

Run these once before `fly deploy`:

- [ ] `fly secrets list --app spinr-backend` shows every required
      secret (see Inventory above).
- [ ] `SUPABASE_SERVICE_ROLE_KEY` in the env looks like a real key
      (starts with `eyJ`, ≥ 40 chars). The production validator will
      refuse to start otherwise — this is a belt-and-braces check.
- [ ] `JWT_SECRET` is not one of the `_INSECURE_JWT_DEFAULTS` set in
      `backend/core/middleware.py`.
- [ ] `ADMIN_PASSWORD` is not `admin123` / `replace-me` / `changeme` /
      `password` and is ≥ 12 chars.
- [ ] `ALLOWED_ORIGINS` does not contain `*`.
- [ ] `RATE_LIMIT_REDIS_URL` starts with `redis://` or `rediss://`.
- [ ] `ENV=production` (the validator is a no-op in `development`).
