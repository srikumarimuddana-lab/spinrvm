# Change Impact & Risk Log — B39 step 21: admin-dashboard safety-incident log/merge validation → zod

- **Issue/gap identified:** `admin-dashboard/src/app/dashboard/safety/page.tsx`
  had two ad hoc validation blocks with no dedicated test coverage: the
  manual "Log a safety incident" dialog's `handleSubmit` (category +
  description required) and the incident-detail drawer's duplicate-merge
  flow's `handleMerge` (canonical target ID required, cannot merge an
  incident into itself) — the fifth admin-dashboard candidate from the
  2026-08-31 broader B39 sweep (`ACTION_ITEMS.md` B39, safety tier).
- **Root cause:** no schema-validation library was adopted on this
  surface; each screen re-implements its own validation inline.
- **Fix/remediation:** extracted both blocks into new
  `admin-dashboard/src/lib/safetyIncidentFormSchema.ts` —
  `isLogIncidentFormValid`/`getLogIncidentFormError` for the log-incident
  form, and `isMergeTargetProvided`/`isMergeTargetDifferentFromIncident`/
  `getMergeIncidentError` (plus a `mergeIncidentSchema` zod object,
  superRefine, same order, for documentation/type purposes) for the merge
  flow. `handleSubmit` and `handleMerge` now each call their respective
  `get*Error(...)` function once instead of their original sequential
  `if` blocks. Pure extraction — byte-for-byte identical accept/reject
  behavior and identical error copy; no validation-rule change.
- **Risk & impact on existing functionality:** both new functions are
  colocated in `src/lib/safetyIncidentFormSchema.ts` and used only by
  `safety/page.tsx`'s two handlers. Blast-radius grep for the three
  distinctive error strings found zero other call sites in `src/`. The
  Merge button's `disabled` prop (`merging || !mergeTargetId.trim()`)
  duplicates only the presence check, not the self-merge check — a
  partial, not exact, duplicate — and was left untouched, same
  discipline as prior steps. The Log Incident button's `disabled` prop
  (`saving` only) does not duplicate any of `handleSubmit`'s checks.
- **User experience effect:** none. Same fields, same order, same error
  copy on both forms.
- **Files modified:**

  | File | What changed | Why |
  |---|---|---|
  | `admin-dashboard/src/lib/safetyIncidentFormSchema.ts` | New file: predicate functions, zod schema, `getLogIncidentFormError` + `getMergeIncidentError` | Extract validation logic |
  | `admin-dashboard/src/lib/__tests__/safetyIncidentFormSchema.test.ts` | New file: 13 accept/reject tests | Close the coverage gap this item names |
  | `admin-dashboard/src/app/dashboard/safety/page.tsx` | `handleSubmit`'s and `handleMerge`'s inline `if` blocks each replaced with one `get*Error(...)` call; added import | Pure extraction |

- **Before/after snippet:**

  ```tsx
  // before (handleMerge)
  const targetId = mergeTargetId.trim();
  if (!targetId) {
      toast({ title: "Enter the canonical incident ID to merge into", variant: "destructive" });
      return;
  }
  if (targetId === incident.id) {
      toast({ title: "Cannot merge an incident into itself", variant: "destructive" });
      return;
  }

  // after
  const mergeError = getMergeIncidentError(mergeTargetId, incident.id);
  if (mergeError) {
      toast({ title: mergeError, variant: "destructive" });
      return;
  }
  const targetId = mergeTargetId.trim();
  ```

- **Rollback plan:** revert the commit — no data migration, no feature
  flag, no live-data mutation involved; this is a client-side pure
  refactor.
- **Verification performed:** 13/13 new tests pass
  (`npx vitest run src/lib/__tests__/safetyIncidentFormSchema.test.ts`);
  full admin-dashboard suite (`npx vitest run`) 49/49 suites, 497/497
  tests passing, 0 regressions; `npx tsc --noEmit` clean (repo-wide);
  `npx eslint` on touched files: 0 errors, 3 pre-existing warnings (1
  unused-import, 2 `react-hooks/set-state-in-effect`) on unrelated lines
  in the same file, unchanged by this diff; **real production build**
  (`npm run build`) completed successfully. Blast-radius grep confirmed
  no other file in `src/` uses either form's distinctive error copy.
- **What was NOT verified:** no manual click-through in a running admin
  dashboard against a live/staging Supabase — verified via unit tests,
  `tsc`, `eslint`, and a production build only, per this repo's stated
  no-active-visual-regression-tooling gap (CLAUDE.md pre-merge gate #6);
  this is a visually-invisible, logic-only change so it was reasoned
  about, not screenshotted.
