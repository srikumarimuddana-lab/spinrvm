# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code (background agent) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/breakup-service-areas-page` (see PR) |
| Related issue or gap ID | `/design Spinr Apps` design/UX audit finding — god-component `service-areas/page.tsx` (2,982 lines) |

## 1. Issue / gap identified

`admin-dashboard/src/app/dashboard/service-areas/page.tsx` was a 2,982-line god-component bundling eight distinct sub-features (service-area CRUD, vehicle pricing, fees & taxes, Spinr Pass subscription plans, documents, incentives, airport zones, dispatch cascade, plus two heatmap-tuning panels and a surge-history chart) in one file. The design audit flagged this as a driver of design drift, and pointed at `rides/_components/*` as the established decomposition pattern already used elsewhere in this codebase.

## 2. Root cause

The page grew incrementally, feature by feature, with each new tab's component defined inline in the same file rather than extracted — unlike `rides/page.tsx`, which already delegates to `rides/_components/*`. Nothing structural forced the file to be one module; it was simply never split.

## 3. Fix / remediation

**Pure code motion only — zero logic changes.** Extracted every sub-feature into its own file under a new `service-areas/_components/` directory, matching the naming/export conventions already used in `rides/_components/*` (kebab-case filenames, `"use client"` at the top, `export default function ComponentName` for the primary component of each file, plain named exports for small shared helpers). Twelve new files were created across twelve separate commits, each verified with `npx tsc --noEmit` before the next was started:

