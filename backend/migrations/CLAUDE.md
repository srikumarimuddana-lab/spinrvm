# Migration Conventions

Migrations live in `backend/migrations/` and are applied in filename order by `backend/scripts/run_migrations.py`. A second runner, `backend/scripts/migrate.py`, used to exist targeting an older `schema_migrations` shape that was never the one actually applied to production — it has been deleted; its one useful piece (CONCURRENTLY-safe SQL splitting) was ported into `run_migrations.py` first. See the root `CLAUDE.md`'s Database Migrations section and `ACTION_ITEMS.md` A39.

Naming: `NN_short_description.sql` where `NN` is a zero-padded sequence number — check the current highest with `ls backend/migrations | sort -V | tail -1` before picking the next one. Pick the next available number — never reuse or reorder existing numbers. If two PRs conflict on a number, the second one renames to the next free slot before merge. Note: the runner uses the full filename as the idempotency key, so already-applied migrations must never be renamed. Duplicate numeric prefixes exist from history and are handled by full-filename keying — do not introduce new duplicates; a CI prefix-uniqueness check blocks them.

Migration rules:
- **Append-only**: never edit a merged migration. Schema changes go in a new file.
- **Forward-compatible**: every migration must be safe to run against production traffic in flight. Wrap long-running `ALTER TABLE` in batched updates.
- **Always reversible on paper**: put the rollback plan in a top comment, even if no down-migration file.
- **RLS first**: every new table that stores user data must ship with RLS policies in the same migration.
- **Indexes for new query patterns**: if you add a `WHERE foo = ?` or `ORDER BY foo`, add the index in the same migration.

Table naming:
- Lowercase, snake_case, plural (`rides`, `drivers`, `corporate_allowances`)
- Junction tables: `<a>_<b>` alphabetical (`corporate_member_rides`)
- Audit tables: `<entity>_audit` or `<entity>_events` (append-only, no updates)

RLS policy pattern:
- Every user-data table has `SELECT` restricted to `auth.uid() = user_id` or role-based equivalents
- `INSERT` / `UPDATE` / `DELETE` explicitly enumerated — never `FOR ALL` on user-writable tables
- Service role (backend) bypasses RLS by design; the frontend anon key must never touch user data directly

Postgres functions for mutating money or credits: call from backend only, never from client. All money-touching functions must be `SECURITY DEFINER` with explicit `search_path` pinning.

## One-off data-migration scripts (not part of backend/migrations/)

A one-time backfill/import script (e.g. migrating records from a legacy system) is not a
schema migration and does not belong in `backend/migrations/` — but it must **never** be
committed to the repo with real production PII in it, plaintext or otherwise. (2026-08-14,
PR #3918: `driver_bank_sin_migration.sql` / `driver_csv_migration.sql` were committed at the
repo root with real, plaintext SIN, bank account/transit/institution numbers, DOBs, and home
addresses for 157 drivers, plus names/phones/emails/license numbers for 189 more — live on
`main`, publicly readable, for 11 days before discovery. See #4547.)

Rules for this class of script:
- **Never commit real production data.** Run it locally against a scratch/throwaway
  connection, or on a branch that is never pushed, and discard it once the import is done.
  If the script itself is worth preserving for audit/reproducibility, commit it with the
  data redacted to synthetic/placeholder values — never the real rows.
- If the script must be built from a real export file rather than generated inline, read
  from a CSV/JSON dump instead of hardcoding rows — the root `.gitignore`'s blanket `*.csv`
  rule already exists precisely to keep that class of file out of git.
- Sensitive columns (SIN, bank account/transit/institution number, government ID, DOB
  outside `users`/`drivers`' normal fields) reach `drivers`/`users` only via the existing
  `encrypt_driver_pii()` Vault RPC. A local staging table holding the plaintext value in
  between is fine — the SQL file that *populates* that staging table with real values is
  the thing that must never reach git history.
- CI has a corresponding backstop (`spinr-sin-bank-pii` rule in `.gitleaks.toml`) that flags
  files naming a SIN/bank-account column identifier alongside bare 9-digit literals — but
  that is a safety net for exactly this mistake, not a substitute for not making it.
