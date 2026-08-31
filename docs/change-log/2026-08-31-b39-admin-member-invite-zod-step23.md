# Change Impact & Risk Log — B39 step 23: admin-dashboard corporate-member invite-email validation → zod

- **Issue/gap identified:** `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/members/page.tsx`'s
  `handleInvite` validated an invite-email input via two sequential
  inline checks (non-empty; matches a hand-rolled email-shape regex)
  with no dedicated test coverage — the seventh admin-dashboard
  candidate from the 2026-08-31 broader B39 sweep (`ACTION_ITEMS.md`
  B39, "invite-email regex").
- **Root cause:** no schema-validation library was adopted on this
  surface; each screen re-implements its own validation inline.
- **Fix/remediation:** extracted the two checks into new
  `admin-dashboard/src/lib/companyMemberInviteSchema.ts` —
  `isInviteEmailProvided` and `isInviteEmailFormatValid` (using the
  page's own original regex, not zod's built-in `.email()` — zod's
  validator accepts/rejects a different string set than this hand-rolled
  pattern, so swapping it in would be a validation-rule change, not a
  pure extraction), plus a `companyMemberInviteSchema` zod object
  (superRefine, same order, for documentation/type purposes).
  `handleInvite` now calls both predicates in the same order instead of
  its original inline checks. Pure extraction — byte-for-byte identical
  accept/reject behavior; the first check stays a silent no-op return
  (no toast), exactly as in the original.
- **Risk & impact on existing functionality:** both new functions are
  colocated in `src/lib/companyMemberInviteSchema.ts` and used only by
  this page's `handleInvite`. Blast-radius grep for the exact regex
  literal found no other file in `src/` using this pattern — isolated.
- **User experience effect:** none. Same field, same order, same error
  copy, same silent-no-op behavior on an empty submit.
- **Files modified:**

  | File | What changed | Why |
  |---|---|---|
  | `admin-dashboard/src/lib/companyMemberInviteSchema.ts` | New file: predicate functions + a documentation zod schema | Extract validation logic |
  | `admin-dashboard/src/lib/__tests__/companyMemberInviteSchema.test.ts` | New file: 11 accept/reject tests | Close the coverage gap this item names |
  | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/members/page.tsx` | `handleInvite`'s two inline checks replaced with the two extracted predicate calls; added import | Pure extraction |

- **Before/after snippet:**

  ```tsx
  // before
  if (!inviteEmail) return;
  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  if (!emailRegex.test(inviteEmail.trim())) {
      toast({ title: "Invalid email address", description: "Please enter a valid email", variant: "destructive" });
      return;
  }

  // after
  if (!isInviteEmailProvided(inviteEmail)) return;
  if (!isInviteEmailFormatValid(inviteEmail)) {
      toast({ title: "Invalid email address", description: "Please enter a valid email", variant: "destructive" });
      return;
  }
  ```

- **Rollback plan:** revert the commit — no data migration, no feature
  flag, no live-data mutation involved; this is a client-side pure
  refactor.
- **Verification performed:** 11/11 new tests pass
  (`npx vitest run src/lib/__tests__/companyMemberInviteSchema.test.ts`);
  full admin-dashboard suite (`npx vitest run`) 51/51 suites, 526/526
  tests passing, 0 regressions; `npx tsc --noEmit` clean (repo-wide);
  `npx eslint` on touched files: 0 errors, 1 pre-existing
  `react-hooks/set-state-in-effect` warning on an unrelated line in the
  same file, unchanged by this diff; **real production build**
  (`npm run build`) completed successfully. Blast-radius grep confirmed
  no other file in `src/` uses this exact regex pattern.
- **What was NOT verified:** no manual click-through in a running admin
  dashboard against a live/staging Supabase — verified via unit tests,
  `tsc`, `eslint`, and a production build only, per this repo's stated
  no-active-visual-regression-tooling gap (CLAUDE.md pre-merge gate #6);
  this is a visually-invisible, logic-only change so it was reasoned
  about, not screenshotted.
