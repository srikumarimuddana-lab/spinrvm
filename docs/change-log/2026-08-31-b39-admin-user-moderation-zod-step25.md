# Change Impact & Risk Log — B39 step 25: admin-dashboard user ban/suspend reason validation

- **Issue/gap identified:** `admin-dashboard/src/app/dashboard/users/page.tsx`'s
  ban/suspend confirmation dialog validated the moderation-reason field
  via the same inline `!moderationReason.trim()` check duplicated in two
  places (the confirm button's `disabled` prop and its `onClick` guard)
  with no dedicated test coverage — the ninth admin-dashboard candidate
  from the 2026-08-31 broader B39 sweep (`ACTION_ITEMS.md` B39,
  "ban/suspend reason").
- **Root cause:** no schema-validation library was adopted on this
  surface; each screen re-implements its own validation inline, and here
  the same check was hand-duplicated across two call sites in the same
  component.
- **Fix/remediation:** extracted the check into new
  `admin-dashboard/src/lib/userModerationSchema.ts` —
  `isModerationReasonValid`. Both the `disabled` prop and the `onClick`
  guard now call the same extracted predicate instead of each spelling
  out `!moderationReason.trim()` separately. This is the
  "exact duplicate → safe to consolidate" case named in this item's own
  precedent (unlike partial-overlap `disabled` props elsewhere in this
  PR, which were left alone) — both call sites were the *identical*
  expression, so consolidating them removes a real drift risk rather
  than introducing one. Pure extraction — byte-for-byte identical
  accept/reject behavior; no validation-rule change. Not a zod schema —
  a single non-empty-after-trim check has no meaningful shape for zod's
  parsing machinery to add value over (documented in the file's own
  header comment).
- **Risk & impact on existing functionality:** the new function is
  colocated in `src/lib/userModerationSchema.ts` and used only by this
  dialog's two call sites. Blast-radius grep confirmed the only other
  `moderationReason.trim()` reference left in the file is the payload
  construction (`const reason = moderationReason.trim()`), not a
  validation check — correctly untouched.
- **User experience effect:** none. Same field, same check, same
  disabled/guard behavior.
- **Files modified:**

  | File | What changed | Why |
  |---|---|---|
  | `admin-dashboard/src/lib/userModerationSchema.ts` | New file: `isModerationReasonValid` predicate | Extract validation logic |
  | `admin-dashboard/src/lib/__tests__/userModerationSchema.test.ts` | New file: 4 accept/reject tests | Close the coverage gap this item names |
  | `admin-dashboard/src/app/dashboard/users/page.tsx` | Both the `disabled` prop and the `onClick` guard's inline `!moderationReason.trim()` replaced with `!isModerationReasonValid(moderationReason)`; added import | Pure extraction + duplicate consolidation |

- **Before/after snippet:**

  ```tsx
  // before
  disabled={!moderationReason.trim() || statusUpdating === pendingStatusChange?.id}
  onClick={async (e) => {
      if (!pendingStatusChange) return;
      if (!moderationReason.trim()) { e.preventDefault(); return; }
      ...

  // after
  disabled={!isModerationReasonValid(moderationReason) || statusUpdating === pendingStatusChange?.id}
  onClick={async (e) => {
      if (!pendingStatusChange) return;
      if (!isModerationReasonValid(moderationReason)) { e.preventDefault(); return; }
      ...
  ```

- **Rollback plan:** revert the commit — no data migration, no feature
  flag, no live-data mutation involved; this is a client-side pure
  refactor.
- **Verification performed:** 4/4 new tests pass
  (`npx vitest run src/lib/__tests__/userModerationSchema.test.ts`);
  full admin-dashboard suite (`npx vitest run`) 53/53 suites, 539/539
  tests passing, 0 regressions; `npx tsc --noEmit` clean (repo-wide);
  `npx eslint` on touched files: 0 errors, 3 pre-existing warnings
  elsewhere in the file, none near the touched lines; **real production
  build** (`npm run build`) completed successfully. Blast-radius grep
  confirmed the only other reference to the field is the (untouched)
  payload-construction trim, not a validation check.
- **What was NOT verified:** no manual click-through in a running admin
  dashboard against a live/staging Supabase — verified via unit tests,
  `tsc`, `eslint`, and a production build only, per this repo's stated
  no-active-visual-regression-tooling gap (CLAUDE.md pre-merge gate #6);
  this is a visually-invisible, logic-only change so it was reasoned
  about, not screenshotted.
