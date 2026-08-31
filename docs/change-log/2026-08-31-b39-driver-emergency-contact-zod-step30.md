# Change Impact & Risk Log — B39 step 30: driver-app emergency-contact validation

- **Issue/gap identified:** `driver-app/app/driver/emergency-contacts.tsx`'s
  `handleAdd` validated a new emergency contact's name and phone number
  via two sequential inline checks (name required; phone must have at
  least 10 digits after non-digit stripping) with no dedicated test
  coverage — the second driver-app candidate from the 2026-08-31 broader
  B39 sweep (`ACTION_ITEMS.md` B39, safety tier).
- **Root cause:** no schema-validation library was adopted on this
  surface; each screen re-implements its own validation inline.
- **Fix/remediation:** extracted the checks into new
  `driver-app/utils/emergencyContactSchema.ts` — `isContactNameValid`,
  `isContactPhoneValid`, and `getEmergencyContactFormError` (runs both
  checks in the same order, returning the same `{ title, message }` pair
  the original passed to `showToast('warning', ...)`). `handleAdd` now
  calls `getEmergencyContactFormError(...)` once instead of the two
  sequential `if` blocks. Pure extraction — byte-for-byte identical
  accept/reject behavior and identical toast copy; no validation-rule
  change.
- **Risk & impact on existing functionality:** all three new functions
  are colocated in `driver-app/utils/emergencyContactSchema.ts` and used
  only by this screen's `handleAdd`. Blast-radius grep for the two
  distinctive toast strings found no other file in `app/`/`utils/`/
  `shared/` using them — isolated.
- **User experience effect:** none. Same fields, same order, same toast
  copy, same accept/reject boundary (a 10-digit phone number still
  accepted, matching the original's `length < 10` strict-less-than
  check).
- **Files modified:**

  | File | What changed | Why |
  |---|---|---|
  | `driver-app/utils/emergencyContactSchema.ts` | New file: predicate functions, `getEmergencyContactFormError` | Extract validation logic |
  | `driver-app/utils/__tests__/emergencyContactSchema.test.ts` | New file: 13 accept/reject tests | Close the coverage gap this item names |
  | `driver-app/app/driver/emergency-contacts.tsx` | `handleAdd`'s two inline `if` blocks replaced with one `getEmergencyContactFormError(...)` call; added import | Pure extraction |

- **Before/after snippet:**

  ```tsx
  // before
  const trimmedName = name.trim();
  const trimmedPhone = phone.trim().replace(/\D/g, '');
  if (!trimmedName) {
      showToast('warning', 'Missing Name', 'Please enter a contact name.');
      return;
  }
  if (trimmedPhone.length < 10) {
      showToast('warning', 'Invalid Phone', 'Please enter a valid phone number (at least 10 digits).');
      return;
  }

  // after
  const formError = getEmergencyContactFormError(name, phone);
  if (formError) {
      showToast('warning', formError.title, formError.message);
      return;
  }
  const trimmedName = name.trim();
  const trimmedPhone = phone.trim().replace(/\D/g, '');
  ```

- **Rollback plan:** revert the commit — no data migration, no feature
  flag, no live-data mutation involved; this is a client-side pure
  refactor.
- **Verification performed:** 13/13 new tests pass
  (`npx jest utils/__tests__/emergencyContactSchema.test.ts`); full
  driver-app suite (`npx jest`) 122/122 suites, 1381/1381 tests passing,
  0 regressions; `npx tsc --noEmit` clean (repo-wide); `npx eslint` on
  touched files: 0 errors, 0 warnings; **real production build**
  (`npm run build:web` → `expo export --platform web`) completed
  successfully. Blast-radius grep confirmed no other file uses these
  distinctive toast strings.
- **What was NOT verified:** no manual click-through of the
  emergency-contacts screen in a running app against a live/staging
  Supabase — verified via unit tests, `tsc`, `eslint`, and a production
  web build only; driver-app has no active visual-regression tooling
  (CLAUDE.md pre-merge gate #6), so this visually-invisible, logic-only
  change was reasoned about, not screenshotted.
