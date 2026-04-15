# Spinr — Operator Tasks
<!-- Human-gated blockers. These CANNOT be resolved by code changes alone. -->
<!-- Work through these IN PARALLEL with the SPR-01–04 sprints.          -->

**Last updated:** 2026-04-14

---

## Priority Legend
🔴 **Critical** — App will not function at all without this  
🟡 **Required** — Needed before real users (pilot city / App Store)  
🟢 **Important** — Needed before production launch, not for internal testing

---

## OPS-01 — Rotate Leaked Supabase Service-Role Key 🔴

**Why urgent:** The real service-role JWT for project `dbbadhihiwztmnqnbdke` was
committed to `backend/.env.example` and is in the git history. Anyone with repo
access can use it to bypass Row Level Security on your entire database.

**Steps:**
1. Go to **Supabase Dashboard → Project `dbbadhihiwztmnqnbdke` → Settings → API**
2. Under "Project API keys", click **Regenerate** next to `service_role`
3. Copy the new key
4. Store it in your secrets vault (1Password, Bitwarden, etc.)
5. Update `backend/.env` with the new value
6. If deployed: update the environment variable in Render / Fly.io / Railway
7. Optionally: run `git filter-repo` to scrub the old key from history

**Status:** ☐ Not done

---

## OPS-02 — Rotate Leaked Google Maps API Key 🔴

**Why urgent:** `AIzaSy…M5m9M` was committed to `rider-app/eas.json` and
`driver-app/eas.json`. Anyone can use your Maps quota and billing.

**Steps:**
1. Go to **Google Cloud Console → APIs & Services → Credentials**
2. Find the key `AIzaSy…M5m9M` → click **Delete** (or Restrict to 0 APIs as stop-gap)
3. Click **Create Credentials → API Key**
4. Restrict the new key:
   - **Application restrictions:** Android apps + iOS apps
   - **Android:** Add your package names (`com.spinr.rider`, `com.spinr.driver`)
   - **iOS:** Add your bundle IDs
   - **API restrictions:** Maps SDK for Android, Maps SDK for iOS, Places API, Directions API
5. Store the new key in EAS Secrets (see `scripts/setup-eas-secrets.sh`)
6. Never put it in `eas.json` again — use EAS Secrets only

**Status:** ☐ Not done

---

## OPS-03 — Provision Supabase Project 🔴

**Why urgent:** Without a database, no features work. This takes ~30 min.

**Steps (in order — do not skip or reorder):**
1. Create a new Supabase project in **Canadian region** (ca-central-1 / AWS Montreal)
2. Enable the `uuid-ossp` extension: **Database → Extensions → uuid-ossp**
3. Run `backend/supabase_schema.sql` in the SQL editor
4. Run `backend/sql/02_add_updated_at.sql`
5. Run `backend/sql/03_features.sql`
6. Run `backend/sql/04_rides_admin_overhaul.sql` (**required** — creates flags, complaints, lost_and_found, driver_location_history)
7. Apply all migrations:
   ```bash
   export DATABASE_URL='postgres://postgres.[project-ref]:[password]@aws-0-ca-central-1.pooler.supabase.com:6543/postgres'
   python -m backend.scripts.run_migrations --status   # preview
   python -m backend.scripts.run_migrations            # apply
   ```
8. Run `backend/supabase_rls.sql` (Row Level Security)
9. Seed the settings row:
   ```sql
   INSERT INTO settings (id, terms_of_service_text, privacy_policy_text)
   VALUES (gen_random_uuid(), 'Your ToS here', 'Your Privacy Policy here')
   ON CONFLICT DO NOTHING;
   ```
10. Save `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` (new, from OPS-01) to vault

**⚠️ Do NOT use `backend/FINAL_SCHEMA.sql`** — it is deprecated and has conflicts.

**Status:** ☐ Not done

---

## OPS-04 — Configure Firebase 🔴

**Why urgent:** Phone auth (OTP) and push notifications both require Firebase.
Internal testing can use dev mode (OTP=123456), but pilot requires this.

**Steps:**
1. Create a Firebase project at console.firebase.google.com
2. **Authentication:** Enable Phone provider; add test phone numbers for QA
3. **Android config:**
   - Download `google-services.json`
   - Place at `rider-app/google-services.json` AND `driver-app/google-services.json`
4. **iOS config:**
   - Download `GoogleService-Info.plist`
   - Place at `rider-app/GoogleService-Info.plist` AND `driver-app/GoogleService-Info.plist`
5. **APNs (iOS push):**
   - In Apple Developer: Certificates → Keys → Create new key with APNs enabled
   - Download the `.p8` file
   - In Firebase → Project Settings → Cloud Messaging → Apple app: upload the `.p8`
