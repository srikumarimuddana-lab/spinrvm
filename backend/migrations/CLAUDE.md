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
