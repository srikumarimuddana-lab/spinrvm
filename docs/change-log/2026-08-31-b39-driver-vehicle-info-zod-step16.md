# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code session |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B39 — step 16, second candidate from the user-directed broader sweep |

## 1. Issue / gap identified

`app/vehicle-info.tsx` gates its "Save Vehicle Info" submit button behind
an inline `isFormValid`:

```ts
const isFormValid =
    form.vehicle_type_id &&
    form.vehicle_make.trim() &&
    form.vehicle_model.trim() &&
    form.vehicle_year.trim() &&
    form.license_plate.trim();
```

**This has a real bug**: `form.vehicle_year.trim()` only checks the field
is non-empty — it never validates the value actually parses as a number.
At submit time, `handleSubmit` computes
`vehicle_year: parseInt(form.vehicle_year) || 0`. A driver who typed a
non-numeric year (e.g. via a pasted value or an external keyboard — the
on-screen field is `keyboardType="numeric"`, which discourages but does
not strictly forbid non-digit input on every platform/IME) would pass
`isFormValid` (the string is non-empty) and reach `handleSubmit`, where
`parseInt('abc')` returns `NaN`, and `NaN || 0` silently substitutes `0`.
The result: `vehicle_year: 0` is written to the driver's vehicle record
via `updateDriverMe`, with no error shown and no indication to the driver
that their input was discarded.

## 2. Root cause

Same class of gap as step 15: ad hoc validation predates zod adoption on
this screen, and the `.trim()` non-empty check was never extended to
validate the value's shape once the field started feeding a numeric
`parseInt` computation downstream.

## 3. Fix / remediation

New colocated `driver-app/utils/vehicleInfoFormSchema.ts` extracts the 5
field-level predicates (`isVehicleTypeSelected`, `isVehicleMakeValid`,
`isVehicleModelValid`, `isVehicleYearValid`, `isLicensePlateValid`), a zod
schema and `isVehicleInfoFormValid` aggregate (drop-in replacement for
the old `isFormValid`), and `getVehicleYearValue` (drop-in replacement
for the old `parseInt(form.vehicle_year) || 0`).

**This is explicitly not a pure byte-for-byte extraction** — per the
user's direction (this session, 2026-08-31, "fix both bugs now, same PR
as their extraction"), `isVehicleYearValid` additionally requires the
trimmed value to parse as a finite integer
(`Number.isFinite(parseInt(trimmed, 10))`), so a non-numeric year now
fails `isVehicleInfoFormValid` and disables the submit button instead of
silently reaching the `|| 0` fallback.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one file, one `isFormValid` computation,
  one submit-time computation.** Grepped `driver-app` for
  `parseInt\(form\.vehicle_year\)` and for any other file computing
  `isFormValid` from `vehicle_year`; only `vehicle-info.tsx` matched, and
  it was fully replaced. No other screen reads or writes
  `form.vehicle_year` in this shape (driver-app's separate
  `become-driver.tsx` onboarding wizard has its own, differently-scoped
  vehicle-year check — a distinct candidate from the same broader sweep,
  not yet migrated, left untouched here).
- **Could this regress a flow that currently works?** For every valid
  numeric year (the entire domain the on-screen numeric-keypad field
  normally produces), `isVehicleInfoFormValid` and `getVehicleYearValue`
  return byte-for-byte the same accept/value as the original code —
  verified against 12 accept/reject test cases. The only behavior change
  is that a non-numeric year now disables the submit button (with the
  existing "Missing Information" warning toast still firing if a driver
  somehow taps a disabled-looking state, since `handleSubmit`'s own
  `if (!isFormValid)` guard is unchanged) instead of silently submitting
  `vehicle_year: 0`.
- **Regulatory/compliance interaction:** vehicle-year is a driver
  eligibility field to admins reviewing vehicle age against Saskatchewan
  regulatory rules (`CLAUDE.md`'s "Vehicle < 10 years old" requirement,
  enforced admin-side, not by this screen). A silently-written
  `vehicle_year: 0` would have corrupted that field for any admin review
  relying on it — this fix prevents garbage data from reaching the
  backend in the first place, rather than requiring backend-side
  cleanup.
