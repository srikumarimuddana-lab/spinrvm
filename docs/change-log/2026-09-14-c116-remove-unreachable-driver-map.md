# Change Impact & Risk Log — C116: remove unreachable `driver-map.tsx`

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code (session) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | https://github.com/srikumarimuddana-lab/spinrvm/pull/5424 |
| Related issue or gap ID | ACTION_ITEMS.md C116 |

## 1. Issue / gap identified

`admin-dashboard/src/components/driver-map.tsx` (`DriverMap`) has zero importers anywhere in
the codebase — it is not reachable from any route, page, layout, or other component. It has
absorbed two consecutive same-day maintenance fixes this week while orphaned (a WebGL-stub
guard and an unrelated tile-server routing change), which is real engineering effort landing
on dead code.

## 2. Root cause

The component's only real consumer — a `lazy(() => import("@/components/driver-map"))` inside
`admin-dashboard/src/app/dashboard/drivers/page.tsx` — was removed on 2026-04-01 (commit
`0342ae50b`, "chore: push pending changes"), and no other route was ever wired to replace it.
A sprint-completion doc (`docs/audit/08_SPRINT_5_COMPLETION.md`) describes a
`dashboard/fleet/page.tsx` that dynamically imported `DriverMap`; no such file, and no "Fleet
Map" sidebar entry, exists anywhere in git history under that path — the doc appears to
describe a plan that either never fully landed or landed and was fully reverted without a
record. Since then the file has sat unreferenced while the codebase's map-related maintenance
work (color-token passes, WebGL-stub guards, tile-provider routing) swept over it by pattern
("touch every admin map file") rather than by reachability.

## 3. Fix / remediation

Per product-owner decision (not re-litigated here — wiring it into a real route is a
feature-design task, not a bug-fix-shaped one), the orphaned component and its test file are
deleted:

- `admin-dashboard/src/components/driver-map.tsx` — deleted
- `admin-dashboard/src/components/driver-map.render.test.tsx` — deleted (test file added
  2026-09-14 alongside the WebGL-stub-guard fix; its own docstring already noted the component
  had no importer)
- `admin-dashboard/src/lib/map/webgl-support.ts` — module docstring updated to drop the
  `driver-map` mention from its list of admin maps that "can adopt the same probe" (the file
  itself, `hasRenderingWebGL()`, is untouched — shared by `ride-route-map.tsx`,
  `monitoring-map.tsx`, `live-map.tsx`)

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** A fresh repo-wide grep (case-insensitive `driver-map`, and
  case-sensitive `DriverMap`) across `admin-dashboard/`, `backend/`, `rider-app/`,
  `driver-app/`, `shared/` — excluding `docs/` (historical change-log/audit records, correctly
  left untouched) and `.planning/` (a different AI tool's generated dependency-graph snapshots,
  out of scope per this task's constraints) — returns **zero** matches after deletion. Before
  deletion the only matches outside the two deleted files, `ACTION_ITEMS.md`, `docs/`, and
  `.planning/` were the one line in `webgl-support.ts`'s docstring, which this change also
  fixes.
- Confirmed via full git history (`git log --all -p`) that `dashboard/service-areas/page.tsx`
  never imported `driver-map` (an earlier change-log entry, `2026-08-21-...md`, claimed it did
  — that claim was already stale/incorrect when written, since the only real importer,
  `dashboard/drivers/page.tsx`'s lazy import, had already been removed nearly 5 months earlier
  in April; that 2026-08-21 doc is historical record and is not edited by this change per this
  task's own instructions).
- `webgl-support.ts`'s exported function `hasRenderingWebGL()` and its own test file
  (`webgl-support.test.ts`) are untouched — only the module-level comment changed, and only to
  remove a reference to a file that no longer exists. Its three real importers
  (`ride-route-map.tsx`, `monitoring-map.tsx`, `live-map.tsx`) are unaffected.
- `@/lib/map/maplibre-base` (the other module `driver-map.tsx` imported) is a shared utility
  used by six other map components — not touched, not deleted.
- Nothing reachable in production depends on this component today, so there is no live-tested
  flow this change can regress.

## 5. User-experience effect

**None.** `DriverMap` was not rendered by any route a rider, driver, corporate admin, or
internal admin could reach — deleting it removes zero pixels from any screen anyone can
currently see. Not visible mid-session to anyone, because it was never visible at all.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/driver-map.tsx` | Deleted | Zero importers repo-wide; confirmed unreachable since 2026-04-01 |
| `admin-dashboard/src/components/driver-map.render.test.tsx` | Deleted | Test file for the now-deleted component |
| `admin-dashboard/src/lib/map/webgl-support.ts` | Docstring: removed `driver-map` from the list of admin maps that can adopt the probe | File no longer exists |
| `ACTION_ITEMS.md` | C116 marked closed | Tracks completion of this item |

## 7. Before / after

Deletion-only change to the two component files (no before/after diff applicable — the "after"
state is the file's absence).

`webgl-support.ts` docstring:

```
# Before
// Lives in its own module rather than inside ride-route-map.tsx so it can be
// tested without importing maplibre-gl and its stylesheet, and so the other
// admin maps (live-map, driver-map, geofence-map) can adopt the same probe when
// they gain raster fallbacks.
```

```
# After
// Lives in its own module rather than inside ride-route-map.tsx so it can be
// tested without importing maplibre-gl and its stylesheet, and so the other
// admin maps (live-map, geofence-map) can adopt the same probe when they gain
// raster fallbacks.
```

## 8. Rollback plan

`git-revert-safe`. This is a pure deletion with no data, migration, config, or live-state
component — `git revert` on the merge commit fully restores both files byte-for-byte and the
one-line docstring edit. No second deploy step, no data cleanup, no feature flag involved.

## 9. Verification performed

- [x] `npx eslint .` (whole admin-dashboard project): 0 errors both before and after — 358
      warnings before, 358 warnings after (exact match, confirmed via a `git stash`
      before/after comparison in the same worktree with the same `node_modules`), confirming
      the deleted files contributed 0 warnings and this change introduces none.
- [x] `npx tsc --noEmit -p .` (whole project): clean, 0 output, before and after.
- [x] `npx vitest run` (whole project, not just touched files): see run summary below.
- [x] **Real production build**: `npm run build` run for real (not just `tsc --noEmit`) inside
      this isolated worktree's own fresh `npm ci` install — see summary below.
- [x] Blast-radius grep performed: repo-wide, case-insensitive `driver-map` and case-sensitive
      `DriverMap`, across all five surfaces, before and after deletion (see §4).
- [x] Adversarial review: `spinr-design-consistency-reviewer` run against the actual diff via
      the Agent tool (CLAUDE.md gate 10) — see findings noted in the PR.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no live-data implications)
- [x] Blast radius is stated, not assumed — zero-importer status re-confirmed fresh today, not
      trusted from the ACTION_ITEMS.md entry alone
- [x] No silent behavior change — nothing user-visible changes, because nothing reachable used
      this component
