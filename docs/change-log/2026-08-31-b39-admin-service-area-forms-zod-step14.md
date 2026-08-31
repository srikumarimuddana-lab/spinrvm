# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code session |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B39 — step 14 (the two candidates named at the end of step 13) |

## 1. Issue / gap identified

`service-areas/page.tsx`'s `handleCreate` gates creating a new top-level
service area with `if (!createForm.name) return;` (silent no-op).
`handleCreateAirportSubRegion` gates creating a new airport sub-region
with `if (!airportForm.name || airportForm.polygon.length < 3) { ... }`
(a "Missing airport boundary" toast). Neither had dedicated test
coverage.

## 2. Root cause

Ad hoc validation predates any schema-validation library adoption on this
form; `admin-dashboard` already has `zod` (added in B39 step 4), but
these checks were never migrated.

## 3. Fix / remediation

New colocated `admin-dashboard/src/lib/serviceAreaFormSchema.ts` extracts
both checks as byte-for-byte equivalent pure predicates:
`isServiceAreaNameValid` (mirrors `!createForm.name`) and
`isAirportZoneValid` (mirrors `!airportForm.name ||
airportForm.polygon.length < 3`, with `MIN_AIRPORT_ZONE_POLYGON_POINTS =
3` hoisted out as a named constant).

New `admin-dashboard/src/lib/__tests__/serviceAreaFormSchema.test.ts` (7
accept/reject cases, including the exact 3-point polygon boundary).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to two functions in one file.** Grepped
  `admin-dashboard/src` for `createForm.name`, `airportForm.name`, and
  `airportForm.polygon`; all appear only inside `ServiceAreasPage`'s own
  `handleCreate`/`handleCreateAirportSubRegion` and their form JSX (plus
  the new schema/test files). No other screen reads or duplicates this
  logic.
- **Could this regress a flow that currently works?** No — this is a
  pure extraction, not a validation-rule change. `isServiceAreaNameValid`
  reproduces `!createForm.name` exactly; `isAirportZoneValid` reproduces
  the original `||`-combined guard exactly, including the `< 3` boundary
  (a polygon of exactly 3 points passes, matching the original). Verified
  against 7 accept/reject cases before applying.
- **Dispatch-area creation interaction:** both handlers call
  `createServiceArea`, which creates a new geofenced dispatch area (or an
  airport sub-region within one) that dispatch, fare, and surge logic all
  key off of. This change touches only the client-side pre-submit gate —
  not the request shape, the backend endpoint, or dispatch/fare/surge
  logic itself. A rejected-client-side name or under-sized polygon still
  cannot reach the network call, exactly as before.
- **Background loops / ride state machine:** not implicated — this is
  admin-only service-area management, no interaction with dispatch's
  runtime matching loop or the ride state machine (only the static area
  definitions dispatch reads).

## 5. User-experience effect

Internal-admin-facing only (not visible to riders, drivers, or corporate
admins — this is the internal Spinr admin dashboard's service-areas
page). No behavior change: `handleCreate` still silently no-ops on an
empty name (unchanged — no error message before or after);
`handleCreateAirportSubRegion` still shows the same "Missing airport
boundary" toast under the same condition. Not visible mid-session to
anyone since this is an admin configuration action, not a live-tested
rider/driver flow.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/serviceAreaFormSchema.ts` | New file — two extracted predicates (`isServiceAreaNameValid`, `isAirportZoneValid`) + hoisted `MIN_AIRPORT_ZONE_POLYGON_POINTS` constant | Pulls `handleCreate`/`handleCreateAirportSubRegion`'s inline checks into a colocated, independently testable module |
| `admin-dashboard/src/lib/__tests__/serviceAreaFormSchema.test.ts` | New file — 7 accept/reject unit tests | Pins the extracted behavior, including the exact polygon-point boundary, so a future edit can't silently loosen/tighten either gate |
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | Both inline checks replaced with calls to the new predicates | Same behavior, now backed by tested, colocated logic instead of ad hoc inline checks |

## 7. Before / after

```ts
// Before
const handleCreate = async () => {
    if (!createForm.name) return;
    // ...
};

const handleCreateAirportSubRegion = async (parentId: string) => {
    const parent = areas.find(a => a.id === parentId);
    if (!airportForm.name || airportForm.polygon.length < 3) {
        crudToast.warn("Missing airport boundary", "Please enter a name and draw the airport boundary on the map.");
        return;
    }
    // ...
};
```

```ts
// After
import { isServiceAreaNameValid, isAirportZoneValid } from "@/lib/serviceAreaFormSchema";

