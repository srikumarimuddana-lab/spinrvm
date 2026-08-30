# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude Code session |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B39 — step 11 (`staff/page.tsx`, named as a remaining candidate at the end of step 10) |

## 1. Issue / gap identified

`staff/page.tsx`'s `handleSubmit` — the staff account create/edit form —
validates required fields and (on create only) a password via two inline
checks (`!form.email || !form.first_name || !form.last_name`,
`!form.password`) with no dedicated test coverage.

## 2. Root cause

Ad hoc validation predates any schema-validation library adoption on this
form; `admin-dashboard` already has `zod` (added in B39 step 4), but this
form was never migrated.

## 3. Fix / remediation

New colocated `admin-dashboard/src/lib/staffFormSchema.ts` extracts the
two inline checks as byte-for-byte equivalent pure predicates:
`isStaffRequiredFieldsValid(email, firstName, lastName)` (mirrors
`!email || !first_name || !last_name`) and `isStaffPasswordValid(password)`
(mirrors `!password`, checked only on the create path). Both are plain
truthy checks on the raw string — no `.trim()` was added, since that would
tighten the accept/reject boundary and be a validation-rule change, not a
pure extraction.

New `admin-dashboard/src/lib/__tests__/staffFormSchema.test.ts` (7
accept/reject cases).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one admin-dashboard page.** Grepped
  `admin-dashboard/src` for `form.email`, `form.first_name`,
  `form.last_name`, `form.password` — all appear only in this page's own
  form state (plus the new schema/test files). A different, unrelated
  form on `support-tickets/tickets/page.tsx` also has a field literally
  named `form.email`, but it is a separate component with its own `form`
  state object, not a shared definition — confirmed by reading both
  files, not just the grep match.
- **Could this regress a flow that currently works?** No — this is a
  pure extraction, not a validation-rule change. `isStaffRequiredFieldsValid`
  reproduces the original three-field truthy check exactly (via
  `z.string().min(1)` on each field, same as a plain `!value` truthy
  check for a string); `isStaffPasswordValid` reproduces `!password`
  exactly. Verified against 7 accept/reject cases before applying.
- **Admin RBAC surface:** staff accounts are the module-grant workflow
  (`AVAILABLE_MODULES`/`ROLE_PRESETS` in `backend/routes/admin/staff.py`).
  This change touches only the client-side pre-submit gate in front of
  `createStaff`/`updateStaff` — not the request body shape, the backend
  endpoint, or the module-grant logic itself. A staff record with a
  missing required field or, on create, a blank password still cannot
  reach the network call, exactly as before.
- **Background loops / ride state machine:** not implicated — this is an
  admin-only staff-management form, no interaction with dispatch or the
  ride state machine.

## 5. User-experience effect

Internal-admin-facing only (super_admin-gated via `useRequireModule("staff")`
— not visible to riders, drivers, or corporate admins). No behavior
change: the same two checks, same silent early-return (this form never
showed an error message for these two guards — pressing "Create"/"Save"
with a missing field just no-ops, unchanged). Not visible mid-session to
anyone since this is an admin management screen, not a live-tested
rider/driver flow.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/staffFormSchema.ts` | New file — two extracted predicates (`isStaffRequiredFieldsValid`, `isStaffPasswordValid`) | Pulls `handleSubmit`'s two inline checks into a colocated, independently testable module |
| `admin-dashboard/src/lib/__tests__/staffFormSchema.test.ts` | New file — 7 accept/reject unit tests | Pins the extracted behavior so a future edit can't silently loosen/tighten the staff-form gate |
| `admin-dashboard/src/app/dashboard/staff/page.tsx` | `handleSubmit`'s two inline checks replaced with calls to the new predicates | Same behavior, now backed by tested, colocated logic instead of ad hoc inline checks |

## 7. Before / after

```ts
// Before
const handleSubmit = async () => {
    if (!form.email || !form.first_name || !form.last_name) return;
    try {
        if (editingId) {
            await updateStaff(editingId, { /* ... */ });
        } else {
            if (!form.password) return;
            await createStaff(form);
        }
        // ...
    } catch (e: any) { /* ... */ }
};
```

```ts
// After
import { isStaffRequiredFieldsValid, isStaffPasswordValid } from "@/lib/staffFormSchema";

const handleSubmit = async () => {
    if (!isStaffRequiredFieldsValid(form.email, form.first_name, form.last_name)) return;
    try {
        if (editingId) {
            await updateStaff(editingId, { /* ... */ });
        } else {
            if (!isStaffPasswordValid(form.password)) return;
            await createStaff(form);
        }
        // ...
    } catch (e: any) { /* ... */ }
};
```

## 8. Rollback plan

`git-revert-safe`. No data migration, no feature flag, no schema/table
change — this is a client-side validation-logic extraction with identical
behavior. Reverting restores the inline checks exactly as they were, with
no follow-up action needed.

## 9. Verification performed

- [x] Automated tests run — unit only: `npm run test:coverage` (the exact
  CI invocation) — 411/411 tests, 41/41 files pass, exit 0 (coverage
  threshold gate unaffected — same pattern as every prior B39 step).
  New file alone: `npx vitest run src/lib/__tests__/staffFormSchema.test.ts`
  — 7/7 pass.
- [ ] Manual repro steps followed in staging — not done; no staging
  access from this session. Verified instead against the 7 unit tests,
  which pin every accept/reject boundary the original inline checks
  covered.
- [x] Blast-radius grep performed — searched `admin-dashboard/src` for
  every field name used by these checks; confirmed isolated to this
  page's own form state.
- [x] Reviewed against relevant CLAUDE.md convention(s) — admin RBAC:
  this form feeds `createStaff`/`updateStaff` (the module-grant
  workflow), but the change is client-side pre-submit validation only,
  not a change to the module list, role presets, or backend gating.
- [ ] N/A — Feature-flagged if user-visible and non-trivial: this is a
  pure extraction with identical behavior on an internal-admin-only,
  super_admin-gated screen, not new or changed UX, per B39's own
  established pattern for every prior step.

`npx tsc --noEmit`: clean. `npx eslint` on the three touched files: 0
errors, 3 pre-existing warnings (`react-hooks/immutability` on
`loadStaff`'s use-before-declaration at line 125, and a
`jsx-a11y/label-has-associated-control` at line 325 — confirmed via `git
diff` that none of the flagged lines are part of this change). **Real
production build** (`npm run build`) completed successfully, exit code 0,
full route manifest generated including `/dashboard/staff` — not just
`tsc`/dev server, per CLAUDE.md's explicit requirement for
admin-dashboard changes.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (grepped, isolated to one
  page's own form)
- [x] No silent behavior change to an already-shipped flow (Section 5 —
  same checks, same silent early-return; internal-admin-only,
  super_admin-gated, not a live rider/driver surface)

## What was NOT verified

- Not tested against a real backend — `createStaff`/`updateStaff`'s
  actual network calls were not exercised end-to-end in this session;
  this change only touches the client-side gate in front of them, which
  is unchanged in shape.
- No visual regression tooling exists for admin-dashboard's actual
  baselines (per CLAUDE.md, admin-dashboard's visual-regression job has
  zero committed baselines — B38) — not applicable here regardless, since
  this change has no visual/UI surface change.
- The rest of `staff/page.tsx` (role-preset selection, module checkboxes,
  MFA reset, delete confirmation) was read in full but has no other
  accept/reject validation rule to extract — those are all
  plain-toggle/select interactions with no inline `if`-guard.