6. **Service Account (backend):**
   - Firebase → Project Settings → Service Accounts → Generate new private key
   - Flatten the JSON to a single line: `jq -c . service-account.json`
   - Store as `FIREBASE_SERVICE_ACCOUNT_JSON` env var
7. **App Check (optional but recommended):**
   - Enable DeviceCheck (iOS) and Play Integrity (Android) in Firebase console
   - Client-side `initializeAppCheck()` is already called in `shared/services/firebase.ts`

**Status:** ☐ Not done

---

## OPS-05 — Stripe Live Mode 🟡

**Why urgent:** Required before accepting real payments from riders.

**Steps:**
1. Complete Stripe business verification at dashboard.stripe.com
2. Add bank account for payouts
3. Submit tax forms (W-9 or W-8BEN depending on business structure)
4. Once verified, get Live Mode keys (`sk_live_…`, `pk_live_…`)
5. Store in vault
6. Add webhook endpoint in Stripe Dashboard:
   - URL: `https://api.spinr.app/api/webhooks/stripe`
   - Events: `payment_intent.succeeded`, `payment_intent.payment_failed`,
     `charge.refunded`, `invoice.payment_succeeded`, `customer.subscription.deleted`
7. Copy webhook signing secret (`whsec_…`) to vault
8. Update backend env: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`
9. Seed Stripe keys in Supabase `settings` table via Admin Dashboard UI

**Status:** ☐ Not done

---

## OPS-06 — Twilio A2P 10DLC Registration 🟡

**Why urgent:** Without this, SMS OTP will be blocked by US/Canadian carriers.
**This approval takes 2-4 weeks. Start it TODAY.**

**Steps:**
1. Create Twilio account at console.twilio.com
2. Buy a 10DLC phone number (Canadian local number preferred)
3. Register your Brand (company name, EIN/BN, website)
4. Register your Campaign (use case: "2FA/OTP", sample message: "Your Spinr OTP is 123456")
5. Wait for carrier approval (2-4 weeks)
6. Once approved, get `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN`
7. Store in vault; seed in Supabase `settings` table via Admin Dashboard

**Note:** While waiting for approval, internal testing uses `OTP_CODE=123456` dev fallback.

**Status:** ☐ Not done — START IMMEDIATELY

---

## OPS-07 — Legal: ToS + Privacy Policy 🟡

**Why urgent:** App Store requires both. Riders/drivers must accept before using.
Screens exist and fetch from backend; they currently show empty strings.

**Steps:**
1. Engage a lawyer familiar with Canadian ride-sharing regulations (Saskatchewan)
2. Draft Terms of Service covering:
   - Driver/rider relationship (independent contractor, not employee)
   - Payment terms, refund policy, cancellation fees
   - Liability limitations
   - Dispute resolution
   - Data use
3. Draft Privacy Policy covering:
   - Location data collection and retention
   - Payment data (handled by Stripe, no card storage)
   - Communications (SMS, push)
   - Third-party services (Firebase, Stripe, Google Maps)
   - Right to deletion (PIPEDA compliance for Canada)
4. Have lawyer sign off
5. Insert final text into Supabase `settings` table (OPS-03 step 9)

**Status:** ☐ Not done

---

## OPS-08 — App Store Accounts 🟡

**Steps:**
1. **Apple Developer Program:** developer.apple.com → Enroll ($99 USD/year)
   - Takes 24-48h for approval
   - Requires D-U-N-S number for companies
2. **Google Play Console:** play.google.com/console → Pay one-time $25 USD
   - Instant activation
3. After both are active, run `scripts/setup-eas-secrets.sh` to register EAS projects

**Status:** ☐ Not done

---

## Quick Status Dashboard

```
OPS-01  Rotate Supabase key      ☐  🔴 CRITICAL
OPS-02  Rotate Maps API key      ☐  🔴 CRITICAL
OPS-03  Provision Supabase DB    ☐  🔴 CRITICAL (blocks all features)
OPS-04  Firebase setup           ☐  🔴 CRITICAL (blocks push + phone auth)
OPS-05  Stripe Live Mode         ☐  🟡 Before pilot
OPS-06  Twilio A2P 10DLC         ☐  🟡 START NOW (2-4 week lead time)
OPS-07  Legal copy               ☐  🟡 Before App Store
OPS-08  App Store accounts       ☐  🟡 Before SPR-04
```

**Internal testing (2-3 people) is unblocked by OPS-03 alone.**
OTP fallback (123456) + mock payments work without OPS-04 through OPS-08.
