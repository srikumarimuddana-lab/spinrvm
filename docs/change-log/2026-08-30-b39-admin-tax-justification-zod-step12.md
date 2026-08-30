# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude Code session |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | corporate |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B39 — step 12 (found while re-scanning `service-areas/page.tsx` for other inline rules after step 10) |

## 1. Issue / gap identified

`service-areas/page.tsx`'s `handleFieldUpdate` gates any GST/PST/HST
field change with an inline check (`if (!justification) return;`) on a
`window.prompt(...)`-collected written justification, with no dedicated
test coverage. Per the page's own comment (A29): "GST/PST/HST config
carries real regulatory + financial weight (every rider's charge, CRA/SK
remittance), so the backend now requires a written justification for any
of these fields."

## 2. Root cause

Ad hoc validation predates any schema-validation library adoption on this
form; `admin-dashboard` already has `zod` (added in B39 step 4), but this
check was never migrated.

## 3. Fix / remediation

New colocated `admin-dashboard/src/lib/taxJustificationSchema.ts` extracts
the check as a byte-for-byte equivalent pure predicate:
`isTaxJustificationValid(justification)` (mirrors `!justification` on the
already-`.trim()`-ed prompt result).

New `admin-dashboard/src/lib/__tests__/taxJustificationSchema.test.ts` (6
accept/reject cases: non-empty, trimmed, `undefined` (Cancel on the
prompt), `null`, empty string, whitespace-only).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one function in one file.** Grepped
  `admin-dashboard/src` for `tax_justification` and `TAX_FIELDS` — both
  appear only inside `handleFieldUpdate` in `service-areas/page.tsx`
  (plus the new schema/test files). No other screen reads or duplicates
  this logic.
