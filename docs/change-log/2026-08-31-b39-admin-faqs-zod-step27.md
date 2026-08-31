# Change Impact & Risk Log — B39 step 27: admin-dashboard FAQ question/answer requiredness check

- **Issue/gap identified:** `admin-dashboard/src/app/dashboard/faqs/page.tsx`'s
  `handleSave` validated the question/answer fields via a single inline
  `!form.question.trim() || !form.answer.trim()` check with no dedicated
  test coverage — the eleventh (final) admin-dashboard candidate from
  the 2026-08-31 broader B39 sweep (`ACTION_ITEMS.md` B39, "faqs").
- **Root cause:** no schema-validation library was adopted on this
  surface; each screen re-implements its own validation inline.
- **Fix/remediation:** extracted the check into new
  `admin-dashboard/src/lib/faqFormSchema.ts` — `isFaqFormValid`.
  `handleSave` now calls the extracted predicate instead of its original
  inline check. Pure extraction — byte-for-byte identical accept/reject
  behavior and error copy; no validation-rule change. Not a zod schema —
  a single combined non-empty-after-trim check has no meaningful shape
  for zod's parsing machinery to add value over.
- **Separately flagged, not fixed (out of scope for this validation
  step):** `handleDelete`'s `catch` block silently swallows a failed
  `deleteFaq` call with only a code comment ("keep dialog open, user can
  retry") — no `setDeleteError` or equivalent, so a failed delete shows
  the admin nothing. Same "silent error swallow" shape as the
  `resolveDispute` gap found and fixed in step 18's session
  (`task_916f2e38`, later fixed in #4754). Queued as a separate task
  suggestion rather than folded into this validation step.
- **Risk & impact on existing functionality:** the new function is
  colocated in `src/lib/faqFormSchema.ts` and used only by this page's
  `handleSave`. Blast-radius grep for the exact error string found no
  other file in `src/` using it besides this page's own (untouched)
  `setSaveError(...)` call — isolated.
- **User experience effect:** none. Same fields, same check, same error
  copy.
- **Files modified:**

  | File | What changed | Why |
  |---|---|---|
  | `admin-dashboard/src/lib/faqFormSchema.ts` | New file: `isFaqFormValid` predicate | Extract validation logic |
  | `admin-dashboard/src/lib/__tests__/faqFormSchema.test.ts` | New file: 6 accept/reject tests | Close the coverage gap this item names |
  | `admin-dashboard/src/app/dashboard/faqs/page.tsx` | `handleSave`'s inline check replaced with `isFaqFormValid(...)`; added import | Pure extraction |

- **Before/after snippet:**

  ```tsx
  // before
  if (!form.question.trim() || !form.answer.trim()) {
      setSaveError("Question and answer are required.");
      return;
  }

  // after
  if (!isFaqFormValid(form.question, form.answer)) {
      setSaveError("Question and answer are required.");
      return;
  }
  ```

- **Rollback plan:** revert the commit — no data migration, no feature
  flag, no live-data mutation involved; this is a client-side pure
  refactor.
- **Verification performed:** 6/6 new tests pass
  (`npx vitest run src/lib/__tests__/faqFormSchema.test.ts`); full
  admin-dashboard suite (`npx vitest run`) 55/55 suites, 549/549 tests
  passing, 0 regressions; `npx tsc --noEmit` clean (repo-wide); `npx
  eslint` on touched files: 0 errors, 1 pre-existing warning on an
  unrelated line in the same file; **real production build**
  (`npm run build`) completed successfully. Blast-radius grep confirmed
  isolation.
- **What was NOT verified:** no manual click-through in a running admin
  dashboard against a live/staging Supabase — verified via unit tests,
  `tsc`, `eslint`, and a production build only, per this repo's stated
  no-active-visual-regression-tooling gap (CLAUDE.md pre-merge gate #6);
  this is a visually-invisible, logic-only change so it was reasoned
  about, not screenshotted.

This closes the last standalone admin-dashboard candidate from the
2026-08-31 broader B39 sweep. Two admin-dashboard items remain from the
original 21-candidate list — `ride-complaint-form.tsx` and
`ride-flag-form.tsx`, both flagged as "silent no-op UX gaps" (no missing-
field error shown at all on an empty submit, not just a missing schema)
— these need a small behavior addition (an error message), not a pure
extraction, so they're being tracked as their own step rather than
folded into this one.
