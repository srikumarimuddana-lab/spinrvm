# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code session |
| Surface(s) | rider-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B39 — step 28, first rider-app compliance/KYC-tier candidate from the user-directed broader sweep |

## 1. Issue / gap identified

`app/become-driver.tsx`'s `validateStep` validates the 3-step
driver-application wizard (Personal / Vehicle / Docs) via a `switch`
block with hand-rolled checks and no dedicated test coverage:

- Step 1 (Personal): `firstName && lastName && email && city` — no
  error toast on failure, just a truthy/falsy return.
- Step 2 (Vehicle): a vehicle-year check
  (`!vehicleYear || isNaN(year) || year < currentYear - 9`, SGI's
  <10-year-old rule) that **does** show an "Invalid Year" toast, then
  `vehicleMake && vehicleModel && vehicleColor && licensePlate &&
  vehicleVin && vehicleType` — again no toast on failure.
- Step 3 (Docs): builds a `missing` array of unsatisfied mandatory
  document requirements (front/back/expiry), shown in one combined
  "Missing Documents" toast if non-empty.

**No correctness bug was found.** The vehicle-year check correctly
mirrors CLAUDE.md's SGI <10-year rule. The lack of an error toast for
steps 1 and the second half of step 2 is a pre-existing UX gap (a
silently-disabled "Next" button gives no explanation), not a
correctness bug — flagged for visibility below, not fixed.

## 2. Root cause

Ad hoc validation predates zod adoption on this screen, consistent with
every other B39 candidate.

## 3. Fix / remediation

New colocated `rider-app/utils/becomeDriverSchema.ts` extracts the
checks into `isPersonalStepValid`, `isVehicleYearValid`,
`isVehicleDetailsValid`, and `getMissingDriverDocuments` — a
byte-for-byte behavioral mirror of the original `switch` block,
including which branches do and don't show a toast on failure.

**Deliberately not changed**: the toast asymmetry described above. Per
CLAUDE.md's "no silent behavior change to an already-shipped flow" gate
and this session's established discipline, a pure extraction step does
not add new user-facing behavior (like a new error toast) without
explicit user authorization — the two prior steps that changed behavior
(15, 16) each did so only after the gap was surfaced and the user
explicitly confirmed the fix via `AskUserQuestion`. This asymmetry is a
UX-completeness nit (a silently-disabled button, not a money or
compliance-adjacent bug), so it is flagged here rather than queued as a
task or fixed inline.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one file, one function
  (`validateStep`).** Grepped `rider-app` for the exact vehicle-year
  condition (`year < currentYear - 9`) and the vehicle-details truthy
  chain; only `become-driver.tsx` matched, fully replaced. `handleUpload`,
  `handleSubmit`, and `openDriverApp` (separate concerns in the same
  file) are untouched.
- **Could this regress a flow that currently works?** For every input
  the original checks accept or reject, the new predicates return
  byte-for-byte the same result — verified against 16 accept/reject
  test cases covering all 4 predicates, including the SGI 9-year-window
  boundary (`currentYear - 9` exactly passes, `currentYear - 10` fails)
  and the mandatory/optional + front/back/expiry document-requirement
  matrix.
- **Regulatory interaction:** the vehicle-year check is this screen's
  only enforcement of CLAUDE.md's SGI "<10 years old" driver-eligibility
  rule at the rider-side driver-application entry point (a second,
  separate enforcement exists at `go_online` per CLAUDE.md's Saskatchewan
  Regulatory section — this screen's check is a UX pre-filter, not the
  sole enforcement). This extraction preserves that check exactly; no
  weakening or strengthening of the age boundary.
- **Dispatch / ride state machine:** not implicated — this is a
  driver-application wizard, no interaction with active rides.

## 5. User-experience effect