- **Dispatch / ride state machine:** not implicated — this is a
  driver-profile settings screen with no interaction with active rides.

## 5. User-experience effect

Driver-facing, on the "Vehicle Information" settings screen (reachable
post-onboarding to update vehicle details, which then requires admin
re-verification). For every value a driver can realistically enter via
the on-screen numeric-keypad field, behavior is unchanged: a valid year
still enables Save and submits that year; an empty year still disables
Save. The only change is that a non-numeric year (only reachable via
paste, an external keyboard, or an IME that permits non-digit input on
a `numeric` keyboard type) now also keeps Save disabled instead of
silently submitting a `0` year. This is the same "keep it disabled until
valid" UX the screen already uses for every other required field — no
new error message, no new modal, no visual change to the disabled-button
styling. Not visible mid-session in any way a driver following normal
input patterns would notice.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/utils/vehicleInfoFormSchema.ts` | New file — `isVehicleInfoFormValid` + `getVehicleYearValue` (extracted + bug-fixed) | Pulls the inline `isFormValid`/`parseInt` computations into a colocated, independently testable module, and fixes the silent-invalid-year gap |
| `driver-app/utils/__tests__/vehicleInfoFormSchema.test.ts` | New file — 12 accept/reject unit tests | Pins the extracted behavior, including the bug fix, so a future edit can't silently reintroduce the gap |
| `driver-app/app/vehicle-info.tsx` | `isFormValid` now calls `isVehicleInfoFormValid(...)`; submit-time `parseInt(form.vehicle_year) || 0` replaced with `getVehicleYearValue(form.vehicle_year)`; import added | Same behavior for all valid input, fixed behavior for the non-numeric-year bug |

## 7. Before / after

```ts
// Before
const isFormValid =
    form.vehicle_type_id &&
    form.vehicle_make.trim() &&
    form.vehicle_model.trim() &&
    form.vehicle_year.trim() &&
    form.license_plate.trim();
// ...
await updateDriverMe.mutateAsync({
    ...form,
    vehicle_year: parseInt(form.vehicle_year) || 0,
});
// BUG: a non-numeric vehicle_year passes isFormValid (non-empty string),
// then silently becomes 0 via parseInt(...) || 0 at submit time.
```

```ts
// After
import { isVehicleInfoFormValid, getVehicleYearValue } from '../utils/vehicleInfoFormSchema';

