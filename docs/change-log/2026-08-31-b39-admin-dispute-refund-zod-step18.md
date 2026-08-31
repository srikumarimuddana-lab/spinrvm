# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code session |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | payments |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B39 — step 18, second admin-dashboard candidate from the user-directed broader sweep (money tier) |

## 1. Issue / gap identified

`dashboard/disputes/page.tsx`'s `handleResolve` validates a partial-refund
amount inline, only when `resolution === "partial_refund"`:

```ts
if (resolution === "partial_refund") {
  const amount = parseFloat(refundAmount);
  if (isNaN(amount) || amount <= 0) {
    setResolveError("Refund amount must be greater than zero");
    return;
  }
  const originalFareAmount = Number(selected.original_fare || 0);
  if (amount > originalFareAmount) {
    setResolveError(
      `Refund cannot exceed the original fare of $${originalFareAmount.toFixed(2)}`
    );
    return;
  }
}
```

No dedicated test coverage existed for this validation. **No correctness
bug was found in this form** — both checks are logically sound. This
step is a pure extraction, matching step 17's finding (no bug), not
steps 15/16 (bug found and fixed).

## 2. Root cause

Ad hoc validation predates zod adoption on this screen, consistent with
every other B39 candidate.

## 3. Fix / remediation

New colocated `admin-dashboard/src/lib/disputeResolutionSchema.ts`
extracts the two checks into `isRefundAmountValid`,
`isRefundWithinOriginalFare`, and a combined `getPartialRefundError`
that returns the same error string (including the exact dollar-figure
template) for the first failing check, in the same priority order —
a byte-for-byte behavioral mirror of the original two sequential `if`
blocks.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one file, one conditional branch inside
  `handleResolve`.** Grepped `admin-dashboard` for `isNaN(amount)` and
  `amount > originalFareAmount`; two coincidental matches were
  inspected and confirmed unrelated: `walletAdjustmentSchema.ts`'s own
  `isNaN` check (a different, already-migrated form — corporate wallet
  adjustment, distinct field shape) and `company-portal/[id]/sections/
  page.tsx`'s `Number.isNaN(amount) || amount < 0` (a different check,
  different variable scope, not a refund-amount validator). Neither
  duplicates this form's logic.