- **Could this regress a flow that currently works?** No — this is a pure
  extraction, not a validation-rule change. `isTaxJustificationValid`
  reproduces `!justification` exactly via `z.string().trim().min(1)`
  (the value passed in is already trimmed at the call site, so the
  schema's own `.trim()` is a no-op there — harmless, and consistent
  with every other B39 predicate's shape). Verified against 6
  accept/reject cases before applying.
- **This is a UX gate only — the backend enforces the same rule
  independently.** Per the page's own comment, the backend "now requires
  a written justification for any of these fields" and would 400 without
  one; this client-side prompt exists "rather than letting the save
  silently 400." This change touches only the client-side pre-submit
  gate in front of `updateServiceArea` — not the backend's own
  requirement, not the request shape, not the tax-rate values themselves.
  A blank justification still cannot reach the network call, exactly as
  before.
- **Regulatory/tax-domain interaction:** GST/PST/HST rates feed rider
  receipt line items (per CLAUDE.md's Saskatchewan Regulatory section:
  "Rider receipts must show GST (5%) and PST (6% where applicable) as
  separate line items"). This change does not touch the tax calculation,
  the receipt line-item logic, or the rate values themselves — only the
  justification-prompt gate in front of a rate *change*.

## 5. User-experience effect

Internal-admin-facing only (not visible to riders, drivers, or corporate
admins — this is the internal Spinr admin dashboard's service-areas tax
configuration). No behavior change: same prompt, same silent-cancel path
on an empty/whitespace-only/cancelled justification. Not visible
mid-session to anyone since this is an admin configuration action, not a
live-tested rider/driver flow.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/taxJustificationSchema.ts` | New file — one extracted predicate (`isTaxJustificationValid`) | Pulls `handleFieldUpdate`'s tax-justification gate into a colocated, independently testable module |
| `admin-dashboard/src/lib/__tests__/taxJustificationSchema.test.ts` | New file — 6 accept/reject unit tests | Pins the extracted behavior so a future edit can't silently loosen/tighten the tax-justification requirement |
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | `handleFieldUpdate`'s inline check replaced with a call to the new predicate | Same behavior, now backed by tested, colocated logic instead of an ad hoc inline check |

## 7. Before / after

```ts
// Before
const handleFieldUpdate = async (areaId: string, field: string, value: any) => {
    try {
        const payload: Record<string, any> = { [field]: value };
        if (TAX_FIELDS.has(field)) {
            const justification = window.prompt("Reason for this tax-configuration change (required):")?.trim();
            if (!justification) return;
            payload.tax_justification = justification;
        }
        await updateServiceArea(areaId, payload);
        // ...
    } catch (e) { /* ... */ }
};
```

```ts
// After
import { isTaxJustificationValid } from "@/lib/taxJustificationSchema";

const handleFieldUpdate = async (areaId: string, field: string, value: any) => {
    try {
        const payload: Record<string, any> = { [field]: value };
        if (TAX_FIELDS.has(field)) {
            const justification = window.prompt("Reason for this tax-configuration change (required):")?.trim();
            if (!isTaxJustificationValid(justification)) return;
            payload.tax_justification = justification;
        }
        await updateServiceArea(areaId, payload);
        // ...
    } catch (e) { /* ... */ }
};
```

## 8. Rollback plan

`git-revert-safe`. No data migration, no feature flag, no schema/table
change — this is a client-side validation-logic extraction with identical
behavior. Reverting restores the inline check exactly as it was, with no
follow-up action needed.

## 9. Verification performed

- [x] Automated tests run — unit only: `npm run test:coverage` (the exact
  CI invocation) — 417/417 tests, 42/42 files pass, exit 0. This PR
  branches from step 11's commit (staff-form schema, PR #4705, not yet
  merged) rather than from `main` directly — step 11's 7 tests are
  included in this count alongside step 12's own 6. New file alone:
  `npx vitest run src/lib/__tests__/taxJustificationSchema.test.ts` —
  6/6 pass.
- [ ] Manual repro steps followed in staging — not done; no staging
  access from this session. Verified instead against the 6 unit tests,
  which pin every accept/reject boundary the original inline check
  covered.
- [x] Blast-radius grep performed — searched `admin-dashboard/src` for
  `tax_justification` and `TAX_FIELDS`; both appear only in this one
  function (plus the new schema/test files).
- [x] Reviewed against relevant CLAUDE.md convention(s) — Saskatchewan
  Regulatory tax section (GST/PST line-item disclosure): the change is a
  client-side gate extraction only, not a change to tax rates, receipt
  line items, or the backend's own independent enforcement.
- [ ] N/A — Feature-flagged if user-visible and non-trivial: this is a
  pure extraction with identical behavior on an internal-admin-only
  screen, not new or changed UX, per B39's own established pattern for
  every prior step.

`npx tsc --noEmit`: clean. `npx eslint` on the three touched files: 0
errors, 65 pre-existing warnings (this is the same large multi-tab file
as step 10 — several unrelated `react-hooks/set-state-in-effect` and
`jsx-a11y/label-has-associated-control` findings) — confirmed via `git
diff` that none fall on the 2 lines this diff actually touches (the new
import line and the one-line predicate-call swap). **Real production
build** (`npm run build`) completed successfully, exit code 0, full route
manifest generated including `/dashboard/service-areas` — not just
`tsc`/dev server, per CLAUDE.md's explicit requirement for
admin-dashboard changes.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (grepped, isolated to one
  function in one file)
- [x] No silent behavior change to an already-shipped flow (Section 5 —
  same prompt, same silent-cancel path; internal-admin-only, not a live
  rider/driver surface; Section 4 — the backend's independent enforcement
  is untouched)

## What was NOT verified

- Not tested against a real backend — the `updateServiceArea` PATCH
  carrying `tax_justification` was not exercised end-to-end in this
  session; this change only touches the client-side gate in front of it,
  which is unchanged in shape.
- No visual regression tooling exists for admin-dashboard's actual
  baselines (per CLAUDE.md, admin-dashboard's visual-regression job has
  zero committed baselines — B38) — not applicable here regardless, since
  this change has no visual/UI surface change (the prompt is a native
  `window.prompt()`, not a rendered dialog).
- The rest of `service-areas/page.tsx` beyond the surge (step 10) and tax
  (step 12) justification gates — fees, incentives, Spinr Pass area
  toggles — was not swept field-by-field for other inline validation
  rules in this step.
