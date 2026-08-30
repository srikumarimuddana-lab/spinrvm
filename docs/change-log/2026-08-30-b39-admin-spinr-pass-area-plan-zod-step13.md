# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude Code session |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | corporate |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B39 — step 13 (found while sweeping `service-areas/page.tsx` per step 12's "still open" note) |

## 1. Issue / gap identified

`service-areas/page.tsx`'s `SpinrPassAreaTab.handleSubmit` — a second,
separate Spinr Pass plan create/edit form — validates the plan name and
price via `if (!form.name || !form.price) return;` with no dedicated test
coverage.

## 2. Root cause

Ad hoc validation predates any schema-validation library adoption on this
form; `admin-dashboard` already has `zod` (added in B39 step 4), but this
check was never migrated.

## 3. Fix / remediation

New colocated `admin-dashboard/src/lib/spinrPassAreaPlanSchema.ts`
extracts the check as two byte-for-byte equivalent pure predicates:
`isSpinrPassPlanNameValid` (mirrors `!form.name`) and
`isSpinrPassPlanPriceValid` (mirrors `!form.price` — a non-empty-string
truthy check, not a numeric validity check).

New `admin-dashboard/src/lib/__tests__/spinrPassAreaPlanSchema.test.ts` (6
accept/reject cases).

**Notable finding, not fixed in this step:** this is a second, independent
implementation of a Spinr Pass plan form. `subscriptions/page.tsx`'s
`PlanModal` (B39 step 9) is a different component with its own `form`
state that also calls `createSubscriptionPlan`/`updateSubscriptionPlan`.
The two are not byte-for-byte equivalent to each other — this form's
`price` is a raw string (`useState({ price: "" })`) parsed with
`parseFloat` only at submit time, checked with a non-empty-string truthy
guard (`"0"` passes, `""` fails); step 9's form keeps `price` as a
`number` and checks `form.price < 0`. Because the underlying types
genuinely differ, this extraction created a separate schema file rather
than reusing step 9's predicates — merging them would have been a
validation-rule change, not a pure extraction. Flagging the duplicate
implementation itself as a candidate for a future de-duplication item;
not addressed here.

**Pre-existing gap, documented not fixed:** neither the original code nor
this extraction guards against a non-numeric price string (e.g. `"abc"`
→ `parseFloat` → `NaN` sent to `createSubscriptionPlan`/
`updateSubscriptionPlan`). This gap predates this change. Per CLAUDE.md's
release-gate guidance (never a silent behavior change to an
already-shipped screen), it is documented in the schema file's comments
and pinned by an explicit test case rather than silently tightened.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one component in one file.** Grepped
  `admin-dashboard/src` for `form.price`; the only other match is
  `subscriptions/page.tsx`'s own `form.price`, confirmed (by reading both
  files) to be a different component's own state, not shared. `form.name`
  is a common field name across several forms in this session's prior
  B39 steps, but each is its own component's local state — verified this
  one is `SpinrPassAreaTab`'s.
- **Could this regress a flow that currently works?** No — this is a
  pure extraction, not a validation-rule change. Both predicates
  reproduce their original truthy checks exactly. Verified against 6
  accept/reject cases, including the deliberately-unchanged
  non-numeric-string-accepted case, before applying.
- **Money-path interaction:** this form's `price` feeds the same
  `createSubscriptionPlan`/`updateSubscriptionPlan` calls as
  `subscriptions/page.tsx`'s already-migrated form (step 9), which in
  turn feed driver-side Stripe Billing. This change touches only the
  client-side pre-submit gate, not the request shape, the backend
  endpoint, or the Stripe integration.
- **Background loops / ride state machine:** not implicated — this is an
  admin-only Spinr Pass plan management form, no interaction with
  dispatch or the ride state machine.

## 5. User-experience effect

