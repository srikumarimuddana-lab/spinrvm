# Change Impact & Risk Log — B39 step 32: driver-app profile-edit validation

- **Issue/gap identified:** `driver-app/app/driver/(tabs)/profile.tsx`'s
  `handleSaveProfile` validated the edit-profile form (name, email,
  gender) via two sequential inline checks (all fields required; email
  must match the screen's own email-shape regex), and the same
  fields-required expression was hand-duplicated a further two times at
  the Save button's `disabled` prop and its style expression — four
  copies of overlapping logic, no dedicated test coverage — the fourth
  driver-app candidate from the 2026-08-31 broader B39 sweep
  (`ACTION_ITEMS.md` B39, "email regex").
- **Root cause:** no schema-validation library was adopted on this
  surface; each screen re-implements its own validation inline, and here
  the fields-required check was hand-copied instead of shared.
- **Fix/remediation:** extracted the logic into new
  `driver-app/utils/driverProfileSchema.ts` — `isProfileFieldsComplete`,
  `isProfileEmailFormatValid` (keeps the screen's own regex, not zod's
  built-in `.email()` — a different accept/reject set, so swapping it in
  would be a validation-rule change), and `getProfileFormError` (runs
  both checks in the same order, returning the same `{ title, message }`
  pair the original passed to `showToast('error', ...)`).
  `handleSaveProfile` now calls `getProfileFormError(...)` once; the
  Save button's `disabled` prop and its style expression — both an
  *exact* duplicate of `handleSaveProfile`'s first check, unlike the
  partial-overlap `disabled` props left alone elsewhere in this series —
  now both call the shared `isProfileFieldsComplete(...)` instead of
  restating the four-clause boolean expression. The screen's local
  `EMAIL_REGEX` constant (now unused) was removed. Pure extraction —
  byte-for-byte identical accept/reject behavior and identical toast
  copy; no validation-rule change.
- **Risk & impact on existing functionality:** all three new functions
  are colocated in `driver-app/utils/driverProfileSchema.ts` and used
  only by this screen's three call sites. Blast-radius grep for the two
  distinctive toast strings and the exact regex literal found one
  coincidental hit — `driver-app/utils/profileSetupSchema.ts` (a B39
  step 6 extraction for the unrelated `app/profile-setup.tsx` signup
  screen) has its own independently-defined, identically-patterned
  `EMAIL_REGEX` — confirmed unrelated by inspection (different screen,
  different call sites), not a duplicate to consolidate.
- **User experience effect:** none. Same fields, same order, same toast
  copy, same disabled-button behavior.
- **Files modified:**

  | File | What changed | Why |
  |---|---|---|
  | `driver-app/utils/driverProfileSchema.ts` | New file: predicate functions, `getProfileFormError` | Extract validation logic |
  | `driver-app/utils/__tests__/driverProfileSchema.test.ts` | New file: 11 accept/reject tests | Close the coverage gap this item names |
  | `driver-app/app/driver/(tabs)/profile.tsx` | `handleSaveProfile`'s two inline checks replaced with one `getProfileFormError(...)` call; both `disabled`-prop/style duplicates of the fields-required check replaced with `isProfileFieldsComplete(...)`; local `EMAIL_REGEX` constant removed (now unused); added import | Pure extraction + duplicate consolidation |

- **Before/after snippet:**

  ```tsx
  // before (handleSaveProfile)
  if (!editFirstName.trim() || !editLastName.trim() || !editEmail.trim() || !editGender) {
      return showToast('error', 'Missing Info', 'Please fill in all fields');
  }
  if (!EMAIL_REGEX.test(editEmail)) return showToast('error', 'Invalid Email', 'Please enter a valid email address');

  // after
  const formError = getProfileFormError(editFirstName, editLastName, editEmail, editGender);
  if (formError) return showToast('error', formError.title, formError.message);
  ```

  ```tsx
  // before (Save button, x2 -- disabled prop and style expression)
  !editFirstName.trim() || !editLastName.trim() || !editEmail.trim() || !editGender || isSaving

  // after
  !isProfileFieldsComplete(editFirstName, editLastName, editEmail, editGender) || isSaving
  ```

- **Rollback plan:** revert the commit — no data migration, no feature
  flag, no live-data mutation involved; this is a client-side pure
  refactor.
- **Verification performed:** 11/11 new tests pass
  (`npx jest utils/__tests__/driverProfileSchema.test.ts`); full
  driver-app suite (`npx jest`) 124/124 suites, 1402/1402 tests passing
  on a clean rerun (one transient unrelated flake on the first run,
  resolved by rerun, confirming it wasn't caused by this diff); `npx tsc
  --noEmit` clean (repo-wide); `npx eslint` on touched files: 0 errors,
  0 warnings; **real production build** (`npm run build:web` → `expo
  export --platform web`) completed successfully. Blast-radius grep
  found one coincidental same-pattern regex in an unrelated screen's
  already-migrated schema file, confirmed unrelated by inspection.
- **What was NOT verified:** no manual click-through of the profile-edit
  modal in a running app against a live/staging Supabase — verified via
  unit tests, `tsc`, `eslint`, and a production web build only;
  driver-app has no active visual-regression tooling (CLAUDE.md
  pre-merge gate #6), so this visually-invisible, logic-only change was
  reasoned about, not screenshotted.
