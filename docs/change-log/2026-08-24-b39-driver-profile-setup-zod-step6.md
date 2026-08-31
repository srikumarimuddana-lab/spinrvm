# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude Code (session) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers (identity/profile, feeds downstream KYC/compliance checks — no dedicated `auth` form yet) |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | ACTION_ITEMS.md B39, step 6 |

## 0. Note on scope pivot

This task was originally scoped to `rider-app/app/login.tsx`, next in B39's
"Still open" list. That screen's only inline check is
`isValid = phoneNumber.length === 10`: no error message or toast, no
branching, and `handlePhoneChange` already hard-caps input to 10 digits
before `isValid` ever runs. There is no "validation-rule coverage is
invisible" gap to close there — a schema extraction would be a one-line
`z.string().length(10)` wrapper with nothing to pin in tests, and per
CLAUDE.md's simplicity-first principle that isn't worth forcing. Per this
task's own fallback instruction, `login.tsx` was left unchanged (noted in
ACTION_ITEMS.md B39 as checked, not skipped-without-record) and this step
instead migrated the next "Still open" item: driver-app's
signup/profile-setup form.

## 1. Issue / gap identified

`driver-app/app/profile-setup.tsx`'s `handleSubmit` validated first name,
last name, email, gender, and service area via six sequential hand-rolled
inline checks (`!isFirstNameValid`, `!isLastNameValid`, `!email.trim()`,
`!isEmailValid`, `!gender`, `!isServiceAreaValid`) with no dedicated test
file — the exact "validation-rule coverage is invisible" gap
ACTION_ITEMS.md B39 names, on the driver-side identity/signup form that
feeds downstream KYC/compliance checks (mirrors rider-app's step 5
equivalent screen).

## 2. Root cause

No schema-validation library was adopted on driver-app's signup/profile
forms specifically (B39's core finding) — validation was written ad hoc as
each field was added, with individual booleans (`isFirstNameValid`,
`isLastNameValid`, `isEmailValid`, `isServiceAreaValid`) duplicated between
`handleSubmit`'s guard clauses, the `isFormValid` aggregate, and three
separate JSX checkmark-icon conditions.

## 3. Fix / remediation

Pure extraction, not a validation-rule change. Moved the six inline checks
into a new colocated `driver-app/utils/profileSetupSchema.ts` using `zod`
(already a `driver-app` dependency since B39 step 3 — not re-added),
preserving the exact same accept/reject rules, the exact same toast
title/message pairs, and the exact same first-failing-check priority
order. `app/profile-setup.tsx` now imports `isFirstNameValid`,
`isLastNameValid`, `isEmailValid` (kept as standalone functions, not
collapsed into the aggregate, because the screen calls each one
separately to drive its own per-field checkmark icon), plus
`isProfileSetupFormValid` for the `isFormValid` boolean and
`getProfileSetupError` for `handleSubmit`'s guard — replacing the local
`validateEmail`/`EMAIL_REGEX`/four inline `const` booleans and the six
`if` blocks.

## 4. Risk & impact on existing functionality

- Blast radius: **isolated to `driver-app/app/profile-setup.tsx`.**
  Grepped `driver-app/app`, `driver-app/utils`, `driver-app/hooks`, and
  `driver-app/lib` for `EMAIL_REGEX`/`isFirstNameValid`/`isLastNameValid`/
  `isServiceAreaValid`/`isEmailValid`. One other hit:
  `driver-app/app/driver/(tabs)/profile.tsx` defines its own, separate
  `EMAIL_REGEX` const for a different screen's edit-profile flow — it is
  not imported from `profile-setup.tsx` and this change does not touch
  it. `profile-setup.tsx` itself is reached from `reactivate-account.tsx`,
  `otp.tsx`, and `_layout.tsx`, but none of those callers read the
  validation logic — they only navigate to the screen.
- No table, background loop, or ride-state-machine interaction — this is
  a client-side form-validation extraction only; the server
  (`createProfile` → `POST /users/profile`, then `registerDriver`) remains
  the source of truth and is untouched.
- Because the extraction is byte-for-byte behaviorally equivalent (same
  length/regex/empty checks, same order), there is no reasonable path for
  this diff to make a previously-valid submission now get rejected, or
  vice versa.

## 5. User-experience effect

None. Driver-facing behavior (which field is flagged first, the exact
toast copy, when each field's checkmark icon appears, when the Create
Profile button is enabled) is unchanged — this is an internal refactor of
the same rules into a tested, named location.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/utils/profileSetupSchema.ts` | New file: `profileSetupSchema` (zod), individual `isFirstNameValid`/`isLastNameValid`/`isEmailValid`/`isServiceAreaValid` predicates, `getProfileSetupError()`, `isProfileSetupFormValid()` | Colocated, testable extraction of the screen's inline validation |
| `driver-app/utils/__tests__/profileSetupSchema.test.ts` | New file: 19 accept/reject test cases | Closes the "no dedicated test coverage" gap B39 names |
| `driver-app/app/profile-setup.tsx` | Removed local `EMAIL_REGEX`/`validateEmail`/four inline `const` booleans; `handleSubmit`'s six inline `if` checks replaced with `getProfileSetupError(...)`; `isFormValid` replaced with `isProfileSetupFormValid(...)`; three JSX checkmark conditions now call the imported predicate functions | Use the extracted helpers instead of duplicating the rules inline |
| `ACTION_ITEMS.md` | Appended B39 step 6 bullet (including the `login.tsx` scope-pivot note) | Record what was checked, what changed, verification results, and what's still open |

## 7. Before / after

```tsx
// Before (app/profile-setup.tsx)
const validateEmail = (email: string): boolean => {
  return EMAIL_REGEX.test(email);
};

const isEmailValid = email.length > 0 && validateEmail(email);
const isFirstNameValid = firstName.trim().length > 1;
const isLastNameValid = lastName.trim().length > 1;
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
  Keyboard.dismiss();
  // ...
};
```

```tsx
// After (app/profile-setup.tsx)
const isFormValid = isProfileSetupFormValid({ firstName, lastName, email, gender, serviceAreaId });

