# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude Code (session) |
| Surface(s) | rider-app |
| Domain (Sentry tag) | rides (identity/profile, feeds downstream KYC/compliance checks — no dedicated `auth` form yet, closest fit is not applicable; treated as a rider-facing profile change) |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | ACTION_ITEMS.md B39, step 5 |

## 1. Issue / gap identified

`rider-app/app/profile-setup.tsx`'s `handleSubmit` validated first name,
last name, email, and gender via five sequential hand-rolled inline
checks (`!form.firstName.trim()`, `!form.lastName.trim()`,
`!form.email.trim()`, an email regex, `!form.gender`) with no dedicated
test file — the exact "validation-rule coverage is invisible" gap
ACTION_ITEMS.md B39 names, on the first login/signup-adjacent form in the
item's own priority ordering (rider identity fields here feed downstream
KYC/compliance checks).

## 2. Root cause

No schema-validation library was adopted on any of the three frontend
surfaces (B39's core finding) — validation was written ad hoc, screen by
screen, as it was needed, with no single place to point a coverage tool
at.

## 3. Fix / remediation

Pure extraction, not a validation-rule change. Moved the five inline
checks into a new colocated `rider-app/utils/profileSetupSchema.ts` using
`zod` (already a project dependency, added in B39 step 1 — not
re-added), preserving the exact same accept/reject rules, the exact same
toast title/message pairs, and the exact same first-failing-check
priority order. `app/profile-setup.tsx` now calls the extracted
`getProfileSetupError()` helper in `handleSubmit` and the extracted
`isProfileSetupValid()` helper in the `isFormValid` boolean, instead of
repeating the checks inline.

## 4. Risk & impact on existing functionality

- Blast radius: **isolated to `rider-app/app/profile-setup.tsx`.**
  Grepped `rider-app/app` and `rider-app/utils` for the email regex
  (`[^\s@]+@[^\s@]+`) and for any other reader/importer of
  `profile-setup.tsx`'s validation — no other screen duplicates or reads
  this specific check. `profile-setup.tsx` itself is reached from
  `otp.tsx`, `index.tsx`, `reactivate-account.tsx`, and
  `(tabs)/account.tsx`'s "Edit Personal Info" link, but none of those
  callers touch the validation logic — they only navigate to the screen.
- No table, background loop, or ride-state-machine interaction — this is
  a client-side form-validation extraction only; the server (`POST
  /users/profile` via `createProfile`) remains the source of truth and is
  untouched.
- Because the extraction is byte-for-byte behaviorally equivalent (same
  regex, same trim/empty checks, same order), there is no reasonable path
  for this diff to make a previously-valid submission now get rejected,
  or vice versa.

## 5. User-experience effect

None. Rider-facing behavior (which field is flagged first, the exact
toast copy, when the Create Profile/Save Changes button is enabled) is
unchanged — this is an internal refactor of the same rules into a tested,
named location.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/utils/profileSetupSchema.ts` | New file: `profileSetupSchema` (zod), `getProfileSetupError()`, `isProfileSetupValid()` | Colocated, testable extraction of the screen's inline validation |
| `rider-app/utils/__tests__/profileSetupSchema.test.ts` | New file: 18 accept/reject test cases | Closes the "no dedicated test coverage" gap B39 names |
| `rider-app/app/profile-setup.tsx` | `handleSubmit`'s five inline `if` checks replaced with `getProfileSetupError(form)`; `isFormValid`'s name/email/gender clause replaced with `isProfileSetupValid(form)` | Use the extracted helper instead of duplicating the rules inline |
| `ACTION_ITEMS.md` | Appended B39 step 5 bullet | Record what changed, verification results, and what's still open |

## 7. Before / after

```tsx
// Before (app/profile-setup.tsx, handleSubmit)
if (!form.firstName.trim()) {
  return showToast('First Name Required', 'Please enter your first name.', 'warning');
}
if (!form.lastName.trim()) {
  return showToast('Last Name Required', 'Please enter your last name.', 'warning');
}
if (!form.email.trim()) {
  return showToast('Email Required', 'Please enter your email address.', 'warning');
}
if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) {
  return showToast('Invalid Email', 'That email doesn’t look right — e.g. name@example.com.', 'warning');
}
if (!form.gender) {
  return showToast('Gender Required', 'Please select your gender.', 'warning');
}

const isFormValid = form.firstName.trim() && form.lastName.trim() && form.email.trim() && form.gender && (isEditing || tosAccepted);
```

```tsx
// After (app/profile-setup.tsx, handleSubmit)
const validationError = getProfileSetupError(form);
if (validationError) {
  return showToast(validationError.title, validationError.message, 'warning');
}

const isFormValid = isProfileSetupValid(form) && (isEditing || tosAccepted);
```

## 8. Rollback plan

Pure code revert — `git revert` this commit. No data written, no
Stripe/wallet/ride-state interaction, no migration, no feature flag
needed: the old inline checks and the new extracted helper are
behaviorally identical, so reverting is a same-behavior code change, not
a data-level rollback.

## 9. Verification performed

- [x] Automated tests run: new `profileSetupSchema.test.ts` (18/18 pass,
      isolated run); full rider-app suite `npx jest` — 1259/1259 tests,
      123/123 suites pass, 0 regressions (confirmed clean on two separate
      full runs; a single earlier full-parallel run showed 2 unrelated
      `homeScreen`/`rideOptionsScreen` timeout flakes, reproduced as
      pre-existing by rerunning those two suites in isolation against
      both `origin/main` and this branch — both pass 68/68 either way,
      and were not present in the two clean full reruns)
- [x] `npx tsc --noEmit` — clean, no errors
- [x] `npx eslint app/profile-setup.tsx utils/profileSetupSchema.ts
      utils/__tests__/profileSetupSchema.test.ts` — clean, no errors
- [x] Real production build: `npm run build:web` (`expo export
      --platform web`) — completed successfully, exit code 0
- [x] Blast-radius grep performed: searched `rider-app/app` and
      `rider-app/utils` for the email regex and for other
      importers/readers of the extracted checks — none found besides
      `app/profile-setup.tsx`
- [x] Reviewed against CLAUDE.md conventions: pure extraction (no
      validation-rule change), surgical diff (only the schema file, test
      file, the one screen, ACTION_ITEMS.md, and this log)
- [x] Feature flag: not applicable — this is a behaviorally-identical
      refactor, not a new/changed UX

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data
      involved)
- [x] Blast radius is stated, not assumed (grep results above)
- [x] No silent behavior change to an already-shipped flow — verified
      byte-for-byte equivalence of every check and its message
