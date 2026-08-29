# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-29 |
| Author | Claude Code session |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | corporate |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B39 — step 9 (the top-level `subscriptions/page.tsx`, following step 8's per-company wallet-adjustment form) |

## 1. Issue / gap identified

`subscriptions/page.tsx`'s `PlanModal.handleSubmit` — the Spinr Pass
subscription plan create/edit form — validates the plan name and price via
two inline checks (`!form.name.trim()`, `form.price < 0`) with no dedicated
test coverage. This is the "top-level subscriptions list" form flagged as
unchecked at the end of B39 step 8.

## 2. Root cause

Ad hoc validation predates any schema-validation library adoption on this
form; `admin-dashboard` already has `zod` (added in B39 step 4), but this
form was never migrated.

## 3. Fix / remediation

New colocated `admin-dashboard/src/lib/subscriptionPlanSchema.ts` extracts
the two inline checks as byte-for-byte equivalent pure predicates:
`isPlanNameValid` (mirrors `!form.name.trim()`) and `isPlanPriceValid`
(mirrors `form.price < 0`). Kept as two separate functions rather than one
aggregate boolean because `handleSubmit` shows a different error message
per failing check.

New `admin-dashboard/src/lib/__tests__/subscriptionPlanSchema.test.ts` (7
accept/reject cases: trimmed name, whitespace-only name, empty name, and
positive/zero/negative price).

Before picking this form, the page's other three tabs were checked:
`TaxConfigModal.handleSave` has no JS-level validation at all — only HTML
`min`/`max` attributes on the GST/PST/HST rate `<Input type="number">`
fields, no inline `if` check gating the save call — so there is no
accept/reject rule to extract, same "nothing to extract" call as
`subscription/page.tsx`'s plan-assignment and `kyb-queue/page.tsx`'s
reject-note in step 8. The Driver Subscriptions and Transactions tabs are
read-only views with no form to validate.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one admin-dashboard page.** Grepped
  `admin-dashboard/src` for the two error message strings ("Plan name is
  required.", "Price must be ≥ 0.") and for `form.name.trim()` /
  `form.price < 0` — both checks appear only in `PlanModal` inside
  `subscriptions/page.tsx` (plus the new schema/test files). No other
  screen reads or duplicates this logic.
- **Could this regress a flow that currently works?** No — this is a pure
  extraction, not a validation-rule change. `isPlanNameValid` reproduces
  `!form.name.trim()` exactly via `z.string().trim().min(1)`;
  `isPlanPriceValid` reproduces `form.price < 0` exactly via `price >= 0`
  (the same boundary — zero is accepted by both). Verified against 7
  accept/reject cases covering every boundary before applying.
- **Money-path interaction:** `handleSubmit` calls `onSave`, which posts to
  `createSubscriptionPlan`/`updateSubscriptionPlan` (`@/lib/api`). The
  `price` field feeds driver-side Stripe Checkout/Billing when a driver
  subscribes, but this change touches only the client-side pre-submit
  gate, not the request body shape, the backend endpoint, or the
  Stripe integration itself — a rejected-client-side price still never
  reaches the network call, exactly as before.
- **Background loops / ride state machine:** not implicated — this is an
  admin-only subscription-plan management form, no interaction with
  dispatch or the ride state machine.

## 5. User-experience effect

Internal-admin-facing only (not visible to riders, drivers, or corporate
admins — this is the internal Spinr admin dashboard's Spinr Pass
subscriptions page). No behavior change: the same two checks, same two
error messages under the dialog's form. Not visible mid-session to anyone
since this is an admin management screen, not a live-tested rider/driver
flow.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/subscriptionPlanSchema.ts` | New file — two extracted predicates (`isPlanNameValid`, `isPlanPriceValid`) | Pulls `PlanModal.handleSubmit`'s two inline checks into a colocated, independently testable module |
| `admin-dashboard/src/lib/__tests__/subscriptionPlanSchema.test.ts` | New file — 7 accept/reject unit tests | Pins the extracted behavior so a future edit can't silently loosen/tighten the plan-form gate |
| `admin-dashboard/src/app/dashboard/subscriptions/page.tsx` | `PlanModal.handleSubmit`'s two inline checks replaced with calls to the new predicates | Same behavior, now backed by tested, colocated logic instead of ad hoc inline checks |

## 7. Before / after

```ts
// Before
const handleSubmit = async () => {
    if (!form.name.trim()) { setError("Plan name is required."); return; }
    if (form.price < 0) { setError("Price must be ≥ 0."); return; }
    setSaving(true);
    // ...
};
```

```ts
// After
import { isPlanNameValid, isPlanPriceValid } from "@/lib/subscriptionPlanSchema";

const handleSubmit = async () => {
    if (!isPlanNameValid(form.name)) { setError("Plan name is required."); return; }
    if (!isPlanPriceValid(form.price)) { setError("Price must be ≥ 0."); return; }
    setSaving(true);
    // ...
};
```

## 8. Rollback plan

`git-revert-safe`. No data migration, no feature flag, no schema/table
change — this is a client-side validation-logic extraction with identical
behavior. Reverting restores the inline checks exactly as they were, with
no follow-up action needed.

## 9. Verification performed

- [x] Automated tests run — unit only: `npm run test:coverage` (the exact
  CI invocation) — 395/395 tests, 39/39 files pass, exit 0 (coverage
  threshold gate unaffected — same pattern as every prior B39 step).
  New file alone: `npx vitest run src/lib/__tests__/subscriptionPlanSchema.test.ts`
  — 7/7 pass.
- [ ] Manual repro steps followed in staging — not done; no staging access
  from this session. Verified instead against the 7 unit tests, which pin
  every accept/reject boundary the original inline checks covered.
- [x] Blast-radius grep performed — searched `admin-dashboard/src` for
  both error message strings and the original inline-check expressions;
  both appear only on this one page (plus the new schema/test files).
- [x] Reviewed against relevant CLAUDE.md convention(s) — money: `price`
  feeds driver-side Stripe Billing, but the change is client-side
  pre-submit validation only, not a change to Decimal arithmetic, the
  Stripe integration, or the endpoint's request/response shape.
- [ ] N/A — Feature-flagged if user-visible and non-trivial: this is a
  pure extraction with identical behavior on an internal-admin-only
  screen, not new or changed UX, per B39's own established pattern for
  every prior step.

`npx tsc --noEmit`: clean. `npx eslint` on the three touched files: 0
errors, 5 pre-existing warnings (`react-hooks/set-state-in-effect` on three
`useEffect` calls at lines 541/542/544 — confirmed via `git diff` that none
of those lines are part of this change). **Real production build**
(`npm run build`) completed successfully, exit code 0, full route manifest
generated including `/dashboard/subscriptions` — not just `tsc`/dev server,
per CLAUDE.md's explicit requirement for admin-dashboard changes.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (grepped, isolated to one page)
- [x] No silent behavior change to an already-shipped flow (Section 5 —
  same checks, same messages; internal-admin-only, not a live rider/driver
  surface)

## What was NOT verified

- Not tested against a real backend — `createSubscriptionPlan`/
  `updateSubscriptionPlan`'s actual network calls were not exercised
  end-to-end in this session; this change only touches the client-side
  gate in front of them, which is unchanged in shape.
- No visual regression tooling exists for admin-dashboard's actual
  baselines (per CLAUDE.md, admin-dashboard's visual-regression job has
  zero committed baselines — B38) — not applicable here regardless, since
  this change has no visual/UI surface change (same dialog, same error
  text, same layout).
- Every other admin-dashboard corporate/billing form not named in this or
  prior B39 steps (KYB queue's other fields, service-areas, staff, etc.)
  was not inspected in this step.