const handleSubmit = async () => {
  const validationError = getProfileSetupError({ firstName, lastName, email, gender, serviceAreaId });
  if (validationError) {
    showToast('warning', validationError.title, validationError.message);
    return;
  }
  Keyboard.dismiss();
  // ...
};
```

## 8. Rollback plan

Pure code revert — `git revert` this commit. No data written, no
Stripe/wallet/ride-state interaction, no migration, no feature flag
needed: the old inline checks and the new extracted helpers are
behaviorally identical, so reverting is a same-behavior code change, not
a data-level rollback.

## 9. Verification performed

- [x] Automated tests run: new `profileSetupSchema.test.ts` (19/19 pass,
      isolated run); full driver-app suite `npx jest --no-coverage` —
      1262/1262 tests, 116/116 suites pass, 0 regressions, single clean
      full run with no flakes observed
- [x] `npx tsc --noEmit` — clean, no errors
- [x] `npx eslint app/profile-setup.tsx utils/profileSetupSchema.ts
      utils/__tests__/profileSetupSchema.test.ts` — clean, no errors
- [x] Real production build: `npm run build:web` (`expo export
      --platform web`) — completed successfully, exit code 0 (`Exported:
      dist`, main web bundle 6.8MB)
- [x] Blast-radius grep performed: searched `driver-app/app`,
      `driver-app/utils`, `driver-app/hooks`, `driver-app/lib` for the
      extracted checks and the email regex — one unrelated hit
      (`app/driver/(tabs)/profile.tsx`'s own separate `EMAIL_REGEX`,
      untouched by this change) besides `app/profile-setup.tsx` itself
- [x] Reviewed against CLAUDE.md conventions: pure extraction (no
      validation-rule change), surgical diff (only the schema file, test
      file, the one screen, ACTION_ITEMS.md, and this log)
- [x] Feature flag: not applicable — this is a behaviorally-identical
      refactor, not a new/changed UX

## 10. What was NOT verified

- No device/simulator run — verification is test suite + type check +
  lint + web production build, consistent with steps 1-5 in this series;
  driver-app and rider-app have no automated visual/snapshot regression
  tooling (CLAUDE.md's pre-merge gate #6), so the per-field checkmark
  icons and toast rendering were reasoned about from the unchanged JSX
  conditions, not screenshotted.
- The `dist/` web export produced by `npm run build:web` was not deployed
  or manually clicked through — the build's exit code and asset manifest
  were checked, not a rendered page.
- `login.tsx`'s trivial 10-digit check was reasoned about, not schema-ized
  or given a new test file, per the fallback rule this task specified for
  a genuinely non-extractable case.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data
      involved)
- [x] Blast radius is stated, not assumed (grep results above)
- [x] No silent behavior change to an already-shipped flow — verified
      byte-for-byte equivalence of every check and its message
