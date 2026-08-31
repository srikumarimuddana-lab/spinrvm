# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code session |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | payments |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B39 — step 19, third admin-dashboard candidate from the user-directed broader sweep (money tier, largest single candidate block) |

## 1. Issue / gap identified

`dashboard/promotions/page.tsx`'s `handleSave` validates a promo-code
form inline with a 9-check sequential block: code presence; then,
skipped entirely for a `free_ride` promo, discount-value presence,
validity, percentage-cap (≤100%), flat-cap (≤$500), and an optional
max-discount cap's own validity/limit; then max-uses (≥1); then an
optional future-expiry-date check. No dedicated test coverage existed
for any of it. **No correctness bug was found** — every check is
logically sound. This step is a pure extraction, matching steps 17/18.

## 2. Root cause

Ad hoc validation predates zod adoption on this screen, consistent with
every other B39 candidate.

## 3. Fix / remediation

New colocated `admin-dashboard/src/lib/promotionFormSchema.ts` extracts
all 9 checks into individual predicates (`isPromoCodeValid`,
`isDiscountValuePresent`, `isDiscountValueValid`,
`isPercentageDiscountValid`, `isFlatDiscountValid`, `isMaxDiscountValid`,
`isMaxDiscountWithinLimit`, `isMaxUsesValid`, `isExpiryDateValid`) plus
a combined `getPromotionFormError` that returns the same `{title,
description}` toast pair for the first failing check, in the same
priority order (including the `!form.freeRide` gate around the
discount-related checks) — a byte-for-byte behavioral mirror of the
original sequential `if` blocks.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one file, one function
  (`handleSave`).** Grepped `admin-dashboard` for the exact
  `discount_type === "percentage" && discountVal`/`discount_type ===
  "flat" && discountVal` conditions (specific enough to avoid the
  false-positive pattern seen in step 18); only the new schema file
  matched. `toggleActive`/`confirmDelete` (separate mutations in the
  same file) are untouched.
- **Could this regress a flow that currently works?** For every input
  the original 9 checks accept or reject, `getPromotionFormError`
  returns byte-for-byte the same result — verified against 31
  accept/reject test cases covering every predicate individually (both
  branches of each conditional check) and every branch of the
  aggregate's priority order, including the free-ride path that skips
  every discount-related check.
- **Money-path interaction:** `createPromotion`/`updatePromotion` write
  a promo code that directly discounts a rider's fare at redemption.
  This validation gate is the only client-side check before that call;
  the fix does not change what reaches it for any previously-valid or
  previously-invalid input.
- **Dispatch / ride state machine:** not implicated — admin-only promo
  management dialog.

## 5. User-experience effect

Admin-facing only, in the promo-code create/edit dialog. No behavior
change for any input — same toast titles/descriptions, same validation
order (including the free-ride skip), same accept/reject boundary. Not
visible to riders/drivers at all (admin-only surface).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/promotionFormSchema.ts` | New file — 9 predicates + `getPromotionFormError` | Pulls the largest inline-check block found in the broader sweep into a colocated, independently testable module |
| `admin-dashboard/src/lib/__tests__/promotionFormSchema.test.ts` | New file — 31 accept/reject unit tests | Pins the extracted behavior so a future edit can't silently change any of the 9 checks' boundaries |
| `admin-dashboard/src/app/dashboard/promotions/page.tsx` | `handleSave`'s 9 sequential `if` blocks replaced with a call to `getPromotionFormError`; import added | Same behavior, now covered by tests |

## 7. Before / after

```ts
// Before (44 lines, 9 sequential checks)
const handleSave = async () => {
  if (!form.code.trim()) {
    toast({ title: "Missing required fields", description: "Please fill in the code.", variant: "destructive" });
    return;
  }
  if (!form.free_ride) {
    if (!form.discount_value) { /* ... */ return; }
    const discountVal = parseFloat(form.discount_value);
    if (isNaN(discountVal) || discountVal <= 0) { /* ... */ return; }
    if (form.discount_type === "percentage" && discountVal > 100) { /* ... */ return; }
    if (form.discount_type === "flat" && discountVal > 500) { /* ... */ return; }
    if (form.max_discount) {
      const maxD = parseFloat(form.max_discount);
      if (isNaN(maxD) || maxD <= 0) { /* ... */ return; }
      if (maxD > 500) { /* ... */ return; }
    }
  }
  const maxUses = parseInt(form.max_uses);
  if (isNaN(maxUses) || maxUses < 1) { /* ... */ return; }
  if (form.expiry_date) {
    const expiry = new Date(form.expiry_date);
    if (expiry <= new Date()) { /* ... */ return; }
  }
  setSaving(true);
  // ...
};
```

```ts
// After
import { getPromotionFormError } from "@/lib/promotionFormSchema";

const handleSave = async () => {
  const error = getPromotionFormError({
    code: form.code,
    freeRide: form.free_ride,
    discountType: form.discount_type,
    discountValue: form.discount_value,
    maxDiscount: form.max_discount,
    maxUses: form.max_uses,
    expiryDate: form.expiry_date,
  });
  if (error) {
    toast({ title: error.title, description: error.description, variant: "destructive" });
    return;
  }
  setSaving(true);
  // ...
};
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
  `npx vitest run src/lib/__tests__/promotionFormSchema.test.ts` —
  31/31 pass. Full suite: `npx vitest run` — 47/47 suites, 483/483
  tests pass, zero failures.
- [ ] Manual repro steps followed in staging — not done; no staging
  access from this session.
- [x] Blast-radius grep performed — searched `admin-dashboard` for the
  exact percentage-cap/flat-cap conditions; only the new schema file
  matched.
- [x] Reviewed against relevant CLAUDE.md convention(s) — money: this
  touches the client-side gate before a promo-code create/update call
  that directly discounts rider fares at redemption; the backend
  independently validates/applies the discount at redemption time (out
  of this diff's scope).
- [x] Money/state-machine dry run (release-gate item 4): not directly
  applicable — no bug fixed, no behavior change, so no before/after
  scenario beyond "identical accept/reject boundary for every input."

`npx tsc --noEmit`: clean. `npx eslint` on the three touched files: 0
errors; 5 pre-existing `react-hooks/set-state-in-effect`/
`exhaustive-deps` warnings remain on unrelated lines of
`promotions/page.tsx` (lines 264, 278, 284, 291 — none inside
`handleSave`, unchanged by this diff). **Real production build**
(`npm run build`) completed successfully.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no
  bug-reintroduction risk since no bug was fixed)
- [x] Blast radius is stated, not assumed (grepped, isolated to one
  file, one function, fully replaced)
- [x] No silent behavior change to an already-shipped flow — this step
  is a pure extraction; no bug found, no behavior change made or
  needed.

## What was NOT verified

- Not tested against a real `createPromotion`/`updatePromotion` API
  call or the backend's own promo validation — no staging access from
  this session.
- No visual regression tooling exists for admin-dashboard's active
  coverage (per CLAUDE.md, zero committed Playwright baselines) — not
  applicable here regardless, no visual/UI change in this diff.
- The remaining 16 candidates from the broader sweep (6 driver-app, 9
  more admin-dashboard) remain open.
