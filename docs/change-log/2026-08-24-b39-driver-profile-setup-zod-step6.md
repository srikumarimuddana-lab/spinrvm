# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude Code (session) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers (identity/profile, feeds downstream KYC/compliance checks — closest fit is not applicable; treated as a driver-facing profile change) |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | ACTION_ITEMS.md B39, step 6 |

## 1. Issue / gap identified

`driver-app/app/profile-setup.tsx`'s `handleSubmit` validated first name,
last name, email, gender, and service area via six sequential hand-rolled
inline checks (`!isFirstNameValid`, `!isLastNameValid`, `!email.trim()`,
an email regex, `!gender`, `!isServiceAreaValid`) with no dedicated test
file — the exact "validation-rule coverage is invisible" gap
ACTION_ITEMS.md B39 names, on the form step 5's "Still open" list
explicitly called out ("driver-app's signup/profile-setup fields").

## 2. Root cause

No schema-validation library was adopted on any of the three frontend
surfaces (B39's core finding) — validation was written ad hoc, screen by
screen, as it was needed, with no single place to point a coverage tool
at. `driver-app` already adopted `zod` in step 3 (`payout.tsx`), but
`profile-setup.tsx` was not yet migrated.

## 3. Fix / remediation

Pure extraction, not a validation-rule change. Moved the six inline
checks into a new colocated `driver-app/utils/driverProfileSetupSchema.ts`
using `zod` (already a project dependency, added in step 3 — not
re-added), preserving the exact same accept/reject rules (including the
`trim().length > 1` boundary for names, distinct from rider-app's
`trim().length > 0` in step 5), the exact same toast title/message pairs,
and the exact same first-failing-check priority order.
`app/profile-setup.tsx` now calls the extracted
`getDriverProfileSetupError()` helper in `handleSubmit` and the extracted
`isDriverProfileSetupValid()` helper for `isFormValid`, instead of
repeating the checks inline. The screen's local per-field `isXValid`
booleans (used for the inline checkmark icons in the JSX) were left
untouched, since they are a UI-display concern, not part of the
gate/error logic being extracted.

## 4. Risk & impact on existing functionality

- Blast radius: **isolated to `driver-app/app/profile-setup.tsx`.**
  Grepped `driver-app` for `EMAIL_REGEX`, `isFirstNameValid`,
  `isLastNameValid`, `isEmailValid`, `isServiceAreaValid`, and
  `validateEmail` — the only other match was
  `driver-app/app/driver/(tabs)/profile.tsx`, which has its own,
  independently hand-rolled `EMAIL_REGEX` constant and inline check for
  editing an existing driver's email (same regex pattern text, different
  variable names, different toast copy). It is a separate screen with its
  own inline check, not a reader/importer of `profile-setup.tsx`'s
  validation — this change does not touch it, and it is **not** migrated
  here (left as a known duplicate, same discipline step 3 used to keep
  its two GST predicates separate rather than merge them into one
  "more correct" shared function).
- No table, background loop, or ride-state-machine interaction — this is
  a client-side form-validation extraction only; the server (`POST
  /users/profile` via `createProfile`) remains the source of truth and is
  untouched.
- Because the extraction is byte-for-byte behaviorally equivalent (same
  regex, same trim/length checks, same order), there is no reasonable
  path for this diff to make a previously-valid submission now get
  rejected, or vice versa.

## 5. User-experience effect

None. Driver-facing behavior (which field is flagged first, the exact
toast copy, when the Create Profile button is enabled) is unchanged —
this is an internal refactor of the same rules into a tested, named
location.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/utils/driverProfileSetupSchema.ts` | New file: `driverProfileSetupSchema` (zod), `getDriverProfileSetupError()`, `isDriverProfileSetupValid()` | Colocated, testable extraction of the screen's inline validation |
| `driver-app/utils/__tests__/driverProfileSetupSchema.test.ts` | New file: 21 accept/reject test cases | Closes the "no dedicated test coverage" gap B39 names |
| `driver-app/app/profile-setup.tsx` | `handleSubmit`'s six inline `if` checks replaced with `getDriverProfileSetupError(...)`; `isFormValid`'s computation replaced with `isDriverProfileSetupValid(...)`; orphaned `isServiceAreaValid` local removed (no longer read once both checks moved into the helper) | Use the extracted helper instead of duplicating the rules inline |
| `ACTION_ITEMS.md` | Appended B39 step 6 bullet | Record what changed, verification results, and what's still open |

## 7. Before / after

```tsx
// Before (app/profile-setup.tsx)
const isServiceAreaValid = serviceAreaId.length > 0;
const isFormValid = isFirstNameValid && isLastNameValid && isEmailValid && gender && isServiceAreaValid;

