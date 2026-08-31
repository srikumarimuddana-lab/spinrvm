# Change Impact & Risk Log — B39 step 20: admin-dashboard create-ride-modal validation → zod

- **Issue/gap identified:**
  `admin-dashboard/src/app/dashboard/rides/_components/create-ride-modal.tsx`'s
  `handleSubmit` validated rider/pickup/dropoff selection and the
  admin-editable total-fare override via four sequential hand-rolled
  early-return checks with no dedicated test coverage — the fourth
  money-tier candidate from the 2026-08-31 broader B39 sweep
  (`ACTION_ITEMS.md` B39, "admin fare override").
- **Root cause:** no schema-validation library was adopted on this
  surface; each screen re-implements its own validation inline.
- **Fix/remediation:** extracted the four checks into new
  `admin-dashboard/src/lib/createRideFormSchema.ts` — predicate
  functions (`isRiderSelected`, `isPickupSelected`, `isDropoffSelected`,
  `isFareAmountValid`) plus a `createRideFormSchema` zod object
  (superRefine, same order, for documentation/type purposes) and a
  `getCreateRideFormError(form)` function that runs the same checks in
  the same order and returns the same error string `setError(...)` was
  called with for the first failing check, or `null` if all pass.
  `handleSubmit` now calls `getCreateRideFormError(...)` once instead of
  the four sequential `if` blocks. Pure extraction — byte-for-byte
  identical accept/reject behavior and identical error copy; no
  validation-rule change. Non-null assertions (`selectedRider!`, etc.)
  were added at the three `adminCreateRide(...)` payload fields that
  previously relied on TypeScript's control-flow narrowing from the
  removed inline `if (!selectedRider) return ...` checks — the schema
  call guarantees the same non-null invariant, TS just can't see through
  the extracted function, so this is a type-only change, not a runtime
  behavior change.
- **Risk & impact on existing functionality:** `getCreateRideFormError`
  and its predicates are new, colocated in
  `src/lib/createRideFormSchema.ts`, and used only by
  `create-ride-modal.tsx`'s `handleSubmit`. Blast-radius grep for the
  three distinctive error strings found zero other call sites in `src/`.
  The submit button's `disabled` prop
  (`loading || !selectedPickup || !selectedDropoff || !selectedRider`)
  duplicates three of the four checks but also includes `loading`, so it
  is not an exact duplicate — left untouched, same discipline as prior
  steps for partial-overlap cases.
- **User experience effect:** none. Same fields, same order, same error
  copy, same accept/reject boundaries (fare exactly `0` still accepted,
  matching the original's `totalNum < 0` strict-less-than check).
- **Files modified:**

  | File | What changed | Why |
  |---|---|---|
  | `admin-dashboard/src/lib/createRideFormSchema.ts` | New file: predicate functions, zod schema, `getCreateRideFormError` | Extract validation logic |
  | `admin-dashboard/src/lib/__tests__/createRideFormSchema.test.ts` | New file: 11 accept/reject tests | Close the coverage gap this item names |
  | `admin-dashboard/src/app/dashboard/rides/_components/create-ride-modal.tsx` | `handleSubmit`'s four inline `if` blocks replaced with one `getCreateRideFormError(...)` call; added non-null assertions at the three payload fields that lost TS narrowing; added import | Pure extraction |

- **Before/after snippet:**

  ```tsx
  // before
  if (!selectedRider) return setError("Please select a rider.");
  if (!selectedPickup) return setError("Please select a valid pickup location from the suggestions.");
  if (!selectedDropoff) return setError("Please select a valid dropoff location from the suggestions.");

  const totalNum = parseFloat(finalFare || "0");
  if (Number.isNaN(totalNum) || totalNum < 0) {
      return setError("Total fare must be a non-negative number.");
  }

  // after
  const formError = getCreateRideFormError({ rider: selectedRider, pickup: selectedPickup, dropoff: selectedDropoff, finalFare });
  if (formError) return setError(formError);

  const totalNum = parseFloat(finalFare || "0");
  ```

- **Rollback plan:** revert the commit — no data migration, no feature
  flag, no live-data mutation involved; this is a client-side pure
  refactor.
- **Verification performed:** 11/11 new tests pass
  (`npx vitest run src/lib/__tests__/createRideFormSchema.test.ts`); full
  admin-dashboard suite (`npx vitest run`) 48/48 suites, 484/484 tests
  passing, 0 regressions; `npx tsc --noEmit` clean (repo-wide, after
  adding the three non-null assertions); `npx eslint` on touched files:
  0 errors, 4 pre-existing `react-hooks/set-state-in-effect` warnings on
  unrelated lines in the same file, unchanged by this diff; **real
  production build** (`npm run build`) completed successfully.
  Blast-radius grep confirmed no other file in `src/` uses this form's
  distinctive error copy.
- **What was NOT verified:** no manual click-through in a running admin
  dashboard against a live/staging Supabase — verified via unit tests,
  `tsc`, `eslint`, and a production build only, per this repo's stated
  no-active-visual-regression-tooling gap (CLAUDE.md pre-merge gate #6);
  this is a visually-invisible, logic-only change so it was reasoned
  about, not screenshotted.
