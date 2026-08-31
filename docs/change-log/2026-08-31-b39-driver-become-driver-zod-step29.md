# Change Impact & Risk Log — B39 step 29: driver-app become-driver wizard validation → schema helpers

- **Issue/gap identified:** `driver-app/app/become-driver.tsx`'s onboarding
  wizard validated personal info, vehicle info (including the vehicle-age
  rule), and the CRC-consent gate via inline checks scattered across
  `validateStep` and `handleSubmit`, with no dedicated test coverage —
  the first (and highest-risk per its own ordering) driver-app candidate
  from the 2026-08-31 broader B39 sweep (`ACTION_ITEMS.md` B39, "KYC
  onboarding — doc expiry, vehicle age, vehicle-info completeness").
- **Root cause:** no schema-validation library was adopted on this
  surface; each screen re-implements its own validation inline.
- **Fix/remediation:** extracted the checks into new
  `driver-app/utils/becomeDriverSchema.ts` — `isPersonalStepValid`,
  `hasAnyVehicleInfo`, `isVehicleYearValid`, `isVehicleInfoComplete`,
  `getVehicleStepError` (runs the vehicle-year and completeness checks
  in the same order as the original, returning the same
  `{ title, message }` Alert.alert pair), and `isCrcConsentValid`.
  `validateStep`'s case 1 and case 2, and `handleSubmit`'s CRC-consent
  guard, now call these instead of their original inline logic. Pure
  extraction — byte-for-byte identical accept/reject behavior and
  identical `Alert.alert` copy; no validation-rule change.
  Compliance-critical: the vehicle-year check mirrors the Saskatchewan
  "vehicle < 10 years old" driver-eligibility rule client-side; the
  backend stays authoritative, same discipline `payoutFormsSchema.ts`'s
  GST/BN regex already established for this surface.
- **Risk & impact on existing functionality:** all six new functions are
  colocated in `driver-app/utils/becomeDriverSchema.ts` and used only by
  `become-driver.tsx`'s `validateStep`/`handleSubmit`. Blast-radius grep
  for the three distinctive `Alert.alert` strings found no other file in
  `app/`/`utils/`/`shared/` using them. The submit button's `disabled`
  prop duplicates the CRC-consent check but also includes `isLoading` —
  a partial, not exact, duplicate — left untouched, same discipline as
  the admin-dashboard steps in this series.
- **User experience effect:** none. Same fields, same order, same
  `Alert.alert` copy, same accept/reject boundaries (a vehicle exactly 9
  years old still accepted, matching the original's `year < currentYear
  - 9` strict-less-than check).
- **Files modified:**

  | File | What changed | Why |
  |---|---|---|
  | `driver-app/utils/becomeDriverSchema.ts` | New file: predicate functions, `getVehicleStepError` | Extract validation logic |
  | `driver-app/utils/__tests__/becomeDriverSchema.test.ts` | New file: 19 accept/reject tests | Close the coverage gap this item names |
  | `driver-app/app/become-driver.tsx` | `validateStep`'s case-1 and case-2 bodies, and `handleSubmit`'s CRC-consent guard, replaced with calls to the extracted functions; added import | Pure extraction |

- **Before/after snippet:**

  ```tsx
  // before (validateStep, case 2)
  const hasVehicleInfo = vehicleMake || vehicleModel || vehicleColor || vehicleYear || licensePlate || vehicleVin || vehicleType;
  if (!hasVehicleInfo) return true;
  const year = parseInt(vehicleYear);
  const currentYear = new Date().getFullYear();
  if (vehicleYear && (isNaN(year) || year < currentYear - 9)) {
      Alert.alert('Invalid Year', 'Vehicle must be 9 years old or newer.');
      return false;
  }
  if (hasVehicleInfo && (!vehicleMake || !vehicleModel || !licensePlate || !vehicleType)) {
      Alert.alert('Incomplete Vehicle Info', 'Please complete all vehicle fields or use "Skip for now".');
      return false;
  }
  return true;

  // after
  const vehicleStepError = getVehicleStepError({
      vehicleMake, vehicleModel, vehicleColor, vehicleYear, licensePlate, vehicleVin, vehicleType,
  });
  if (vehicleStepError) {
      Alert.alert(vehicleStepError.title, vehicleStepError.message);
      return false;
  }
  return true;
  ```

- **Rollback plan:** revert the commit — no data migration, no feature
  flag, no live-data mutation involved; this is a client-side pure
  refactor.
- **Verification performed:** 19/19 new tests pass
  (`npx jest utils/__tests__/becomeDriverSchema.test.ts`); full
  driver-app suite (`npx jest`) 121/121 suites, 1368/1368 tests passing,
  0 regressions; `npx tsc --noEmit` clean (repo-wide); `npx eslint` on
  touched files: 0 errors, 0 warnings; **real production build**
  (`npm run build:web` → `expo export --platform web`) completed
  successfully, not just `tsc`/dev server, per CLAUDE.md's explicit
  requirement. Blast-radius grep confirmed no other file uses these
  distinctive `Alert.alert` strings.
- **What was NOT verified:** no manual click-through of the onboarding
  wizard in a running app against a live/staging Supabase — verified via
  unit tests, `tsc`, `eslint`, and a production web build only; this
  repo has no active visual-regression tooling for rider-app/driver-app
  (CLAUDE.md pre-merge gate #6), so this visually-invisible, logic-only
  change was reasoned about, not screenshotted.

This is the first driver-app B39 step in the broader-sweep series;
admin-dashboard's own candidates are fully closed as of step 28.
