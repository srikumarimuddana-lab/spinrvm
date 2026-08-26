# Saskatoon Launch — Checklist + Procedures

**Audience:** launch lead, ops, and engineering for the Spinr Saskatoon go-live.

**Why this exists:** the code is ~90% launch-ready (all 6 P0s shipped, L-P0 launch-readiness items shipped in #172, 5 P1 fixes pending merge on `claude/code-review-ratings-grWmo`). The remaining work is mostly infrastructure config, vendor activation, Saskatchewan regulatory, and operational readiness — none of which lives in the codebase. This runbook is the single source of truth for what must be green before public launch in Saskatoon, with the exact procedure to verify each item.

**Scope:** Saskatoon only (single-city launch). Other Saskatchewan cities are out of scope and must remain dark (service-area geofencing — see section H).

**How to use this doc:**

1. Read the **checklist** first (section 0) to see all gates.
2. For each unchecked gate, jump to the corresponding lettered section for the procedure.
3. Tick each gate as you complete it. **Do not launch until every gate in section 0 is ticked.**
4. Hand the completed doc to engineering for the launch-readiness review.

---

## 0. Checklist (the gates)

### Code

- [ ] **C-1** PR for `claude/code-review-ratings-grWmo` opened, reviewed, and merged to `main` (5 commits: SOS retry, wallet AlertDialog, auth logger sweep, refresh-token Sentry tag, CLAUDE.md doc drift).
- [ ] **C-2** Railway auto-deploy from `main` completed successfully (check Railway deploy log).
- [ ] **C-3** Mobile builds (rider + driver app) shipped via EAS — commit message contained `[build]` and the EAS build URL is in the launch ticket.
- [ ] **C-4** Saskatoon **service-area geofence** code shipped and tested. _(See section H — this is the only remaining real implementation task, ~1 day of work. Not in the codebase as of today.)_

### Backend infrastructure

- [ ] **B-1** All required production env vars set on Railway (see section B).
- [ ] **B-2** Supabase project is in `ca-central-1` (PIPEDA residency).
- [ ] **B-3** Managed Redis attached on Railway; `REDIS_URL`, `RATE_LIMIT_REDIS_URL`, `WS_REDIS_URL` all point at it.
- [ ] **B-4** Database migrations run cleanly (no failures in startup log).
- [ ] **B-5** Backend `/healthz` returns 200 from a curl against the production URL.

### Payments

- [ ] **P-1** Stripe **live** secret key + publishable key in `app_settings` Supabase table.
- [ ] **P-2** Stripe webhook endpoint pointing at `https://<prod-backend>/payments/webhook` with the live signing secret in `app_settings`.
- [ ] **P-3** Stripe Connect onboarding tested end-to-end with one real driver in Saskatoon (Express account, payout schedule confirmed).
- [ ] **P-4** Test charge of $1 succeeds and reconciles correctly in the daily Stripe reconcile cron log.
- [ ] **P-5** Stripe's non-waivable first-payout delay (typically 7–14 days for a new platform account) and reserve-hold policy reviewed against the launch cash-flow plan — see `docs/finance/stripe-payout-readiness.md`. A driver-acquisition blitz or promo spike is exactly the kind of "sudden increase" Stripe's own terms name as a reserve trigger; confirm this is priced in before committing to bonus payout timelines.

### Communications

- [ ] **T-1** Twilio live SID + auth token + Saskatchewan-area-code (306 or 639) verified sender number in `app_settings`.
- [ ] **T-2** Test OTP SMS arrives at a real Saskatoon phone within 15 s.
- [ ] **T-3** Firebase service account JSON in `FIREBASE_SERVICE_ACCOUNT_JSON` (Railway env).
- [ ] **T-4** `FIREBASE_DRIVER_APP_ID` set (driver Firebase auth fails closed without this — verified by code).
- [ ] **T-5** Test push notification arrives on a real device within 5 s.

### Observability

- [ ] **O-1** `SENTRY_DSN` set in Railway env; first event captured from prod (trigger any error to confirm).
- [ ] **O-2** Sentry alert rule wired: `tag:spinr_alert equals refresh_token_reuse` → PagerDuty / on-call. _(Code shipped in commit `1984fc9`.)_
- [ ] **O-3** Sentry alert rule for `OTP_LOCKOUT_TRIGGERED` log error → ops Slack channel.
- [ ] **O-4** Loguru → Sentry bridge verified working (any backend `logger.error` produces a Sentry event).
- [ ] **O-6** Ledger/settlement alert rules wired — six `spinr_alert` tags, two of which page. Specs and response procedures in `docs/runbooks/ledger-alerts.md`. **Blocks turning on `ledger_double_entry_enabled` / `ledger_atomic_settle_enabled`:** four of the six only fire once a flag is on, so flipping a flag before the rules exist means the code's own failure reporting goes to nobody. `settlement_state_unverifiable` is the one that must not wait — it backs a promise made to the rider on screen ("our team has been notified").
- [ ] **O-5** Dashboard up showing the KPIs from CLAUDE.md (match rate, cancellation, dispatch P95, payment success, WAV requests).

### Saskatchewan regulatory

- [ ] **R-1** Provincial ride-share licence (Saskatchewan Highway Traffic Board / Ministry of Highways) obtained, copy filed.
- [ ] **R-2** Municipal Saskatoon Transportation Network Company (TNC) bylaw compliance confirmed with City of Saskatoon (currently bylaw 9266 or successor).
- [ ] **R-3** SGI ride-share endorsement on the company's TNC fleet policy in effect.
- [ ] **R-4** Privacy Policy + Terms of Service published, `consent_version` stamped on signup flow.
- [ ] **R-5** Driver contractor agreement reviewed by Saskatchewan employment counsel (control-of-work risk).
- [ ] **R-6** Data Breach response plan tested with a tabletop drill (see `docs/runbooks/data-breach.md`).

### Driver supply

- [ ] **D-1** Minimum **5–10 active drivers** onboarded in Saskatoon, each with:
  - Class 5 (or higher) Saskatchewan licence, valid
  - 3+ years licensed experience, clean abstract (no major violations 3 yr, no Criminal Code driving offences)
  - SGI ride-share endorsement on personal auto policy
  - Vehicle < 10 years old, valid annual inspection
  - Criminal Record Check + Vulnerable Sector Check on file
  - Stripe Connect Express account fully onboarded (no `requirements.currently_due`)
- [ ] **D-2** At least 1 **WAV-equipped driver** active in Saskatoon (otherwise WAV request flow is theatre — Saskatchewan Transportation Act accessibility mandate).
- [ ] **D-3** Driver app installed on all driver devices; test ride completed end-to-end with each.

### Pricing & service area

- [ ] **S-1** Saskatoon service area geofence active in production (see section H).
- [ ] **S-2** Pricing config set in `app_settings`:
  - Base fare (CAD)
  - Per-km rate
  - Per-minute rate
  - Booking fee
  - Surge cap 2.5× (CLAUDE.md hard rule — do not raise without legal review)
  - GST 5% line item
  - PST 6% line item (Saskatchewan rate)
- [ ] **S-3** Surge engine auto-mode confirmed running every 2 min; manual override available in admin.
- [ ] **S-4** Rider receipt sample reviewed — GST and PST appear as separate line items.

### Operations

- [ ] **OP-1** On-call rotation set in PagerDuty with at least 2 engineers; P1 SLA ≤ 2 hours.
- [ ] **OP-2** Support email/phone live and monitored (riders + drivers).
- [ ] **OP-3** Refund policy documented; support team trained on it.
- [ ] **OP-4** Comms plan for launch day (rider invite list, driver shift schedule, support coverage window).
- [ ] **OP-5** Rollback procedure rehearsed — section N below.

### Pre-launch smoke

- [ ] **SM-1** Backend smoke test (L-P0-4) green against production URL.
- [ ] **SM-2** End-to-end test: rider books → driver accepts → trip completes → rider rated → driver paid out — done with a real rider account and a real driver account in production.
- [ ] **SM-3** SOS smoke test — fire one SOS from a test ride; admin dashboard shows the event, emergency contact notified, audit row written.
- [ ] **SM-4** Refund smoke test — process one refund through admin; Stripe reconcile cron matches it.

**Launch gate:** every box above ticked, signed off by launch lead.

---

## A. Pre-launch code state

### A-1. Merge `claude/code-review-ratings-grWmo`

```bash
# From this session, the branch is already pushed.
# Open the PR via GitHub UI:
#   https://github.com/srikumarimuddana-lab/spinrvm/pull/new/claude/code-review-ratings-grWmo
# Title:  P1 hardening: SOS retry, wallet confirm, logger sweep, Sentry tag, doc drift
# Reviewers: at least 1 backend + 1 mobile reviewer
# Required checks: security-gates, guard-rail, pip-compile-check, migration-safety-gate (no migrations in this PR so the last one is a no-op).
```

After merge, confirm Railway deploys and the backend starts cleanly:

```bash
curl -fsSL https://<prod-backend>/healthz
# Expect: {"status":"healthy","database":"connected", ...}
```

### A-2. Trigger mobile builds via EAS

Mobile builds only run when a commit message on `main` contains `[build]`. After the PR merges, push an empty trigger commit:

```bash
git checkout main
git pull origin main
git commit --allow-empty -m "chore: trigger EAS build for Saskatoon launch [build]"
git push origin main
```

EAS will build both rider and driver app. Check `https://expo.dev/accounts/<your-org>/projects` for build status. Submit both to TestFlight + Play Internal Track first; promote to production after the smoke tests pass.

---

## B. Backend env vars (Railway)

Required and validated at startup per `backend/core/config.py`:

| Env var | Validation rule | Where to get it |
|---|---|---|
| `SUPABASE_URL` | Required in production | Supabase project settings → API |
| `SUPABASE_SERVICE_ROLE_KEY` | Required in production | Same page (the `service_role` key, NOT `anon`) |
| `JWT_SECRET` | ≥ 32 chars, not a dev value | `openssl rand -hex 32` |
| `ADMIN_PASSWORD` | Not `admin123` (startup fails) | Generated by your secret manager |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Required for Firebase auth (driver app login) | Firebase Console → Project settings → Service accounts → Generate new private key |
| `FIREBASE_DRIVER_APP_ID` | Required — driver auth fails closed without it | Firebase Console → Project settings → General → App ID for the driver app |
| `REDIS_URL` | Required in prod (or rate-limit + OTP-lockout state is lost on restart) | Railway Redis plugin (auto-injected if attached) |
| `RATE_LIMIT_REDIS_URL` | Set to `$REDIS_URL` | Same Redis instance is fine |
| `WS_REDIS_URL` | Set to `$REDIS_URL` | Same Redis instance is fine |
| `SENTRY_DSN` | Optional but required for production | Sentry project settings → Client Keys (DSN) |
| `ENV` | Set to `production` | This triggers prod guards (no OTP `1234` bypass, weak-secret checks) |

Verification:

```bash
# After deploy, the startup log should NOT contain any of:
#   "Weak JWT_SECRET in production"
#   "ADMIN_PASSWORD is the default"
#   "SUPABASE_URL not set"
#   "Redis missing in production"
# If any of those appear, fix the env var and redeploy.

railway logs --service backend --tail 200 | grep -i "production\|weak\|missing"
# Expect: no matches.
```

---

## C. Supabase configuration

### C-1. Region

Supabase project must be in **ca-central-1** (Montreal). PIPEDA data-residency requirement.

Verify:

```bash
# Supabase Dashboard → Settings → General → Project Region
# Expect: "Central Canada (ca-central-1)" — Montreal
```

If the project is in any other region, **do not launch**. Migration is a multi-day exercise that requires legal sign-off.

### C-2. RLS enabled

Every user-data table has RLS policies (per CLAUDE.md). Spot-check:

```sql
-- In the Supabase SQL editor:
SELECT schemaname, tablename, rowsecurity
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN ('users', 'rides', 'drivers', 'payments', 'corporate_wallets', 'audit_logs');
-- Expect: rowsecurity = true for all of them.
```

### C-3. Migrations applied

Migrations run on backend startup. Confirm the highest expected slot:

```bash
ls backend/migrations/ | sort -V | tail -1
# Expect: 101_users_add_is_rider.sql (or whatever is highest on main at launch)
```

Check the production DB:

```sql
SELECT filename FROM _migrations ORDER BY applied_at DESC LIMIT 5;
-- Expect: latest filename matches what's in backend/migrations/ on main.
```

### C-4. Backups + PITR

- Daily backups: Supabase auto on Pro plan. Confirm enabled.
- PITR (point-in-time-recovery) window: ≥ 7 days.
- Test restore: see `docs/runbooks/pitr-restore.md`.

---

## D. Stripe configuration

### D-1. Live keys in `app_settings`

Stripe keys live in the `app_settings` Supabase table (not env), per CLAUDE.md — so they can be rotated without redeployment.

```sql
-- In the Supabase SQL editor:
UPDATE app_settings SET value = 'sk_live_...' WHERE key = 'stripe_secret_key';
UPDATE app_settings SET value = 'pk_live_...' WHERE key = 'stripe_publishable_key';
UPDATE app_settings SET value = 'whsec_...' WHERE key = 'stripe_webhook_secret';
```

Or use the admin dashboard → Settings → Payments (preferred — emits an audit log row).

### D-2. Webhook endpoint

In Stripe Dashboard → Developers → Webhooks → Add endpoint:

- **URL:** `https://<prod-backend>/payments/webhook`
- **Events to send:** at minimum `payment_intent.succeeded`, `payment_intent.payment_failed`, `charge.refunded`, `account.updated`, `payout.paid`, `payout.failed`
- **Signing secret:** copy into `app_settings.stripe_webhook_secret`

Verify:

```bash
# Send a test webhook from Stripe Dashboard → Webhooks → your endpoint → Send test webhook.
# In backend logs, look for:
#   "Processed Stripe event evt_test_..."
# If you see "Stripe signature verification failed" the signing secret is wrong.
```

### D-3. Stripe Connect

Drivers are paid via Stripe Connect Express. Verify with one driver:

1. Driver completes onboarding in driver app → opens Stripe Express dashboard.
2. In Stripe Dashboard → Connect → Accounts → find driver → confirm `requirements.currently_due` is empty.
3. Trigger a $1 test payout via admin dashboard → Earnings → Payouts.
4. Confirm Stripe shows the payout as `paid` within 24 h (instant payout if enabled).

### D-4. Reconciliation cron

The daily 02:00 UTC Stripe ↔ DB reconcile cron runs unconditionally (`backend/utils/stripe_reconcile.py`). After one full day in production:

```bash
# Backend logs around 02:00 UTC:
#   "stripe_reconcile: scanned N rides, M discrepancies"
# Expect M = 0 in healthy state. Any non-zero entry creates an audit_logs row
# with action in {DB_PAID_STRIPE_MISSING, DB_PAID_STRIPE_MISMATCH,
# DB_PAID_AMOUNT_MISMATCH, STRIPE_ORPHAN} — investigate each.
```

---

## E. Twilio configuration

### E-1. Live credentials in `app_settings`

```sql
UPDATE app_settings SET value = 'AC...'  WHERE key = 'twilio_account_sid';
UPDATE app_settings SET value = '...'    WHERE key = 'twilio_auth_token';
UPDATE app_settings SET value = '+1306...' WHERE key = 'twilio_from_number';
```

The from-number should be a **306** or **639** number (Saskatchewan area codes) to maximise SMS deliverability in-province.

### E-2. Test OTP delivery

```bash
# From a real phone in Saskatoon:
curl -X POST https://<prod-backend>/auth/send-otp \
  -H "Content-Type: application/json" \
  -d '{"phone":"+1306..."}'
# Expect: 200 OK, SMS arrives within 15 s.
# If Twilio is misconfigured, the response will succeed but no SMS arrives —
# AND the backend log will say "Twilio not configured — OTP bypass active (code=1234)".
# That string in prod logs is a launch blocker.
```

---

## F. Firebase configuration

### F-1. Service account

`FIREBASE_SERVICE_ACCOUNT_JSON` must be the stringified JSON of a Firebase service-account private key with the **Firebase Authentication Admin** role.

### F-2. Driver app ID binding

`FIREBASE_DRIVER_APP_ID` must match the App ID of the driver Firebase app. Without it, `routes/auth.py:firebase_auth_login` fails closed with 503 — driver login will not work.

### F-3. FCM credentials

For push notifications to land on driver/rider devices, the Firebase project must have FCM enabled and the rider + driver apps registered with matching package IDs / bundle IDs.

Verify:

```bash
# Send a push from admin dashboard → Notifications → Send test.
# Confirm arrival on a real device within 5 s.
# If push fails, check FCM credentials and APNs cert (iOS).
```

---

## G. Sentry + alerting

### G-1. DSN

Set `SENTRY_DSN` in Railway env. Restart backend. Trigger any error (e.g. POST to a malformed endpoint) and confirm the event lands in Sentry within 30 s.

### G-2. Refresh-token reuse alert

Code shipped in commit `1984fc9` emits a tagged Sentry event when refresh-token reuse is detected. Set up the alert rule in Sentry UI:

- **Project:** spinr-backend (production)
- **Alert type:** Issue Alert
- **When:** `An event is captured`
- **If:** `event.tag spinr_alert equals refresh_token_reuse`
- **Then:** Send notification to PagerDuty service `spinr-oncall`
- **Frequency:** Every event (do not throttle — each occurrence is a potential account takeover)

### G-3. OTP lockout alert

Auth changes in commit `ccf931c` emit `OTP_LOCKOUT_TRIGGERED` at ERROR level. Set up:

- **When:** `An event is captured`
- **If:** `message contains OTP_LOCKOUT_TRIGGERED`
- **Then:** Notify ops Slack channel `#spinr-auth-alerts`
- **Frequency:** Every event (spikes indicate credential stuffing)

### G-4. KPI dashboard

Stand up a dashboard (Grafana, Datadog, or Sentry's metric explorer) showing live values for the KPIs in CLAUDE.md:

- Match rate (target ≥ 85%)
- Rider cancellation rate (target ≤ 8%)
- Driver cancellation rate (target ≤ 3%)
- Driver utilization (target ≥ 55%)
- P95 dispatch latency (target < 2 s)
- P95 fare calc latency (target < 300 ms)
- Payment success rate (target ≥ 99%)
- Safety incident rate (target < 1 / 10k rides)

---

## H. Saskatoon service area geofence

**Status: NOT YET CODED.** This is the only remaining real implementation task before public launch. Roughly 1 day of work.

What's needed:

1. New migration `102_service_areas.sql` with a `service_areas` table:
   ```sql
   CREATE TABLE service_areas (
     id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
     code TEXT UNIQUE NOT NULL,         -- 'saskatoon', 'regina', etc.
     display_name TEXT NOT NULL,
     polygon JSONB NOT NULL,             -- GeoJSON polygon
     is_active BOOLEAN DEFAULT FALSE,
     min_drivers_to_accept_rides INT DEFAULT 1,
     created_at TIMESTAMPTZ DEFAULT now(),
     updated_at TIMESTAMPTZ DEFAULT now()
   );
   -- Seed Saskatoon polygon (rough city limits) as is_active = true.
   -- Other cities can be seeded as is_active = false for later activation.
   ```
2. Dispatch service filter: `find_nearby_drivers` and ride-creation must reject rides whose pickup is outside any active service area.
3. Surge engine respects service areas (already per-area in `surge_engine.py` but verify).
4. Admin dashboard page to manage service areas (toggle active, edit polygon).
5. Rider app: graceful "Spinr isn't available in your area yet" screen when pickup is outside the geofence.

**Until this lands, launching publicly is unsafe:** a rider in Regina could book a ride that no driver is available for, dispatch loops fruitlessly, and the rider has a broken experience.

**Workaround for invite-only soft launch:** if you must launch before this code lands, restrict the rider invite list to verified Saskatoon residents AND keep all drivers physically in Saskatoon. This is brittle and not recommended for public launch.

Open the implementation as a fresh ticket and PR once approved. Estimate: 1 day of backend + 0.5 day of admin UI + 0.5 day of rider app graceful-fail screen.

---

## I. Saskatchewan regulatory

### I-1. Provincial ride-share licence

Saskatchewan TNC regulation is under the Highway Traffic Board / Ministry of Highways and Infrastructure. Confirm Spinr Inc. (or operating entity) is licensed as a Transportation Network Company in the province. Keep the licence number and expiry date in the launch ticket.

### I-2. Municipal Saskatoon bylaw

City of Saskatoon currently has bylaw governing TNC operations (verify the current bylaw number — bylaws are renumbered periodically). External research this cycle turned up "Bylaw No. 9651" as a possible current cite and a comparable Regina "Vehicle(s) For Hire Bylaw" with conflicting reported per-trip/annual fee figures — **neither is confirmed against the City directly; treat as a lead to verify, not a fact to act on.** Details and the open verification question are tracked in `ACTION_ITEMS.md` (TNC licensing fee-schedule verification) and `docs/legal/company-insurance-and-licensing.md`. Confirm compliance:

- Per-ride fee remittance (if any) to the City
- Driver background check standard meets municipal requirement
- Insurance proof on file with the City
- Vehicle inspection regime aligned with municipal rule

### I-3. SGI ride-share endorsement

Two layers:

- **Company TNC fleet policy** with SGI Auto Fund — covers Period 1 (driver online, no ride) at minimum.
- **Each driver's personal SGI policy** must have the **ride-share endorsement** added — this is non-negotiable per CLAUDE.md.

The driver onboarding flow already requires document upload of insurance with endorsement; confirm the verification step is gated on a human reviewer (or that the OCR-based check is robust) before driver `go_online` is allowed.

**Turnaround time is an open operational unknown, not yet confirmed with SGI or local police services.** SGI endorsement processing time and Vulnerable Sector Check turnaround (I-3 above, J-1 below) both gate how far in advance driver recruiting must start before launch day — see `docs/growth/driver-acquisition-strategy.md` and the tracked item in `ACTION_ITEMS.md`. This is distinct from the SGI *quarterly regulatory reporting* format questions in `docs/compliance/sgi-quarterly.md`, which is a separate open thread with SGI.

### I-7. Company-level insurance (separate from driver SGI endorsement)

Section I above covers driver-facing insurance only. The company also needs its own commercial general liability, technology E&O, and cyber coverage before public launch — not yet documented anywhere in this repo. See `docs/legal/company-insurance-and-licensing.md` (new) and the tracked `ACTION_ITEMS.md` entry. Uride's own Saskatchewan-market launch was reportedly delayed by insurance-arrangement lead time — budget for this taking longer than expected.

### I-4. Privacy Policy + ToS

- Publish at https://spinr.ca/privacy and https://spinr.ca/terms (or your domain).
- Both must reference PIPEDA, Saskatchewan Transportation Act retention rules (7 yr trip / 3 yr GPS), and the right-to-delete process.
- `consent_version` column on `users` table must be populated with the current version on every signup. Material changes to either doc require re-consent on next app open.

### I-5. Contractor agreement

Saskatchewan employment counsel must review the driver agreement for control-of-work risk per CLAUDE.md. Avoid in app copy: "shift schedule", "uniform required", "must accept N rides per week". Drivers are contractors, not employees.

### I-6. Data breach plan

Tabletop a breach scenario using `docs/runbooks/data-breach.md`. Confirm:

- Who declares the incident
- Who notifies the Privacy Commissioner (24-hour scope, 72-hour notification if "real risk of significant harm")
- Who manages user comms
- Where evidence is preserved (Sentry export, log retention, audit_logs snapshot)

---

## J. Driver supply

### J-1. Onboarding minimums

Each driver must meet CLAUDE.md's eligibility rules (enforced both at onboarding and every `go_online` call):

| Check | Source | Cadence |
|---|---|---|
| Class 5 (or higher) SK licence | SGI licence card upload + verification | At onboarding; expiry monitored |
| 3+ yr clean abstract | SGI driver abstract upload | At onboarding; refreshed annually |
| Criminal Record Check | Sterling, Triton, or equivalent | At onboarding; refreshed annually |
| Vulnerable Sector Check | Local police service | At onboarding; refreshed annually |
| SGI ride-share endorsement | Insurance pink slip upload | At onboarding; expiry monitored |
| Vehicle < 10 yr old | Registration upload | At onboarding |
| Annual vehicle inspection | Inspection certificate | At onboarding; refreshed annually |
| Stripe Connect Express account | Stripe onboarding flow | At onboarding; refreshed if Stripe demands |

### J-2. Minimum supply for soft launch

At least **5–10 drivers** active in Saskatoon for the first 2 weeks. Below that, match rate suffers, riders churn, and the launch impression is bad.

For the recruiting funnel, sourcing channels, and bonus-structure options to reach this minimum on schedule, see `docs/growth/driver-acquisition-strategy.md` — this section states the *gate*, that doc covers *how to fill it*.

### J-3. WAV driver

At least **1 wheelchair-accessible vehicle** driver online in Saskatoon. Saskatchewan Transportation Act mandates WAV service when a WAV driver is available in the service area. Code shipped in PR #240 (WAV dispatch matching); the regulatory check is whether a real WAV driver exists.

### J-4. Driver training

Each driver completes a brief onboarding walkthrough:

- How to go online / offline
- How to accept / decline / cancel an offer
- Insurance period basics (period 1 vs 2 vs 3)
- SOS button behaviour
- Refund / dispute escalation path

---

## K. Operations & on-call

### K-1. PagerDuty rotation

- At least **2 engineers** on the on-call roster (primary + backup).
- P1 SLA: page within 2 minutes; ack within 15 minutes; response within 2 hours.
- Sentry alerts (G-2, G-3) and Stripe reconcile failures all route to PagerDuty.

### K-2. Support coverage

- Support email + phone number staffed for at least 12 h/day during the first 4 weeks.
- Escalation path: support → on-call engineer (for outages) → launch lead (for regulatory).

### K-3. Refund policy

Document and train support on:

- When to refund automatically (no driver found, app bug, wrong route via no fault of rider)
- When to escalate (dispute over fare, claimed driver misconduct)
- How to issue refund via admin → Rides → Refund (creates audit log row)

### K-4. Comms plan

- Day-of launch: ops lead + on-call engineer in a shared comms channel (Slack `#spinr-launch`)
- Rider invite list confirmed (if soft launch)
- Driver shift schedule confirmed for launch day + 48 h after

---

## L. Pre-launch smoke (run within 24 h of launch)

### L-1. Backend smoke (existing L-P0-4)

```bash
curl -fsSL https://<prod-backend>/healthz
curl -fsSL https://<prod-backend>/api/health/fare
# Both: expect 200 with structured JSON.
```

### L-2. Full ride lifecycle in production

Use a real rider account and a real driver account:

1. Rider opens app, books ride.
2. Driver receives offer, accepts.
3. Driver navigates to pickup, marks arrived.
4. Driver starts trip, ends trip.
5. Rider sees receipt with GST + PST line items.
6. Rider rates driver.
7. Driver sees earnings update; payout shows pending.
8. Verify in admin dashboard the ride is visible and audit log row exists.

If any step fails, do not launch.

### L-3. SOS smoke

1. Start a test ride.
2. Long-press SOS button on rider app.
3. Confirm: button turns amber (sending), then green (alert sent).
4. Admin dashboard → Safety → Incidents — confirm row exists with rider id, ride id, geohashed location.
5. Confirm emergency contact (in rider profile) received the notification.
6. Confirm audit_logs row written with action `sos_triggered`.

### L-4. Refund smoke

1. Complete a $1 test trip.
2. Admin dashboard → Rides → find the ride → Refund.
3. Confirm Stripe shows refund within 5 min.
4. Confirm next daily reconcile cron run shows 0 discrepancies for this ride.

---

## M. Day-of launch sequence

**T−24 h**

- Confirm every box in section 0 is ticked.
- Final code freeze (no merges to `main` until T+24 h after launch).
- Ops lead + on-call engineer on standby.

**T−1 h**

- Sanity-check backend `/healthz`.
- Sanity-check at least 3 drivers are online and `is_available = true`.
- Sanity-check Sentry is receiving events (trigger a known 4xx to confirm).

**T = launch**

- Send rider invite emails (or open public signup).
- Driver app shows online drivers in Saskatoon.
- First booking expected.

**T+1 h**

- Confirm first ride completed cleanly.
- Check KPI dashboard: match rate, P95 dispatch latency, payment success.
- Check Sentry: zero unexpected errors.

**T+4 h**

- Sample 5 rides in admin dashboard. Verify each has clean audit trail and correct fare breakdown.
- Confirm no `STRIPE_ORPHAN` or `DB_PAID_STRIPE_MISMATCH` in the audit log.

**T+24 h**

- Daily Stripe reconcile cron output reviewed: 0 discrepancies.
- KPIs against targets (CLAUDE.md). Anything below target → investigate root cause before T+48 h.
- Lift code freeze if everything is green.

---

## N. Rollback procedures

### N-1. Backend rollback (Railway)

If a deployment causes a P1 incident, revert via Railway:

```bash
# Railway dashboard → backend service → Deployments → previous successful deploy → Redeploy.
# Confirms within ~1 min; container restarts; old image takes traffic.
```

Or via CLI:

```bash
railway service backend
railway deployments
railway rollback <deployment-id>
```

### N-2. Database rollback

Migrations are append-only. A bad migration is rolled back by:

- Writing a new forward-fix migration with the correct schema.
- For data-only badness, restore via PITR (`docs/runbooks/pitr-restore.md`) — be aware this means losing any writes after the restore point.

### N-3. Mobile app rollback

EAS update can ship a JS-only fix in minutes (rider + driver apps). Native rollback requires resubmission and review — plan for 1–7 days.

For a critical native bug:

1. Ship an EAS update with a feature flag forcing the offending feature off.
2. Communicate to users via in-app banner + push.

### N-4. Kill switch

If a critical issue affects the entire platform, the fastest stop is:

- Set every driver in Saskatoon to `is_online = false` via admin dashboard bulk action (script in `backend/scripts/`) — a supply-side lever, stops drivers from taking new offers.
- Or flip the `new_ride_requests_enabled` app_settings flag off (ACTION_ITEMS.md G5) — a demand-side lever, checked at the top of `POST /rides` before any DB write. Rejects new bookings with a clean 503 without touching driver online status. Cleaner than the bulk `is_online=false` action for an incident that's specifically about the booking/dispatch/payment path rather than driver supply (e.g. a dispatch bug, a payment-processing problem, a Stripe reserve-hold scenario) — flip it back to `true` (the default) to resume.
- Or set the Saskatoon service area `is_active = false` (once H is implemented) — instantly stops new rides without disrupting in-flight ones.
- Riders see "Spinr is temporarily unavailable" screen.
- In-flight rides complete normally regardless of which lever is used.

---

## O. Post-launch monitoring (first 7 days)

Daily standup, 15 min, reviewing:

- Match rate trend (target ≥ 85% by day 7)
- Cancellation rates (rider ≤ 8%, driver ≤ 3%)
- Payment success rate (≥ 99%)
- Sentry new errors (count + severity)
- Stripe reconcile output (any discrepancy = investigate same day)
- Safety incidents (every one investigated individually per CLAUDE.md)
- Support ticket volume + P1 response time

End of week 1: write a launch retro. What went well, what broke, what needs attention before opening more cities.

---

## Sign-off

| Section | Owner | Signed off |
|---|---|---|
| Section 0 checklist (all gates green) | Launch lead | ___________ |
| Section A code state | Engineering lead | ___________ |
| Section B–G infrastructure | Engineering lead | ___________ |
| Section H Saskatoon geofence | Engineering lead | ___________ |
| Section I regulatory | Legal / compliance | ___________ |
| Section J driver supply | Driver ops | ___________ |
| Section K operations | Ops lead | ___________ |
| Section L pre-launch smoke | Engineering + ops | ___________ |
| Launch authorisation | CEO / launch lead | ___________ |

**No section may be signed off without the corresponding gate(s) in section 0 being ticked.**
