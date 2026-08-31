# Change Impact & Risk Log — B39 step 28: admin-dashboard ride-complaint/flag forms — silent no-op fix

- **Issue/gap identified:** `ride-complaint-form.tsx`'s and
  `ride-flag-form.tsx`'s `handleSubmit` functions guarded missing
  required fields with a bare `if (!x) return;` — a silent no-op with no
  toast, no error message, nothing visible to the admin. Every other
  error path on both forms (API failures) already uses the `toast(...)`
  pattern; only the client-side requiredness check was silent. These are
  the two "silent no-op UX gaps" named in the 2026-08-31 broader B39
  sweep (`ACTION_ITEMS.md` B39) — explicitly called out as *not even
  having the field-level message* several other candidates in the same
  sweep had before their own fixes.
- **Root cause:** no schema-validation library was adopted on this
  surface; the requirement was enforced only via each Submit/Flag
  button's `disabled` prop, with no fallback message for the handler
  itself if it's ever reached with an invalid state (a race between a
  state update and the click, a form submitted via Enter in a text
  field, or any future code path that calls `handleSubmit` directly).
- **Fix/remediation:** unlike every other step in this PR's B39 series,
  this is **not a pure extraction** — it adds a small, deliberate
  behavior change (a previously-silent path now shows a toast).
  Extracted the checks into new `admin-dashboard/src/lib/rideComplaintFormSchema.ts`
  (`isRideComplaintFormValid` + `getRideComplaintFormError`) and
  `admin-dashboard/src/lib/rideFlagFormSchema.ts`
  (`isRideFlagFormValid` + `getRideFlagFormError`), each returning the
  same `{ title, description }` shape the rest of that form's `toast(...)`
  calls already use. Both `handleSubmit` functions now call
  `toast({ ...formError, variant: "destructive" })` instead of silently
  returning.
- **Risk & impact on existing functionality:** both new files are used
  only by their one respective form. Blast-radius grep for the new error
  copy found no other file using it. Blast-radius grep for the two form
  components' importers found exactly one: `ride-detail-modal.tsx` —
  which only renders them as dialogs and passes no additional logic
  around `handleSubmit`; nothing there depends on the old silent-return
  behavior. In ordinary use this path is already unreachable — the
  Submit/Flag button's `disabled` prop blocks the click before
  `handleSubmit` runs — so the fix only becomes visible on the abnormal
  paths described above, which previously failed with zero feedback.
- **User experience effect:** **admin-facing, visible change** — an
  admin who somehow triggers `handleSubmit` with a missing required
  field (previously impossible to notice: the dialog just sat there)
  now sees a destructive toast naming what's missing, consistent with
  every other error path already on these two forms. This is the one
  genuinely-new behavior in an otherwise pure-extraction PR series, so
  it's called out explicitly per CLAUDE.md's "no silent behavior change"
  release gate.
- **Files modified:**

  | File | What changed | Why |
  |---|---|---|
  | `admin-dashboard/src/lib/rideComplaintFormSchema.ts` | New file: predicate + `getRideComplaintFormError` | Extract + surface missing-field error |
  | `admin-dashboard/src/lib/rideFlagFormSchema.ts` | New file: predicate + `getRideFlagFormError` | Extract + surface missing-field error |
  | `admin-dashboard/src/lib/__tests__/rideComplaintFormSchema.test.ts` | New file: 4 accept/reject tests | Close the coverage gap this item names |
  | `admin-dashboard/src/lib/__tests__/rideFlagFormSchema.test.ts` | New file: 2 accept/reject tests | Close the coverage gap this item names |
  | `admin-dashboard/src/app/dashboard/rides/_components/ride-complaint-form.tsx` | Silent `if (!category \|\| !description) return;` replaced with a toast-producing check; added import | Fix the silent no-op |
  | `admin-dashboard/src/app/dashboard/rides/_components/ride-flag-form.tsx` | Silent `if (!reason) return;` replaced with a toast-producing check; added import | Fix the silent no-op |

- **Before/after snippet:**

  ```tsx
  // before (ride-complaint-form.tsx)
  const handleSubmit = async () => {
      if (!category || !description) return;
      setLoading(true);
      ...

  // after
  const handleSubmit = async () => {
      const formError = getRideComplaintFormError(category, description);
      if (formError) {
          toast({ ...formError, variant: "destructive" });
          return;
      }
      setLoading(true);
      ...
  ```

- **Rollback plan:** revert the commit — no data migration, no
  feature flag, no live-data mutation involved; this is a client-side
  change with no server-side effect either way (both before and after,
  an invalid submit never reaches the API).
- **Verification performed:** 6/6 new tests pass
  (`npx vitest run src/lib/__tests__/rideComplaintFormSchema.test.ts
  src/lib/__tests__/rideFlagFormSchema.test.ts`); full admin-dashboard
  suite (`npx vitest run`) 57/57 suites, 555/555 tests passing, 0
  regressions; `npx tsc --noEmit` clean (repo-wide); `npx eslint` on
  touched files: 0 errors, 5 pre-existing `jsx-a11y/label-has-associated-
  control` warnings on unrelated lines in both files, unchanged by this
  diff; **real production build** (`npm run build`) completed
  successfully. Blast-radius grep confirmed both new error strings are
  unique to their new files, and both form components have exactly one
  importer (`ride-detail-modal.tsx`), unaffected by this change.
- **What was NOT verified:** no manual click-through in a running admin
  dashboard against a live/staging Supabase to trigger the actual
  abnormal path (bypassing the `disabled` button) — verified via unit
  tests, `tsc`, `eslint`, and a production build only, per this repo's
  stated no-active-visual-regression-tooling gap (CLAUDE.md pre-merge
  gate #6); the new toast copy was reasoned about and matched against
  the existing toast pattern on both forms, not screenshotted.

This closes the last two admin-dashboard candidates from the
2026-08-31 broader B39 sweep. Every admin-dashboard item from that
sweep is now done; the 6 remaining candidates are all driver-app.
