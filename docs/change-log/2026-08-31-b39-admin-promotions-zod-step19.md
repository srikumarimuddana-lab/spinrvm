# Change Impact & Risk Log — B39 step 19: admin-dashboard promotions form validation → zod

- **Issue/gap identified:** `admin-dashboard/src/app/dashboard/promotions/page.tsx`'s
  `handleSave` validated a promo code's discount terms (discount value, its
  type-specific cap, an optional max-discount cap, max uses, expiry date)
  via six sequential hand-rolled `if` blocks with no dedicated test
  coverage — the largest single ad hoc validation block found in the
  2026-08-31 broader B39 sweep (`ACTION_ITEMS.md` B39).
- **Root cause:** no schema-validation library was adopted on this
  surface; each screen re-implements its own validation inline
  (`isNaN`/`parseFloat`/`parseInt` checks), so validation-rule coverage is
  invisible and cannot be pointed at by a coverage tool.
- **Fix/remediation:** extracted the six checks into new
  `admin-dashboard/src/lib/promotionFormSchema.ts` — one predicate
  function per check (`isPromoCodeValid`, `isDiscountValueProvided`,
  `isDiscountAmountValid`, `isPercentageWithinLimit`,
  `isFlatDiscountWithinLimit`, `isMaxDiscountValid`,
  `isMaxDiscountWithinLimit`, `isMaxUsesValid`, `isExpiryDateValid`) plus
  a `promotionFormSchema` zod object (superRefine, same order, for
  documentation/type purposes) and a `getPromotionFormError(form)`
  function that runs the same checks in the same order and returns the
  same `{ title, description }` pair the original passed to `toast(...)`
  for the first failing check, or `null` if all pass. `handleSave` now
  calls `getPromotionFormError(form)` once instead of the six inline `if`
  blocks. Pure extraction — byte-for-byte identical accept/reject
  behavior and identical toast copy; no validation-rule change.
- **Risk & impact on existing functionality:** `getPromotionFormError`
  and its predicate functions are new, colocated in
  `src/lib/promotionFormSchema.ts`, and used only by
  `promotions/page.tsx`'s `handleSave`. Blast-radius grep for the
  distinctive toast strings ("Flat discount cannot exceed",
  "Percentage discount cannot exceed", "Max discount cap") found zero
  other call sites in `src/` — this validation shape is unique to this
  form. The Save button's `disabled` prop
  (`saving || !form.code.trim() || (!form.free_ride && !form.discount_value)`)
  duplicates only two of the six checks and was left untouched — it is
  a partial, not exact, duplicate, so touching it would be a separate
  UX-risk change outside this step's pure-extraction scope, matching the
  discipline steps 15-18 followed for similar partial-overlap cases.
- **User experience effect:** none. Same fields, same order, same error
  titles/descriptions, same accept/reject boundaries (percentage exactly
  100%, flat exactly $500, max-discount exactly $500, max-uses exactly 1,
  expiry exactly "now" all still resolve identically to the original
  strict `>`/`<` comparisons).
- **Files modified:**

  | File | What changed | Why |
  |---|---|---|
  | `admin-dashboard/src/lib/promotionFormSchema.ts` | New file: predicate functions, zod schema, `getPromotionFormError` | Extract validation logic |
  | `admin-dashboard/src/lib/__tests__/promotionFormSchema.test.ts` | New file: 21 accept/reject tests | Close the coverage gap this item names |
  | `admin-dashboard/src/app/dashboard/promotions/page.tsx` | `handleSave`'s six inline `if` blocks replaced with one `getPromotionFormError(form)` call; added import | Pure extraction |

- **Before/after snippet:**

  ```tsx
  // before
  if (!form.code.trim()) {
      toast({ title: "Missing required fields", description: "Please fill in the code.", variant: "destructive" });
      return;
  }
  if (!form.free_ride) {
      if (!form.discount_value) { /* ... */ return; }
      const discountVal = parseFloat(form.discount_value);
      if (isNaN(discountVal) || discountVal <= 0) { /* ... */ return; }
      // ...four more checks...
  }
  const maxUses = parseInt(form.max_uses);
  if (isNaN(maxUses) || maxUses < 1) { /* ... */ return; }
  if (form.expiry_date) {
      const expiry = new Date(form.expiry_date);
      if (expiry <= new Date()) { /* ... */ return; }
  }

  // after
  const formError = getPromotionFormError(form);
  if (formError) {
      toast({ ...formError, variant: "destructive" });
      return;
  }
  ```

- **Rollback plan:** revert the commit (or `git checkout` the previous
  version of `promotions/page.tsx` and delete the two new files) — no
  data migration, no feature flag, no live-data mutation involved; this
  is a client-side pure refactor.
- **Verification performed:** 21/21 new tests pass
  (`npx vitest run src/lib/__tests__/promotionFormSchema.test.ts`); full
  admin-dashboard suite (`npx vitest run`) 47/47 suites, 473/473 tests
  passing, 0 regressions; `npx tsc --noEmit` clean (repo-wide); `npx
  eslint` on touched files: 0 errors, 5 pre-existing
  `react-hooks/set-state-in-effect` warnings on unrelated lines in the
  same file, unchanged by this diff; **real production build**
  (`npm run build`) completed successfully. Blast-radius grep confirmed
  no other file in `src/` uses this validation's distinctive error copy.
- **What was NOT verified:** no manual click-through in a running admin
  dashboard against a live/staging Supabase — verified via unit tests,
  `tsc`, `eslint`, and a production build only, per this repo's stated
  no-active-visual-regression-tooling gap (CLAUDE.md pre-merge gate #6);
  this is a visually-invisible, logic-only change (identical UI, identical
  toast copy) so it was reasoned about, not screenshotted.
