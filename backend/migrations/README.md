# Database Migrations

This directory holds append-only SQL migrations for the Spinr Supabase
backend. Resolved from the production-readiness audit finding P0-B4
(2026-04).

## Convention

1. **Filename format**: `NN[suffix]_short_snake_case_name.sql` where
   `NN` is a **zero-padded two-digit integer** that increases
   monotonically. Example: `22_stripe_events.sql`.
2. **Suffixes** (`10b_…`) are reserved for the rare case where two
   migrations legitimately shared a numeric prefix historically and
   renumbering would break provenance. New migrations must use a fresh
   two-digit prefix — do **not** introduce a suffix.
3. **Lexicographic sort == application order.** The runner applies
   files in `sorted()` order; `10_foo.sql` < `10b_foo.sql` < `11_foo.sql`.
4. **Append-only.** Once a migration has been applied to any shared
   environment (staging, prod), never edit it. The runner detects a
   checksum mismatch and refuses to proceed. To amend a schema, write a
   new migration.
5. **Idempotency.** Prefer `CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF
   NOT EXISTS`, and guarded `DO $$ … $$` blocks so re-applying a
   migration (e.g. on a partial-failure replay) is safe.

## Applying migrations

Use the runner in `backend/scripts/run_migrations.py`:

```bash
export DATABASE_URL='postgres://postgres.<ref>:<service-role-pw>@aws-0-<region>.pooler.supabase.com:6543/postgres'

# See what's pending.
python -m backend.scripts.run_migrations --status

# Dry-run (no DB writes).
python -m backend.scripts.run_migrations --dry-run

# Apply all pending migrations.
python -m backend.scripts.run_migrations
```

The runner is idempotent: each file is wrapped in a transaction that
commits the SQL changes together with an `INSERT` into
`schema_migrations`, so either both land or neither does. A subsequent
run skips files whose filename + checksum already appear in the
tracking table.

### Bootstrapping an existing environment

If you're pointing the runner at a database that already has migrations
`01…23` applied (via the legacy "paste into Supabase SQL Editor"
workflow), manually INSERT the pre-existing filenames once so the
runner knows to skip them:

```sql
INSERT INTO schema_migrations (filename, checksum) VALUES
  ('01_add_driver_user_id.sql', '<sha256>'),
  ('02_dynamic_documents.sql',  '<sha256>'),
  -- …through 23_profile_image_url.sql
;
```

The sha256 of each file is printed by the runner's `--status` output;
copy those values into the bootstrap `INSERT`.

## Follow-up

Full Alembic adoption (down-revisions, autogenerate, branch
resolution) is tracked as **P1 work item 0.6** in
`docs/audit/production-readiness-2026-04/09_ROADMAP_CHECKLIST.md`.
The current runner is deliberately small — it solves the P0 concern
(deterministic ordering + provenance) without introducing a new
framework.

## Historical notes

* `10_disputes_table.sql` and `10b_service_area_driver_matching.sql`
  both started life as `10_…`. The duplicate was resolved on
  2026-04-14 by renaming the second to `10b_` so lexicographic sort is
  total. Both files may already be applied in long-running environments;
  the runner's checksum check will flag drift if either was locally
  modified.
* `01_add_driver_user_id.sql` was previously `001_…` (three-digit,
  inconsistent with the rest). Renamed on 2026-04-14 for consistency.
* `23_profile_image_url.sql` was previously `add_profile_image_url.sql`
  (no prefix). Renamed on 2026-04-14 so it sorts into its historical
  position at the end of the sequence.
