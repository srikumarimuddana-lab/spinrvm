# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "automated KYB re-verification" (business decision: scheduled staleness reminder for admins, not auto-reverification/auto-status-change) — schema slice |

## 1. Issue / gap identified

KYB (Know Your Business) is verified once at onboarding
(`kyb_reviewed_at`/`kyb_last_decision`, migration 225) with no periodic
re-check. A company approved years ago never gets flagged for review
again.

## 2. Root cause

Never built.

## 3. Fix / remediation

New migration `282_corporate_kyb_reverification.sql`: one nullable
`corporate_accounts.kyb_reverify_flagged_at` column. Reviewed by the
`spinr-migration-reviewer` agent — verdict SAFE TO APPLY, no blockers.
**This column is explicitly NOT the source of truth for staleness** — the
admin-dashboard "needs re-verification" filter (round2-32) computes that
live from the already-existing `kyb_reviewed_at` column; this column
exists purely as the background loop's own replay-safety claim flag
(same shape as `corporate_wallets.low_balance_notified_at`) so its log
line + metric fire once per stale period per company, not on every tick.

`backend/repositories/corporate_repo.py`: two new functions —
`list_companies_needing_kyb_reverification` (active + KYB-approved +
`kyb_reviewed_at` older than a caller-supplied cutoff) and
`mark_kyb_reverify_flagged` — re-exported from `db_supabase.py`.
`list_companies_needing_kyb_reverification` deliberately does not filter
on the claim flag in the DB query; the loop applies its own cooldown
check in Python over the result set, mirroring
`list_wallets_low_balance_no_autotopup`'s established pattern
(`utils/corporate_low_balance.py`) rather than building a new `$or`
filter for it.

**Incidental fix, caught while adding this code**: found and removed
five lines of dead, unreachable code at the very end of
`corporate_repo.py` — a stray `return await run_sync(_ins)` sitting after
`get_section_spend_map`'s own `return` statement (referencing an
undefined name `_ins` that doesn't exist in that function's scope).
Harmless at runtime (unreachable, Python doesn't error on dead code) but
a real leftover artifact from an earlier edit this round, not something
this commit's own diff would have introduced — found by reading the
file's tail while orienting for this addition, not by a targeted search
for it.

## 4. Risk & impact on existing functionality

- **Blast radius: additive only, plus the incidental dead-code removal.**
  One new nullable column, two new functions. No existing column,
  function, or query touched.
- The dead-code removal is provably safe: the removed lines were never
  reachable (they came after a `return` in the same function body), so
  no behavior could possibly depend on them executing — confirmed by
  reading the surrounding function, not just trusting that "unreachable"
  claim.
- Grepped every consumer of `corporate_accounts`: none read or write
  `kyb_reverify_flagged_at` yet — the loop wiring is the next commit.

## 5. User-experience effect

None yet — schema and repository layer only.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/282_corporate_kyb_reverification.sql` | New migration: `kyb_reverify_flagged_at` column | Background loop's own replay-safety claim flag |
| `backend/repositories/corporate_repo.py` | 2 new functions; removed 2 lines of pre-existing dead code | Data-access layer for the new loop + incidental cleanup |
| `backend/db_supabase.py` | Re-exported the 2 new names in both dual-import branches | Match the established re-export convention |

## 7. Rollback plan

`ALTER TABLE public.corporate_accounts DROP COLUMN IF EXISTS
kyb_reverify_flagged_at;` (documented in the migration's top comment).
Zero data-loss risk — nothing writes to the column yet in this commit.
The dead-code removal has no rollback concern (it was never executed).

## 8. Verification performed

- [x] `spinr-migration-reviewer` agent review — SAFE TO APPLY, explicitly
      confirmed the "no index needed" design choice is correct given how
      the column is actually read/written.
- [x] `ast.parse` syntax check on all three modified Python files —
      clean, both before and after the dead-code removal.
- [x] Confirmed the dead code was genuinely unreachable (positioned after
      a `return` statement in its enclosing function) before removing it.
- [x] Confirmed via grep that no existing route/loop reads or writes
      `kyb_reverify_flagged_at` yet.

## 9. Sign-off

- [x] Rollback plan is concrete and reversible with zero data-loss risk
- [x] Blast radius is stated, not assumed — additive-only, confirmed by
      diff and grep; the incidental fix is provably behavior-neutral
- [x] No behavior change to any working flow — nothing calls the new
      code yet; the removed code never ran

## What was NOT verified

Did not run the migration against a real or throwaway Supabase schema —
verified via the migration-reviewer agent + manual read instead, per this
round's standing instruction. This is schema + plumbing only — the loop
that actually reads/writes this data is the next commit.
