# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code (agent session) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety, payments, rides (test-coverage, cross-cutting) |
| PR / commit link | (this commit) |
| Related issue or gap ID | `ACTION_ITEMS.md` C49 (audit finding N18 / ranked blocker #29); continues `docs/change-log/2026-08-31-rls-role-level-test-coverage.md` |

## 1. Issue / gap identified

The 2026-08-31 RLS DB-role-level test harness (`backend/tests/rls/`)
covered only 5 tables (`users`, `drivers`, `rides`, `financial_events`,
`driver_insurance_periods`). That change-log's own "candidates flagged as
good next targets" list named 7 more tables/policy groups with zero real
Postgres-role coverage: `lost_and_found`/`lost_and_found_messages`,
`referral_payouts`, `auto_payout_batches`, `complaints`, and the
`refresh_tokens`/`stripe_events`/`schema_migrations` deny-all policies.
C49 picked up that exact list as its "remaining scope."

## 2. Root cause

Same architectural gap as 2026-08-31 — no test exercised these specific
tables' policies from a real `anon`/`authenticated` Postgres role — just
not yet closed for this second batch of tables when C49 was opened.

## 3. Fix / remediation

Extended the existing `backend/tests/rls/` harness rather than building a
new one:

- `conftest.py`: added two minimal stub tables (`admin_staff`, `payouts` —
  `id text primary key` only) so migrations 25 and 314 can `ALTER TABLE`
  them verbatim; applied migrations 22 (`stripe_events`), 24
  (`schema_migrations`), 25 (`refresh_tokens_and_token_version`), 26
  (`rls_coverage_gap` — the deny-all policies), 68 (`complaints_table`,
  patched — see §4), 69 (`lost_and_found_table`, patched) + 69a
  (`lost_and_found_repair_schema`), 115 (`lost_and_found_chat`) + the new
  fix migration 412, 171 (`referral_payouts`), and 314
  (`auto_payout_and_instant_kill_switch`) — each read from disk and
  executed as written, same pattern as the 2026-08-31 harness. Extended the
  truncate-table list in `pg_cur` with the 7 new real tables (deliberately
  excluding the 3 stub-only additions `schema_migrations`/`admin_staff`/
  `payouts`, which don't need per-test truncation).
- `test_lost_and_found_rls.py` (19 tests): `lost_and_found` (reporter
  select/insert own; unrelated/anon denied; forged-reporter-id insert
  denied; no update/delete policy; service_role bypass) and
  `lost_and_found_messages` (reporter-on-case select; driver-on-case
  select — the bug regression test; unrelated-user denied; reporter
  insert own; forged-sender denied; system-message insert denied;
  non-party-even-as-self denied; no update/delete policy; service_role
  bypass).
- `test_referral_and_payout_rls.py` (14 tests): `referral_payouts`
  (referrer/referee can each select their own row — both halves of the OR
  clause; unrelated/anon denied; insert/update/delete all denied via
  `WITH CHECK (false)`/`USING (false)`; service_role bypass) and
  `auto_payout_batches` (anon/authenticated denied all 4 actions since
  every policy is `TO service_role` only; service_role can do all 4).
- `test_complaints_and_deny_all_rls.py` (22 tests): `complaints` (same
  shape as `lost_and_found`) and the migration-26 deny-all trio
  (`refresh_tokens`/`stripe_events`/`schema_migrations`), parametrized
  over `anon`/`authenticated` for select/insert denial, plus one
  service-role-bypass test covering all three.
- **Bug found and fixed**: writing the `lost_and_found_messages` driver
  test surfaced a real RLS logic bug in migration 115 — see §4 below.
  Fixed via new migration `412_lost_and_found_messages_rls_driver_visibility_fix.sql`
  (`SECURITY DEFINER` helper function `is_party_to_lost_and_found_case`,
  same pattern this repo already uses for cross-table RLS lookups).

## 4. Risk & impact on existing functionality

- **Blast radius of the test-only changes: isolated**, same reasoning as
  2026-08-31 — new test files plus additive `conftest.py` fixture setup
  applied only inside a disposable, per-session throwaway database. No
  production code, route, or existing migration was edited.
- **Blast radius of migration 412 (the one production change in this
  batch)**: `lost_and_found_messages`'s `lfm_select`/`lfm_insert` policies
  only. Grepped for every other reader of these two policies or of
  `lost_and_found_messages` generally:
  - No other migration references `lfm_select`/`lfm_insert` or redefines
    them after 115.
  - The FastAPI backend's own lost-and-found routes use the Supabase
    service-role key (`repositories/_base.py`'s `supabase` client), which
    **bypasses RLS entirely** — this fix changes nothing for that access
    path; it only matters for a client (e.g. `supabase-js`) querying this
    table directly with a rider/driver JWT, which was already broken for
    drivers before this fix (they got zero rows, silently, not an error).
  - Reporter-side behavior (already correct pre-fix) is bit-for-bit
    unchanged: the new `is_party_to_lost_and_found_case()` helper's `OR`
    clause is logically identical to the inline `EXISTS` it replaces, just
    no longer itself subject to `lost_and_found`'s RLS.
  - No other RLS policy in the repo calls or depends on the new
    `is_party_to_lost_and_found_case()` function (new in this migration,
    unreferenced elsewhere).
  - **Conclusion: additive/corrective, not destructive** — the fix turns a
    silent always-false branch into a working one; it cannot regress the
    reporter path, which never touched the buggy branch.
- **Two schema-drift/logic findings surfaced by applying real migration
  SQL verbatim, not introduced by this change:**
  1. **Fixed**: migration 115's `lfm_select`/`lfm_insert` driver-visibility
     bug (root cause: a policy's own subquery against another table is
     itself filtered by that table's RLS — Postgres RLS semantics, not an
     environment quirk). Confirmed via a failing test against a real
     Postgres before the fix, passing after. See migration 412's header
     comment for the full mechanism writeup.
  2. **Not fixed, documented only**: migrations 68 (`complaints`) and 69
     (`lost_and_found`) declare `ride_id`/`reporter_id` (and
     `reported_id`/`resolved_by` for complaints) as `UUID REFERENCES
     rides(id)`/`REFERENCES users(id)`, but the current
     `backend/supabase_schema.sql` declares `rides.id`/`users.id` as
     `TEXT` — applying either migration verbatim raises
     `psycopg2.errors.DatatypeMismatch`. This is a real, pre-existing
     production-relevant drift (not something this session created), and
     it cannot be resolved without a human checking
     `information_schema.columns` against the live production database —
     no session so far has had that access. **Neither merged migration
     file was edited to work around this** — per the append-only migration
     rule, the harness patches the column type at apply time only inside
     `conftest.py` (`_complaints_sql`/`_laf_sql` regex substitutions,
     scoped to this test fixture, with a comment explaining why). See the
     ACTION_ITEMS.md C49 entry's "Bug found, not fixed" bullet for the
     full writeup and who else reads these tables in production
     (`routes/admin/support.py`, `services/zoho_desk_integration.py` for
     `complaints`).
- **Self-inflicted regression caught and fixed within this same change**:
  adding the new tables' blanket `GRANT ... ON ALL TABLES IN SCHEMA public`
  re-opened migration 290's `financial_events` lockdown (the same
  interaction the 2026-08-31 harness already had to guard against once).
  Fixed by re-applying the identical `REVOKE`/`GRANT` sequence immediately
  after the new blanket grant — caught by the existing
  `test_anon_cannot_insert_financial_event`/
  `test_authenticated_cannot_insert_financial_event` tests failing, not by
  inspection, so the fix is verified rather than assumed.
- **Who else reads/writes the 7 tables covered by the new SQL applied in
  the fixture**: none outside the disposable per-session test database —
  same isolation argument as 2026-08-31.

## 5. User-experience effect

None for the test files themselves. For migration 412: no rider/driver/
corporate-admin/internal-admin-facing change through the app's actual
request path (which goes through the service-role key and was never
subject to this bug). The only behavior change is for a hypothetical
direct-Postgres-REST client using a driver's own JWT against
`lost_and_found_messages` — previously silently empty, now correctly
populated. Not visible mid-session to anyone using the shipped app today.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/rls/conftest.py` | Added 2 stub tables; applied migrations 22/24/25/26/68/69/69a/115/412/171/314; extended truncate list; re-applied `financial_events` revoke a second time. | Extends the harness to the C49 remaining-scope table set. |
| `backend/tests/rls/test_lost_and_found_rls.py` | New — 19 tests. | Covers `lost_and_found`/`lost_and_found_messages`. |
| `backend/tests/rls/test_referral_and_payout_rls.py` | New — 14 tests. | Covers `referral_payouts`/`auto_payout_batches`. |
| `backend/tests/rls/test_complaints_and_deny_all_rls.py` | New — 22 tests. | Covers `complaints` + migration 26's deny-all trio. |
| `backend/migrations/412_lost_and_found_messages_rls_driver_visibility_fix.sql` | New. `SECURITY DEFINER` helper function + `lfm_select`/`lfm_insert` policy replacement. | Fixes the driver-visibility RLS bug found while writing the tests above. |
| `ACTION_ITEMS.md` | C49 entry updated: new 2026-09-11 status, bugs-found bullets, files/change-log references. | Status tracking. |

## 7. Before / after

Migration 412 is the one behavior-changing diff in this batch (all other
files are purely additive test code):

**Before** (migration 115, `lfm_select`):
```sql
FOR SELECT USING (
    EXISTS (
        SELECT 1 FROM lost_and_found lf
        WHERE lf.id::text = lost_and_found_id
          AND (auth.uid()::text = lf.reporter_id::text
               OR auth.uid()::text = lf.driver_id::text)
    )
)
```
The subquery's scan of `lost_and_found` is itself filtered by
`lost_and_found`'s own SELECT policy (reporter-only) before the
`OR ... driver_id` branch is ever evaluated — a driver querying this
directly always gets zero rows, regardless of the `OR`.

**After** (migration 412):
```sql
FOR SELECT USING (public.is_party_to_lost_and_found_case(lost_and_found_id))
```
where `is_party_to_lost_and_found_case()` is `SECURITY DEFINER`, so its
internal lookup against `lost_and_found` is not itself subject to that
table's RLS — both the reporter and driver branches now evaluate
correctly. `lfm_insert` received the identical fix for its mirrored
subquery.

## 8. Rollback plan

- **Test files + `conftest.py` extensions**: `git revert` is complete and
  sufficient — pure test infrastructure, no data or running system
  affected.
- **Migration 412**: per its own header comment —
  ```sql
  DROP POLICY IF EXISTS lfm_select ON lost_and_found_messages;
  DROP POLICY IF EXISTS lfm_insert ON lost_and_found_messages;
  -- then re-run migration 115's original lfm_select/lfm_insert bodies
  -- verbatim to restore pre-fix behavior
  DROP FUNCTION IF EXISTS public.is_party_to_lost_and_found_case(text);
  ```
  No data was written or migrated by 412 (policy/function DDL only), so
  this is a same-deploy-window SQL rollback, not a second deploy, and
  nothing here touches Stripe charges, wallet deltas, or ride state.

## 9. Verification performed

- [x] **Automated tests run — full `tests/rls/` suite, against a real
  local Postgres 16 instance** (same sandbox setup as 2026-08-31 —
  `service postgresql start`, `TEST_DATABASE_URL` pointed at it):
  ```
  cd backend && python3 -m pytest tests/rls -c /dev/null --confcutdir=tests/rls -q
  ```
  **Result: 116 passed, 0 failed, 0 regressions** (up from 61 before this
  batch: 61 → 80 → 116 across the three new test files, each verified
  individually before moving to the next, per the task-decomposition
  convention).
- [x] `ruff check` and `ruff format --check` on all changed/new Python
  files — clean.
- [x] Migration-number collision check: `git fetch origin main` +
  `git ls-tree -r --name-only origin/main -- backend/migrations | sort -V
  | tail -5` confirmed 412 was free at time of writing.
- [x] Blast-radius grep performed for migration 412 — see §4 above
  (no other migration or app code path depends on `lfm_select`/
  `lfm_insert` or would be affected by the new `SECURITY DEFINER`
  function).
- [x] Regression verified concretely: `test_driver_on_case_can_select_messages`
  failed (empty result) against migration 115 alone, then passed once
  migration 412 was applied in the same session — the fix is proven
  against a real Postgres, not asserted from reading the SQL.
- [ ] Manual repro in staging — n/a, no staging access from this sandbox.
- [ ] Feature-flagged — n/a; migration 412 is corrective RLS-policy DDL,
  not a rollout of new user-facing behavior, and the affected access path
  (direct-JWT Postgres queries) isn't behind a flag mechanism in this repo.

## 10. What was NOT verified

- **Not verified against production's actual live policy/schema state** —
  same caveat as 2026-08-31, plus the new complaints/lost_and_found
  UUID-vs-TEXT drift finding specifically requires production
  `information_schema.columns` access this session does not have. Do not
  treat the "not fixed" bug above as anything but an open, human-actionable
  finding.
- **CI wiring still not done** — `tests/rls/` still has no Postgres service
  container in any GitHub Actions workflow; these 55 new tests will
  self-skip in CI exactly like the original 32, until that's wired up.
- **Full-suite co-collection with the mocked `backend/tests/conftest.py`
  stack was not run end-to-end in this sandbox** — same limitation as
  2026-08-31 (this sandbox lacks the backend's other ~149 dependencies);
  the skip-not-error behavior under a plain `pytest` invocation is
  inferred from the skip condition, not directly observed this session
  either.
- **Migration 412 was not exercised against a full `run_migrations.py
  --dry-run`/`--status` pass** — applied directly via the test harness's
  own SQL execution, not through the actual migration runner script, so
  the runner's idempotency-key bookkeeping for this specific file was not
  itself tested (though this is the same pattern every prior migration in
  this harness follows, and the runner's general mechanics are unrelated
  to this file's content).
- **No coverage claim beyond the 7 tables/policy groups listed above** —
  the residual ~127–207-minus-covered policy count is still unenumerated;
  see `ACTION_ITEMS.md` C49 for the running total.
