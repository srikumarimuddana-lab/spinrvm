# Change Impact & Risk Log — migration 281 `company_id` FK type

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-04 |
| Author | Claude Code (session with @srikumarimuddana) |
| Surface(s) | backend (schema only) |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/missing-regulator-removal-date-column-o5sihd` |
| Related issue or gap ID | operator report: `42804` while applying pending migrations |

## 1. Issue / gap identified

Applying `backend/migrations/281_corporate_subscriptions.sql` fails outright:

```
ERROR: 42804: foreign key constraint "corporate_subscriptions_company_id_fkey" cannot be implemented
DETAIL: Key columns "company_id" and "id" are of incompatible types: text and uuid.
```

Because the file contains no `CONCURRENTLY` statement, `scripts/migrate.py` runs it inside a
single transaction, so the failure rolls the entire migration back. Neither
`corporate_subscriptions` nor `corporate_subscription_plans` exists on any environment, and
the migration is not recorded in `schema_migrations` anywhere.

## 2. Root cause

`corporate_subscriptions.company_id` was declared `TEXT` while
`corporate_accounts.id` is `UUID` (migration `05_corporate_accounts.sql`, restated identically
in `08_complete_schema.sql`). Postgres will not build an FK between mismatched key types.

Every other `company_id` in the schema is already `UUID` — `206_corporate_sections.sql:23`
and five tables in `27_corporate_b2b_v1.sql` (master wallet, allowances, top-up requests,
policies, allowed domains). 281 was the sole outlier, so this is a typo in one file, not a
schema-wide convention disagreement.

Nothing caught it in CI: no test reads migration SQL, and no job applies the migrations to a
throwaway Postgres. The first execution was an operator running it against a real database.

## 3. Fix / remediation

- `281_corporate_subscriptions.sql`: `company_id TEXT` → `company_id UUID`, matching the
  referenced PK and every other `company_id` column.
- New `backend/tests/test_migration_fk_column_types.py`: static check over all migration files
  asserting that an inline `col TYPE … REFERENCES table(id)` column is declared with the same
  type as that table's `id` PK. Catches this whole class before an operator does.

**Corrected in place rather than via a follow-up migration** — a deliberate exception to the
append-only rule in `backend/migrations/CLAUDE.md`. The rule protects migrations that have
already been applied somewhere; this one, by construction, has been applied nowhere (single
transaction, full rollback, no `schema_migrations` row). A follow-up `ALTER TABLE` could not
work either, since there is no table to alter — 281 would keep failing on every fresh database.
The reason is recorded in the file's header comment so a future reader doesn't see an edited
migration and assume the rule was overlooked.

## 4. Risk & impact on existing functionality

**Blast radius: isolated — schema-only, zero rows, zero live consumers.**

Grepped for every reader/writer of the two tables:

| Consumer | Effect |
|---|---|
| `services/corporate_subscription_service.py` (`assign_subscription`, `cancel_subscription`) | None — passes `company_id` as a Python `str`; PostgREST casts a UUID-shaped string to `uuid` transparently. `id` and `plan_id` stay `TEXT` and are unchanged. |
| `routes/corporate_subscriptions.py` | None — already normalizes via `validate_id()` before every call. |
| `routes/webhooks.py` (Stripe `customer.subscription.*`) | None — matches on `stripe_subscription_id` (`TEXT`), not `company_id`. |
| `repositories/corporate_repo.py` helpers | None — `.eq("company_id", <str>)` compiles the same against a `uuid` column. |
| `routes/admin/settings.py` | Comment reference only. |

No rows exist to migrate: the tables have never been created. No background loop in
`core/lifespan.py` touches them. No wallet delta, ride-state transition, or
`corporate_wallet_apply_delta` call is involved — this table is flat SaaS platform-fee state,
deliberately separate from fare settlement.

**Residual risk:** an environment whose `corporate_accounts.id` drifted to `TEXT` (the
"known drift" scenario documented in `17_corporate_accounts_fk.sql`) would now fail 281 with
the mirror-image of this error. Verify before applying:

```sql
SELECT data_type FROM information_schema.columns
 WHERE table_schema='public' AND table_name='corporate_accounts' AND column_name='id';
