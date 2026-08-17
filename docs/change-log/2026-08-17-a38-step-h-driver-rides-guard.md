# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Claude (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate / safety (PIPEDA retention, not corporate — closest existing tag; see note) |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | ACTION_ITEMS.md A38 |

## 1. Issue / gap identified

`purge_pii_retention()`'s Step H (the sanctioned 7-year DSAR hard-delete path, migration 216) checks `driver_insurance_periods`/`payouts`/`bank_accounts` for a driver account and `rides.rider_id` for any account before hard-deleting — but never checks `rides.driver_id`. A driver account with completed ride history but, for some reason, no rows in those three other tables would pass Step H's eligibility guard today and be hard-deleted at the 7-year mark despite having ride history that should be retained.

## 2. Root cause

Migration 216's original Step H guard was written to protect specific regulatory tables (insurance periods, payouts, bank accounts) plus the general rider-history case (`rides.rider_id`), but never added the symmetric driver-history case (`rides.driver_id`). This is the same class of gap A35 found in an ad-hoc account-deletion script (2026-08-16) — just narrower, and inside the *sanctioned* DSAR path rather than an ad-hoc one. Surfaced by `spinr-regulatory-compliance-checker`'s review of the A35 fix: `services/test_account_cleanup_service.py`'s `_blocking_reasons()` (the new, stricter replacement cleanup tool A35 shipped) deliberately added this exact `rides.driver_id` check as a check Step H itself lacks — but A35 did not patch Step H's own SQL, since that's money/regulatory-adjacent production code affecting real hard-delete behavior and was deliberately left for its own dedicated review (this PR).

## 3. Fix / remediation