const handleCreate = async () => {
    if (!isServiceAreaNameValid(createForm.name)) return;
    // ...
};

const handleCreateAirportSubRegion = async (parentId: string) => {
    const parent = areas.find(a => a.id === parentId);
    if (!isAirportZoneValid(airportForm.name, airportForm.polygon.length)) {
        crudToast.warn("Missing airport boundary", "Please enter a name and draw the airport boundary on the map.");
        return;
    }
    // ...
};
```

## 8. Rollback plan

`git-revert-safe`. No data migration, no feature flag, no schema/table
change — this is a client-side validation-logic extraction with identical
behavior. Reverting restores the inline checks exactly as they were, with
no follow-up action needed.

## 9. Verification performed

- [x] Automated tests run — unit only: `npm run test:coverage` (the exact
  CI invocation) — 432/432 tests, 44/44 files pass, exit 0 (coverage
  threshold gate unaffected — same pattern as every prior B39 step; the
  jump from step 13's 423/43 baseline reflects other work merged to
  `main` in between, not this change). New file alone:
  `npx vitest run src/lib/__tests__/serviceAreaFormSchema.test.ts` — 7/7
  pass.
- [ ] Manual repro steps followed in staging — not done; no staging
  access from this session. Verified instead against the 7 unit tests.
- [x] Blast-radius grep performed — searched `admin-dashboard/src` for
  every field name used by these checks; confirmed isolated to this
  page's own handlers and form JSX.
- [x] Reviewed against relevant CLAUDE.md convention(s) — this is a
  client-side pre-submit validation extraction only, not a change to
  `createServiceArea`'s request shape or dispatch/fare/surge logic that
  reads service areas.
- [ ] N/A — Feature-flagged if user-visible and non-trivial: this is a
  pure extraction with identical behavior on an internal-admin-only
  screen, not new or changed UX, per B39's own established pattern for
  every prior step.

`npx tsc --noEmit`: clean (after an `npm install` — see Environment note
below; `git status` confirmed no `package.json`/lockfile drift from the
install). `npx eslint` on the three touched files: 0 errors, 65
pre-existing warnings (this is the same large multi-tab file as steps
10/12/13) — confirmed via `git diff` that none fall on the 3 lines this
diff touches (the import line and the two guard swaps). **Real production
build** (`npm run build`) completed successfully, exit code 0, full route
manifest generated including `/dashboard/service-areas` — not just
`tsc`/dev server, per CLAUDE.md's explicit requirement for
admin-dashboard changes.

**Environment note:** `main` had picked up an unrelated Storybook
addition (PR #4724) between step 13 and this step. Its `node_modules`
wasn't installed in this session, so `tsc --noEmit` initially failed on
3 pre-existing `.stories.tsx` files (`Cannot find module
'@storybook/nextjs-vite'`) and `eslint` failed outright
(`eslint-plugin-storybook` not found) — both entirely unrelated to this
diff (confirmed via `git status` showing no local changes to those
files, and via `git log` showing PR #4724 as their origin). A plain
`npm install` resolved both, with `git status` confirming zero
`package.json`/`package-lock.json` drift from the install (a clean
install matching the existing lockfile, not a dependency change of its
own). Documented here per CLAUDE.md's "no silently softened error"
guidance, even though it wasn't a code issue.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (grepped, isolated to two
  functions in one file)
- [x] No silent behavior change to an already-shipped flow (Section 5 —
  same checks, same messages/no-message; internal-admin-only, not a live
  rider/driver surface)

## What was NOT verified

- Not tested against a real backend — `createServiceArea`'s actual
  network calls were not exercised end-to-end in this session; this
  change only touches the client-side gates in front of them, which are
  unchanged in shape.
- No visual regression tooling exists for admin-dashboard's actual
  baselines (per CLAUDE.md, admin-dashboard's visual-regression job has
  zero committed baselines — B38) — not applicable here regardless, since
  this change has no visual/UI surface change.
- The rest of `service-areas/page.tsx` (fees, incentives, the heatmap
  numeric-field clamps, and the Spinr Pass area toggles beyond the plan
  form migrated in step 13) remains unswept for other inline validation
  rules beyond what steps 10-14 have already found and migrated.
- The duplicate-Spinr-Pass-plan-form finding from step 13 (two
  independent implementations of the same kind of form) is a design
  question flagged for a future decision, not resolved by this
  extraction-only step.
