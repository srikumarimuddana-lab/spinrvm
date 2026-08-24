# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-23 |
| Author | Claude Code session (vikas@ngitservices.com) |
| Surface(s) | rider-app |
| Domain (Sentry tag) | payments |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B39 — step 2 (of an intentionally incremental, one-form-at-a-time migration) |

## 1. Issue / gap identified

B39: no schema-validation library on any frontend surface. Step 1
(2026-08-22, PR #4461) migrated `rider-app/app/work-allowance-request.tsx`.
Per the item's own recommended-fix ordering ("payment-sheet and
allowance-request forms first"), the next highest-risk unmigrated form is
`rider-app/app/wallet.tsx`'s "Add Funds" custom-amount input, which feeds a
real Stripe `PaymentSheet` top-up.

## 2. Root cause

Same as B39's original root cause — the screen validated the top-up amount
with an inline check (`effectiveAmount >= 1 && effectiveAmount <= 500`)
duplicated across two call sites (`canTopUp` and `handleTopUp`'s guard),
with no colocated test coverage of the validation rule itself.

## 3. Fix / remediation

Extracted the existing inline check into a colocated
`rider-app/utils/walletTopUpSchema.ts` (`walletTopUpSchema` — a
`z.number().min(1).max(500)` — plus an `isWalletTopUpAmountValid(amount)`
helper) and pointed both `canTopUp` and `handleTopUp`'s guard at it. This is
a pure extraction — byte-for-byte equivalent accept/reject behavior, not a
validation-rule change, per B39's own warning against changing behavior on
an already-shipped screen.

### Before / after

```ts
// Before
const canTopUp = effectiveAmount >= 1 && effectiveAmount <= 500 && !topUpLoading;
// ...
if (effectiveAmount < 1 || effectiveAmount > 500) {
  showToast('Invalid Amount', 'Please select or enter an amount between $1 and $500.', 'warning');
  return;
}

// After
const canTopUp = isWalletTopUpAmountValid(effectiveAmount) && !topUpLoading;
// ...
if (!isWalletTopUpAmountValid(effectiveAmount)) {
  showToast('Invalid Amount', 'Please select or enter an amount between $1 and $500.', 'warning');
  return;
}
```

## 4. Risk & impact on existing functionality

**Blast radius: isolated to `wallet.tsx`.** Grepped the rest of `rider-app`,
`driver-app`, and `admin-dashboard` for any other importer of the old inline
range check or of `wallet.tsx`'s exports — `wallet.tsx` does not export
`effectiveAmount`/`canTopUp`, and no other file references
`walletTopUpSchema`/`isWalletTopUpAmountValid` (both are new). No other
screen reads or writes this validation logic.

The one behavior difference from a literal find-replace: `z.number()`
rejects `NaN` the same way the original comparison chain did (`NaN >= 1` and
`NaN <= 500` are both `false` in JS, so the old code already treated `NaN`
as invalid) — confirmed equivalent, not a change, and covered by an explicit
test case.

## 5. User-experience effect

None. Rider-facing: identical accept/reject behavior for the "Add Funds"
custom-amount field (still $1–$500 inclusive), same toast copy on rejection.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/utils/walletTopUpSchema.ts` | New — `walletTopUpSchema` (zod) + `isWalletTopUpAmountValid` helper | Colocated, testable validation rule (B39) |
| `rider-app/utils/__tests__/walletTopUpSchema.test.ts` | New — 17 accept/reject test cases | Close "validation-rule coverage is invisible" gap for this form |
| `rider-app/app/wallet.tsx` | `canTopUp` and `handleTopUp`'s guard now call `isWalletTopUpAmountValid` instead of the inline range check | Pure extraction, no behavior change |
| `docs/change-log/2026-08-23-b39-wallet-topup-zod-step2.md` | New change-log | Required |

## 7. Rollback plan

**`git-revert-safe`** — pure extraction of existing logic into a helper
function; reverting the commit restores the original inline checks with no
schema/migration/config to undo.

## 8. Verification performed

- [x] 17/17 new `walletTopUpSchema` accept/reject tests pass
- [x] Full rider-app suite: 1098/1098 tests pass (116 suites), 0 regressions
- [x] `npx tsc --noEmit` clean
- [x] `npx eslint` clean on all three touched/new files
- [x] **Real production build**: `npm run build:web` (`expo export --platform web`) completed successfully — not just `tsc`/dev server, per CLAUDE.md's explicit requirement

## What was NOT verified

- Not exercised against a live Stripe test-mode `PaymentSheet` flow in this
  session — this change touches only the pre-submit amount-validation gate,
  not the Stripe `initPaymentSheet`/`presentPaymentSheet` call itself, which
  is unmodified.
- No visual regression tooling exists for rider-app (per CLAUDE.md release
  gate #6) — this change has zero UI/layout impact (no new copy, no new
  states) and was reasoned about, not screenshotted.

## 9. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — grepped, isolated to `wallet.tsx`
- [x] No silent behavior change — before/after snippet shows the extraction is behavior-preserving
