# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code (session `session_01173usfHtfdzMMzYpWeWmVm`) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | Follow-up to `docs/change-log/2026-09-14-webgl-stub-detection-and-blocked-tile-notice.md` and `docs/change-log/2026-09-14-monitoring-map-webgl-stub-guard.md`, both of which explicitly named `live-map.tsx` and `driver-map.tsx` as "still owed." User asked to continue this same fix to the remaining maps. |

## 1. Issue / gap identified

Two more admin-dashboard maps — `live-map.tsx` (the ride-detail live-tracking
map) and `driver-map.tsx` (a fleet driver map) — had the identical gap
already fixed twice this week elsewhere: no WebGL-capability check, so a
browser whose WebGL context is a non-drawing stub gets a MapLibre map that
never paints a pixel, with nothing on screen explaining why.

## 2. Root cause

Same root cause as the two prior fixes this week (see the linked change
logs for the full writeup) — not re-investigated here, only ported.

## 3. Fix / remediation

Both files get the same guard as `monitoring-map.tsx`:
`const [webglOk] = useState<boolean>(() => hasRenderingWebGL());` (lazy
initializer, computed once), a `if (!webglOk) return;` in the mount effect
before `new maplibregl.Map(...)` is ever called, and a new render branch
showing a plain-language message instead of a blank canvas.