Rider-facing, on the "Become a Driver" application wizard. No behavior
change for any input — same fields, same validation order, same
accept/reject boundary, same toast (or lack thereof) per branch. Not
visible mid-session in any way an applicant would notice.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/utils/becomeDriverSchema.ts` | New file — 4 predicates extracted from `validateStep` | Pulls the 3-step wizard's validation into a colocated, independently testable module |
| `rider-app/utils/__tests__/becomeDriverSchema.test.ts` | New file — 16 accept/reject unit tests | Pins the extracted behavior, including the SGI year boundary, so a future edit can't silently change it |
| `rider-app/app/become-driver.tsx` | `validateStep`'s inline checks replaced with calls to the new predicates; import added | Same behavior, now covered by tests |

## 7. Before / after

```ts
// Before
const validateStep = (step: number) => {
  switch (step) {
    case 1:
      return firstName && lastName && email && city;
    case 2:
      const year = parseInt(vehicleYear);
      const currentYear = new Date().getFullYear();
      if (!vehicleYear || isNaN(year) || year < currentYear - 9) {
        showToast('Invalid Year', 'Vehicle must be 9 years old or newer.', 'warning');
        return false;
      }
      return vehicleMake && vehicleModel && vehicleColor && licensePlate && vehicleVin && vehicleType;
    case 3:
      const missing: string[] = [];
      if (!licenseNumber) missing.push('Driver License Number');
      requirements.forEach(req => { /* ... */ });
      if (missing.length > 0) {
        showToast('Missing Documents', `Please provide: ${missing.join(', ')}`, 'warning');
        return false;
      }
      return true;
    default:
      return true;
  }
};
```

```ts
// After
import {
  isPersonalStepValid,
  isVehicleYearValid,
  isVehicleDetailsValid,
  getMissingDriverDocuments,
} from '../utils/becomeDriverSchema';

const validateStep = (step: number) => {
  switch (step) {
    case 1:
      return isPersonalStepValid(firstName, lastName, email, city);
    case 2:
      if (!isVehicleYearValid(vehicleYear)) {
        showToast('Invalid Year', 'Vehicle must be 9 years old or newer.', 'warning');
        return false;
      }
      return isVehicleDetailsValid(vehicleMake, vehicleModel, vehicleColor, licensePlate, vehicleVin, vehicleType);
    case 3:
      const missing = getMissingDriverDocuments(licenseNumber, requirements, docs);
      if (missing.length > 0) {
        showToast('Missing Documents', `Please provide: ${missing.join(', ')}`, 'warning');
        return false;
      }
      return true;
    default:
      return true;
  }
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
  `npx jest utils/__tests__/becomeDriverSchema.test.ts` — 16/16 pass.
  Full suite: `npx jest` — an initial run showed 1 failure (test
  timeout) in an unrelated file, `__tests__/rideOptionsScreen.test.tsx`;
  confirmed a flake, not caused by this diff: that file passes 119/119
  in isolation, and a clean full-suite re-run with this exact diff
  applied passed 136/136 suites, 1916/1916 tests, zero failures.
- [ ] Manual repro steps followed in staging — not done; no staging
  access from this session.
- [x] Blast-radius grep performed — searched `rider-app` for the exact
  vehicle-year condition and the vehicle-details truthy chain; only
  `become-driver.tsx` matched, fully replaced.
- [x] Reviewed against relevant CLAUDE.md convention(s) — Saskatchewan
  Regulatory: the vehicle-year check mirrors CLAUDE.md's SGI "<10 years
  old" driver-eligibility rule exactly; this extraction does not change
  that boundary.
- [x] Money/state-machine dry run (release-gate item 4): not directly
  applicable — no bug fixed, no behavior change, so no before/after
  scenario beyond "identical accept/reject boundary for every input,
  including the regulatory year boundary."

`npx tsc --noEmit`: clean. `npx eslint` on the three touched files:
clean, no errors or warnings. **Real production build**
(`npm run build:web` → `expo export --platform web`) completed
successfully.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no
  bug-reintroduction risk since no bug was fixed)
- [x] Blast radius is stated, not assumed (grepped, isolated to one
  file, one function, fully replaced)
- [x] No silent behavior change to an already-shipped flow — this step
  is a pure extraction; no bug found, no behavior change made or
  needed, including deliberately preserving the pre-existing toast
  asymmetry described in Section 3 rather than "fixing" it unilaterally.

## What was NOT verified

- Not tested against a real `registerDriver` API call or the backend's
  own validation of the submitted application (no staging access from
  this session).
- No visual regression tooling exists for rider-app (per CLAUDE.md, no
  automated visual/snapshot regression tooling exists for this surface)
  — not applicable here regardless, no visual/UI change in this diff.
- The pre-existing toast asymmetry (steps 1 and the vehicle-details half
  of step 2 give no error message on failure) was identified but not
  fixed — flagged in Section 3, not queued as a task (UX-completeness
  nit, not a money/compliance-adjacent bug).
- The remaining 9 candidates from the broader sweep (rider-app 2,
  driver-app 6, admin-dashboard 2 silent no-ops) remain open.
