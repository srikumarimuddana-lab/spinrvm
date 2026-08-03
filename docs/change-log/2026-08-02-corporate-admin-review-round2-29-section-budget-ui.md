# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | admin-dashboard (company portal) |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "department/section budgets" — company-portal UI slice (final) |

## 1. Issue / gap identified

Final slice: the round2-28 API exists but the existing sections
management screen doesn't set a cap or show spend — and its own header
text/docstring ("budgets stay per-employee allowances") is now stale
given this round's visibility-only addition.

## 2. Root cause

Never built; the stale copy predates this feature (it was accurate when
written at migration 206's original design time).

## 3. Fix / remediation

- `CompanySection` type (`lib/companyApi.ts`) gains
  `monthly_budget_cap`/`budget_month`/`budget_spend_used`;
  `createCompanySection`/`updateCompanySection` accept the new field in
  their body types.
- Updated the existing `company-portal/[id]/sections/page.tsx`'s header
  paragraph and file-level docstring — both explicitly said "budgets stay
  per-employee allowances" with no mention that a section-level number
  now exists; reworded to state both facts accurately (employee limits
  are still per-member; section budgets are new, visibility-only, never
  block a booking) rather than leaving stale copy next to a new feature
  that contradicts it.
- Each active section row now shows "Budget: $X of $Y used this month
  (Z%)" when a cap is set, or "No monthly budget set" otherwise, plus an
  inline number input + Save button to set/change/clear it — matching
  this page's existing minimal inline-edit style (no new Dialog
  component introduced; the page has none today).
- Client-side validates non-negative before calling the API (a UX
  courtesy; the real enforcement is the backend's `ge=0` Pydantic
  constraint from round2-28, unchanged).

## 4. Risk & impact on existing functionality

- **Blast radius: one existing page's row-rendering block restructured
  (name/count/archive-button row now wrapped in a column, with a new
  budget sub-row appended) + one new state pair
  (`budgetDrafts`/`savingBudget`) + one new handler.** The existing
  create/archive/assign handlers (`handleCreate`, `handleArchive`,
  `handleAssign`) and their JSX are unchanged — confirmed by diff, only
  the section-row `<div>` structure changed to accommodate the new
  sub-row.
- Bracket-balance check (no TS/JS toolchain run, per this round's
  instruction) on both touched files — clean.
- Grepped for other consumers of `CompanySection`/`createCompanySection`/
  `updateCompanySection`: none outside this page and `companyApi.ts`
  itself — no other screen's behavior is affected by the type/signature
  additions (all new fields are optional).
- The budget UI only renders for `status === "active"` sections, matching
  the existing pattern where the Archive button is also
  active-status-only — no new control appears on an archived section.

## 5. User-experience effect

**Corporate-admin (company-side) facing.** A company admin managing
sections now sees each active section's monthly spend against an
optional budget, and can set/change/clear that budget inline. No
existing control (create, archive, member assignment) changes behavior.
The page's own description text now accurately reflects that a
section-level budget exists (previously it explicitly said the opposite).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/company-portal/[id]/sections/page.tsx` | Section row restructured to add a budget sub-row; new state + `handleSaveBudget`; updated header/docstring copy | Company-portal UI for the round2-28 API; fix stale "budgets stay per-employee" copy |
| `admin-dashboard/src/lib/companyApi.ts` | `CompanySection` type + `createCompanySection`/`updateCompanySection` body types gain the 3 new fields | Typed client for the round2-28 API |

## 7. Rollback plan

`git revert` the commit. Purely additive/cosmetic UI change — no data
written by this commit itself beyond what a company admin explicitly
sets via the new Save button (which round2-28's endpoint already
accepted before this UI existed).

## 8. Verification performed

- [x] Bracket-balance check on both touched files (no TS/JS toolchain
      run, per this round's instruction) — balanced.
- [x] Confirmed via diff that `handleCreate`/`handleArchive`/
      `handleAssign` and their JSX are byte-for-byte unchanged — only the
      section-row wrapper structure and the new budget sub-row were
      touched.
- [x] Confirmed the new field names/types in `CompanySection` match the
      round2-28 backend response shape exactly
      (`monthly_budget_cap`/`budget_month`/`budget_spend_used`).
- [x] Grepped for other consumers of the three touched exports: none
      outside this page.
- [x] Did **not** run `npm run build`, `tsc --noEmit`, `eslint`, or start
      the dev server — per this round's explicit instruction. This is
      the fourth `admin-dashboard`/company-portal file this round that
      has never been compiled (alongside round2-17, round2-20, round2-25)
      — all four should be checked together in the end-of-round
      `npm run build` pass, per CLAUDE.md's requirement that a real
      production build (not just `tsc --noEmit`) runs before any
      `admin-dashboard` change merges.

## 9. Sign-off

- [x] Rollback plan is concrete — `git revert`, no data involved beyond
      what a company admin explicitly opts into via the new control
- [x] Blast radius is stated, not assumed — confirmed via diff that all
      three existing handlers are unchanged
- [x] No silent behavior change to a working flow — create/archive/
      assign work exactly as before; only new, clearly-optional UI was
      added, and the one behavior-adjacent copy change (header text) was
      made because the old text had become factually wrong, not silently
      left to mislead

## What was NOT verified

Did not run `npm run build` or click through this page in a browser — no
visual/functional confirmation exists yet that the inline budget input,
percentage calculation, or Save flow behave correctly against real API
responses. This closes out the department/section-budgets feature build
(round2-26 through round2-29, 4 commits) and, with it, all seven of the
"ask me one-by-one" business-decision items reached so far except #67
(automated KYB re-verification, not yet reached). The end-of-round
`npm run build` pass for `admin-dashboard` now has four UI slices from
this round it has never compiled — this should be the first thing
checked in that pass, ahead of any test-suite run.
