# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code session |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | payments |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B39 — step 17, first admin-dashboard candidate from the user-directed broader sweep (money tier) |

## 1. Issue / gap identified

`dashboard/users/page.tsx`'s `requestWalletAction` validates a per-user
(rider/driver) wallet credit/debit amount and reason inline:

```ts
if (!selectedUser?.id || !walletAmount || !/^\d+(\.\d{1,2})?$/.test(walletAmount.trim()) || parseFloat(walletAmount) <= 0) {
    setWalletError("Enter a positive amount");
    return;
}
if (!walletReason.trim() || walletReason.trim().length < 3) {
    setWalletError("Reason must be at least 3 characters");
    return;
}
```

No dedicated test coverage existed for this validation. Unlike the two
prior B39 steps this session, **no correctness bug was found in this
form** — the checks are logically sound (regex correctly bounds decimal
places, `parseFloat(...) <= 0` correctly rejects zero/negative, the
reason-length check is straightforward). This step is a pure extraction.

## 2. Root cause

Ad hoc validation predates zod adoption on this screen, consistent with
every other B39 candidate.

## 3. Fix / remediation

New colocated `admin-dashboard/src/lib/userWalletActionSchema.ts`
extracts the two checks into `isWalletAmountValid`, `isWalletReasonValid`,
and a combined `getWalletActionError` that returns the same error string
for the first failing check, in the same priority order (amount first,
then reason) — a byte-for-byte behavioral mirror of the original two
sequential `if` blocks.

**Distinct from the existing `walletAdjustmentSchema.ts`** (the
corporate-accounts wallet-adjustment form, already migrated): that form
validates a signed delta (one amount, credit or debit encoded by sign)
via `parseFloat`/`isNaN`, capped at $10,000, posting to
`corporate-accounts/{id}/wallet/adjust`. This form validates an
always-positive amount (direction is a separate `action: "credit" |
"debit"` field) via a decimal-places regex, with its own reason-length
floor (3 chars, not just non-empty), posting to `creditUserWallet`/
`debitUserWallet`. Different field shapes and call sites — kept as a
separate schema file per the established B39 pattern (see step 13's
`spinrPassAreaPlanSchema.ts` vs `subscriptionPlanSchema.ts` precedent).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one file, one function
  (`requestWalletAction`).** Grepped `admin-dashboard` for the exact
  regex (`/^\d+(\.\d{1,2})?$/`) and for `walletAmount.trim()`; only
  `users/page.tsx` matched, fully replaced. `confirmWalletAction` (the
  actual mutation, called only after `requestWalletAction` passes and
  the confirm dialog is accepted) is untouched — this step only extracts
  the validation gate, not the money-moving call itself.
- **Could this regress a flow that currently works?** For every input
  the original two checks accept or reject, `getWalletActionError`
  returns byte-for-byte the same result — verified against 11
  accept/reject test cases covering valid amounts, zero/negative
  amounts, over-precision amounts, non-numeric amounts, and short/empty
  reasons. One behavioral nuance preserved deliberately: the original's
  `!selectedUser?.id` case falls into the *same* "Enter a positive
  amount" error path as an invalid amount (not a separate message) —
  this was kept as an explicit ternary at the call site
  (`!selectedUser?.id ? "Enter a positive amount" : getWalletActionError(...)`)
  rather than folded into the schema itself, since `selectedUser` is a
  page-level concern, not a wallet-form field.
- **Money-path interaction:** `creditUserWallet`/`debitUserWallet` move
  real money on a rider/driver's wallet balance (backend converts to
  `Decimal` per CLAUDE.md). This validation gate is the only client-side
  check before the confirm dialog; the fix does not change what reaches
  that dialog for any previously-valid or previously-invalid input.
- **Dispatch / ride state machine:** not implicated — this is an
  admin-only user-management dialog, no interaction with active rides.

## 5. User-experience effect

Admin-facing only, in the user-details dialog's wallet credit/debit
form. No behavior change for any input — same error messages, same
validation order, same accept/reject boundary. Not visible to
riders/drivers at all (admin-only surface), and not visible mid-session
to an admin using the screen normally, since the extraction is
byte-for-byte.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/userWalletActionSchema.ts` | New file — `isWalletAmountValid`, `isWalletReasonValid`, `getWalletActionError` | Pulls the two inline checks into a colocated, independently testable module |
| `admin-dashboard/src/lib/__tests__/userWalletActionSchema.test.ts` | New file — 11 accept/reject unit tests | Pins the extracted behavior so a future edit can't silently change the validation boundary |
| `admin-dashboard/src/app/dashboard/users/page.tsx` | `requestWalletAction`'s two inline `if` blocks replaced with a call to `getWalletActionError`; import added | Same behavior, now covered by tests |

## 7. Before / after

```ts
// Before
const requestWalletAction = (action: "credit" | "debit") => {
    if (!selectedUser?.id || !walletAmount || !/^\d+(\.\d{1,2})?$/.test(walletAmount.trim()) || parseFloat(walletAmount) <= 0) {
        setWalletError("Enter a positive amount");
        return;
    }
    if (!walletReason.trim() || walletReason.trim().length < 3) {
        setWalletError("Reason must be at least 3 characters");
        return;
    }
    setWalletError("");
    // ...
};
```

```ts
// After
import { getWalletActionError } from "@/lib/userWalletActionSchema";