Internal-admin-facing only (not visible to riders, drivers, or corporate
admins — this is the internal Spinr admin dashboard's service-areas
Spinr Pass tab). No behavior change: the same two checks, same silent
early-return (this form shows no error message for these guards, only a
generic "Failed to save plan" toast if the network call itself fails —
unchanged). Not visible mid-session to anyone since this is an admin
configuration screen, not a live-tested rider/driver flow.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/spinrPassAreaPlanSchema.ts` | New file — two extracted predicates (`isSpinrPassPlanNameValid`, `isSpinrPassPlanPriceValid`) | Pulls `SpinrPassAreaTab.handleSubmit`'s two inline checks into a colocated, independently testable module |
| `admin-dashboard/src/lib/__tests__/spinrPassAreaPlanSchema.test.ts` | New file — 6 accept/reject unit tests | Pins the extracted behavior, including the pre-existing non-numeric-price gap, so a future edit can't silently change it either way without a test failing |
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | `handleSubmit`'s inline check replaced with calls to the new predicates | Same behavior, now backed by tested, colocated logic instead of an ad hoc inline check |

## 7. Before / after

```ts
// Before
const handleSubmit = async () => {
    if (!form.name || !form.price) return;
    const data = {
        name: form.name, price: parseFloat(form.price), /* ... */
    };
    // ...
};
```

```ts
// After
import { isSpinrPassPlanNameValid, isSpinrPassPlanPriceValid } from "@/lib/spinrPassAreaPlanSchema";

const handleSubmit = async () => {
    if (!isSpinrPassPlanNameValid(form.name) || !isSpinrPassPlanPriceValid(form.price)) return;
    const data = {
        name: form.name, price: parseFloat(form.price), /* ... */
    };
    // ...
};
```

## 8. Rollback plan

`git-revert-safe`. No data migration, no feature flag, no schema/table
change — this is a client-side validation-logic extraction with identical
behavior. Reverting restores the inline check exactly as it was, with no
follow-up action needed.

## 9. Verification performed

- [x] Automated tests run — unit only: `npm run test:coverage` (the exact
  CI invocation) — 423/423 tests, 43/43 files pass, exit 0 (coverage
  threshold gate unaffected — same pattern as every prior B39 step).
  New file alone: `npx vitest run src/lib/__tests__/spinrPassAreaPlanSchema.test.ts`
  — 6/6 pass.
- [ ] Manual repro steps followed in staging — not done; no staging
  access from this session. Verified instead against the 6 unit tests.
- [x] Blast-radius grep performed — searched `admin-dashboard/src` for
  `form.price`; confirmed the only other match is an unrelated
  component's own state.
- [x] Reviewed against relevant CLAUDE.md convention(s) — money: `price`
  feeds driver-side Stripe Billing via the same subscription-plan API as
  step 9's form, but the change is client-side pre-submit validation
  only.
- [ ] N/A — Feature-flagged if user-visible and non-trivial: this is a
  pure extraction with identical behavior on an internal-admin-only
  screen, not new or changed UX, per B39's own established pattern for
  every prior step.

`npx tsc --noEmit`: clean. `npx eslint` on the three touched files: 0
errors, 65 pre-existing warnings (this is the same large multi-tab file
as steps 10/12) — confirmed via `git diff` that none fall on the 2 lines
this diff touches. **Real production build** (`npm run build`) completed
successfully, exit code 0, full route manifest generated including
`/dashboard/service-areas` — not just `tsc`/dev server, per CLAUDE.md's
explicit requirement for admin-dashboard changes.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (grepped, isolated to one
  component in one file, cross-checked against the similarly-named
  step 9 form)
- [x] No silent behavior change to an already-shipped flow (Section 5 —
  same checks, same silent early-return; the pre-existing non-numeric-
  price gap is explicitly preserved and documented, not silently fixed
  or silently left undocumented)

## What was NOT verified

- Not tested against a real backend — `createSubscriptionPlan`/
  `updateSubscriptionPlan`'s actual network calls were not exercised
  end-to-end in this session; this change only touches the client-side
  gate in front of them, which is unchanged in shape.
- No visual regression tooling exists for admin-dashboard's actual
  baselines (per CLAUDE.md, admin-dashboard's visual-regression job has
  zero committed baselines — B38) — not applicable here regardless, since
  this change has no visual/UI surface change.
- Whether the duplicate-implementation finding (two independent Spinr
  Pass plan forms) should be consolidated is a design question, not
  something this extraction-only step resolves — flagged in
  ACTION_ITEMS.md for a future decision, not decided here.
- `service-areas/page.tsx`'s create-service-area form
  (`!createForm.name`) and airport-zone form (`!airportForm.name ||
  polygon.length < 3`) were found during this sweep but not migrated in
  this step — flagged as open candidates, not addressed.