- **`live-map.tsx`**: the mount effect already had a real dependency array
  (`[pickupLat, pickupLng, dropoffLat, dropoffLng]`, unlike
  `monitoring-map.tsx`'s `[]`-only effect) — `webglOk` was added to it
  rather than suppressed, since it's a stable value that never changes
  post-mount and adding it is the correct, lint-clean fix (confirmed:
  0 new warnings, exact pre-existing warning count preserved). Message:
  *"...Ride status, route, and driver/rider details in the panel are still
  live."* — confirmed against the parent `page.tsx`, which renders a
  slide-in info panel (status/route/driver/rider/trail count) independently
  of the map.
- **`driver-map.tsx`**: this component also renders a legend bar (driver
  online/offline counts) as a sibling above the map container, unlike the
  single-branch shape of the other two fixed files. Kept the legend bar
  always rendering and only swapped the map `<div>` for the message when
  `!webglOk`, so the counts stay visible and accurate regardless of map
  state. Message: *"...Driver counts above are still live."* The other two
  effects in this file (marker/service-area sync, pan-to-selected-area)
  were not touched — both already only ever act through `mapRef.current`,
  which stays `null` forever when `webglOk` is false, so their own
  pre-existing `if (!map) return;` guards already no-op correctly with zero
  changes needed.

**Real finding, not part of the fix itself:** `DriverMap` has no importer
anywhere in the codebase today (grepped repo-wide for both the file path
and the component name; no dynamic import, no barrel re-export). It is not
currently reachable from any route. Fixed anyway, matching this backlog's
explicit "still owed" list and so the component is correct once/if it is
wired up — but flagged plainly rather than silently treating it as
equivalent in urgency to `live-map.tsx`, which *is* live and reachable
today.

**Alternative considered:** skip `driver-map.tsx` since it's unreachable.
**Rejected** — the user asked for both named files, the fix is free (zero
behavioral risk to anything, since nothing renders this component today
regardless of what its code does), and leaving it inconsistent with its
two siblings would be a trap for whoever does wire it up later.

## 4. Risk & impact on existing functionality

- **Blast radius: two components' mount paths, isolated.**
  `hasRenderingWebGL()` itself is unmodified and already shipped with its
  own test coverage from the first fix this week; this diff only adds two
  more call sites. `live-map.tsx` has one consumer, `page.tsx` (confirmed
  by grep); `driver-map.tsx` has zero consumers (see above), so its blast
  radius is, literally, nothing reachable today.
- **Behavior when `webglOk` is true (every real admin today) is
  byte-identical to before** in both files — confirmed by the "constructs a
  MapLibre map as before" regression test in each new test file.
- **`live-map.tsx`'s pre-existing `driverLat`/`driverLng` exhaustive-deps
  warning is untouched** — out of scope for this diff, not introduced by
  it, confirmed present before and after at the identical line.
- **No visual-regression coverage exists for either page** (same
  disclosure every admin-dashboard map change this week has carried) —
  `/dashboard/rides/live/[id]` and wherever `DriverMap` would eventually
  mount are not in the seeded baseline set.

## 5. User-experience effect

Internal-admin-facing only.

- **`live-map.tsx`**: an admin tracking a live ride whose browser can't
  render WebGL now sees a clear message instead of a blank map; the info
  panel (status, route, driver/rider details) is unaffected either way.
- **`driver-map.tsx`**: no real admin can hit this today (unreachable from
  any route) — the fix is preventative/consistency-driven, not resolving
  an active user report.
- Every other admin: no change whatsoever.

**Not feature-flagged**, same reasoning as the two prior fixes this week:
the only behavioral effect is replacing a silent failure with an
explanation.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/rides/live/[id]/live-map.tsx` | Adds `webglOk` guard + render branch | Close the gap the two prior fixes named as still owed |
| `admin-dashboard/src/app/dashboard/rides/live/[id]/live-map.render.test.tsx` | New file, 2 tests | First render test of this component |
| `admin-dashboard/src/components/driver-map.tsx` | Adds `webglOk` guard + render branch, legend bar kept always-visible | Same gap, different render shape (legend bar sibling) |
| `admin-dashboard/src/components/driver-map.render.test.tsx` | New file, 3 tests | First render test of this component |
| `ACTION_ITEMS.md` | Updates the "still owed" tracking (see C115's sibling note, or a new entry — see the PR) | Keep the backlog record accurate |

## 7. Before / after

Same shape as `monitoring-map.tsx`'s own before/after (see that change log)
— applied identically to both files here, with `driver-map.tsx`'s render
branch nested inside its existing wrapper `<div>` rather than replacing the
whole component return, to keep the legend bar always visible.

## 8. Rollback plan

`git-revert-safe` — client-side render logic only, no migration, no
persisted state, no live-data mutation, no config.

## 9. Verification performed

- [x] `npx eslint` on all 4 files: 0 errors. `live-map.tsx`: exactly its
      pre-existing 1 warning (confirmed via `git stash` diff). `driver-map.tsx`:
      0 warnings, matching its own clean baseline.
- [x] `npx tsc --noEmit -p .`: clean.
- [x] `npx vitest run` on both new test files plus `webgl-support.test.ts`,
      `pages.smoke.test.tsx`, and `monitoring-map.render.test.tsx`:
      **42/42 passed, 0 regressions.**
- [x] **A real `npm run build`**: `✓ Compiled successfully`, TypeScript
      clean, all pages generated including `/dashboard/rides/live/[id]`.
      Run against the main checkout after syncing it to this worktree's
      exact base commit (it had drifted 146 commits behind — synced via a
      clean fast-forward, `git status` confirmed empty before and after,
      no work lost or discarded).
- [x] Blast-radius grep: every importer of `src/lib/map/webgl-support`
      (now four: `ride-route-map.tsx`, `monitoring-map.tsx`, `live-map.tsx`,
      `driver-map.tsx`); confirmed zero importers of `DriverMap` itself
      repo-wide.
- [x] Reviewed by `spinr-design-consistency-reviewer` (CLAUDE.md gate 10) —
      see findings below.

## Findings from `spinr-design-consistency-reviewer`

**Verdict: ON-BRAND & COMPLETE — safe to merge.** Independently re-derived
(not trusted from this session's own report):

- Re-ran `eslint`/`tsc`/all affected `vitest` suites directly — identical
  results (42/42 passing, 0 new warnings in either file).
- Re-derived the guard's control flow in both files from the code itself:
  confirmed `if (!webglOk) return;` is the first substantive line in each
  mount effect, before `new maplibregl.Map(...)`; confirmed `webglOk` has
  no setter anywhere in either file (provably immutable post-mount), so
  adding it to `live-map.tsx`'s existing dependency array cannot change
  when that effect re-fires relative to its four pre-existing deps;
  confirmed `driver-map.tsx`'s other two effects correctly no-op via their
  own pre-existing `if (!map) return;` guards once `mapRef.current` stays
  permanently `null`.
- Cross-checked both message claims against the real surrounding UI rather
  than trusting the diff's own description: `driver-map.tsx`'s legend bar
  is confirmed to be an unconditional sibling rendered above the
  webglOk-gated map area; `live-map.tsx`'s "Ride status, route, and
  driver/rider details in the panel are still live" was checked against
  `page.tsx`'s actual Info Panel section labels (Status/Route/Driver/Rider)
  and matches word-for-word.
- Checked for new color literals introduced by this diff (grepped both
  files for `#`/`rgb(`) — none; only pre-existing marker/layer colors
  untouched by this change. Zero brand-drift risk.
- Copy/tone confirmed to reuse the established opening sentence verbatim
  from the two prior fixes this week, with only the trailing
  context-specific clause customized per file — same pattern
  `monitoring-map.tsx` itself used.

**One real, separate finding — filed as `ACTION_ITEMS.md` C116, not a
blocker on this diff:** `DriverMap` has no importer anywhere in the
codebase, confirmed independently (repo-wide grep for both the file path
and the component name, plus a `git log --diff-filter=A` search back to
the file's original commit finding no point in its history with a
consumer). Notably, this file absorbed *another* same-day, unrelated fix
this morning (`ee3498254`) that also didn't question its reachability —
this is a real, recurring pattern (correct maintenance landing on
apparently-dead code) worth a standalone ticket to either wire the
component into a route or delete it, not just a note that fades into the
next session's blind spot.

## 10. What was NOT verified

- Not tested in a real browser (no browser available in this environment).
- `driver-map.tsx` cannot be exercised end-to-end today since nothing
  imports it — the fix is verified at the unit/render-test level only.
- `geofence-map.tsx`, the third file this backlog's own tracking has
  always grouped with these two, remains untouched — not requested this
  round.

## 11. Sign-off

- [x] Rollback plan is concrete and testable — plain `git revert`.
- [x] Blast radius stated: two components' mount paths; one has zero
      current reachability, stated plainly rather than implied to be
      equally urgent as the other.
- [x] No silent behavior change to a working flow — every admin whose
      browser already worked sees byte-identical behavior.