-- expect: uuid
```

The reported error itself proves the target database returns `uuid`, so the drift case does not
apply there.

## 5. User-experience effect

**Nobody, immediately** — backend schema only, and the tables are not yet in use.

Downstream: with 281 blocked, the whole pending-migration batch behind it was blocked too,
which is what left `drivers.regulator_removal_*` (migration 275) missing and the admin SGI
removal-queue endpoint returning 503. Unblocking 281 lets that batch through. Once applied,
the corporate subscription admin screens stop erroring on a missing table — no copy change, no
mid-session change for any rider or driver.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/281_corporate_subscriptions.sql` | `company_id` `TEXT` → `UUID`; header comment recording the in-place correction and why append-only does not apply | The FK is unimplementable against a `UUID` PK, so the migration could never apply |
| `backend/tests/test_migration_fk_column_types.py` | New static test: inline FK column types must match the referenced `id` PK type across all migrations | No existing test or CI job would have caught this before production |

## 7. Before / after

```sql
-- Before
CREATE TABLE IF NOT EXISTS public.corporate_subscriptions (
    id                      TEXT PRIMARY KEY,
    company_id              TEXT NOT NULL REFERENCES public.corporate_accounts(id),
```

```sql
-- After
CREATE TABLE IF NOT EXISTS public.corporate_subscriptions (
    id                      TEXT PRIMARY KEY,
    company_id              UUID NOT NULL REFERENCES public.corporate_accounts(id),
```

## 8. Rollback plan

The migration's own rollback block (unchanged, top of the file) still applies:

```sql
DROP TABLE IF EXISTS public.corporate_subscriptions;
DROP TABLE IF EXISTS public.corporate_subscription_plans;
```

Safe with no data-level remediation: nothing has ever written to these tables, so no live data
is at risk. This does **not** touch Stripe — a Stripe Subscription created by
`assign_subscription` is external state and must be cancelled in Stripe separately; that
concern is unchanged by this diff and cannot arise until the tables exist and an admin assigns
a plan.

Reverting the code change alone (back to `TEXT`) is not a rollback — it restores the broken
state.

## 9. Verification performed

- [x] Automated tests run (unit): `tests/test_migration_fk_column_types.py`,
      `test_migration_ordering.py`, `test_corporate_subscription_service.py`,
      `test_corporate_subscriptions_route.py`, `test_webhooks_corporate_subscription.py` —
      **35 passed**.
- [x] New test verified non-vacuous: re-run against the pre-fix `TEXT` text, both assertions
      fail with the expected message. It also finds no other mismatch across all 285 migration
      files (81 tables' PK types resolved), so 281 was the only instance.
- [x] `ruff check` + `ruff format` clean on the new test.
- [x] Blast-radius grep performed: `corporate_subscriptions`, `corporate_subscription_plans`,
      `REFERENCES .*corporate_accounts`, `company_id` across `backend/routes/`,
      `backend/services/`, `backend/repositories/`, `backend/migrations/`.
- [x] Reviewed against `backend/migrations/CLAUDE.md` (append-only exception justified above;
      rollback comment already present; RLS policies unchanged and still ship in-file).
- [x] Feature flag not applicable — schema DDL with no user-visible behavior and no live rows.

## 10. What was NOT verified

- **The migration was not executed.** No Postgres server is available in this session (`psql`
  client only, no server), and the repo has no throwaway-Postgres test harness. The fix is
  verified by static analysis and by matching the convention every other `company_id` follows —
  not by an actual successful `CREATE TABLE`. **Run `python scripts/migrate.py --dry-run` and
  then the real apply against staging before production.**
- Not tested against live Supabase; the corporate-subscription tests use the mocked
  `mock_supabase_client` fixture, which does not enforce column types and so would pass under
  either declaration.
- The new test is a regex-based static check. It does not cover FKs added by a later
  `ALTER TABLE ... ADD CONSTRAINT`, composite FKs, or a column whose type is changed after
  creation. A real "apply all migrations to a scratch Postgres in CI" job remains the only
  complete answer — a standing gap, not closed here.
- Whether migrations 276–285 apply cleanly once 281 is unblocked was not verified; only 281's
  specific failure was diagnosed and fixed.
