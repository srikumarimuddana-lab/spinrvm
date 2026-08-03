# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate, payments |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "no pricing/fee mechanism exists for the corporate product" (business decision: flat SaaS subscription, full Stripe automation) |

## 1. Issue / gap identified

Spinr's corporate accounts have no monetization mechanism at all beyond
passing through the driver's normal consumer fare — there is no fee,
subscription, or billing concept for the company itself. CLAUDE.md
already states the intended model ("SaaS corporate accounts... never
per-trip cuts on consumer rides") but no code implements it.

## 2. Root cause

Never built. Corporate billing infrastructure (`corporate_wallet_apply_delta`,
Stripe customer creation at KYB approval) exists for *funding rides*, not
for *charging the company for the platform itself*.

## 3. Fix / remediation

First of a multi-commit build (decomposed per CLAUDE.md's ≤3-files/subtask
rule — this is the schema-only slice; service/webhook/route/UI land in
follow-up commits, each with its own Change Impact Log):

- New migration `280_corporate_subscriptions.sql`:
  - `corporate_subscription_plans` — admin-managed flat-tier catalog
    (`monthly_price NUMERIC(10,2)`, optional `stripe_price_id`).
  - `corporate_subscriptions` — one row per company subscription
    lifecycle (`status`: active/past_due/cancelled), with `price` locked
    in at assignment time so a later catalog price change never
    retroactively rebills an existing subscription. State is mirrored
    from real Stripe Subscription objects (created in the follow-up
    service commit) via webhook handlers (follow-up commit), the same
    pattern already used for `driver_subscriptions` (Spinr Pass,
    migration 09) — Stripe owns the recurring-charge schedule, this
    table is a read model of it.
  - Partial unique index `(company_id) WHERE status IN ('active',
    'past_due')` enforces at most one live subscription per company.
  - Both tables: RLS enabled, no policies (backend-only, service role
    bypass) — matches the `driver_statements` template (migration 272).
  - Reviewed by the `spinr-migration-reviewer` agent before committing:
    verdict "SAFE TO APPLY", numbering/RLS/reversibility/indexes/money-
    column-safety all confirmed clean.
- `backend/repositories/corporate_repo.py`: new CRUD helpers —
  `list_corporate_subscription_plans`, `get_corporate_subscription_plan`,
  `get_active_corporate_subscription`, `get_corporate_subscription_by_stripe_id`,
  `list_corporate_subscriptions_for_company`,
  `create_corporate_subscription_row`, `update_corporate_subscription` —
  following the file's existing `run_sync`/`_rows_from_res` pattern
  exactly.
- `backend/db_supabase.py`: re-exported the six new names in both
  dual-import branches, alphabetically ordered to match the existing
  list.

**No ride fare, dispatch, or wallet-funding code path is touched by this
commit or reachable through it** — this is exclusively new tables + pure
data-access helpers, nothing calls them yet.

## 4. Risk & impact on existing functionality

- **Blast radius: additive only.** Two brand-new tables, no existing
  table altered. Two brand-new repository functions groups appended to
  the end of `corporate_repo.py` (no existing function touched) and
  re-exported alongside, not in place of, existing names in
  `db_supabase.py`.
- Grepped `corporate_repo.py`/`db_supabase.py` for every other consumer
  of the surrounding code (the `upsert_corporate_policy` function these
  helpers were appended after): unaffected, its `return` statement was
  not touched — the new section starts strictly after it.
- No app code calls any of the new tables/helpers yet in this commit —
  zero runtime behavior change. The migration is safe to apply against
  live traffic (new, empty tables; no lock risk, no backfill).
- FK `corporate_subscriptions.company_id -> corporate_accounts(id)` has
  no `ON DELETE` clause (defaults to `NO ACTION`) — per migration
  reviewer, this is the correct default here (blocks company deletion
  until subscriptions are cleaned up, which is desired, not accidental).

## 5. User-experience effect

None yet — schema and repository layer only, no route or UI wired up.
No rider, driver, corporate-admin, or internal-admin surface changes in
this commit.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/280_corporate_subscriptions.sql` | New migration: `corporate_subscription_plans`, `corporate_subscriptions` | Data model for flat SaaS corporate billing |
| `backend/repositories/corporate_repo.py` | 7 new CRUD helper functions appended | Data-access layer for the new tables |
| `backend/db_supabase.py` | Re-exported the 7 new names in both dual-import branches | Match the established re-export convention |

## 7. Rollback plan

`DROP TABLE IF EXISTS public.corporate_subscriptions; DROP TABLE IF
EXISTS public.corporate_subscription_plans;` (documented in the
migration's top comment, correct child-then-parent order). Zero data
loss risk since no other code path writes to these tables yet in this
commit — nothing in production could depend on them existing.

## 8. Verification performed

- [x] `spinr-migration-reviewer` agent review — verdict SAFE TO APPLY,
      no blockers found.
- [x] `ast.parse` syntax check on both modified Python files — clean.
- [x] Manually traced that no existing function's `return` statement or
      surrounding code was altered by the appended block (only new code
      added after the file's existing final line).
- [x] Confirmed via grep that the 7 new names don't collide with any
      existing export in `db_supabase.py`.

## 9. Sign-off

- [x] Rollback plan is concrete and reversible with zero data-loss risk
- [x] Blast radius is stated, not assumed — additive-only, confirmed by
      diff inspection
- [x] No behavior change to any working flow — nothing calls the new
      code yet

## What was NOT verified

Did not run the migration against a real or throwaway Supabase schema —
per this round's "no tests/CI until everything is developed" instruction,
verified statically (migration reviewer agent + manual read) instead.
Did not run `pytest` for `corporate_repo.py`/`db_supabase.py` changes
(no behavior to test yet — pure data access with no caller). This is
schema + plumbing only; the actual Stripe-integration risk (subscription
creation, webhook sync, money movement) is entirely in the follow-up
commits, each of which will carry its own verification and its own
"what was NOT verified" section.
