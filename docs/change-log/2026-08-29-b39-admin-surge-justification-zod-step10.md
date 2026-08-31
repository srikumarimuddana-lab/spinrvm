# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-29 |
| Author | Claude Code session |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | corporate |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B39 — step 10 (KYB queue re-checked and confirmed to have nothing to migrate; moved to `service-areas/page.tsx`'s surge-justification gate) |

## 1. Issue / gap identified

`service-areas/page.tsx`'s `GeneralTabForm.handleSave` gates a surge-override
save with an inline check
(`surgeTouched && needsJustification && !form.surge_justification.trim()`)
implementing CLAUDE.md's documented surge rule — "Admin manual override
accepts 1.0–10.0 but any value > 2.5 requires documented justification
(regulatory + reputational risk)" — with no dedicated test coverage.

## 2. Root cause

Ad hoc validation predates any schema-validation library adoption on this
form; `admin-dashboard` already has `zod` (added in B39 step 4), but this
check was never migrated.

## 3. Fix / remediation

New colocated `admin-dashboard/src/lib/surgeJustificationSchema.ts`
extracts the check as two byte-for-byte equivalent pure predicates:
`needsSurgeJustification(surgeEnabled, multiplier)` (mirrors
`form.surge_enabled && surgeValue > 2.5`) and
`isSurgeJustificationValid(justification)` (mirrors
`!form.surge_justification.trim()`). `SURGE_JUSTIFICATION_THRESHOLD = 2.5`
mirrors `backend/utils/surge_engine.py`'s `SURGE_CAP` value (not imported
from it — the two are independent constants that happen to share a value;
see Section 4 for why that's acceptable here).

New `admin-dashboard/src/lib/__tests__/surgeJustificationSchema.test.ts` (9
accept/reject cases: above/at/under the threshold, surge disabled,
trimmed/whitespace-only justification).

Before picking this check, `kyb-queue/page.tsx` (named as the "KYB queue's
other fields" candidate at the end of step 9) was re-inspected field by
field and confirmed to have nothing to migrate: `approve`/`reject` are
plain action buttons with no input; the reject dialog's `rejectNote`
textarea is explicitly optional (`note: rejectNote.trim() || undefined`
— empty is a *valid* value, not a rejected one); the document-preview
link and `corporate-accounts/[id]/page.tsx`'s "KYB Verification" section
are pure read-only display. This confirms step 8's original note rather
than turning up new work.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one component in one file.** Grepped
  `admin-dashboard/src` for `surge_justification`, `needsJustification`,
  `surgeValue`, and `surgeTouched` — all four appear only inside
  `GeneralTabForm` in `service-areas/page.tsx` (plus the new schema/test
  files). No other screen reads or duplicates this logic.
- **Could this regress a flow that currently works?** No — this is a pure
  extraction, not a validation-rule change. `needsSurgeJustification`
  reproduces `form.surge_enabled && surgeValue > 2.5` exactly (same `&&`,
  same strict `>`, same threshold); `isSurgeJustificationValid` reproduces
  `!form.surge_justification.trim()` exactly via
  `z.string().trim().min(1)`. Verified against 9 accept/reject cases
  covering every boundary (including exactly-at-threshold) before
  applying.
- **This is a UX gate only — the enforced cap is untouched.** Per
  CLAUDE.md: "every fare-calc call site (`fare_service.py`,
  `routes/fares.py`, `features.py`) always clamps to `SURGE_CAP` (2.5×)
  — the override never actually reaches a rider's fare above the cap."
  This change touches only the admin-dashboard client-side justification
  prompt in front of the `onSave` call — not the backend clamp, not the
  audit-log write, not the value actually applied to fares. A blank
  justification still cannot reach the network call, exactly as before.
  `SURGE_JUSTIFICATION_THRESHOLD` is a new admin-dashboard-local constant
  with the same value as the backend's `SURGE_CAP`, not a shared import
  — the two systems (frontend UX gate, backend fare clamp) were already
  independent before this change; this extraction does not introduce or
  remove that independence.
- **Surge-domain interaction:** this form calls `onSave(updates)`, which
  PATCHes the service area's `surge_source`/`surge_enabled`/
  `surge_multiplier`/`surge_justification` fields. This change does not
  touch that request shape, the backend endpoint, or the surge engine
  itself (`backend/utils/surge_engine.py`) — only the client-side
  pre-submit gate in front of it.

## 5. User-experience effect

Internal-admin-facing only (not visible to riders, drivers, or corporate
admins — this is the internal Spinr admin dashboard's service-areas page).
No behavior change: the same gate, same `alert()` message, same silent
pass-through once justification is provided. Not visible mid-session to
anyone since this is an admin configuration screen, not a live-tested
rider/driver flow.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/surgeJustificationSchema.ts` | New file — two extracted predicates (`needsSurgeJustification`, `isSurgeJustificationValid`) + hoisted `SURGE_JUSTIFICATION_THRESHOLD` constant | Pulls `GeneralTabForm.handleSave`'s surge-justification gate into a colocated, independently testable module |
| `admin-dashboard/src/lib/__tests__/surgeJustificationSchema.test.ts` | New file — 9 accept/reject unit tests | Pins the extracted behavior so a future edit can't silently loosen/tighten the surge-justification requirement |
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | `needsJustification` and the `handleSave` guard now call the new predicates instead of the inline expressions | Same behavior, now backed by tested, colocated logic instead of ad hoc inline checks |

## 7. Before / after

```ts
// Before
const surgeValue = parseFloat(String(form.surge_multiplier)) || 1.0;
const needsJustification = form.surge_enabled && surgeValue > 2.5;

const handleSave = async () => {
    const surgeTouched = /* ... */;
    if (surgeTouched && needsJustification && !form.surge_justification.trim()) {
        alert("A written justification is required for surge multipliers above 2.5× (regulatory + reputational risk).");
        return;
    }
    // ...
};
```

```ts
// After
import { needsSurgeJustification, isSurgeJustificationValid } from "@/lib/surgeJustificationSchema";

const surgeValue = parseFloat(String(form.surge_multiplier)) || 1.0;
const needsJustification = needsSurgeJustification(form.surge_enabled, surgeValue);

const handleSave = async () => {
    const surgeTouched = /* ... */;
    if (surgeTouched && needsJustification && !isSurgeJustificationValid(form.surge_justification)) {
        alert("A written justification is required for surge multipliers above 2.5× (regulatory + reputational risk).");
        return;
    }
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
  CI invocation) — 404/404 tests, 40/40 files pass, exit 0 (coverage
  threshold gate unaffected — same pattern as every prior B39 step).
  New file alone: `npx vitest run src/lib/__tests__/surgeJustificationSchema.test.ts`
  — 9/9 pass.
- [ ] Manual repro steps followed in staging — not done; no staging access
  from this session. Verified instead against the 9 unit tests, which pin
  every accept/reject boundary the original inline check covered.
- [x] Blast-radius grep performed — searched `admin-dashboard/src` for
  all four surge-justification identifiers used by this check; all
  appear only in this one component (plus the new schema/test files).
- [x] Reviewed against relevant CLAUDE.md convention(s) — surge: this is
  exactly the "any value > 2.5 requires documented justification"
  admin-override rule from Critical Conventions; the change is a pure
  client-side gate extraction and does not touch `SURGE_CAP`, the
  fare-calc clamp, or the surge engine.
- [ ] N/A — Feature-flagged if user-visible and non-trivial: this is a
  pure extraction with identical behavior on an internal-admin-only
  screen, not new or changed UX, per B39's own established pattern for
  every prior step.

`npx tsc --noEmit`: clean. `npx eslint` on the three touched files: 0
errors, 65 pre-existing warnings (this is a large multi-tab file with
several unrelated `react-hooks/set-state-in-effect` and
`jsx-a11y/label-has-associated-control` findings) — confirmed via `git
diff` that none of them fall on the 3 lines this diff actually touches
(the new import line, and the two one-line predicate-call swaps at the
`needsJustification` assignment and the `handleSave` guard). **Real
production build** (`npm run build`) completed successfully, exit code 0,
full route manifest generated including `/dashboard/service-areas` — not
just `tsc`/dev server, per CLAUDE.md's explicit requirement for
admin-dashboard changes.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (grepped, isolated to one
  component in one file)
- [x] No silent behavior change to an already-shipped flow (Section 5 —
  same gate, same message; internal-admin-only, not a live rider/driver
  surface; Section 4 — the enforced `SURGE_CAP` clamp is untouched)

## What was NOT verified

- Not tested against a real backend — the `onSave` PATCH to the service
  area's surge fields was not exercised end-to-end in this session; this
  change only touches the client-side gate in front of it, which is
  unchanged in shape.
- No visual regression tooling exists for admin-dashboard's actual
  baselines (per CLAUDE.md, admin-dashboard's visual-regression job has
  zero committed baselines — B38) — not applicable here regardless, since
  this change has no visual/UI surface change (same alert text, same
  conditional textarea).
- `service-areas/page.tsx` is a very large multi-tab file (fees,
  incentives, heatmap config, Spinr Pass area toggles, cascade editor,
  etc.). Only the one surge-justification rule was audited and migrated
  in this step; the rest of the file was not swept field-by-field for
  other inline validation rules.