const requestWalletAction = (action: "credit" | "debit") => {
    const error = !selectedUser?.id ? "Enter a positive amount" : getWalletActionError(walletAmount, walletReason);
    if (error) {
        setWalletError(error);
        return;
    }
    setWalletError("");
    // ...
};
```

## 8. Rollback plan

`git-revert-safe`. No data migration, no schema/table change, no feature
flag. Reverting restores the original two inline `if` blocks exactly —
no bug is being fixed in this step, so a revert carries no correctness
regression risk, only a loss of test coverage. No backend or Stripe-side
change to roll back; no already-applied production data is affected
(this is a client-side pre-submit validation gate, not a completed
wallet mutation).

## 9. Verification performed

- [x] Automated tests run — unit only:
  `npx vitest run src/lib/__tests__/userWalletActionSchema.test.ts` —
  11/11 pass. Full suite: `npx vitest run` — 45/45 suites, 443/443
  tests pass, zero failures.
- [ ] Manual repro steps followed in staging — not done; no staging
  access from this session.
- [x] Blast-radius grep performed — searched `admin-dashboard` for the
  exact regex and the `walletAmount.trim()` check; only `users/page.tsx`
  matched, fully replaced.
- [x] Reviewed against relevant CLAUDE.md convention(s) — money: this
  touches the client-side gate before a wallet credit/debit call; the
  backend converts to `Decimal` per CLAUDE.md's Decimal-only rule (this
  extraction does not touch backend money math, only the client-side
  pre-submit string/regex validation, unchanged from the original).
- [x] Money/state-machine dry run (release-gate item 4): not directly
  applicable — no bug fixed, no behavior change, so no before/after
  scenario beyond "identical accept/reject boundary for every input."

`npx tsc --noEmit` on the touched files: clean (57 pre-existing,
unrelated error lines exist elsewhere in the repo — confirmed via
`git stash` to reproduce identically without this diff — all in
map-related components (`maplibre-gl`/`GeoJSON` type-resolution issues),
none touching `users/page.tsx` or `userWalletActionSchema.ts`). `npx
eslint` on the three touched files: 0 errors; 3 pre-existing
`react-hooks/set-state-in-effect` warnings remain on unrelated lines
of `users/page.tsx` (lines 94, 205, 227 — none inside
`requestWalletAction`, unchanged by this diff).

**Real production build**: attempted (`npm run build`) and it
**failed** — but for a reason confirmed entirely unrelated to this
diff via `git stash`: `maplibre-gl@6.6.0` (the version
`package.json` currently pins) dropped its default export, breaking
every file across the app that does `import maplibregl from
"maplibre-gl"` (8 files, none touched by this step — see the
Change Impact Log entry's "Environment note" and the queued task
suggestion `task_66016ac3` for the fix). This is a real,
previously-undocumented gap in admin-dashboard's build health, not
introduced by this change. Per CLAUDE.md's requirement to say
explicitly which verification was run: **the real production build did
not complete for this change**, for a pre-existing, unrelated reason —
verification instead relies on `tsc --noEmit` (clean on touched files),
`eslint` (clean on touched files), and the full `vitest` suite (all
green), which is a narrower guarantee than a completed build.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no
  bug-reintroduction risk since no bug was fixed)
- [x] Blast radius is stated, not assumed (grepped, isolated to one
  file, one function, fully replaced)
- [x] No silent behavior change to an already-shipped flow — this step
  is a pure extraction; no bug found, no behavior change made or
  needed. (Unlike steps 15 and 16, no Section 10 exception applies
  here.)

## What was NOT verified

- **The real production build (`npm run build`) did not complete** —
  see Section 9. It fails on `main` for a pre-existing, unrelated
  reason (`maplibre-gl@6.6.0`'s dropped default export breaking 8
  map-related files, none touched by this diff), confirmed via
  `git stash`. A task suggestion (`task_66016ac3`) was queued to fix
  this separately rather than folding an unrelated dependency/import
  fix into a B39 form-validation step. This is a real gap in this
  step's verification depth relative to steps 15/16 (both of which
  completed a real build) — flagged explicitly rather than silently
  treating `tsc`/`eslint`/`vitest` as equivalent.
- Not tested against a real backend `creditUserWallet`/`debitUserWallet`
  call or the backend's own amount validation — no staging access from
  this session.
- No visual regression tooling exists for admin-dashboard's active
  coverage (per CLAUDE.md, the Playwright visual-regression job has zero
  committed baselines as of B38) — not applicable here regardless, no
  visual/UI change in this diff.
- The remaining 18 candidates from the broader sweep (6 driver-app, 11
  more admin-dashboard) remain open, along with the newly-found
  maplibre-gl build break (tracked separately, not part of B39).
