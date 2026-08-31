# Change Impact & Risk Log — B39 step 26: admin-dashboard venue-name requiredness check

- **Issue/gap identified:** `admin-dashboard/src/app/dashboard/venues/page.tsx`'s
  venue editor validated the venue-name field only via the Save button's
  `disabled` prop (`!d.name.trim()`), with no dedicated test coverage —
  the tenth admin-dashboard candidate from the 2026-08-31 broader B39
  sweep (`ACTION_ITEMS.md` B39, "venues").
- **Root cause:** no schema-validation library was adopted on this
  surface; the one validation rule this page has lives inline in a JSX
  prop expression.
- **Fix/remediation:** extracted the check into new
  `admin-dashboard/src/lib/venueFormSchema.ts` — `isVenueNameValid`. The
  Save button's `disabled` prop now calls the extracted predicate
  instead of spelling out `!d.name.trim()` inline. Pure extraction —
  byte-for-byte identical accept/reject behavior; no validation-rule
  change. Not a zod schema — a single non-empty-after-trim check has no
  meaningful shape for zod's parsing machinery to add value over
  (documented in the file's own header comment).
- **Out of scope, flagged not fixed:** this page also uses raw
  `alert`/`confirm` (lines ~184, 191-192) instead of the toast pattern
  used elsewhere in admin-dashboard — a UX-pattern finding from the same
  broader sweep, distinct from this item's validation-logic scope. Not
  touched here.
- **Risk & impact on existing functionality:** the new function is
  colocated in `src/lib/venueFormSchema.ts` and used only by this page's
  Save button. Blast-radius grep for `d.name.trim()` found no other
  file in `src/` using this exact expression — isolated.
- **User experience effect:** none. Same field, same check, same
  disabled-button behavior.
- **Files modified:**

  | File | What changed | Why |
  |---|---|---|
  | `admin-dashboard/src/lib/venueFormSchema.ts` | New file: `isVenueNameValid` predicate | Extract validation logic |
  | `admin-dashboard/src/lib/__tests__/venueFormSchema.test.ts` | New file: 4 accept/reject tests | Close the coverage gap this item names |
  | `admin-dashboard/src/app/dashboard/venues/page.tsx` | Save button's inline `!d.name.trim()` replaced with `!isVenueNameValid(d.name)`; added import | Pure extraction |

- **Before/after snippet:**

  ```tsx
  // before
  <button onClick={save} disabled={saving || !d.name.trim()} ...>

  // after
  <button onClick={save} disabled={saving || !isVenueNameValid(d.name)} ...>
  ```

- **Rollback plan:** revert the commit — no data migration, no feature
  flag, no live-data mutation involved; this is a client-side pure
  refactor.
- **Verification performed:** 4/4 new tests pass
  (`npx vitest run src/lib/__tests__/venueFormSchema.test.ts`); full
  admin-dashboard suite (`npx vitest run`) 54/54 suites, 543/543 tests
  passing, 0 regressions; `npx tsc --noEmit` clean (repo-wide); `npx
  eslint` on touched files: 0 errors, 1 pre-existing warning on an
  unrelated line in the same file; **real production build**
  (`npm run build`) completed successfully. Blast-radius grep confirmed
  no other file uses this exact expression.
- **What was NOT verified:** no manual click-through in a running admin
  dashboard against a live/staging Supabase — verified via unit tests,
  `tsc`, `eslint`, and a production build only, per this repo's stated
  no-active-visual-regression-tooling gap (CLAUDE.md pre-merge gate #6);
  this is a visually-invisible, logic-only change so it was reasoned
  about, not screenshotted.