const handleSubmit = async () => {
  if (!isFirstNameValid) {
    showToast('warning', 'First Name Required', 'Please enter your first name (at least 2 letters).');
    return;
  }
  if (!isLastNameValid) {
    showToast('warning', 'Last Name Required', 'Please enter your last name (at least 2 letters).');
    return;
  }
  if (!email.trim()) {
    showToast('warning', 'Email Required', 'Please enter your email address.');
    return;
  }
  if (!isEmailValid) {
    showToast('warning', 'Invalid Email', 'That email doesn’t look right — e.g. name@example.com.');
    return;
  }
  if (!gender) {
    showToast('warning', 'Gender Required', 'Please select your gender.');
    return;
  }
  if (!isServiceAreaValid) {
    showToast('warning', 'Service Area Required', 'Please select the area where you plan to drive.');
    return;
  }
  ...
```

```tsx
// After (app/profile-setup.tsx)
// isServiceAreaValid removed: it was only read by the two blocks below,
// both now replaced by the extracted helper — an orphaned variable per
// CLAUDE.md's "remove only the imports/variables your own change
// orphaned" rule, caught by eslint's no-unused-vars.
const isFormValid = isDriverProfileSetupValid({ firstName, lastName, email, gender, serviceAreaId });

const handleSubmit = async () => {
  const validationError = getDriverProfileSetupError({ firstName, lastName, email, gender, serviceAreaId });
  if (validationError) {
    showToast('warning', validationError.title, validationError.message);
    return;
  }
  ...
```

## 8. Rollback plan

Pure code revert — `git revert` this commit. No data written, no
Stripe/wallet/ride-state interaction, no migration, no feature flag
needed: the old inline checks and the new extracted helper are
behaviorally identical, so reverting is a same-behavior code change, not
a data-level rollback.

## 9. Verification performed

- [x] Automated tests run: new `driverProfileSetupSchema.test.ts`
      (21/21 pass, isolated run); full driver-app suite `npx jest` —
      116/116 suites, 1264/1264 tests pass, 0 failures (an earlier
      attempt to capture a from-scratch `origin/main` baseline via `git
      stash` did not actually remove this PR's new, untracked test file
      — `git stash` only stashes tracked-file changes by default — so
      that particular run isn't a clean pre-change baseline; the
      post-restore full run above, with the new file present, is what's
      relied on: 0 failures)
- [x] `npx tsc --noEmit` — clean, no errors
- [x] `npx eslint app/profile-setup.tsx utils/driverProfileSetupSchema.ts
      utils/__tests__/driverProfileSetupSchema.test.ts` — clean, no errors
- [x] Real production build: `npm run build:web` (`expo export
      --platform web`) — completed successfully, exit code 0
- [x] Blast-radius grep performed: searched `driver-app` for
      `EMAIL_REGEX`/`isFirstNameValid`/`isLastNameValid`/`isEmailValid`/
      `isServiceAreaValid`/`validateEmail` — only other match is
      `app/driver/(tabs)/profile.tsx`'s independent, un-migrated
      duplicate (noted in section 4)
- [x] Reviewed against CLAUDE.md conventions: pure extraction (no
      validation-rule change), surgical diff (only the schema file, test
      file, the one screen, ACTION_ITEMS.md, and this log)
- [x] Feature flag: not applicable — this is a behaviorally-identical
      refactor, not a new/changed UX

## 10. What was NOT verified

- `driver-app/app/driver/(tabs)/profile.tsx`'s separate, independently
  hand-rolled email-edit check (same regex pattern text) was identified
  but deliberately **not** migrated in this PR — it is a distinct screen
  with its own copy, not a caller of `profile-setup.tsx`'s validation.
  Migrating it is left for a future B39 step.
- No visual/screenshot regression tooling exists for driver-app (per
  CLAUDE.md's standing gate #6) — the UI-facing checkmark icons
  (`isFirstNameValid` etc., left untouched in the screen) were reasoned
  about, not screenshotted.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data
      involved)
- [x] Blast radius is stated, not assumed (grep results above)
- [x] No silent behavior change to an already-shipped flow — verified
      byte-for-byte equivalence of every check and its message
