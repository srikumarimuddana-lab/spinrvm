# Change Impact & Risk Log — B39 step 31: driver-app safety-report validation

- **Issue/gap identified:** `driver-app/app/report-safety.tsx`'s
  `handleSubmit` validated the category and description fields via two
  sequential inline checks with no dedicated test coverage — the third
  driver-app candidate from the 2026-08-31 broader B39 sweep
  (`ACTION_ITEMS.md` B39, safety tier).
- **Root cause:** no schema-validation library was adopted on this
  surface; each screen re-implements its own validation inline.
- **Fix/remediation:** extracted the checks into new
  `driver-app/utils/reportSafetySchema.ts` — `isSafetyCategoryValid`,
  `isSafetyIssueValid`, and `getReportSafetyFormError` (runs both checks
  in the same order, returning the same `{ title, message }` pair the
  original passed to `showToast('warning', ...)`). `handleSubmit` now
  calls `getReportSafetyFormError(...)` once instead of the two
  sequential `if` blocks. Pure extraction — byte-for-byte identical
  accept/reject behavior and identical toast copy; no validation-rule
  change. `isSafetyCategoryValid` accepts `string | null` to match the
  screen's `category` state type (`useState<SafetyCategory | null>`) —
  a type-only accommodation, not a behavior change (the original
  `!category` check already treated `null` and `''` identically).
- **Risk & impact on existing functionality:** all three new functions
  are colocated in `driver-app/utils/reportSafetySchema.ts` and used
  only by this screen's `handleSubmit`. Blast-radius grep for the two
  distinctive toast strings found no other file in `app/`/`utils/`/
  `shared/` using them — isolated.
- **User experience effect:** none. Same fields, same order, same toast
  copy.
- **Files modified:**

  | File | What changed | Why |
  |---|---|---|
  | `driver-app/utils/reportSafetySchema.ts` | New file: predicate functions, `getReportSafetyFormError` | Extract validation logic |
  | `driver-app/utils/__tests__/reportSafetySchema.test.ts` | New file: 10 accept/reject tests | Close the coverage gap this item names |
  | `driver-app/app/report-safety.tsx` | `handleSubmit`'s two inline `if` blocks replaced with one `getReportSafetyFormError(...)` call; added import | Pure extraction |

- **Before/after snippet:**

  ```tsx
  // before
  if (!category) {
      showToast('warning', 'Category Required', 'Please select a category for your safety report.');
      return;
  }
  if (!issue.trim()) {
      showToast('warning', 'Description Required', 'Please describe the safety issue before submitting.');
      return;
  }

  // after
  const formError = getReportSafetyFormError(category, issue);
  if (formError) {
      showToast('warning', formError.title, formError.message);
      return;
  }
  ```

- **Rollback plan:** revert the commit — no data migration, no feature
  flag, no live-data mutation involved; this is a client-side pure
  refactor.
- **Verification performed:** 10/10 new tests pass
  (`npx jest utils/__tests__/reportSafetySchema.test.ts`); full
  driver-app suite (`npx jest`) 123/123 suites, 1391/1391 tests passing,
  0 regressions; `npx tsc --noEmit` clean (repo-wide, after widening
  `isSafetyCategoryValid`'s parameter type to `string | null`); `npx
  eslint` on touched files: 0 errors, 0 warnings; **real production
  build** (`npm run build:web` → `expo export --platform web`) completed
  successfully. Blast-radius grep confirmed no other file uses these
  toast strings.
- **What was NOT verified:** no manual click-through of the
  report-safety screen in a running app against a live/staging Supabase
  — verified via unit tests, `tsc`, `eslint`, and a production web build
  only; driver-app has no active visual-regression tooling (CLAUDE.md
  pre-merge gate #6), so this visually-invisible, logic-only change was
  reasoned about, not screenshotted.
