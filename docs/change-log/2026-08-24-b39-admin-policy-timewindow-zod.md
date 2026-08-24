# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude Code session (vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | corporate |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B39 — step 6 (second admin-dashboard zod migration, following step 4's `allowance-dialog.tsx` and step 5's rider-app `profile-setup.tsx`) |

## 1. Issue / gap identified

`corporate-accounts/[id]/policy/page.tsx` (admin sets a company's ride
policy — active toggle, max fare cap, payment source, and per-day
booking time windows) validates its time-window rows with the same
predicate, `w.end <= w.start`, written by hand in two places: the
`TimeWindowRow` child component's `invalid` flag (drives the red border
and the inline "End must be after start" message) and the parent's
`hasInvalidWindow` (gates the Save button and blocks `handleSave`). This
is the exact duplicated-logic-can-drift pattern B39 names by example and
step 4 already fixed once on `allowance-dialog.tsx` — it recurs here on a
second, separate admin-dashboard corporate form.

## 2. Root cause

Ad hoc validation predates zod's adoption on this surface (step 4 was
the first). The child component and the parent each re-derive the same
boolean from the same two fields instead of sharing one function, so
nothing enforces that the row-level highlight and the save-guard agree —
they happen to today, but a future edit to one site would silently
diverge from the other.

## 3. Fix / remediation

New colocated `admin-dashboard/src/lib/policyTimeWindowSchema.ts`:

- `timeWindowSchema` — a `zod` object over `{day, start, end}` (day
  restricted to the same seven-literal union already used locally in the
  page) with a `superRefine` reproducing the exact `end <= start` check,
  attached to the `end` field, with the identical message "End must be
  after start".
- `isTimeWindowValid(window)` — boolean wrapper, replaces
  `TimeWindowRow`'s inline `const invalid = w.end <= w.start;`.
- `hasInvalidTimeWindow(windows)` — replaces the parent's inline
  `timeWindows.some((w) => w.end <= w.start)`.

Both call sites in `policy/page.tsx` now import these two helpers
instead of re-deriving the predicate. No dependency change — `zod` was
already a direct `admin-dashboard` dependency as of step 4.

The `maxFare` input's `min={1}` / `max={10000}` HTML attributes were
deliberately **not** touched or backed by a new JS check: `handleSave`
never validated them in JS before this change (only the browser's native
number-input behavior applies), so adding a zod bound on them would be a
new validation rule, not a pure extraction — out of scope for this pass
per B39's own "pure extraction, not a validation-rule change" guidance.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one page.** Grepped
  `admin-dashboard/src` for every importer of
  `policyTimeWindowSchema` — only `corporate-accounts/[id]/policy/page.tsx`
  and its own new test file. No other file reads the extracted helpers.
- A same-pattern duplicate of this exact `w.end <= w.start` check exists
  in `admin-dashboard/src/app/company-portal/[id]/policy/page.tsx`
  (`invalidWindow = draft.allowed_time_windows.find((w) => w.end <=
  w.start)`) — a separate, self-serve company-portal surface distinct
  from this internal admin review flow. It was **not** touched or
  imported from; it is a separate unmigrated instance, named here and in
  ACTION_ITEMS.md's "still open" list rather than silently left
  undocumented.
- No API contract, data schema, or background-job change. The `patch`
  object built and sent to `patchCompanyPolicy` in `handleSave` is
  unchanged in shape and contents.
- No change to `getCompanyPolicy`/`patchCompanyPolicy` or any backend
  route — this PR only changes what the admin's *input form* accepts
  before that call is made.

## 5. User-experience effect

None for the admin using this page: identical accept/reject behavior for
every time-window row, identical error message text and placement,
identical Save-button disabled state for the same inputs as before.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/policyTimeWindowSchema.ts` | New — zod schema + `isTimeWindowValid`/`hasInvalidTimeWindow` helpers | Colocated, testable validation |
| `admin-dashboard/src/lib/__tests__/policyTimeWindowSchema.test.ts` | New — 16 accept/reject cases | Close validation-coverage gap |
| `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/policy/page.tsx` | `TimeWindowRow`'s `invalid` and the parent's `hasInvalidWindow` use the shared helpers instead of inline predicates | Pure extraction, no behavior change |
| `ACTION_ITEMS.md` | B39 — recorded step 6 completion | Track migration progress |
| `docs/change-log/2026-08-24-b39-admin-policy-timewindow-zod.md` | New change-log | Required |

## 7. Before / after

```ts
// Before — TimeWindowRow
const invalid = w.end <= w.start;

// After
const invalid = !isTimeWindowValid(w);
```

```ts
// Before — parent component
const hasInvalidWindow = timeWindows.some((w) => w.end <= w.start);

// After
const hasInvalidWindow = hasInvalidTimeWindow(timeWindows);
```

## 8. Rollback plan

**`git-revert-safe`** — pure client-side extraction; no data/schema/config
touched, no migration involved.

## 9. Verification performed

- [x] 16/16 new `policyTimeWindowSchema.test.ts` accept/reject cases pass
      (every day literal, equal/reversed/one-minute-wide boundaries, the
      exact error message text, and the issue's field path)
- [x] Full admin-dashboard suite: 367/367 tests pass (37 files), 0
      regressions
- [x] `npx tsc --noEmit` clean
- [x] `npx eslint` clean on all touched files (one pre-existing,
      unrelated `react-hooks/set-state-in-effect` warning on an untouched
      line of the same page file, not introduced by this change)
- [x] **Real production build**: `npm run build` (`next build`) completed
      successfully, exit 0 — not just `tsc`/dev server
- [x] Blast-radius grep performed: `policyTimeWindowSchema` importers —
      only the migrated page and its test file
- [x] Reviewed against B39's own risk note: additive-only, one form, pure
      extraction — the two original inline predicates collapsed into two
      shared helpers but preserve identical message text and boolean
      semantics

## What was NOT verified

- End-to-end manual repro against real Supabase dev / a live corporate
  account (client-side validation only; not exercised against the actual
  `PATCH .../policy` endpoint's own server-side validation, which remains
  unchanged and authoritative).
- The `company-portal/[id]/policy/page.tsx` duplicate of this same check
  was identified but deliberately not migrated in this pass — it is a
  different surface (self-serve company portal, not internal
  admin-dashboard) and out of this task's scope; left for a future step.
- The `maxFare` field's numeric bounds (`min`/`max` on the `<Input>`)
  were not given JS-level validation since none existed before this
  change — reasoned about as out of scope rather than independently
  verified against backend behavior.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — grepped, isolated to one page
      (plus one explicitly-named, deliberately-untouched duplicate on a
      different surface)
- [x] No silent behavior change — same predicate, same error message,
      documented before/after per call site