const isFormValid = isVehicleInfoFormValid({
    vehicleTypeId: form.vehicle_type_id,
    vehicleMake: form.vehicle_make,
    vehicleModel: form.vehicle_model,
    vehicleYear: form.vehicle_year,
    licensePlate: form.license_plate,
});
// ...
await updateDriverMe.mutateAsync({
    ...form,
    vehicle_year: getVehicleYearValue(form.vehicle_year),
});
// isVehicleInfoFormValid now rejects a non-numeric year, so the submit
// button stays disabled and getVehicleYearValue's own 0-fallback is
// unreachable in the normal flow.
```

## 8. Rollback plan

`git-revert-safe`. No data migration, no schema/table change, no feature
flag. Reverting restores the original `isFormValid`/`parseInt` logic
exactly, **including the silent-invalid-year bug** — this is a real
regression risk of a rollback (a non-numeric year could again be
silently coerced to `0` and written to the driver's vehicle record), not
just a UI-behavior reversion, so a rollback here should be paired with
re-applying at least the bug fix even if the extraction itself is
reverted. No backend change to roll back; no already-applied production
data (this is a client-side pre-submit validation gate, not a completed
mutation) is affected by the revert itself — any already-submitted
`vehicle_year: 0` rows from before this fix are a separate, pre-existing
data-quality question outside this diff's scope (not investigated here).

## 9. Verification performed

- [x] Automated tests run — unit only:
  `npx jest utils/__tests__/vehicleInfoFormSchema.test.ts` — 12/12 pass.
  Full suite: `npx jest` — first run showed 11 failures in
  `__tests__/services/backgroundMessaging.android.test.ts` (a different,
  unrelated file); investigated and confirmed a pre-existing full-suite
  flake, not caused by this diff: (a) that file passes 18/18 in isolation
  both with this diff applied and with it `git stash`-ed out (only the
  tracked file, `vehicle-info.tsx`, is stash-able — the two new files are
  untracked and stay present either way, so this isolates the delta to
  the `vehicle-info.tsx` edit alone), and (b) a clean full-suite re-run
  with this exact diff applied passed 120/120 suites, 1349/1349 tests,
  with zero failures. Treated as the one allowed re-run to confirm
  flakiness per CLAUDE.md's CI-red guidance — confirmed flake, not a real
  regression.
- [ ] Manual repro steps followed in staging — not done; no staging
  access from this session. The bug fix was not exercised against a real
  backend `PATCH` call — verified only via the unit test pinning
  `isVehicleInfoFormValid({ ..., vehicleYear: 'abc' }) === false`.
- [x] Blast-radius grep performed — searched `driver-app` for
  `parseInt\(form\.vehicle_year\)` and related `isFormValid`/`vehicle_year`
  patterns; only `vehicle-info.tsx` matched, fully replaced. The
  differently-scoped `become-driver.tsx` onboarding-wizard vehicle-year
  check was identified as a separate, not-yet-migrated broader-sweep
  candidate and explicitly left untouched.
- [x] Reviewed against relevant CLAUDE.md convention(s) — "do not
  silently swallow errors": while that section is framed around
  DB/auth/payment/dispatch errors specifically, the same principle
  applies here at the client-validation layer — the original code
  silently substituted a fallback (`0`) that masked invalid input rather
  than surfacing it, which this fix corrects at the UI-gate level.
- [x] Money/state-machine dry run (release-gate item 4): not directly
  applicable — this change touches neither a ride-state transition nor a
  wallet/Stripe money path. Described above as a concrete before/after
  scenario instead (non-numeric year → silent `vehicle_year: 0` write,
  now → submit blocked).

`npx tsc --noEmit`: clean. `npx eslint` on the three touched files:
clean, no errors or warnings. **Real production build**
(`npm run build:web` → `expo export --platform web`) completed
successfully — not just `tsc`/dev server, per CLAUDE.md's explicit
requirement for driver-app changes.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert` — see
  Section 8's caveat about the bug reintroduction risk)
- [x] Blast radius is stated, not assumed (grepped, isolated to one
  file, one `isFormValid`/`parseInt` pair, both replaced)
- [ ] No silent behavior change to an already-shipped flow — **this is a
  deliberate exception, explicitly called out**: the invalid-year fix IS
  a behavior change on an already-shipped screen, made with explicit
  user direction (not unilaterally) after the gap was surfaced and
  flagged during the broader B39 sweep, per CLAUDE.md's "Escalate,
  don't silently ship, when in doubt" gate. This is not a silent change
  — it is documented here, in ACTION_ITEMS.md, and was confirmed with
  the user before implementation.

## What was NOT verified

- Not tested against a real backend `PATCH /drivers/me` call or the
  backend's own validation of `vehicle_year` (if any) — this session has
  no staging access. The fix was verified only at the client-side
  validation-gate layer.
- No visual regression tooling exists for driver-app (per CLAUDE.md, no
  automated visual/snapshot regression tooling exists for this surface)
  — not applicable here regardless, since this change has no visual/UI
  surface change (same fields, same disabled-button styling, same
  warning toast).
- Whether any already-submitted `vehicle_year: 0` rows exist in
  production from before this fix was not investigated — that is a
  separate, pre-existing data-quality question outside this diff's scope.
- The remaining 19 candidates from the broader sweep (6 more driver-app,
  12 more admin-dashboard) remain open — this step addresses only the
  second-highest-risk finding (the first, rider-app's custom-tip bug,
  shipped in step 15 / PR #4739).
