# Change Impact & Risk Log — B39 step 33: driver-app account-deletion confirmation validation

- **Issue/gap identified:** `driver-app/app/driver/settings.tsx`'s
  `executeDelete` validated the type-DELETE confirmation input via a
  single inline check with no dedicated test coverage — the fifth
  driver-app candidate from the 2026-08-31 broader B39 sweep
  (`ACTION_ITEMS.md` B39, PIPEDA-adjacent — gates the driver-facing
  self-serve account-deletion request).
- **Root cause:** no schema-validation library was adopted on this
  surface; each screen re-implements its own validation inline.
- **Fix/remediation:** extracted the check into new
  `driver-app/utils/accountDeletionSchema.ts` —
  `isDeleteConfirmationValid`. `executeDelete` now calls the extracted
  predicate instead of its original inline check. Pure extraction —
  byte-for-byte identical accept/reject behavior; no validation-rule
  change. Kept as a plain predicate, not a zod schema — a single
  case-insensitive string-equality check has no meaningful shape for
  zod's parsing machinery to add value over. The toast copy at the call
  site is untouched (it uses this screen's `t(...)` i18n keys, not
  literal strings, so there was no copy to extract).
- **Risk & impact on existing functionality:** the new function is
  colocated in `driver-app/utils/accountDeletionSchema.ts` and used only
  by this screen's `executeDelete`. Blast-radius grep for the exact
  check expression found no other file in `app/`/`utils/`/`shared/`
  using it. No `disabled`-prop duplicate exists on this screen's confirm
  button (unlike some earlier steps in this series) — nothing else to
  reconcile.
- **User experience effect:** none. Same field, same check, same
  accept/reject behavior (still case-insensitive and whitespace-trimmed,
  matching the original).
- **Files modified:**

  | File | What changed | Why |
  |---|---|---|
  | `driver-app/utils/accountDeletionSchema.ts` | New file: `isDeleteConfirmationValid` predicate | Extract validation logic |
  | `driver-app/utils/__tests__/accountDeletionSchema.test.ts` | New file: 7 accept/reject tests | Close the coverage gap this item names |
  | `driver-app/app/driver/settings.tsx` | `executeDelete`'s inline check replaced with `isDeleteConfirmationValid(...)`; added import | Pure extraction |

- **Before/after snippet:**

  ```tsx
  // before
  if (deleteInput.trim().toUpperCase() !== 'DELETE') {
      showToast('warning', t('settings.notConfirmedTitle'), t('settings.notConfirmedMsg'));
      return;
  }

  // after
  if (!isDeleteConfirmationValid(deleteInput)) {
      showToast('warning', t('settings.notConfirmedTitle'), t('settings.notConfirmedMsg'));
      return;
  }
  ```

- **Rollback plan:** revert the commit — no data migration, no feature
  flag, no live-data mutation involved; this is a client-side pure
  refactor.
- **Verification performed:** 7/7 new tests pass
  (`npx jest utils/__tests__/accountDeletionSchema.test.ts`); full
  driver-app suite (`npx jest`) 125/125 suites, 1409/1409 tests passing,
  0 regressions; `npx tsc --noEmit` clean (repo-wide); `npx eslint` on
  touched files: 0 errors, 0 warnings; **real production build**
  (`npm run build:web` → `expo export --platform web`) completed
  successfully. Blast-radius grep confirmed no other file uses this
  exact check expression.
- **What was NOT verified:** no manual click-through of the two-step
  account-deletion flow in a running app against a live/staging Supabase
  — verified via unit tests, `tsc`, `eslint`, and a production web build
  only; driver-app has no active visual-regression tooling (CLAUDE.md
  pre-merge gate #6), so this visually-invisible, logic-only change was
  reasoned about, not screenshotted.