Migration 321 re-issues `CREATE OR REPLACE FUNCTION purge_pii_retention()` (the established pattern for this function — see migration 296's own `migration-override-ok` precedent) with **one change**: adds `OR EXISTS (SELECT 1 FROM rides r2 WHERE r2.driver_id = d.id)` to Step H's driver-side guard, in **both** places it appears — the live-delete loop's `WHERE` clause and the dry-run `COUNT` query (they must stay identical, since dry-run exists specifically so an operator can preview what the live path would do without one silently lying about the other). No other line in the ~380-line function is touched — verified by diffing the full function body against migration 296's original (only the Step H comment and the two added `EXISTS` clauses differ).

## 4. Risk & impact on existing functionality

- **Blast radius: single function, one guard clause, in two places.** `purge_pii_retention()` has exactly one caller in application code: `backend/utils/retention_purge.py`'s `run_tick()`, invoked by a background loop registered in `core/lifespan.py`. Grepped for other callers — none.
- **Direction of the change is strictly more conservative.** This can only ever **exclude more accounts** from Step H's hard delete (a driver with ride history that previously slipped through the guard now correctly blocks). It cannot cause an account that is currently ineligible to become eligible, and it cannot affect the rider-side check, Step N's profile scrub, or any of the other 12 lettered steps in this function (A through N) — none of which were touched.
- **No risk to already-deleted accounts.** This only changes eligibility for *future* purge runs — it does not and cannot retroactively restore anything already hard-deleted under the old (gap-having) guard. That is an accepted, stated limit (see Rollback below) — there is nothing to reconcile.
- **financial_events is unaffected by this change.** Step H actively `DELETE`s `financial_events` rows for an eligible account as part of the purge itself — it is not a blocking-existence guard the way `driver_insurance_periods`/`payouts`/`bank_accounts`/now-`rides.driver_id` are. Reopening whether `financial_events` presence *should* also gate eligibility is a separate, larger design question outside A38's stated scope and was not touched here.
- **No migration or schema change** beyond the function redefinition — `payouts`/`bank_accounts`/`rides` schemas are untouched.

## 5. User-experience effect

None visible mid-session. This affects only the outcome of a 6-hourly background retention-purge tick for accounts that are already 7+ years past a DSAR deletion request — an exceedingly rare, delayed, backend-only code path with no rider/driver/admin-facing UI.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/321_purge_pii_retention_step_h_driver_rides_guard.sql` | New migration — `CREATE OR REPLACE FUNCTION purge_pii_retention()`, full body from migration 296 plus one added `rides.driver_id` `EXISTS` clause in both Step H branches | Close the A38 gap in the sanctioned DSAR hard-delete path |
| `backend/tests/test_step_h_driver_rides_guard_migration.py` | New file — 12 tests pinning the SQL contract textually (no live Postgres in CI, same convention as `test_pipeda_30day_profile_scrub_migration.py`/`test_deletion_hard_delete_migration.py`) | Regression coverage: both branches carry the fix identically, existing guards/rider-check/Step N/permissions all unchanged |
| `docs/change-log/2026-08-17-a38-step-h-driver-rides-guard.md` | This file | Mandatory Change Impact Log for a regulatory-adjacent migration |

## 7. Before / after

```sql
-- Before (both the live-delete WHERE clause and the dry-run COUNT query)
AND NOT EXISTS (
    SELECT 1 FROM drivers d
    WHERE d.user_id = u.id
      AND ( EXISTS (SELECT 1 FROM driver_insurance_periods dip WHERE dip.driver_id = d.id)
         OR EXISTS (SELECT 1 FROM payouts p       WHERE p.driver_id = d.id)
         OR EXISTS (SELECT 1 FROM bank_accounts b WHERE b.driver_id = d.id) )
)
```

```sql
-- After
AND NOT EXISTS (
    SELECT 1 FROM drivers d
    WHERE d.user_id = u.id
      AND ( EXISTS (SELECT 1 FROM driver_insurance_periods dip WHERE dip.driver_id = d.id)
         OR EXISTS (SELECT 1 FROM payouts p       WHERE p.driver_id = d.id)
         OR EXISTS (SELECT 1 FROM bank_accounts b WHERE b.driver_id = d.id)
         OR EXISTS (SELECT 1 FROM rides r2        WHERE r2.driver_id = d.id) )
)
```

## 8. Rollback plan

Re-issue `CREATE OR REPLACE FUNCTION purge_pii_retention()` back to migration 296's body (drop the two added `EXISTS` clauses) — a single, standard SQL statement, no data to unwind. As stated in §4, a rollback would only widen eligibility for *future* purge ticks going forward; it cannot retroactively affect any account already processed either before or after this migration.

## 9. Verification performed

- [x] `pytest backend/tests/test_step_h_driver_rides_guard_migration.py backend/tests/test_pipeda_30day_profile_scrub_migration.py backend/tests/test_deletion_hard_delete_migration.py backend/tests/test_retention_purge.py backend/tests/test_ai_retention_migration.py backend/tests/test_audit_logs_lockdown.py backend/tests/test_financial_events_ride_id_fk_contract.py backend/tests/test_test_account_cleanup_service.py -q --no-cov` — 81/81 pass.
- [x] `ruff check` + `ruff format --check` on the new test file — clean.
- [x] `diff` of the new migration's full function body against migration 296's original — confirmed the ONLY substantive differences are the Step H comment update and the two added `EXISTS (SELECT 1 FROM rides r2 WHERE r2.driver_id = d.id)` clauses; every other step (A–G, I–N), the result JSON, the `REVOKE`/`GRANT`, and the audit-log insert are byte-identical.
- [x] Blast-radius grep performed for `purge_pii_retention` callers — only `utils/retention_purge.py`'s `run_tick()`.
- [x] Reviewed against CLAUDE.md's migration conventions (append-only via `CREATE OR REPLACE` + `migration-override-ok`, matching migration 296's own precedent for this exact function) and PIPEDA/retention conventions.
- [ ] Not run against a live Postgres instance — no integration-test harness for this function exists in this repo; verification is textual/static (same limitation and same convention as every other migration touching this function, per `test_pipeda_30day_profile_scrub_migration.py`'s own docstring: "CI has no Postgres, so these checks pin the SQL contract textually").

## Manual review (Codex auto-review currently off — see CLAUDE.md's "PR review handling" section, C7/C9)

- **`spinr-migration-reviewer`**: one blocker — this file was originally numbered 319, but `319_driver_crc_consents.sql`/`319_late_tip_debit_types.sql`/`320_driver_appeals.sql` merged to `main` from other PRs while this branch was in progress. Renamed to `321_*` (the actual next-free slot at review time) and updated every in-file self-reference (the Step H comment, the trailing `COMMENT ON FUNCTION` string) plus the test file's path and the Change Impact Log references. Everything else — `migration-override-ok` usage, byte-identical-except-the-fix function body, reversibility, `SECURITY DEFINER`/`search_path`, `REVOKE`/`GRANT`, index coverage (the new `rides.driver_id` predicate is covered by the existing `idx_rides_driver_created` index) — verified clean. (The reviewer also flagged what it read as an unrelated bundled edit to `backend/migrations/CLAUDE.md`; re-checked directly against this commit's actual diff — no such edit exists in it, a false positive.)
- **`spinr-regulatory-compliance-checker`**: **SAFE TO MERGE**, no blockers. Confirmed the fix correctly closes the gap in both branches, correctly serves the Saskatchewan Transportation Act's 7-year retention rule (traced the interaction with Step B's unconditional 7-year ride purge and confirmed no DSAR-stuck-forever risk — the same convergence behavior as the pre-existing rider-side guard), and stays correctly scoped (Step N and `financial_events` handling confirmed untouched).

## What was NOT verified

- Not exercised against a real Postgres instance (no live-DB test harness for this function anywhere in this repo's history — same limitation as every prior change to `purge_pii_retention()`).
- Whether any account in production today actually has this specific shape (driver with `rides.driver_id` history but no `driver_insurance_periods`/`payouts`/`bank_accounts` rows) and would be affected by this fix — not checked; this closes a structural gap regardless of whether it has been hit yet in practice (A38 itself found it by code review, not by observing a real incident).
- Whether `financial_events` presence should also gate Step H eligibility — explicitly out of scope for A38, flagged as a separate, larger design question if it's ever picked up.