1. `service-area-shared.tsx` — cross-tab helpers: `CITY_PRESETS`, `regulatoryDefaultsForProvince`, `polygonToText`, `getAreaPolygon`, `getAreaCenter`, the lazy `GeofenceMap` import, and the small inline-editable field components `FieldInput`, `FieldTextarea`, `FieldToggle` (plus the pre-existing unused `_FieldSelect`, kept as dead code — see §"Noticed but not touched").
2. `vehicle-pricing-editor.tsx` — the Vehicle Pricing tab.
3. `documents-editor.tsx` — the Documents tab.
4. `cascade-editor.tsx` — the Dispatch Cascade tab.
5. `incentives-tab.tsx` — the Incentives tab (+ `INCENTIVE_TYPES`).
6. `surge-history-chart.tsx` — the per-area surge history chart shown at the bottom of the General tab.
7. `area-heatmap-overrides.tsx` — the per-area heatmap-tuning override panel (`AreaHeatmapOverrides`, the page's one previously-exported non-default symbol, directly imported by `area-heatmap-overrides.test.tsx`).
8. `heatmap-config-tab.tsx` — the global (platform-wide) heatmap settings tab (+ `HeatmapNumericField`, `HEATMAP_DEFAULTS`).
9. `general-tab-form.tsx` — the General tab (name/city, regulatory + safety panel, surge pricing, driver matching, geofence editor).
10. `area-fees-editor.tsx` — the Fees & Taxes tab (+ its private `FeeEditForm` helper).
11. `spinr-pass-area-tab.tsx` — the Spinr Pass tab (kill switch, mandatory-subscription toggle, plan CRUD, subscribers table, + `DURATION_OPTIONS`).
12. `airport-zones-tab.tsx` — the Airport Zones (sub-regions) tab, extracted as a props-driven leaf component; its state (`addAirportFor`, `airportForm`, `airportMapKey`) and handlers (`handleCreateAirportSubRegion`, `handleDelete`, `handleFieldUpdate`) stay owned by `ServiceAreasPage` exactly as before, just passed down as props instead of being inline JSX — this was the only tab not already broken into its own local function inside the original file.

`page.tsx` now contains only the page shell: state ownership, data loading, the create-service-area form, the area list/expand/tab-switch chrome, and the delete-confirmation dialog — 494 lines, down from 2,982 (an 83% reduction). Every one of the 8 tabs is now its own file, matching the `rides/_components/` pattern the audit pointed at.

Alongside each extraction, now-dead imports in `page.tsx` (whose only consumer moved to the new file) were removed in the same commit — icons, hooks, and API functions with zero remaining call sites. These are described per-commit below; none of them change runtime behavior (an unused import has no effect at runtime — it is simply dropped from the bundle by the bundler either way).

## 4. Risk & impact on existing functionality

**Blast-radius check (performed before any edit, confirmed via repo-wide grep):**
- `grep -rn "service-areas/page"` across the repo found exactly two non-comment code references:
  - `admin-dashboard/src/__tests__/dashboard/pages.smoke.test.tsx:590` — imports the page's **default export** only (`const { default: Page } = await import(...)`). Unaffected: `ServiceAreasPage` is still the default export of `page.tsx`.
  - `admin-dashboard/src/__tests__/dashboard/area-heatmap-overrides.test.tsx:73` — imported the **named** `AreaHeatmapOverrides` export directly from `page.tsx`. This one **did** need an update (see below).
- Four `lib/*.ts` files (`serviceAreaFormSchema.ts`, `spinrPassAreaPlanSchema.ts`, `taxJustificationSchema.ts`, `surgeJustificationSchema.ts`) reference `dashboard/service-areas/page.tsx`'s function names only inside doc comments explaining what each Zod schema validates — not imports. See "Noticed but not touched" below.
- No other file in the repo imports anything from `service-areas/page.tsx`, and no test imports any of the page's other internal (non-default, non-`AreaHeatmapOverrides`) helper functions — they were all unexported implementation details, now equally unexported implementation details of their new files.
- **Isolated to the `service-areas` route** — this refactor touches no shared component, hook, or utility used by other pages (the two shared pieces it produces, `service-area-shared.tsx` and the per-tab components, are new files consumed only from within `service-areas/_components/` and `service-areas/page.tsx` itself).

**Test-import update:** `area-heatmap-overrides.test.tsx`'s import was changed from `@/app/dashboard/service-areas/page` to `@/app/dashboard/service-areas/_components/area-heatmap-overrides` (the file `AreaHeatmapOverrides` actually moved to). Per CLAUDE.md's testing guidance, the import was updated rather than the test being skipped or deleted. All 15 of that test file's cases still pass, as does the `service-areas` entry in `pages.smoke.test.tsx`.

**State-ownership preserved exactly.** No state was lifted up or pushed down. Every piece of state that spans what look like separate tabs stayed owned by its original component and is passed down as a prop, unchanged:
- `areas`, `plans`, `vehicleTypes`, `areaFees`/`feesLoading` remain owned by `ServiceAreasPage` and are passed into `VehiclePricingEditor`, `AreaFeesEditor`, `SpinrPassAreaTab`, `CascadeEditor`, `IncentivesTab` exactly as before.
- `addAirportFor` / `airportForm` / `airportMapKey` remain owned by `ServiceAreasPage`, now passed as props into the new `AirportZonesTab` leaf component instead of being read directly from closure — same values, same setters, same handlers, just crossing a props boundary instead of a JSX-inline boundary.
- Every other extracted component (`GeneralTabForm`, `DocumentsEditor`, `SurgeHistoryChart`, `AreaHeatmapOverrides`, `HeatmapConfigTab`) already owned its own local state before this refactor (it was already a separate function in the same file) — moving the function to a new file changes nothing about where its `useState` calls live.

**Money/insurance/dispatch-adjacent code paths:** this page touches surge pricing (`GeneralTabForm`'s surge fields), fees/taxes (`AreaFeesEditor`), and dispatch cascade (`CascadeEditor`) — all of which write through the existing `updateServiceArea`/`createAreaFee`/etc. API calls, byte-for-byte unchanged. No request payload, no validation branch, no conditional, and no handler signature was altered — only which file the code physically lives in.

## 5. User-experience effect

None. This is a pure internal refactor of file organization; no JSX output, className, prop name, handler signature, `data-slot`/`aria-*`/`role` attribute, or conditional render branch was changed. An admin using this page mid-session (any tab open, any form half-filled) sees no difference before or after this change — and since this is a static-file/module reorganization with no runtime feature flag or state-migration step, there is no way for an in-flight session to observe a transition at all.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | Reduced from 2,982 → 494 lines; 8 tab components + the shared-helpers module extracted; now-dead imports removed per extraction | God-component breakup |
| `admin-dashboard/src/app/dashboard/service-areas/_components/service-area-shared.tsx` | New — geo/city-preset helpers, lazy `GeofenceMap`, shared field-input components | Cross-tab shared helpers |
| `admin-dashboard/src/app/dashboard/service-areas/_components/vehicle-pricing-editor.tsx` | New — Vehicle Pricing tab | Sub-feature extraction |
| `admin-dashboard/src/app/dashboard/service-areas/_components/documents-editor.tsx` | New — Documents tab | Sub-feature extraction |
| `admin-dashboard/src/app/dashboard/service-areas/_components/cascade-editor.tsx` | New — Dispatch Cascade tab | Sub-feature extraction |
| `admin-dashboard/src/app/dashboard/service-areas/_components/incentives-tab.tsx` | New — Incentives tab | Sub-feature extraction |
| `admin-dashboard/src/app/dashboard/service-areas/_components/surge-history-chart.tsx` | New — per-area surge history chart | Sub-feature extraction |
| `admin-dashboard/src/app/dashboard/service-areas/_components/area-heatmap-overrides.tsx` | New — per-area heatmap tuning overrides (`AreaHeatmapOverrides`) | Sub-feature extraction |
| `admin-dashboard/src/app/dashboard/service-areas/_components/heatmap-config-tab.tsx` | New — global heatmap settings tab | Sub-feature extraction |
| `admin-dashboard/src/app/dashboard/service-areas/_components/general-tab-form.tsx` | New — General tab | Sub-feature extraction |
| `admin-dashboard/src/app/dashboard/service-areas/_components/area-fees-editor.tsx` | New — Fees & Taxes tab | Sub-feature extraction |
| `admin-dashboard/src/app/dashboard/service-areas/_components/spinr-pass-area-tab.tsx` | New — Spinr Pass tab | Sub-feature extraction |
| `admin-dashboard/src/app/dashboard/service-areas/_components/airport-zones-tab.tsx` | New — Airport Zones tab (props-driven leaf) | Sub-feature extraction |
| `admin-dashboard/src/__tests__/dashboard/area-heatmap-overrides.test.tsx` | Import path updated to the new file location | `AreaHeatmapOverrides` moved out of `page.tsx` |

## 7. Before / after

Not applicable — every diff in this change is additive-move (code relocated verbatim into a new file, with an import added at the call site) or subtractive-only (dead imports whose sole consumer moved away). There is no behavior-changing diff to snippet: git-diffing any extracted function's body between its old and new location shows zero textual difference in the function itself, only its surrounding file (imports/exports).

## 8. Rollback plan

`git revert` of the PR's merge commit is sufficient and complete: this change touches no live data (no DB writes, no Stripe charges, no wallet deltas, no ride state, no migrations). Reverting restores `page.tsx` to its pre-refactor 2,982-line form and deletes the 12 new `_components/` files with no data-level cleanup required, unlike the money/ride-state changes this rule is primarily written to guard.

## 9. Verification performed

- [x] **`npx tsc --noEmit`** run after every single one of the 12 extraction commits (not just once at the end) — clean every time, no errors.
- [x] **Real production build**: `npm run build` (not just dev server / `tsc --noEmit`) — completed successfully (`✓ Compiled successfully in 49s`, TypeScript pass `✓ Finished TypeScript in 34.2s`, static page generation `✓ Generating static pages using 3 workers (78/78)`). The route list is unchanged and still includes `ƒ /dashboard/service-areas`. (The `BACKEND_URL env var is required in production` lines are a pre-existing warning from this sandbox lacking that env var at build time — unrelated to this change, and present for every route, not specific to service-areas.)
- [x] **Full test suite**: `npm run test` (vitest) run at the end — **all 59 test files, 562 tests, passed (exit code 0)**. `area-heatmap-overrides.test.tsx` (15 tests, the one file whose import needed updating) and the `service-areas` case in `pages.smoke.test.tsx` were additionally run in isolation after each relevant commit and passed every time.
- [x] **Blast-radius grep performed** (§4): repo-wide `grep -rn "service-areas/page"` and `grep -rn "AreaHeatmapOverrides"`, confirming exactly the two test-file consumers described above and no other importers.
- [x] Reviewed against CLAUDE.md conventions: task decomposition (≤3 files/commit, 12 separate commits), surgical changes (no unrelated reformatting), blast-radius-first, no silent behavior change.
- [x] Feature flag: not applicable — this is a non-user-visible, zero-behavior-change internal refactor; CLAUDE.md's flagging rule applies to user-visible/non-trivial changes, and there is nothing for a user to observe here.

## 10. What was NOT verified / visual regression disclosure

- **No manual click-through in a running dev server or staging deploy was performed** — verification relied on `tsc --noEmit` (after every commit), a full production `npm run build`, and the existing automated test suite (component tests + page-smoke test), not on visually operating the page. The `npm run build` success and the passing `pages.smoke.test.tsx`/`area-heatmap-overrides.test.tsx` cases are the evidence that the page renders without throwing, not that every pixel looks unchanged.
- **Visual regression coverage: none exists for this page, stated explicitly per CLAUDE.md gate #6.** admin-dashboard's Playwright visual-regression job (`e2e/visual-regression.spec.ts`) is real, CI-wired, and — as of this writing — merge-blocking for its 6 seeded baselines (`login`, `dashboard-home`, `dashboard-drivers`, `dashboard-monitoring`, `dashboard-settings`, `dashboard-rides`). `dashboard-service-areas` is **not** one of the 6 seeded pages, so this change has **zero automated visual coverage either way** — this is not a change in coverage caused by this PR, the page has simply never had a baseline. This was reasoned about (identical JSX, identical `className`s, identical conditional render branches — verified by diffing each extracted function's body against its original inline text) rather than screenshotted.
- **Not tested against a live Supabase-backed dashboard session** — the test suite mocks `@/lib/api`; no request was fired against a real backend during this work.
- **`_FieldSelect` (moved into `service-area-shared.tsx`) was pre-existing dead code, not created or removed by this change** — it was unreferenced in the original 2,982-line file (no call sites at all, hence the leading underscore convention marking it unused) and remains unreferenced after being moved. Left in place per CLAUDE.md's "notice unrelated dead code → mention it, don't delete it (unless asked)."
- **Doc-comment staleness in unrelated files, noticed but not touched**: four schema files under `admin-dashboard/src/lib/` (`serviceAreaFormSchema.ts`, `spinrPassAreaPlanSchema.ts`, `taxJustificationSchema.ts`, `surgeJustificationSchema.ts`) have doc comments saying their validators are consumed by "`dashboard/service-areas/page.tsx`'s `<FunctionName>`". The function names are still accurate (nothing was renamed), but for `GeneralTabForm` and `SpinrPassAreaTab` specifically, the file path in the comment is now stale — those two functions moved to `general-tab-form.tsx` and `spinr-pass-area-tab.tsx` respectively, not `page.tsx`. These are comments only (no import, no behavior), fall outside every literal boundary of this task (they live in `admin-dashboard/src/lib/`, not the page or its new `_components/`), and CLAUDE.md's surgical-changes rule says not to touch adjacent files for something else — so they were left as-is. Flagging here rather than silently leaving it for someone to notice later.