- **Could this regress a flow that currently works?** For every input
  the original two checks accept or reject, `getPartialRefundError`
  returns byte-for-byte the same result — verified against 9
  accept/reject test cases covering invalid/negative/zero amounts, an
  amount exceeding the original fare (including the exact boundary
  case where they're equal), and a valid amount within range.
- **Money-path interaction:** `resolveDispute` posts the resolved
  refund amount for a rider dispute. This validation gate is the only
  client-side check before that call for the `partial_refund` path; the
  fix does not change what reaches it for any previously-valid or
  previously-invalid input.
- **Dispatch / ride state machine:** not implicated — admin-only
  dispute-resolution dialog.
- **Separately noticed, explicitly out of scope for this step:**
  `handleResolve`'s `catch` block silently swallows a failed
  `resolveDispute` API call (`console.error` only, no
  `setResolveError`) — an error-handling gap, not a validation gap,
  and not part of the original 21-candidate broader-sweep list. Flagged
  as a separate task suggestion (`task_916f2e38`) rather than folded
  into this validation-focused extraction.

## 5. User-experience effect

Admin-facing only, in the dispute-resolution dialog's partial-refund
amount field. No behavior change for any input — same error messages
(including the exact dollar-figure text), same validation order, same
accept/reject boundary. Not visible to riders/drivers at all
(admin-only surface).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/disputeResolutionSchema.ts` | New file — `isRefundAmountValid`, `isRefundWithinOriginalFare`, `getPartialRefundError` | Pulls the two inline checks into a colocated, independently testable module |
| `admin-dashboard/src/lib/__tests__/disputeResolutionSchema.test.ts` | New file — 9 accept/reject unit tests | Pins the extracted behavior so a future edit can't silently change the validation boundary |
| `admin-dashboard/src/app/dashboard/disputes/page.tsx` | `handleResolve`'s partial-refund `if` blocks replaced with a call to `getPartialRefundError`; import added | Same behavior, now covered by tests |

## 7. Before / after

```ts
// Before
if (resolution === "partial_refund") {
  const amount = parseFloat(refundAmount);
  if (isNaN(amount) || amount <= 0) {
    setResolveError("Refund amount must be greater than zero");
    return;
  }
  const originalFareAmount = Number(selected.original_fare || 0);
  if (amount > originalFareAmount) {
    setResolveError(
      `Refund cannot exceed the original fare of $${originalFareAmount.toFixed(2)}`
    );
    return;
  }
}
```

```ts
// After
import { getPartialRefundError } from "@/lib/disputeResolutionSchema";

if (resolution === "partial_refund") {
  const originalFareAmount = Number(selected.original_fare || 0);
  const error = getPartialRefundError(refundAmount, originalFareAmount);
  if (error) {
    setResolveError(error);
    return;
  }
}
```

## 8. Rollback plan

`git-revert-safe`. No data migration, no schema/table change, no feature
flag. Reverting restores the original inline checks exactly — no bug
is being fixed in this step, so a revert carries no correctness
regression risk, only a loss of test coverage. No backend change to
roll back; no already-applied production data is affected (client-side
pre-submit validation gate only).

## 9. Verification performed

- [x] Automated tests run — unit only:
  `npx vitest run src/lib/__tests__/disputeResolutionSchema.test.ts` —
  9/9 pass. Full suite: `npx vitest run` — 46/46 suites, 452/452 tests
  pass, zero failures.
- [ ] Manual repro steps followed in staging — not done; no staging
  access from this session.
- [x] Blast-radius grep performed — searched `admin-dashboard` for both
  original conditions; two coincidental matches inspected and confirmed
  unrelated (see Section 4).
- [x] Reviewed against relevant CLAUDE.md convention(s) — money: this
  touches the client-side gate before a refund-resolution call; the
  backend independently validates/processes the refund (out of this
  diff's scope).
- [x] Money/state-machine dry run (release-gate item 4): not directly
  applicable — no bug fixed, no behavior change, so no before/after
  scenario beyond "identical accept/reject boundary for every input."

`npx tsc --noEmit`: clean, repo-wide (the 57 pre-existing error lines
noted in step 17's Change Impact Log — caused by an unrelated
`maplibre-gl@6.6.0` build break — were independently fixed on `main`
between step 17 and this step; verified fixed before starting this
step, see ACTION_ITEMS.md's "maplibre-gl build break ... already fixed
independently" note). `npx eslint` on the three touched files: 0
errors; 3 pre-existing `react-hooks/set-state-in-effect` warnings
remain on unrelated lines of `disputes/page.tsx` (lines 106, 113, 118 —
none inside `handleResolve`, unchanged by this diff). **Real production
build** (`npm run build`) completed successfully — the first B39
admin-dashboard step able to complete this, now that the maplibre-gl
break is fixed (step 17 could not run a completing build for a
pre-existing, unrelated reason).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no
  bug-reintroduction risk since no bug was fixed)
- [x] Blast radius is stated, not assumed (grepped, isolated to one
  file, one conditional branch, fully replaced)
- [x] No silent behavior change to an already-shipped flow — this step
  is a pure extraction; no bug found, no behavior change made or
  needed.

## What was NOT verified

- Not tested against a real `resolveDispute` API call or the backend's
  own refund validation — no staging access from this session.
- No visual regression tooling exists for admin-dashboard's active
  coverage (per CLAUDE.md, zero committed Playwright baselines) — not
  applicable here regardless, no visual/UI change in this diff.
- The `resolveDispute` catch-block silent-error-swallow gap noticed
  while reading this file was NOT fixed here (out of scope, flagged
  separately as `task_916f2e38`) — this step addresses only the
  amount-validation extraction the broader sweep originally identified.
- The remaining 17 candidates from the broader sweep (6 driver-app, 10
  more admin-dashboard) remain open.
