# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "department/section budgets" (business decision: visibility-only spend tracking, not hard enforcement) — schema slice |

## 1. Issue / gap identified

`corporate_sections` (migration 206) exists purely for grouping/reporting
— "budgets remain per-member allowances," by explicit prior design. No
mechanism shows a company how much a department/section has spent in a
month.

## 2. Root cause

Never built — deliberately deferred at migration 206's original design
time.

## 3. Fix / remediation

Researched the booking-time policy flow before designing anything (see
prior turn): the two independent corporate booking code paths in
`routes/rides/booking.py` converge on a single shared settlement function
(`services/payment_service.py::settle_corporate`), so a settlement-time
hook (next commit) covers both without needing to touch either booking
path. Also confirmed the closely analogous per-member allowance cap
required a real atomic Postgres function (`corporate_allowance_apply_delta`,
migration 258) after a documented production race-condition bug — the
explicit product decision this round was **visibility only, no
enforcement**, specifically to avoid that risk class.

New migration `281_corporate_section_budgets.sql`:

- `corporate_sections.monthly_budget_cap NUMERIC(10,2)` — nullable,
  opt-in, non-negative `CHECK`. Never read by any booking-time or
  settlement-time gate; purely a number a company sets for its own
  reference.
- `corporate_section_spend` — one row per (section_id, month), a running
  `used` total.
- `corporate_section_spend_add(section_id, month, delta)` — a small
  `SECURITY DEFINER` Postgres function (same pinned-`search_path`
  convention as every other money-adjacent RPC in this codebase,
  migration 203) doing a single-statement `INSERT ... ON CONFLICT DO
  UPDATE SET used = used + delta`. **Deliberately not a compare-then-write
  ceiling check** — a plain atomic increment needs no row lock the way a
  "read current, decide, write" cap enforcement would, which is exactly
  why visibility-only scope is materially safer than hard enforcement
  here, not just smaller.
- Reviewed by the `spinr-migration-reviewer` agent: found one real issue
  (this table enabled RLS but shipped zero policies, while its
  overstated comment claimed parity with `corporate_sections`, which
  does have an explicit admin policy) — fixed by adding the matching
  `"Admin full access corporate_section_spend"` policy before committing,
  not left as a known gap.
- `backend/repositories/corporate_repo.py`: `record_section_spend`
  (calls the RPC) and `get_section_spend_map` (batch read for the section
  list endpoint, avoiding N+1) — re-exported from `db_supabase.py` in
  both dual-import branches.

**No app code calls these yet in this commit** — settlement-time wiring
is the next commit.

## 4. Risk & impact on existing functionality

- **Blast radius: additive only.** One new nullable column on
  `corporate_sections` (default `NULL`, no existing row's behavior
  changes), one new table, one new RPC function, two new repo functions.
  Nothing in `corporate_sections`' existing 8 columns, its unique index,
  or its RLS policy was touched.
- Grepped every consumer of `corporate_sections`
  (`routes/corporate_company_bookings.py`'s `list_sections`/
  `create_section`/`update_section`/`archive_section`,
  `routes/corporate_company.py`'s member `section_id` reassignment
  check): none read or write `monthly_budget_cap` yet — this commit adds
  the column but no code path touches it.
- The RPC function is `SECURITY DEFINER` with `EXECUTE` granted only to
  `service_role` (matching `corporate_wallet_apply_delta`/
  `corporate_allowance_apply_delta`'s grant posture) — not callable from
  an anon/authenticated frontend session.

## 5. User-experience effect

None yet — schema and repository layer only, no route or UI wired up.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/281_corporate_section_budgets.sql` | New migration: `monthly_budget_cap` column, `corporate_section_spend` table + RLS policy, `corporate_section_spend_add` RPC | Data model for visibility-only section spend tracking |
| `backend/repositories/corporate_repo.py` | 2 new functions: `record_section_spend`, `get_section_spend_map` | Data-access layer for the new table/RPC |
| `backend/db_supabase.py` | Re-exported the 2 new names in both dual-import branches | Match the established re-export convention |

## 7. Rollback plan

`DROP TABLE IF EXISTS public.corporate_section_spend;` (cascades the RLS
policy and the function's data dependency); `ALTER TABLE
public.corporate_sections DROP COLUMN IF EXISTS monthly_budget_cap;`
(documented in the migration's top comment). The RPC function itself
becomes orphaned but harmless if not explicitly dropped — no other object
depends on it. Zero data-loss risk: nothing writes to these objects yet
in this commit.

## 8. Verification performed

- [x] `spinr-migration-reviewer` agent review — found and fixed one real
      issue (missing RLS policy despite a comment claiming parity) before
      committing, not after.
- [x] `ast.parse` syntax check on both modified Python files — clean.
- [x] Confirmed via grep that no existing route reads/writes
      `monthly_budget_cap` or the new table yet.
- [x] Confirmed the RPC's `EXECUTE` grant matches the `service_role`-only
      posture of the two existing money-adjacent RPCs in this codebase.

## 9. Sign-off

- [x] Rollback plan is concrete and reversible with zero data-loss risk
- [x] Blast radius is stated, not assumed — additive-only, confirmed by
      diff and grep
- [x] No behavior change to any working flow — nothing calls the new
      code yet

## What was NOT verified

Did not run the migration against a real or throwaway Supabase schema —
per this round's "no tests/CI until everything is developed" instruction,
verified via the migration-reviewer agent + manual read instead. The
atomic-increment RPC's correctness under real concurrent load was
reasoned from Postgres's documented single-statement upsert semantics,
not exercised against a real database. This is schema + plumbing only —
the settlement hook (the only place this data gets written) is the next
commit, and the reversal/refund case (a settled ride later reversed) is
explicitly NOT handled — the running total only ever increases, a stated
limitation of the visibility-only scope, not an oversight.
