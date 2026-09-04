# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code (background agent), on behalf of vikas@ngitservices.com |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | see PR description |
| Related issue or gap ID | `/design Spinr Apps` audit finding: "Break up the god-components (`drivers/page.tsx` 3,786 lines...)" |

## 1. Issue / gap identified

`admin-dashboard/src/app/dashboard/drivers/page.tsx` was a 3,786-line single
file mixing all of DriversPage's state/data-fetching, the drivers list
table + filter toolbar, the driver-detail slideout (7+ tabs), and ~15
already-standalone presentational sub-components — all in one file. The
design audit flagged this as the reason design drift happens on this page:
finding and reusing an existing token is harder in a file this size than
adding "just one more" hardcoded color.

## 2. Root cause

The file grew incrementally over many features (payouts, referrals,
training/LMS, rides history, documents, verification, subscriptions, bulk
Stripe-sync tools) without ever being decomposed the way
`rides/_components/*` already demonstrates elsewhere in this same codebase
(rides/page.tsx is 312 lines; RideList, RideDetailModal, etc. are separate
files). Nothing forced the split, so it never happened.

## 3. Fix / remediation

**Pure code motion only — zero behavior change.** Extracted 7 new files
under `admin-dashboard/src/app/dashboard/drivers/_components/`. No prop was
renamed, no handler signature changed, no conditional branch was altered, no
className/`data-slot`/`aria-*`/`role` attribute was touched, and no state
was lifted or pushed between components. `page.tsx` shrank from 3,786 to
1,852 lines (51%).

Two extraction tiers, in order of risk:

**Tier 1 (near-zero risk)** — these were already self-contained, top-level,
prop-driven functions living below the `DriversPage` component in the same
file (not closures over its state — a top-level JS function structurally
cannot close over another function's locals). Moving them out and adding
`export` + an import is equivalent to what already happens at module-eval
time; there is no way for this class of move to change runtime behavior
short of a wiring mistake, and `npx tsc --noEmit` after each step is a
strong check for exactly that (see §9).

1. `driver-detail-shared.tsx` — `driverDisplayName`, `QuickStat`,
   `DetailSection`, `DetailField`, `CopyableField`, `EditField`,
   `EditBooleanField`, and the work-authorization helpers
   (`WORK_AUTH_LABELS`, `WorkAuthView`, `workAuthLocal`, `workAuth`,
   `WORK_AUTH_FLAG_LABELS`).
2. `driver-documents-helpers.tsx` — `matchesRequirement`,
   `VerificationSummaryCard`, `DocSummary`, `DocExpirySummaryCard`,
   `DocCard`.
3. `driver-payouts-tab.tsx` — `PAYOUT_STATUS_STYLE`,
   `PAYOUT_STATUS_BADGE_VARIANT`, `PayoutMetric`, `DriverPayoutsTab`.
4. `driver-referrals-tab.tsx` — `DriverReferralsTab`.
5. `driver-training-tab.tsx` — `TRAINING_STATUS_STYLES`,
   `TRAINING_STATUS_BADGE_VARIANT`, `DriverTrainingTab`.
6. `driver-rides-tab.tsx` — `RIDE_STATUS_STYLE`, `RIDE_STATUS_BADGE_VARIANT`,
   `DRIVER_RIDES_PAGE_SIZE_OPTIONS`, `RidesSortKey`, `DriverRidesTab`.

**Tier 2 (higher risk, one component, extra care)**

7. `driver-list-table.tsx` — the PageHeader filter/bulk-action toolbar,
   status tabs, search box, the drivers table (header, loading skeleton,
   rows, empty state), pagination, `AreaStatsTable`, and `DriverCharts`.
   This mirrors the `rides/_components/ride-list.tsx` pattern already
   established in this codebase: a controlled/presentational component fed
   by props, with `DriversPage` still owning every piece of state (search,
   filters, sort, `selected`, bulk-op flags, etc.). ~45 props were threaded
   through, each keeping its **original variable name** (no renames) to
   minimize any chance of a mismatched wiring at the call site. The
   `STATUS_TABS` constant moved with it (its only consumer).

**One deliberate non-"pure-motion" cleanup, called out explicitly:** after
moving the table JSX out, `page.tsx`'s own local `SortIcon` component and
its usage inside the table header became dead code (unreachable — its only
render call sites moved to `driver-list-table.tsx`, which re-declares its
own `SortIcon` from the props it now receives). That orphaned definition
was removed, along with orphaned imports its removal and the other
extractions left behind (`ZoomIn`, `Award`, `GraduationCap`,
`DriverStatementsPanel`, `Users`, `Wifi`, `Download`, `Eye`, `EyeOff`,
`RefreshCw`, `Tag`, `UserX`, `Globe`, `PageHeader`, `TableHead`,
`ArrowUpDown`/`ArrowUp`/`ArrowDown`, `DriverStatsCards`, `DriverCharts`,
`AreaStatsTable`, `logPiiReveal`) — dead-code/orphaned-import removal only,
zero behavior change, and directly caused by this refactor's own motion
(not pre-existing dead code left alone per CLAUDE.md's surgical-changes
rule).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped the whole repo for any import of
  `dashboard/drivers/page` — two hits, both harmless:
  `src/__tests__/dashboard/pages.smoke.test.tsx` (dynamically imports the
  page's **default export** only — unaffected by internal decomposition;
  it passed in the full test run, see §9) and a doc-comment in
  `src/lib/driverPhotoUploadSchema.ts` referencing the file by name (not a
  code import). Next.js page files are not otherwise importable/imported by
  convention, and that held here. No other file in the repo imports
  anything from the old monolithic `page.tsx`, so nothing outside this PR's
  own files needed updating.
- **State ownership unchanged.** Every `useState`/`useEffect`/`useCallback`
  in `DriversPage` stayed exactly where it was. The 7 new files are either
  (a) already-standalone components that only ever received props, or (b)
  one presentational component (`driver-list-table.tsx`) that receives
  more props than before but creates no new state and lifts/pushes none.
- **New internal cross-file dependency surface:** `page.tsx` now imports
  from all 7 new files, and `driver-list-table.tsx` imports
  `driverDisplayName` from `driver-detail-shared.tsx`. This is new
  *module* coupling, not new *runtime behavior* coupling — same call
  graph, different file boundaries.
- **PR #4945 interaction — merged mid-flight, now resolved.** This branch
  was created from `main` before #4945 (`claude/extract-keyboard-row-shared-component`,
  "extract keyboard-accessible ClickableTableRow") had merged, so the
  original 7 extraction commits preserved the pre-#4945 shape (a plain
  `<TableRow>` + inline `tabIndex`/`onClick`/`onKeyDown`/`aria-label`),
  verified byte-for-byte via grep counts against the pre-extraction
  original (11 `onKeyDown` handlers: 10 sortable column headers + 1 row; 10
  `role="columnheader"`; 10 `aria-sort`). **#4945 merged into `main` while
  this PR was in progress** (merge commit `592463cbf`, 2026-09-04). This
  branch was rebased onto the updated `main`; the resulting conflict was
  exactly where predicted — the driver row inside the newly-extracted
  `driver-list-table.tsx` — and was resolved by applying the identical
  `ClickableTableRow` swap `page.tsx` itself received in #4945: `<TableRow
  ... onClick={...} tabIndex={0} aria-label={...} onKeyDown={...}>` became
  `<ClickableTableRow ... onActivate={...} ariaLabel={...}>` (tabIndex,
  Enter/Space handling, and the focus ring now come from
  `ClickableTableRow` itself). Re-verified post-rebase: `tsc --noEmit`
  clean, `npm run build` clean, `npm run test` 562/562 passing, and the 10
  sortable-column-header `onKeyDown` handlers + 10 `role="columnheader"` +
  10 `aria-sort` attributes are still intact (the row's own `onKeyDown` is
  no longer literal source text — it's inside `ClickableTableRow` now,
  same as every other page #4945 touched).

## 5. User-experience effect

None intended, and none should be observable. This is an internal-admin
file reorganization with identical JSX, identical conditional rendering,
identical CSS classes, and identical event handlers — the rendered DOM for
`/dashboard/drivers` should be pixel-identical before and after. Not
visible mid-session to anyone (admins don't have this page open across a
deploy in a way that matters here — it's a fresh page load either way).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/page.tsx` | Shrank 3,786 → 1,852 lines. 7 blocks cut out verbatim and replaced with imports + (for the list-table) a single `<DriverListTable .../>` call with ~45 props. Orphaned `SortIcon`/imports removed (see §3). | God-component breakup |
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-detail-shared.tsx` | New. `driverDisplayName`, `QuickStat`, `DetailSection`, `DetailField`, `CopyableField`, `EditField`, `EditBooleanField`, work-authorization helpers. | Extraction #1 |
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-documents-helpers.tsx` | New. `matchesRequirement`, `VerificationSummaryCard`, `DocSummary`, `DocExpirySummaryCard`, `DocCard`. | Extraction #2 |
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-payouts-tab.tsx` | New. Payout status maps, `PayoutMetric`, `DriverPayoutsTab`. | Extraction #3 |
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-referrals-tab.tsx` | New. `DriverReferralsTab`. | Extraction #4 |
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-training-tab.tsx` | New. Training status maps, `DriverTrainingTab`. | Extraction #5 |
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-rides-tab.tsx` | New. Ride status maps, page-size options, `RidesSortKey`, `DriverRidesTab`. | Extraction #6 |
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-list-table.tsx` | New. Filter/bulk-action toolbar, status tabs, search, table, pagination, `AreaStatsTable`, `DriverCharts`. | Extraction #7 |

## 7. Before / after

Pure additive/relocation diffs throughout — no behavior-changing logic diff
to show. The one representative "before/after" worth showing is the
call-site swap in `page.tsx`, where ~275 lines of inline JSX became one
component call with matching props (values unchanged, only where they live
changed):

```
# Before (page.tsx, abbreviated)
return (
    <div className="space-y-5">
        <PageHeader ... /* ~275 lines of toolbar + table JSX */ />
        ...
        <DriverCharts charts={data?.charts || null} loading={loading} />
        {/* Driver Detail Slideout */}
        <Sheet ...>
```

```
# After (page.tsx)
return (
    <div className="space-y-5">
        <DriverListTable
            data={data} loading={loading} serviceAreaId={serviceAreaId}
            /* …~45 props, each the same variable, same name… */
            pageSize={PAGE_SIZE}
        />
        {/* Driver Detail Slideout */}
        <Sheet ...>
```

## 8. Rollback plan

`git revert` is a complete rollback here — this is a pure, additive
frontend refactor with no migration, no data write, no feature flag, and
no live-data side effect. Reverting the merge commit (or the 7 individual
commits in reverse order) restores the exact original `page.tsx` with no
follow-up action needed.

## 9. Verification performed

- [x] `npx tsc --noEmit` run and clean after **every single commit** (7
      times), not just at the end — each extraction was verified
      independently before the next one started, per CLAUDE.md task
      decomposition.
- [x] Unused-import check (custom script, cross-checked against `tsc`)
      after every commit — caught and removed every orphaned import
      (`ZoomIn`, `Award`, `GraduationCap`, `DriverStatementsPanel`, and the
      larger list from the final extraction).
- [x] **Real production build**: `npm run build` — succeeded, exit code 0,
      no errors, `/dashboard/drivers` present in the emitted route list.
      This is the actual `next build`, not `tsc --noEmit` alone or a dev
      server.
- [x] Full test suite: `npm run test` (vitest, the real configured runner
      per `package.json`'s `"test"` script — not `npx jest`, which this
      repo's CLAUDE.md notes fails with unrelated ESM config errors) — **59
      test files, 562 tests, all passed**, including the drivers-page
      smoke test that dynamically imports `@/app/dashboard/drivers/page`.
- [x] Blast-radius grep performed repo-wide for any import of
      `dashboard/drivers/page` (see §4) — isolated, two harmless hits.
- [x] Reviewed against `CLAUDE.md`'s admin-dashboard visual-regression
      convention — see §"What was NOT verified" below; this is the one
      real gap in this verification.
- [x] Accessibility attributes spot-checked post-extraction, **twice**:
      once against the pre-#4945 original (11 `onKeyDown`, 10
      `role="columnheader"`, 10 `aria-sort`, plain `<TableRow>` +
      `tabIndex`/`onKeyDown`/`aria-label`), and again after rebasing onto
      `main` post-#4945-merge and applying the matching `ClickableTableRow`
      swap (10 `onKeyDown` — the 10 sortable headers only, row activation
      now inside `ClickableTableRow` — 10 `role="columnheader"`, 10
      `aria-sort`, `<ClickableTableRow onActivate=... ariaLabel=...>`). See
      §4 for the full rebase/conflict-resolution account.
- [ ] Feature-flagged: **N/A**. This is not a behavior change — there is
      nothing to flag. (CLAUDE.md's flagging gate exists for user-visible
      or behavior-changing work; this PR is neither by design.)

### What was NOT verified

- **The real Playwright `dashboard-drivers` visual-regression test could
  not be run in this sandbox.** `dashboard-drivers` is one of admin-dashboard's
  6 seeded baselines in `e2e/visual-regression.spec.ts`, and per this
  repo's current `CLAUDE.md`, that check is **now merge-blocking** (as of
  the 2026-09-04 B38 closure — `continue-on-error` is off for all 6 pages,
  `dashboard-drivers` included). I attempted to run it for real:
  `npx playwright install chromium --with-deps` failed in this sandbox
  because the environment's network proxy blocks `cdn.playwright.dev`
  ("request blocked: no rule or allowlist entry allows host") — the
  Chromium binary itself cannot be downloaded here, so the spec cannot
  execute at all, not even against mocked data (the mocking itself
  — `setupAdminMocks` + route interception for `/api/**` — would have made
  this runnable if the browser were available, since it needs no real
  backend/Supabase connection). **This means this PR's actual CI run is
  the first real verification the pixel output is unchanged** — I am
  reasoning, not screenshotting: identical JSX moved verbatim, identical
  conditional branches, identical `className`s, so the rendered DOM should
  be pixel-identical, but that is an inference from the diff, not a
  captured comparison. If the CI visual-regression job flags a diff on
  `dashboard-drivers`, treat that as a real signal to investigate (most
  likely a transcription slip somewhere in the ~275-line JSX block moved
  into `driver-list-table.tsx`), not as noise to wave off.
- **The driver-detail slideout (Sheet: header + Tabs for
  overview/documents/verification/notes/distance/history/subscriptions,
  ~875 lines) was deliberately left inline in `page.tsx` and NOT
  extracted further**, even though it is the single largest remaining
  block and the audit's own suggested boundary ("a driver-detail side
  panel"). Reasoning: unlike the Tier 1 pieces, this block is not already
  self-contained — extracting it means threading ~70+ free variables as
  props across a single ~900-line JSX tree, and a large fraction of this
  file's state (`selected: any`, `driverDocs: any[]`, `editForm:
  Record<string, any>`, etc.) is typed `any`, which materially weakens
  `tsc`'s ability to catch a prop-mixup mistake (two same-shaped `any`
  props swapped at a call site type-checks cleanly and only fails at
  runtime). Combined with having no way to exercise the real, now
  merge-blocking visual-regression baseline in this sandbox (see above), I
  judged that extracting this block carried a real, uncaught-by-tooling
  risk of a subtle behavior change that I could not fully rule out with
  the verification available to me — the CLAUDE.md rule 9 case for
  "escalate / stop rather than force it." The 7 extractions in this PR are
  a complete, verified, and honestly-scoped subset; decomposing the detail
  slideout is a reasonable, valuable follow-up that would benefit from a
  human's design-judgment call on sub-boundaries (which tab bodies group
  together, whether the Sheet header is its own file, etc.) and from being
  checked against a real, rendered browser rather than reasoned about.
- **No visual diff was captured for any of the 7 new files individually**
  beyond what the (unrunnable-here) full-page baseline would show — there
  is no component-level snapshot tooling in this repo for
  admin-dashboard, only the 6 full-page baselines above.
- Not tested against a live/staged Supabase-backed admin session — all
  verification here is static (tsc/build) and mocked (vitest unit tests +
  the one smoke test that renders the page's default export against
  mocked API responses).
