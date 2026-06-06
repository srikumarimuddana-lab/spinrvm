# Runbook — Supabase Region Migration (US → `ca-central-1`)

**Owner:** `infra` + `compliance` · **Trigger:** Supabase project is not in a Canadian region
**Closes:** PIPEDA data-residency gap (`docs/vendor-inventory.md` "Supabase region — VERIFY")
**Regs:** PIPEDA 22-2 (data residency), Saskatchewan Transportation Act

---

## Why this runbook exists

Supabase **cannot change the region of an existing project** — Postgres has no
cross-region move. The only supported path is to **create a new project in
`ca-central-1`, migrate into it, cut over, and decommission the US project.**

This is not optional housekeeping. `backend/core/config.py` (`_guard_production_secrets`)
**refuses to boot in production** unless `SUPABASE_REGION` starts with `ca-`. PIPEDA
requires primary storage in Canada; a US project storing rider/driver PII is a
residency violation. Moving *into* Canada is the compliant direction — but the
migration itself handles PII, so it is a `compliance`-supervised event.

> The "Pause & Restore" region shortcut in the Supabase dashboard is **Free-tier
> only** and does not apply to a paid production project. Use one of the two paths
> below.

References: [Migrating within Supabase](https://supabase.com/docs/guides/platform/migrating-within-supabase) ·
[Change Project Region](https://supabase.com/docs/guides/troubleshooting/change-project-region-eWJo5Z) ·
[Backup/Restore via CLI](https://supabase.com/docs/guides/platform/migrating-within-supabase/backup-restore)

---

## Pick your path

| Situation | Path | Why |
|---|---|---|
| **Pre-launch / test data only** (current state) | **A — Fresh project** | No production PII to preserve. Stand up a clean `ca-central-1` project, run migrations, discard the US data. Lowest risk — no dump, no PII-in-transit. |
| **Live with real rider/driver PII** | **B — Data migration** | Must preserve data. Dump/restore (or logical replication) **plus** a cross-project re-encryption of pgsodium PII. Treat as a P1 compliance operation. |

When in doubt, confirm whether the US project holds real PII before choosing — see
"Verify current state" at the bottom.

---

## What lives in the US project (full inventory)

Do not assume "the database" is the whole job.

| Component | Moves how |
|---|---|
| Postgres schema + data — incl. `app_settings` (Stripe/Twilio/Maps keys), `stripe_events`, `schema_migrations`, all `SECURITY DEFINER` RPCs | Path A: re-run migrations fresh. Path B: `pg_dump`/`supabase db dump` → restore, or logical replication |
| ⚠️ **Encrypted driver PII** — `drivers.license_number`, `vehicles.vin` → `vault.secrets` UUIDs under pgsodium key `drivers_pii_key` | **Never via `pg_dump`.** See landmine #1 |
| Extensions: `pgsodium`, `pgcrypto` | Enable on the target **before** restore / first migration |
| Storage buckets: `driver-documents`, `kyb-documents` (private), `ride-snapshots` (public) | Recreate bucket + RLS; Path B also copies objects (`backend/docs/STORAGE_BUCKETS.md`) |
| Realtime (rider/driver apps subscribe directly with the anon key) | Recreate; new anon key → landmine #3 |
| Supabase **Auth** / `auth.users` / OAuth | **Nothing** — Spinr uses its own JWT auth (custom `users` table, OTP, refresh tokens). Verify, but there is no GoTrue user table to move |
| `pg_cron` / DB-scheduled jobs | **Nothing** — the 16 background loops are app-side asyncio, not in the DB |
| Cloudinary assets | External to Supabase — unaffected |

---

## Landmines (read before touching anything)

**1. A naïve `pg_dump` destroys encrypted driver PII.** `backend/migrations/32_encrypt_sensitive_fields.sql`
stores `license_number` / `vin` as `vault.secrets` UUIDs encrypted by **pgsodium**
under the project-scoped key `drivers_pii_key`. The pgsodium **root key is held
per-project by Supabase and is not in a normal dump.** Restore the ciphertext into a
new project and `vault.decrypted_secrets` returns garbage — PII is permanently lost.
The migration is effectively a **cross-project key rotation**: decrypt on the source
via `decrypt_driver_pii()`, move plaintext over a secure backend channel, re-encrypt
on the target via `encrypt_driver_pii()` (mints fresh `vault.secrets` under the *new*
project's `drivers_pii_key`). Reuse the logic in `backend/scripts/rotate_pii_key.py`
(see `docs/runbooks/pii-key-rotation.md`), pointed at two projects instead of one.
*(Path A skips this entirely — there is no PII to preserve.)*

**2. New project = new keys → update them in every place, identically.** New
`SUPABASE_URL`, service-role key, and anon key. Per `docs/runbooks/railway-fly-failover.md`
the backend secrets live on **both Railway and Fly** — a mismatch is a split-brain
outage. And **set `SUPABASE_REGION=ca-central-1`** on both or production won't boot
(`config.py` guard; already present in `backend/fly.toml`). See the env matrix below.

**3. Shipped mobile builds embed the old anon key + URL for Realtime.** Installed
rider/driver apps subscribe to the *old* project's Realtime via
`EXPO_PUBLIC_SUPABASE_ANON_KEY`. Primary live transport is the backend WebSocket
(`routes/websocket.py` + Redis), so the WS fallback covers ride status / location —
confirm that before deleting the US project, or ship an app update first. *(Pre-launch:
no installed builds in the wild — non-issue.)*

**4. Don't truncate `schema_migrations` (Path B).** It dumps with the data, so
`backend/scripts/migrate.py` (full-filename idempotency key, highest is `135`) won't
re-run every migration. Preserve it.

**5. `ride-snapshots` URLs are absolute (Path B).** `rides.route_snapshot_url` and
already-sent receipt emails hold full URLs at the *old* project's public bucket host.
Decommission the US project and those images 404. Keep the old public bucket alive
read-only, or rewrite stored URLs to the new host.

**6. Stripe needs no dashboard change.** Webhooks target `api-spinr.spinr.ca`, not
Supabase, and `stripe_events` carries the dedupe table — so idempotency survives.
Just ensure only one project processes webhooks during any dual-run window.

---

## Path A — Fresh project (recommended pre-launch)

A clean Canadian project; the US test data is discarded.

### 1. Provision
- [ ] Create a new Supabase project in **`ca-central-1`**.
- [ ] Confirm `pgsodium` and `pgcrypto` are enabled (Database → Extensions). pgsodium
      is on by default for projects created after 2023-06.

### 2. Schema + RPCs + PII key
- [ ] Point `PG_CONNECTION_STRING` / `SUPABASE_*` at the **new** project.
- [ ] Run the ordered migrations from repo root:
      ```bash
      cd backend && python scripts/migrate.py --env production
      ```
      This creates `schema_migrations`, applies `00 … 135` in order, and — via
      migration `32` — creates the `drivers_pii_key` pgsodium key and the
      `encrypt_driver_pii` / `decrypt_driver_pii` RPCs.

### 3. Storage buckets
- [ ] Recreate the three buckets and their RLS (`backend/docs/STORAGE_BUCKETS.md`):
      `driver-documents` (private), `kyb-documents` (private), `ride-snapshots` (**public**).

### 4. App settings
- [ ] Re-enter Stripe / Twilio / Google Maps keys in the admin dashboard (they live in
      the `app_settings` table, which starts empty on a fresh project).

### 5. Cut over env (see matrix) → smoke test → decommission
- [ ] Flip the env vars in the matrix below, redeploy, run the verification checklist,
      then **delete the US project and its backups**.

---

## Path B — Migrate existing data (production with real PII)

Use a maintenance window (dump/restore) or logical replication for near-zero downtime.

### 1. Provision + extensions
- [ ] New `ca-central-1` project; enable `pgsodium`, `pgcrypto` **before** restore.

### 2. Schema + data (everything except encrypted PII)
- [ ] `supabase db dump` (or `pg_dump`) the source; restore into the target. For large
      data, dump schema and data separately and parallelize `pg_restore`.
- [ ] **Preserve `schema_migrations`** so `migrate.py` does not re-run migrations.
- [ ] Null out / skip the encrypted columns in this pass — they are handled in step 3.

### 3. Re-encrypt PII across projects (the landmine)
- [ ] Snapshot `pgsodium.valid_key` on **both** projects to
      `reports/compliance/key-rotation/YYYY-MM-DD-region-migration.csv`.
- [ ] For each driver/vehicle row: `decrypt_driver_pii(old_uuid)` on the **source**,
      carry plaintext over a secure backend channel (TLS, ephemeral, audit-logged),
      `encrypt_driver_pii(plaintext)` on the **target**, store the new UUID. Reuse
      `backend/scripts/rotate_pii_key.py` batching; dry-run against staging first.
- [ ] Confirm row counts match and no row stays unreadable.

### 4. Storage objects
- [ ] Copy every object in `driver-documents`, `kyb-documents`, `ride-snapshots` to the
      new buckets (Supabase CLI / Storage API / `rclone`). Recreate bucket RLS.
- [ ] Decide `ride-snapshots` strategy (landmine #5): keep old bucket read-only, or
      rewrite `rides.route_snapshot_url`.

### 5. Cut over env → verify → decommission
- [ ] Freeze writes, do a final delta sync, flip env (matrix below), run verification,
      then decommission the US project (after `compliance` sign-off).

---

## Env-var cutover matrix

Every surface that holds Supabase credentials gets the **new** project's values.

| Surface | Where set | Vars |
|---|---|---|
| Backend (Railway) | Railway env | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_REGION=ca-central-1` |
| Backend (Fly) | `fly secrets set` | same three — **identical** to Railway (`railway-fly-failover.md`) |
| Rider + Driver apps | EAS build env | `EXPO_PUBLIC_SUPABASE_URL`, `EXPO_PUBLIC_SUPABASE_ANON_KEY` |
| Admin dashboard | Vercel env | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` (if used) |

> Backend `JWT_SECRET`, `ADMIN_*`, Firebase, and Redis values **do not change** —
> only the Supabase trio moves. Keep everything else identical across Railway and Fly.

---

## Verification (before decommissioning the US project)

- [ ] `/health` is green on both Railway and Fly against the new project.
- [ ] Backend boots — the `SUPABASE_REGION=ca-central-1` guard passes (proves region).
- [ ] Auth: OTP send + verify, token refresh.
- [ ] Ride flow: search → driver accept → in_progress → completed, with live WS updates
      to both rider and driver.
- [ ] Driver doc upload writes to `driver-documents` and a signed URL renders.
- [ ] A completed ride uploads to `ride-snapshots` and the receipt email image loads.
- [ ] Driver PII (`license_number`, `vin`) decrypts correctly (Path B only).
- [ ] A Stripe **test** webhook processes exactly once (idempotent).
- [ ] Realtime subscription in the apps works, or the WS fallback fully covers it.

---

## Decommission (residency is only achieved here)

- [ ] `compliance` sign-off recorded.
- [ ] **Delete the US project and its PITR backups**; capture written confirmation from
      Supabase. Until this is done, PII still resides in the US.
- [ ] The dump file / any plaintext-PII export is a regulated artifact: encrypted at
      rest, TLS in transit, ephemeral, deleted after, never on a personal laptop.
- [ ] Update `docs/vendor-inventory.md` — flip the Supabase region from "VERIFY" to
      confirmed `ca-central-1` with the migration date.

---

## Safety checks

- `SUPABASE_REGION=ca-central-1` set on **both** backends or production refuses to boot.
- Railway and Fly Supabase secrets are **identical** — a mismatch is split-brain.
- pgsodium `drivers_pii_key` exists on the target **before** any encrypt/decrypt call
  (migration 32 creates it).
- Never `pg_dump` `vault.secrets` and expect it to decrypt in the new project.
- Only one project processes Stripe webhooks at a time during any dual-run window.

---

## Related runbooks

- `docs/runbooks/pii-key-rotation.md` — the re-encryption mechanics reused in Path B
- `docs/runbooks/pitr-restore.md` — recovery if a PII row becomes unreadable
- `docs/runbooks/railway-fly-failover.md` — backend secret/cutover topology
- `docs/runbooks/data-breach.md` — if PII is exposed during the move
- `backend/docs/STORAGE_BUCKETS.md` — bucket names, visibility, and RLS

---

## Verify current state (if unsure whether real PII exists)

- [ ] Supabase dashboard → Settings → General → **Region** (confirms US vs `ca-central-1`).
- [ ] `SELECT count(*) FROM drivers WHERE license_number IS NOT NULL;` and the same for
      `vehicles.vin` — non-trivial counts of real drivers mean Path B + `compliance`.
- [ ] Check whether `app_settings` holds **live** Stripe keys (`sk_live_…`) vs test
      keys (`sk_test_…`) — live keys imply real traffic.
