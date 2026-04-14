# 01 — Supabase Setup

**Goal:** stand up the production Postgres + PostGIS + RLS + storage
backing Spinr. Single Supabase project hosts everything.

**Time:** ~90 minutes.

**Pre-reqs:** payment method on Supabase; Spinr repo cloned locally
so you can access the SQL files.

---

## Step 1 — Create the project

1. Go to https://supabase.com/dashboard → **New project**.
2. **Organization:** create or pick the one matching your company.
3. **Name:** `spinr-production`. Do NOT include environment-specific
   suffixes in the project ref; the project ref lives in URLs and is
   what the validator looks at.
4. **Database password:** generate a 40-char password in your vault.
   **Save it now** — Supabase shows it only once. You'll need it for
   `DATABASE_URL`.
5. **Region:** pick the region nearest your users. For Canadian
   riders, use `ca-central-1` (AWS Montreal). Cannot be changed later
   without a dump/restore.
6. **Pricing plan:** **Pro ($25/mo minimum).** Free tier lacks PITR
   and pauses on inactivity — unacceptable for production.
7. **Create new project.** Wait 2-3 minutes for provisioning.

---

## Step 2 — Enable required extensions

Supabase dashboard → **Database** → **Extensions**.

Enable:

- [ ] `uuid-ossp` — required by `supabase_schema.sql`.
- [ ] `pg_stat_statements` — for P1 performance monitoring.
- [ ] `pgcrypto` — used by some migrations.
- [ ] `postgis` — **only if** you later adopt the PostGIS dispatch
      algorithm (audit P6). Current code uses haversine math, so this
      is optional at launch.

---

## Step 3 — Grab connection details

Supabase dashboard → **Project Settings** → **API**.

Record these in your vault:

- **Project URL:** `https://<project-ref>.supabase.co` — this is your
  `SUPABASE_URL`.
- **service_role** key (click Reveal) — this is your
  `SUPABASE_SERVICE_ROLE_KEY`. Starts with `eyJ`, ~220 chars.
- **anon** key (optional, for client-side Supabase if ever needed).

Supabase dashboard → **Project Settings** → **Database** →
**Connection string** → **URI** tab → **Transaction mode** (port 6543).

- Copy this string. It's your `DATABASE_URL` for running migrations.
  Example:
  ```
  postgres://postgres.<ref>:<password>@aws-0-ca-central-1.pooler.supabase.com:6543/postgres
  ```

---

## Step 4 — Apply the schema (from your workstation)

You need the repo cloned and `python3` + `psql` installed.

### 4a. Export connection env var

```bash
export DATABASE_URL='postgres://postgres.<ref>:<password>@aws-0-ca-central-1.pooler.supabase.com:6543/postgres'
```

### 4b. Apply `supabase_schema.sql` (core tables + RPCs)

```bash
cd ~/Spinr   # or wherever you cloned
psql "$DATABASE_URL" -f backend/supabase_schema.sql
```

Expected output: many `CREATE TABLE`, `CREATE FUNCTION`, `CREATE INDEX`
lines. No errors.

### 4c. Apply `sql/02`, `sql/03`, `sql/04`

```bash
psql "$DATABASE_URL" -f backend/sql/02_add_updated_at.sql
psql "$DATABASE_URL" -f backend/sql/03_features.sql
psql "$DATABASE_URL" -f backend/sql/04_rides_admin_overhaul.sql
```

> Skip `backend/sql/01_postgis_schema.sql` unless you enabled PostGIS
> in step 2. The app does not require it.

### 4d. Apply migrations via the runner

```bash
cd backend
bash scripts/run_migrations.sh --status   # show pending
bash scripts/run_migrations.sh            # apply
```

The runner uses `schema_migrations` (created by migration 24) to track
state, so re-runs are safe. You should see 26 migrations applied by
default (as of 2026-04).

### 4e. Apply RLS policies

```bash
psql "$DATABASE_URL" -f backend/supabase_rls.sql
```

### 4f. Verify RLS coverage

```bash
psql "$DATABASE_URL" -c \
  "SELECT tablename FROM pg_tables WHERE schemaname='public'
   AND NOT rowsecurity ORDER BY tablename;"
```

**Expected:** zero rows. Every `public` table has RLS enabled. If you
see rows, something in `supabase_rls.sql` did not apply. Stop and
investigate — do not proceed to launch with unprotected tables.

---

## Step 5 — Seed the `settings` row

The app reads business configuration from a single `settings` row.
Most columns have sane defaults from `sql/03_features.sql`; three
MUST be filled in before launch.

### 5a. Minimum required via psql

```sql
UPDATE settings
SET
  terms_of_service_text = 'Insert full ToS here — must be non-empty.',
  privacy_policy_text   = 'Insert full Privacy Policy here — non-empty.',
  -- Stripe + Twilio keys go in here too; easier via admin UI in step 5b.
  stripe_secret_key     = 'sk_live_...',
  stripe_publishable_key= 'pk_live_...',
  stripe_webhook_secret = 'whsec_...',
  twilio_account_sid    = 'AC...',
  twilio_auth_token     = '...',
  twilio_from_number    = '+1306...',
  sendgrid_api_key      = 'SG...',
  cloudinary_cloud_name = '...',
  cloudinary_api_key    = '...',
  cloudinary_api_secret = '...'
WHERE id = 1;
```

### 5b. Easier: do this from the admin dashboard after backend deploys

After [`03-admin-vercel.md`](./03-admin-vercel.md) is done, log in
and go to **Settings**. Paste each value there. The admin UI writes
to the same `settings` row via a backend endpoint.

---

## Step 6 — Create storage bucket for driver documents

Supabase dashboard → **Storage** → **New bucket**.

- **Name:** `driver-documents`
- **Public:** OFF. The backend generates signed URLs for access.
- **Allowed MIME types:** `image/jpeg`, `image/png`, `image/webp`,
  `application/pdf`.
- **Size limit:** 10 MB.

The backend reads the bucket name from the `STORAGE_BUCKET` env var
(default `driver-documents`). Keep the name consistent.

---

## Step 7 — Enable PITR (Point-in-Time Recovery)

Supabase dashboard → **Database** → **Backups**.

Enable PITR. Retention window: **7 days** is the minimum for
acceptable RTO; 14 days preferred. Note the estimated monthly cost —
it's usage-based.

Write down the time of the first successful backup. You'll want it
for the disaster-recovery drill (Phase G.7 in the checklist).

---

## Step 8 — Enable logging

Supabase dashboard → **Project Settings** → **Logs**.

- Postgres Logs: ENABLE.
- API logs: ENABLE (catches unauthorized queries from leaked keys).
- Retention: minimum 7 days on Pro.

---

## Step 9 — Lock down dashboard access

Supabase dashboard → **Organization Settings** → **Team**.

- Require 2FA for every team member.
- Prune any personal accounts. Each admin should have their own
  account — never share credentials.

---

## Done when…

- [ ] `psql "$DATABASE_URL" -c "SELECT 1"` succeeds.
- [ ] `SELECT count(*) FROM schema_migrations` returns 26 (or the
      current migration count).
- [ ] RLS coverage query in §4f returns 0 rows.
- [ ] `SELECT terms_of_service_text IS NOT NULL AND length(terms_of_service_text) > 100
      FROM settings WHERE id=1` returns `t`.
- [ ] PITR is enabled.
- [ ] `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `DATABASE_URL` are
      all in your vault.

Next: [`05-third-party-services.md`](./05-third-party-services.md) to
stand up Firebase, Stripe, Twilio, Redis, etc.
