# Change Impact & Risk Log — B39 step 24: admin-dashboard driver photo-upload MIME validation

- **Issue/gap identified:** `admin-dashboard/src/app/dashboard/drivers/page.tsx`'s
  `handlePhotoUpload` validated the uploaded file's MIME type via a
  single inline `file.type.startsWith("image/")` check with no dedicated
  test coverage — the eighth admin-dashboard candidate from the
  2026-08-31 broader B39 sweep (`ACTION_ITEMS.md` B39, "photo-upload MIME
  check").
- **Root cause:** no schema-validation library was adopted on this
  surface; each screen re-implements its own validation inline.
- **Fix/remediation:** extracted the check into new
  `admin-dashboard/src/lib/driverPhotoUploadSchema.ts` —
  `isPhotoFileTypeValid`. `handlePhotoUpload` now calls the extracted
  predicate instead of its original inline check. Pure extraction —
  byte-for-byte identical accept/reject behavior; no validation-rule
  change. This one is a plain TypeScript predicate, not a zod schema — a
  single `File.type` string-prefix check has no meaningful shape for
  zod's parsing machinery to add value over, so a zod wrapper would only
  add indirection (documented in the file's own header comment).
- **Risk & impact on existing functionality:** the new function is
  colocated in `src/lib/driverPhotoUploadSchema.ts` and used only by
  this page's `handlePhotoUpload`. Blast-radius grep for the exact
  `type.startsWith("image/")` pattern found no other file in `src/`
  using it — isolated.
- **User experience effect:** none. Same check, same order, same error
  copy.
- **Files modified:**

  | File | What changed | Why |
  |---|---|---|
  | `admin-dashboard/src/lib/driverPhotoUploadSchema.ts` | New file: `isPhotoFileTypeValid` predicate | Extract validation logic |
  | `admin-dashboard/src/lib/__tests__/driverPhotoUploadSchema.test.ts` | New file: 9 accept/reject tests | Close the coverage gap this item names |
  | `admin-dashboard/src/app/dashboard/drivers/page.tsx` | `handlePhotoUpload`'s inline check replaced with the extracted predicate call; added import | Pure extraction |

- **Before/after snippet:**

  ```tsx
  // before
  if (!file.type.startsWith("image/")) {
      toast({ title: "Invalid file", description: "Please choose an image (JPEG, PNG, WebP, or GIF).", variant: "destructive" });
      return;
  }

  // after
  if (!isPhotoFileTypeValid(file)) {
      toast({ title: "Invalid file", description: "Please choose an image (JPEG, PNG, WebP, or GIF).", variant: "destructive" });
      return;
  }
  ```

- **Rollback plan:** revert the commit — no data migration, no feature
  flag, no live-data mutation involved; this is a client-side pure
  refactor.
- **Verification performed:** 9/9 new tests pass
  (`npx vitest run src/lib/__tests__/driverPhotoUploadSchema.test.ts`);
  full admin-dashboard suite (`npx vitest run`) 52/52 suites, 535/535
  tests passing, 0 regressions; `npx tsc --noEmit` clean (repo-wide);
  `npx eslint` on touched files: 0 errors — 45 pre-existing warnings
  throughout this large file (unrelated component/hooks/img-tag lint
  rules), none within or near the touched lines, confirmed by grep;
  **real production build** (`npm run build`) completed successfully.
  Blast-radius grep confirmed no other file uses this exact check.
- **What was NOT verified:** no manual click-through in a running admin
  dashboard against a live/staging Supabase — verified via unit tests,
  `tsc`, `eslint`, and a production build only, per this repo's stated
  no-active-visual-regression-tooling gap (CLAUDE.md pre-merge gate #6);
  this is a visually-invisible, logic-only change so it was reasoned
  about, not screenshotted.
