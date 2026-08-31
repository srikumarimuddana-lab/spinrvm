# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude (agent session, on behalf of vikas@ngitservices.com) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides (rider PII table), safety-adjacent (RLS hardening) |
| PR / commit link | (local commit — not yet pushed/PR'd) |
| Related issue or gap ID | `ACTION_ITEMS.md` B40 |

## 1. Issue / gap identified

`saved_addresses` (rider home/work address book) has RLS **enabled** but **zero
policies** — confirmed directly against production (`pg_policy` returns zero
rows for `saved_addresses`, `pg_class.relrowsecurity = true`). This means
`anon`/`authenticated` PostgREST roles are fully denied by default (fail-closed
— not a live vulnerability), while every other user-data table in this repo
carries an explicit owner-scoped policy as defense-in-depth.

## 2. Root cause

`saved_addresses` was created directly in `backend/supabase_schema.sql`
(`ALTER TABLE saved_addresses ENABLE ROW LEVEL SECURITY;`, line 384) without
ever getting a companion `CREATE POLICY` statement — unlike `users`/`drivers`/
`rides` (`backend/supabase_rls.sql`), `emergency_contacts` (migration 120), or
`disputes` (migration 142), all of which shipped RLS-enable and policy
creation together per `backend/migrations/CLAUDE.md`'s "RLS first" rule. This
gap predates the migrations directory's current convention discipline and was
never caught because `routes/addresses.py` has always used the service-role
key, which made the missing policies invisible to app-level testing.

## 3. Fix / remediation

New migration `backend/migrations/378_saved_addresses_rls_policies.sql` adds
three idempotent (`pg_policies`-guarded) policies:

- `saved_addresses_owner_select` — `FOR SELECT USING (auth.uid()::text = user_id)`
- `saved_addresses_owner_insert` — `FOR INSERT WITH CHECK (auth.uid()::text = user_id)`
- `saved_addresses_owner_delete` — `FOR DELETE USING (auth.uid()::text = user_id)`

**Column type / cast:** `saved_addresses.id` and `.user_id` are both `TEXT`
(`backend/supabase_schema.sql:231-232`, `id TEXT PRIMARY KEY`, `user_id TEXT
NOT NULL REFERENCES users(id)`) — not `UUID`. The policy therefore casts
`auth.uid()::text = user_id`, matching the existing `emergency_contacts`
(migration 120) and `disputes` (migration 142) TEXT-column pattern, not the
uncast `auth.uid() = user_id` pattern used for UUID-typed owner columns
(`driver_bonuses` migration 179, `corporate_members` migration 142).

**No UPDATE policy** — deliberate, matching B40's own acceptance criteria
(`SELECT`/`INSERT`/`DELETE` only) and the `emergency_contacts` precedent,
which also ships no UPDATE policy. `routes/addresses.py` has no PUT/PATCH
endpoint for this table (only `GET` / `POST` / `DELETE` — see file listing
below); there is no "edit a saved address" flow to cover, so no UPDATE policy
was added speculatively (CLAUDE.md: simplicity first, no speculative
configurability).

Bonus (beyond the four numbered deliverables, but directly serving
verification item 5/6 below): added `backend/tests/rls/test_saved_addresses_rls.py`
to the repo's existing DB-role-level RLS test tier
(`backend/tests/rls/`, added 2026-08-31 per
`docs/change-log/2026-08-31-rls-role-level-test-coverage.md`) — 9 new tests
exercising the allow/deny paths for all three new policies plus a
service-role-bypass confirmation, and extended `backend/tests/rls/conftest.py`
to build the `saved_addresses` table + apply migration 378 in its fixture
(alongside the existing `users`/`drivers`/`rides`/`financial_events`/
`driver_insurance_periods` coverage). This directly satisfies CLAUDE.md's
Testing Conventions line ("Every auth/RLS policy — both allowed and denied
paths — must have a test") for the exact gap this migration closes, using
infrastructure that already existed for this purpose.

## 4. Risk & impact on existing functionality

**Blast radius: isolated, checked directly.**

- `backend/routes/addresses.py` is the **only** in-repo module that reads or
  writes `saved_addresses`. Its three endpoints:
  - `GET /addresses` → `db_supabase.get_rows("saved_addresses", {"user_id": current_user["id"]}, ...)`
  - `POST /addresses` → `db_supabase.insert_one("saved_addresses", address.dict())`
  - `DELETE /addresses/{address_id}` → `db_supabase.delete_one("saved_addresses", {"id": address_id, "user_id": current_user["id"]})`
  All three go through `db_supabase` → `repositories/_base.py`'s generic CRUD
  helpers, which are backed by the `supabase` client instantiated in
  `backend/supabase_client.py` with `SUPABASE_SERVICE_ROLE_KEY`
  (`supabase_client.py:11`, `SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")`,
  `create_client(SUPABASE_URL, SUPABASE_KEY)` at line 20). The Supabase
  service-role key bypasses RLS entirely by design — this is the same
  mechanism every other RLS-locked table in this repo already relies on for
  backend access (`docs/change-log/2026-08-31-rls-role-level-test-coverage.md`'s
  `test_service_role_bypasses_rls_on_users`/`..._on_rides` confirm this
  behavior for the sibling tables; this change adds the equivalent
  `test_service_role_bypasses_rls_on_saved_addresses` test for this table).
  RLS policies (present, absent, or changed) have **zero effect** on this
  code path.
- Grepped `rider-app/` and `driver-app/` for direct `saved_addresses`
  references: none — neither app talks to Supabase/PostgREST directly for
  this table (or at all; both go through the FastAPI backend).
- Migration 373 (`saved_addresses_legacy_import_metadata`) already documented
  this exact RLS gap in its own header comment as "flagged separately as its
  own finding, not silently fixed here" — this migration is that follow-up.
- No other migration, background loop, or admin route writes to
  `saved_addresses` outside the PIPEDA purge functions (migrations 216, 228,
  285, 289, 296, 321, 323, 324, 335 — all `DELETE FROM saved_addresses WHERE
  user_id = v_uid` inside `SECURITY DEFINER` Postgres functions, which also
  bypass RLS as the function owner unless explicitly scoped otherwise — these
  are unaffected by adding SELECT/INSERT/DELETE policies for the
  `authenticated` role).
- **New test infra dependency**: `backend/tests/rls/conftest.py`'s
  session-scoped `pg_conn` fixture now also builds `saved_addresses` and
  applies migration 378. This fixture is shared with the existing
  `test_core_tables_rls.py` and `test_money_and_safety_rls.py` files — ran
  the full `tests/rls` suite after the change (see §9) to confirm no
  regression to the other 32 pre-existing tests in that tier.

## 5. User-experience effect

None. No rider/driver/corporate-admin/internal-admin-facing change. Not
visible mid-session to anyone — `routes/addresses.py`'s request/response
shape, status codes, and behavior are byte-for-byte unchanged; this migration
touches only Postgres-level RLS policies that the app's current access path
(service-role key) never evaluates.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/378_saved_addresses_rls_policies.sql` | New: idempotent `CREATE POLICY` for SELECT/INSERT/DELETE on `saved_addresses`, scoped `auth.uid()::text = user_id` | Close ACTION_ITEMS.md B40 |
| `backend/tests/rls/conftest.py` | Extended `pg_conn` fixture to create `saved_addresses` (extracted verbatim from `supabase_schema.sql`) and apply migration 378; added `saved_addresses` to `pg_cur`'s per-test TRUNCATE list | Enable DB-role-level RLS tests for the new policies |
| `backend/tests/rls/test_saved_addresses_rls.py` | New: 9 tests covering owner SELECT/INSERT/DELETE allow paths, cross-user deny paths, anon deny, and service-role bypass | Testing Conventions: every RLS policy needs an allowed + denied path test |
| `ACTION_ITEMS.md` | B40 marked done, linked to this migration and log | Close the tracked backlog item |

No application code (`routes/addresses.py`, `db_supabase.py`,
`repositories/_base.py`) was touched — this is a pure RLS-policy addition.

## 7. Before / after

Before (production, confirmed via `pg_policy`):

```sql
-- saved_addresses: RLS enabled, ZERO policies
-- anon: fully denied (fail-closed)
-- authenticated: fully denied (fail-closed)
-- service_role: bypasses RLS regardless (routes/addresses.py's actual path)
```

After (migration 378):

```sql
CREATE POLICY saved_addresses_owner_select
    ON saved_addresses FOR SELECT
    USING (auth.uid()::text = user_id);

CREATE POLICY saved_addresses_owner_insert
    ON saved_addresses FOR INSERT
    WITH CHECK (auth.uid()::text = user_id);

CREATE POLICY saved_addresses_owner_delete
    ON saved_addresses FOR DELETE
    USING (auth.uid()::text = user_id);
```

`routes/addresses.py` is unchanged — this before/after is purely at the
Postgres RLS layer, not an application behavior change.

## 8. Rollback plan

No code to revert (pure SQL migration + test-only changes). If the policies
ever needed to be undone (e.g. a future direct-PostgREST integration hit an
unexpected denial and needs re-diagnosis):

```sql
DROP POLICY IF EXISTS saved_addresses_owner_select ON saved_addresses;
DROP POLICY IF EXISTS saved_addresses_owner_insert ON saved_addresses;
DROP POLICY IF EXISTS saved_addresses_owner_delete ON saved_addresses;
```

This reverts to the exact current production state (RLS enabled, zero
policies, anon/authenticated fully denied). No data is touched by either the
migration or its rollback. `git revert` of the test-file changes is
sufficient and safe (test-only, no data-consistency concern).

## 9. Verification performed

- [x] Migration applied against a scratch local Postgres 16 instance
  (`sudo -u postgres psql -d spinr_migration_scratch -f 378_saved_addresses_rls_policies.sql`,
  with stub `auth.uid()`/`users`/`saved_addresses` matching
  `backend/supabase_schema.sql`'s real shape) — applied without error, and a
  **second** run was byte-idempotent (no error, no duplicate policy rows —
  confirmed via `pg_policies`).
- [x] Manual behavioral verification in the same scratch DB (raw psql, before
  writing the automated test below): as `authenticated` with
  `request.jwt.claim.sub` set to user-a's id, `SELECT` returned only
  user-a's row; an `INSERT` with `user_id = 'user-b'` was rejected with
  `42501: new row violates row-level security policy for table
  "saved_addresses"`; a `DELETE` targeting user-b's row affected 0 rows
  (RLS hides it, not an error) and the row was confirmed still present via a
  subsequent service-role (superuser) `SELECT`. Scratch database dropped
  after verification.
- [x] **Automated tests written and run**: new
  `backend/tests/rls/test_saved_addresses_rls.py` (9 tests) — with
  `TEST_DATABASE_URL` pointed at a local scratch Postgres 16 instance,
  `python3 -m pytest tests/rls/test_saved_addresses_rls.py -c /dev/null
  --confcutdir=tests/rls -v` → **9/9 passed**. Full `tests/rls` suite
  (including the pre-existing `test_core_tables_rls.py` and
  `test_money_and_safety_rls.py`) re-run after the `conftest.py` change →
  **41/41 passed**, confirming no regression to the shared fixture.
- [x] Existing mocked-Supabase suite for this route,
  `backend/tests/test_p3_addresses_favorites_safety_disputes.py`, re-run
  (`python3 -m pytest tests/test_p3_addresses_favorites_safety_disputes.py -q
  --no-cov`) → **31/31 passed** — confirms no regression to
  `routes/addresses.py`'s application-level behavior (expected, since no
  application code was touched, but verified rather than assumed).
- [x] Blast-radius grep performed: confirmed `routes/addresses.py` is the
  only in-repo reader/writer of `saved_addresses`; confirmed via
  `supabase_client.py:11` that the backend's Supabase client is instantiated
  with `SUPABASE_SERVICE_ROLE_KEY` (bypasses RLS by design); grepped
  `rider-app/` and `driver-app/` for direct `saved_addresses` references —
  none found.
- [x] Manual `spinr-migration-reviewer` checklist applied (Agent/Task tool
  unavailable in this session, so `.claude/agents/spinr-migration-reviewer.md`'s
  checklist was applied by hand): numbering OK (378, next free after 377),
  append-only OK (new file only), RLS OK (SELECT/INSERT/DELETE enumerated,
  no `FOR ALL`, correct TEXT-column cast), reversibility OK (rollback
  comment present), forward-compat OK (pure `CREATE POLICY` in an idempotent
  `DO` block, no lock on a hot table), indexes OK/N/A
  (`idx_saved_addr_user(user_id)` already exists at
  `supabase_schema.sql:241`, covers the new policies' predicate column),
  money safety N/A, retention OK (no `CASCADE`, no interaction with the
  PIPEDA purge functions). No blockers or warnings found. Full checklist
  output included in the session transcript.
- [x] `python3` / no `ruff` run needed — this change is pure SQL + test
  files (no application `.py` logic changed outside the new test file,
  which follows the existing `tests/rls/` file's style exactly).
- [ ] Feature-flagged: not applicable — a Postgres RLS-policy addition with
  no application code change and no user-visible behavior change has
  nothing to flag; the service-role access path this app actually uses is
  provably unaffected (see §9's service-role-bypass test).
- [ ] `npm run build` — not applicable, no `admin-dashboard`/`rider-app`/
  `driver-app` files were touched.

## 10. What was NOT verified

- **Not run against a real production/staging Supabase.** No
  `DATABASE_URL`/production credentials exist in this session. Verification
  used a local scratch Postgres 16 database (created and dropped within this
  session) with a hand-built `auth.uid()` shim and the `saved_addresses`
  table extracted verbatim from `backend/supabase_schema.sql` — this
  confirms the migration's SQL is syntactically valid, idempotent, and that
  its RLS semantics behave as intended under Postgres's real RLS engine, but
  the actual production Supabase RLS policy application, its `auth.uid()`
  implementation, and its GRANT/REVOKE table-level permission state were not
  exercised directly.
- **Confirmed via code reading, not a live end-to-end HTTP request against a
  running backend**, that `routes/addresses.py`'s service-role access path
  is unaffected. No running FastAPI server + real Supabase project was
  available in this session to issue an actual `GET/POST/DELETE
  /addresses` HTTP request and observe unchanged behavior end-to-end — the
  claim rests on (a) `supabase_client.py:11`'s service-role key
  instantiation, (b) the existing `test_p3_addresses_favorites_safety_
  disputes.py` mocked-Supabase suite passing unchanged, and (c) Postgres's
  documented RLS-bypass-for-privileged-roles behavior, confirmed generically
  for the sibling tables by the pre-existing `test_service_role_bypasses_
  rls_on_users`/`..._on_rides` tests and specifically for this table by the
  new `test_service_role_bypasses_rls_on_saved_addresses` test added here.
- **`spinr-migration-reviewer` was applied manually, not via the Agent/Task
  tool** — that tool was unavailable in this session. The checklist was
  followed item-by-item by hand against the actual migration file; a
  from-scratch subagent pass might catch something a manual pass misses,
  though the migration is small and closely mirrors two already-reviewed,
  already-merged precedents (`emergency_contacts` migration 120, `disputes`
  migration 142).

## 11. Sign-off

- [x] Rollback plan is concrete and testable (three `DROP POLICY IF EXISTS`
  statements; reverts to the exact current production state)
- [x] Blast radius is stated, not assumed (isolated to `routes/addresses.py`,
  confirmed as the only reader/writer; service-role bypass confirmed by
  test, not just claimed)
- [x] No silent behavior change to an already-shipped flow — `routes/
  addresses.py`'s behavior is provably unchanged (existing test suite
  re-run, new service-role-bypass test added)
