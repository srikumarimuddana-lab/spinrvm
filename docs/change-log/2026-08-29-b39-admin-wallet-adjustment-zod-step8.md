# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-29 |
| Author | Claude Code session |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | corporate |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B39 — step 8 (admin-dashboard's second zod migration, following step 4's allowance-dialog and step 6's driver-app profile-setup) |

## 1. Issue / gap identified

`corporate-accounts/[id]/page.tsx`'s `handleAdjust` — the corporate wallet
manual-adjustment form — validates a signed CAD amount and a required
reason via three separate inline checks
(`isNaN(amount) || amount === 0`, `Math.abs(amount) > MAX_SINGLE_ADJUSTMENT`,
`!notes.trim()`) with no dedicated test coverage. This is the exact
"wallet adjustments" form B39's own text names by name as a remaining
unmigrated admin-dashboard corporate/billing form.

## 2. Root cause

Ad hoc validation predates any schema-validation library adoption on this
form; `admin-dashboard` already has `zod` (added in B39 step 4 for
`allowanceFormSchema.ts`), but this specific form was never migrated.

## 3. Fix / remediation

New colocated `admin-dashboard/src/lib/walletAdjustmentSchema.ts` extracts
the three inline checks as byte-for-byte equivalent pure predicates:
`isAdjustmentAmountValid` (mirrors the `isNaN`/zero check),
`isAdjustmentAmountWithinLimit` (mirrors the `Math.abs(...) >
MAX_SINGLE_ADJUSTMENT` check), and `isAdjustmentNoteValid` (mirrors
`!notes.trim()`). A combined `isAdjustmentAmountFullyValid` is also
exported for any future non-toast caller, though `handleAdjust` itself
still calls the two amount predicates separately (see Before/After) so
its per-failure toast message is unchanged. The `MAX_SINGLE_ADJUSTMENT =
10000` constant moved from a local `const` inside the component to this
schema file — same value, now a single source instead of the page
building its "$10,000.00" toast copy from a number the validation logic
also depends on.

New `admin-dashboard/src/lib/__tests__/walletAdjustmentSchema.test.ts` (18
accept/reject cases: positive/negative/fractional amounts, zero, NaN, the
exact ±cap boundary, over-cap on either sign, non-empty/whitespace-only
notes).

Before picking this form, the other two forms named in the same
"still open" note were checked and found to have nothing worth
extracting (documented in ACTION_ITEMS.md, not repeated in full here):
`subscription/page.tsx`'s plan-assignment `handleAssign` is gated only on
`!selectedPlan` (a plain truthy check, no rule behind it), and
`kyb-queue/page.tsx`'s reject-note field is explicitly optional
(`rejectNote.trim() || undefined`) — same "nothing to extract" call as
`rider-app/login.tsx` in step 7, not a skipped-without-checking gap.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one admin-dashboard page.** Grepped
  `admin-dashboard/src` for every reference to `handleAdjust` and
  `MAX_SINGLE_ADJUSTMENT` — both appear only in
  `corporate-accounts/[id]/page.tsx` (plus the new schema file and its
  test). No other screen reads or duplicates this logic.
- **Could this regress a flow that currently works?** No — this is a
  pure extraction, not a validation-rule change. Each new predicate
  reproduces its original inline check exactly:
  `isNaN(x) || x === 0` → `Number.isNaN(x) || x === 0` (equivalent;
  `parseFloat`'s `NaN` result behaves identically under both spellings);
  `Math.abs(x) > MAX` → `!(Math.abs(x) <= MAX)`, the same boundary
  (`<=` accepts exactly `MAX`, matching the original `>` rejecting only
  strictly-over); `!notes.trim()` → `!adjustmentNoteSchema.safeParse(notes.trim()).success`
  with a `.min(1)` schema, the same non-empty-after-trim rule. Verified
  against 18 accept/reject cases covering every boundary before applying.
- **Money-path interaction:** `handleAdjust` posts directly to
  `/api/admin/corporate-accounts/{id}/wallet/adjust`
  (`corporate_wallet_apply_delta`-backed per CLAUDE.md's Critical
  Conventions). This change touches only the client-side pre-submit gate,
  not the request body shape, the backend endpoint, or the wallet-delta
  function itself — a rejected-client-side amount still never reaches the
  network call, exactly as before.
- **Background loops / ride state machine:** not implicated — this is an
  admin-only corporate wallet form, no interaction with dispatch or the
  ride state machine.

## 5. User-experience effect

Internal-admin-facing only (not visible to riders, drivers, or corporate
admins — this is the internal Spinr admin dashboard's corporate-accounts
detail page). No behavior change: the same three checks, same two toast
messages on invalid amount, same silent-cancel on an empty reason. Not
visible mid-session to anyone since this is an admin action screen, not a
live-tested rider/driver flow.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/walletAdjustmentSchema.ts` | New file — three extracted predicates + combined helper + hoisted `MAX_SINGLE_ADJUSTMENT` constant | Pulls `handleAdjust`'s three inline checks into a colocated, independently testable module, closing B39's "validation-rule coverage is invisible" gap for this form |
| `admin-dashboard/src/lib/__tests__/walletAdjustmentSchema.test.ts` | New file — 18 accept/reject unit tests | Pins the extracted behavior so a future edit can't silently loosen/tighten the wallet-adjustment gate |
| `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/page.tsx` | `handleAdjust`'s three inline checks replaced with calls to the new predicates; `MAX_SINGLE_ADJUSTMENT` import replaces the local `const` | Same behavior, now backed by tested, colocated logic instead of ad hoc inline checks |

## 7. Before / after

```ts
// Before
const MAX_SINGLE_ADJUSTMENT = 10000; // $10,000 CAD safety cap

const handleAdjust = async () => {
    if (!id) return;
    const raw = window.prompt("Adjustment amount (signed CAD):");
    if (!raw) return;
    const adjustmentAmount = parseFloat(raw);
    if (isNaN(adjustmentAmount) || adjustmentAmount === 0) {
        toast({ title: "Invalid amount", description: "Adjustment amount cannot be zero", variant: "destructive" });
        return;
    }
    if (Math.abs(adjustmentAmount) > MAX_SINGLE_ADJUSTMENT) {
        toast({
            title: "Amount exceeds limit",
            description: `Single adjustment cannot exceed $${MAX_SINGLE_ADJUSTMENT.toFixed(2)}`,
            variant: "destructive",
        });
        return;
    }
    const notes = window.prompt("Reason (required):") ?? "";
    if (!notes.trim()) return;
    // ...
};
```

```ts
// After
import {
    MAX_SINGLE_ADJUSTMENT,
    isAdjustmentAmountValid,
    isAdjustmentAmountWithinLimit,
    isAdjustmentNoteValid,
} from "@/lib/walletAdjustmentSchema";

const handleAdjust = async () => {
    if (!id) return;
    const raw = window.prompt("Adjustment amount (signed CAD):");
    if (!raw) return;
    const adjustmentAmount = parseFloat(raw);
    if (!isAdjustmentAmountValid(adjustmentAmount)) {
        toast({ title: "Invalid amount", description: "Adjustment amount cannot be zero", variant: "destructive" });
        return;
    }
    if (!isAdjustmentAmountWithinLimit(adjustmentAmount)) {
        toast({
            title: "Amount exceeds limit",
            description: `Single adjustment cannot exceed $${MAX_SINGLE_ADJUSTMENT.toFixed(2)}`,
            variant: "destructive",
        });
        return;
    }
    const notes = window.prompt("Reason (required):") ?? "";
    if (!isAdjustmentNoteValid(notes)) return;
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
  CI invocation) — 388/388 tests, 38/38 files pass, exit 0 (coverage
  threshold gate unaffected — same pattern as every prior B39 step).
  New file alone: `npx vitest run src/lib/__tests__/walletAdjustmentSchema.test.ts`
  — 18/18 pass.
- [ ] Manual repro steps followed in staging — not done; no staging
  access from this session. Verified instead against the 18 unit tests,
  which pin every accept/reject boundary the original inline checks
  covered.
- [x] Blast-radius grep performed — searched `admin-dashboard/src` for
  every reference to `handleAdjust` and `MAX_SINGLE_ADJUSTMENT`; both
  appear only on this one page (plus the new schema/test files).
- [x] Reviewed against relevant CLAUDE.md convention(s) — money: this
  form feeds `corporate_wallet_apply_delta`, but the change is
  client-side pre-submit validation only, not a change to Decimal
  arithmetic, the delta function, or the endpoint's request/response
  shape.
- [ ] N/A — Feature-flagged if user-visible and non-trivial: this is a
  pure extraction with identical behavior on an internal-admin-only
  screen, not new or changed UX, per B39's own established pattern for
  every prior step.

`npx tsc --noEmit`: clean. `npx eslint` on the three touched files: 0
errors, 2 pre-existing warnings (`react-hooks/set-state-in-effect` on two
`useEffect` calls at lines 193/198 — confirmed via `git diff` that
neither line is part of this change). **Real production build**
(`npm run build`) completed successfully, exit code 0, full route
manifest generated — not just `tsc`/dev server, per CLAUDE.md's explicit
requirement for admin-dashboard changes.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (grepped, isolated to one page)
- [x] No silent behavior change to an already-shipped flow (Section 5 —
  same checks, same messages, same silent-cancel path; internal-admin-only,
  not a live rider/driver surface)

## What was NOT verified

- Not tested against a real backend — `walletAdjust`'s actual POST to
  `/api/admin/corporate-accounts/{id}/wallet/adjust` was not exercised
  end-to-end in this session; this change only touches the client-side
  gate in front of that call, which is unchanged in shape.
- No visual regression tooling exists for admin-dashboard's actual
  baselines (per CLAUDE.md, admin-dashboard's visual-regression job has
  zero committed baselines — B38) — not applicable here regardless, since
  this change has no visual/UI surface (the form is three sequential
  `window.prompt()` calls, not a rendered dialog).
- `subscriptions/page.tsx` (the top-level subscriptions list, distinct
  from the per-company `subscription/page.tsx` already checked in this
  step) was not inspected — flagged in ACTION_ITEMS.md as the next
  candidate, not claimed as checked here.
