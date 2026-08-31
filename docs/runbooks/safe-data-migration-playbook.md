# Safe Data Migration Playbook

**Why this exists:** the driver bank/SIN + CSV migration (PR #3918, 2026-08-14) is the case
study for almost everything in this document. One migration produced *two* separate PII
exposures, discovered two weeks apart, from two different root causes:

1. **Git exposure** (#4547, #4596): the staging scripts (`driver_bank_sin_migration.sql`,
   `driver_csv_migration.sql`) were committed to the repo root with real plaintext SIN, bank
   details, and contact/license data for 346 driver records, live on a **public** repo for
   11+ days before anyone noticed.
2. **Database exposure** (found 2026-08-31, same incident): the staging tables the scripts
   created (`driver_bank_import`, `driver_csv_import`) were still sitting in production
   afterward — never dropped — **with Row Level Security disabled**, meaning the same data
   was separately readable live over the public API the whole time, independent of git.

Neither failure required anyone to do anything malicious or even unusual — both were the
predictable result of "get the migration done" not including "and then clean up after it" as
a required, checked step. This playbook exists so the next migration doesn't repeat either
failure. See `docs/audit/breach-record.md` Incident 1 for the full record of what happened.

This is a checklist, not a gate to slow migrations down — a migration that follows it is not
slower than the one that caused this incident, it just doesn't leave anything behind.

---

## 1. Before you start — classify the data

Treat as PII by default, not as an exception: names, emails, phone numbers, government IDs
(SIN, driver's license), financial account/routing numbers, dates of birth, home addresses,
precise GPS coordinates. "It's just for a one-time migration" is not an exemption — migrated
data has the same protection obligations as data entered through the app.

## 2. Decide what kind of change this is, up front

| If the change is... | It goes here | PII rule |
|---|---|---|
| A permanent, repeatable schema change | `backend/migrations/NN_*.sql`, following `backend/migrations/CLAUDE.md` | Never contains literal PII rows — schema/DDL only |
| A one-time backfill/cutover from an external source | A local script, **never committed with real data** | See §3–5 below |

If you're not sure which one you're writing, it's the second kind — a numbered migration
file is for schema, not for one-time data loads.

## 3. Staging the source data

- Read from a local file (CSV/JSON export), not data hardcoded into the script. The repo's
  root `.gitignore` already has a blanket `*.csv` rule for exactly this reason — use it,
  don't work around it.
- Never `git add` the file the export lives in, and never paste real rows into a script you
  intend to commit. If the script is worth keeping for reproducibility, commit it with the
  data redacted to synthetic values — the *logic*, not the *rows*.
- If a database staging table is unavoidable (it usually is, for matching/joining against
  existing records):
  - **Enable Row Level Security on it at creation time, in the same statement block that
    creates the table.** Not "later," not "before we go live" — at creation. This is the
    single change that would have prevented the second half of this incident.
    ```sql
    CREATE TABLE driver_migration_staging (...);
    ALTER TABLE driver_migration_staging ENABLE ROW LEVEL SECURITY;
    -- no policies needed — RLS-with-no-policy is this schema's existing convention for
    -- "service_role only," see e.g. driver_crc_consents, email_send_log.
    ```
  - Name it so it's unmistakably temporary — a `_staging` or `_migration_scratch` suffix,
    not a name indistinguishable from a permanent table.
  - Never grant it to `anon` or `authenticated`. The backend's `service_role` bypasses RLS
    by design and doesn't need a grant to use it.

## 4. Running the migration

- Test against a small sample first — a Supabase branch or dev project, not production
  directly, if one is available for the size of change.
- Never log or print full PII values at any point (this is already a repo-wide rule — see
  root `CLAUDE.md`'s PIPEDA logging section — it applies just as much to a one-off script's
  console output as to application code).
- Sensitive fields destined for `users`/`drivers` (SIN, license number) go through the
  existing `encrypt_driver_pii()` Vault RPC on the way in — never write them to a permanent
  column in plaintext.

## 5. Immediately after the migration finishes — the step that failed here

Treat this as part of the migration, not a follow-up task for later:

1. **Drop the staging table(s) in the same sitting as confirming the migration succeeded.**
   If the script's own comments say "run this cleanup step when satisfied" (as both incident
   scripts' did), *run it* before moving on to anything else — a cleanup step that depends on
   someone remembering to come back to it later is the exact failure mode this incident
   demonstrates.
2. Delete the local export file(s) used, once no longer needed.
3. **Check for any table with RLS disabled that shouldn't be** — ask whoever has Supabase
   access to run a table listing and look at the RLS column, or use the Supabase security
   advisor directly. This takes under a minute and would have caught this incident's second
   half immediately.
4. Confirm nothing from the script itself was committed with real data — `git status`,
   `git diff --cached`, and a second look before pushing.

## 6. Ongoing / automatic safeguards already in place — keep maintaining them

- **`.gitleaks.toml`**: `spinr-sin-bank-pii` and `spinr-driver-export-pii` catch PII-shaped
  content in new commits. These are gated on the *specific* column/table identifiers from
  this incident — a future migration with a different PII shape (e.g. a different set of
  column names) needs its own rule added, the same way these two were. Don't assume the
  existing rules generalize to a shape they weren't written for.
- **The pre-commit hook's PII-in-diff scan** — catches PII in newly-staged content, not
  pre-existing tracked files, so it's not a substitute for §3 above.
- **`backend/migrations/CLAUDE.md`'s "One-off data-migration scripts" section** — the
  canonical convention for this class of script; this playbook is the fuller lifecycle
  version of the same rule, cross-linked from there.
- **A quick RLS check after any migration** (§5.3 above) is cheap enough to make a habit of,
  not just an incident-response step.

## TL;DR checklist

- [ ] Classified every field as PII or not, treating anything ambiguous as PII
- [ ] Confirmed this is a one-off script, not a schema change (or vice versa) — filed in the right place
- [ ] Source data read from a gitignored local file, never hardcoded into a committed script
- [ ] Any staging table created with RLS enabled **at creation**, `service_role`-only
- [ ] No PII values printed/logged during the run
- [ ] Sensitive fields routed through `encrypt_driver_pii()` on the way into permanent tables
- [ ] Staging table(s) dropped and local export file(s) deleted **immediately after success**,
      not deferred
- [ ] Re-checked table RLS status after the migration, not just before
- [ ] `git status`/`git diff --cached` checked clean of real data before any push
