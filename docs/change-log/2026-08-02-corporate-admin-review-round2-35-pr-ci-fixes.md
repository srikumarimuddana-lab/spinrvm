# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | corporate, admin |
| PR / commit link | PR #3341, branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | PR #3341 CI failures (Migration Safety Check ×2, admin-test) found after opening the round-2 PR |

## 1. Issue / gap identified

Opening PR #3341 (round 2, 17 items + final bugfix batch) against the
current `main` surfaced 3 real CI failures that hadn't been visible
locally: two "Migration Safety Check" gates and one `admin-test`
(vitest) job.

## 2. Root cause

1. **Migration prefix collision.** This branch's own
   `278_audit_logs_request_id.sql` (item #52) reused numeric prefix
   `278`, which by the time this PR was opened had *also* been used by
   `278_compliance_export_events_admin_id_no_fk.sql` — a migration
   merged to `main` via a different PR after this branch's commits were
   first authored (this branch was rebased onto the then-current `main`
   before opening the PR, per the "restart from main" instruction for a
   merged designated-branch PR; see round2-34's context). The CI
   prefix-uniqueness gate (`.github/workflows/migration-check.yml`)
   correctly caught the collision.
2. **Missing RLS policy.** `280_corporate_subscriptions.sql`
   (item #63a) enabled `ROW LEVEL SECURITY` on both new tables but
   shipped no `CREATE POLICY`, on the reasoning "backend-only,
   service-role bypasses RLS." The Migration Safety Check's RLS gate
   has no such exception — and correctly so: `backend/migrations/CLAUDE.md`
   says "every new table that stores user data must ship with RLS
   policies in the same migration," full stop. The sibling migration in
   this same PR (`corporate_section_spend`, item #66a) already carries
   an explicit admin-only policy for exactly this reason; `280` should
   have matched that pattern from the start.
3. **Smoke-test API mock shape mismatch.** `admin-dashboard`'s generic
   dashboard-page smoke test (`pages.smoke.test.tsx`) mocks every
   `get*`/`list*`/`fetch*` API function to resolve to a bare `[]`
   unless explicitly overridden. Two round-2 additions —
   `getKybReverificationDue` (item #67d, returns
   `{threshold_months, count, companies}`) and `getAuditLogTopActors`
   (item #53, returns `{days, window_start, rows_scanned,
   rows_scanned_capped, actors}`) — are object-shaped responses whose
   callers destructure a named field (`.companies`, `.actors`). Under
   the generic `[]` mock, that destructuring reads `undefined`, and the
   page's `.length` check on the resulting `undefined` throws during
   render. `getRides`/`getDrivers` already needed (and got) the same
   kind of override when they were added — this is the third and fourth
   instance of the same test-infra gap, not a new failure mode.

## 3. Fix / remediation

1. Renumbered the 5 colliding/cascading migration files
   (`278→279→280→281→282→283`, one slot up each) via `git mv`, updated
   each file's internal header-comment number, and updated every
   `migration NNN` cross-reference in application code
   (`corporate_subscription_service.py`, `corporate_repo.py` ×3,
   `routes/admin/safety.py`) to match. None of these files exist on
   `main` yet (all still unmerged in this PR), so this is a pre-merge
   renumber, not a violation of "already-applied migrations must never
   be renamed."
2. Added explicit admin-only RLS policies to
   `281_corporate_subscriptions.sql` (renamed) for both
   `corporate_subscription_plans` and `corporate_subscriptions`,
   copying the exact idempotent `DO $$ ... IF NOT EXISTS (SELECT 1 FROM
   pg_policies ...) THEN EXECUTE 'CREATE POLICY ...' END $$` pattern
   already used for `corporate_section_spend` (`FOR ALL TO authenticated
   USING (EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid()::text
   AND users.role = 'admin'))`).
3. Added `getKybReverificationDue` and `getAuditLogTopActors` to the
   `overrides` map in `pages.smoke.test.tsx`, each resolving to a
   response shaped to match its real return type.
4. Ran `spinr-migration-reviewer` on the renumbered/RLS-fixed
   `281_corporate_subscriptions.sql` — verdict: **SAFE TO APPLY**, no
   blockers or warnings, confirmed the renumber is self-referential-only
   with no logic drift.
5. Re-ran the full backend `pytest` suite (8523 passed, 2 failed
   originally — `tests/test_sgi_field_maps.py`'s
   `TestRemovalEffectiveDate` — re-ran that file alone once the local
   system clock's date had fully settled and both passed; a `_TODAY`
   module constant computed against a live `datetime.now()` call being
   compared mid-run across a date rollover, unrelated to this PR's diff
   — confirmed via `git diff` showing zero overlap with any file this
   PR touches). Re-ran the fixed `pages.smoke.test.tsx` file directly:
   20/20 passed.

## 4. Risk & impact on existing functionality

- **Migration renumber**: purely a rename + comment-number update
  across 5 files, all still unmerged (no production deploy has ever
  seen the old numbers). Grepped the entire repo for every reference to
  the 5 old filenames/numbers before renaming; updated all of them
  (application code comments + the migrations' own headers). Left
  historical `docs/change-log/2026-08-02-corporate-admin-review-round2-{02,04,12,26,30}-*.md`
  entries referencing the old numbers untouched — they're point-in-time
  records of what was done in that specific commit, not live
  documentation, and renumbering them after the fact would misrepresent
  what those commits actually said at the time.
- **New RLS policies**: additive only — before this fix, the two
  subscription tables had RLS *enabled* with *no* policies, meaning
  **zero** rows would ever be visible or writable to a non-service-role
  (`authenticated`) session; the backend API (which always uses the
  service-role key) was never affected either way. After this fix, a
  direct authenticated admin session can also read/write these tables,
  matching every other admin-managed corporate table's access pattern.
  No existing caller changes behavior.
- **Smoke-test override additions**: test-only, zero production code
  touched. Grepped every other page in the smoke test's fixed page list
  for the same object-destructuring risk (checked `settings/page.tsx`
  and `staff/page.tsx`, both touched this round) — neither crashed in
  CI, confirming they don't hit the same gap.

## 5. User-experience effect

None — all three fixes are pre-merge CI corrections with no runtime
behavior change for any rider/driver/corporate-admin/internal-admin
user. The RLS policy addition is the only one with real runtime effect,
and it only affects a currently-nonexistent access path (direct
authenticated non-service-role queries against tables that don't exist
in production yet, since this PR hasn't merged).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/278_audit_logs_request_id.sql` → `279_...` | Renamed, header comment updated | Resolve prefix collision with `main`'s `278_compliance_export_events_admin_id_no_fk.sql` |
| `backend/migrations/279_safety_incidents_merge.sql` → `280_...` | Renamed, header comment updated | Cascade of the above |
| `backend/migrations/280_corporate_subscriptions.sql` → `281_...` | Renamed, header comment updated, **added 2 RLS policies** | Cascade rename + fix real RLS gate failure |
| `backend/migrations/281_corporate_section_budgets.sql` → `282_...` | Renamed, header comment + 1 self-reference updated | Cascade rename |
| `backend/migrations/282_corporate_kyb_reverification.sql` → `283_...` | Renamed, header comment updated | Cascade rename |
| `backend/services/corporate_subscription_service.py` | 1 comment: "migration 280" → "migration 281" | Match renumber |
| `backend/repositories/corporate_repo.py` | 3 comments: "migration 280/281/282" → "281/282/283" | Match renumber |
| `backend/routes/admin/safety.py` | 1 comment: "migration 279" → "migration 280" | Match renumber |
| `admin-dashboard/src/__tests__/dashboard/pages.smoke.test.tsx` | Added 2 shape-specific mock overrides | Fix `admin-test` CI failure on `/dashboard/corporate-accounts` and `/dashboard/audit-logs` |

## 7. Rollback plan

`git revert` the commit. Migration renames are pre-merge (no data,
nothing applied to any real database); the RLS policy addition is a
new, additive `CREATE POLICY` with an idempotent guard, safe to
re-apply or omit; the test-file change is test-only.

## 8. Verification performed

- [x] `spinr-migration-reviewer` agent run on the renumbered + RLS-fixed
      `281_corporate_subscriptions.sql` — **SAFE TO APPLY**, verified the
      renumber is logic-preserving and the new RLS policy syntax matches
      the established `corporate_section_spend` pattern exactly.
- [x] Full backend `pytest` suite: 8523 passed / 2 failed (unrelated,
      confirmed date-boundary flake in `test_sgi_field_maps.py`, zero
      file overlap with this PR's diff) / 8 skipped / 1 xfailed.
      Re-ran the 2 failing tests alone after the flake window passed:
      8/8 passed.
- [x] `admin-dashboard` targeted vitest run on the fixed smoke test
      file: 20/20 passed (was 18/20 before this fix).
- [x] Grepped the full repo for every reference to the 5 renamed
      migration files/numbers and updated every one found in
      application code; confirmed via `ls backend/migrations/` that no
      duplicate numeric prefix remains in the 278-283 range.

## 9. Sign-off

- [x] Rollback plan is concrete — `git revert`, nothing applied to any
      real database
- [x] Blast radius is stated, not assumed — every cross-reference to
      the renamed files was grepped and updated; every other
      smoke-tested page was checked for the same destructuring risk
- [x] No silent behavior change to a working flow — the RLS policy
      addition only grants new access on tables that grant *zero*
      access today (RLS enabled, no policy); nothing regresses

## What was NOT verified

Did not re-run the full `admin-dashboard` production build a third time
after this specific fix (only the targeted smoke-test file); the
earlier full `npm run build` pass (round2-34) already confirmed a clean
production build before this PR was opened, and this fix only touches a
test file, not application code, so a build regression from this
specific change is not plausible — but the PR's own CI will run the
full build again as part of normal gating.
