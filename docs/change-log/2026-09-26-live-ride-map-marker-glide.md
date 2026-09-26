# Change Impact & Risk Log — admin live-ride map: driver marker glides between polls

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code (agent session) |
| Surface(s) | admin-dashboard (internal staff only) |
| Domain (Sentry tag) | admin |
| PR / commit link | Branch `claude/spinr-animations-admin-ux-x7nl5x`: feat(admin): live-ride map driver marker glides between polls (W4.1) |
| Related issue or gap ID | UX enhancement program W4.1 (`.claude/plans/2026-09-25-ux-enhancement-program.md`); finishes the item left open in `2026-09-26-admin-marker-interpolation.md` "What was NOT verified" |

## 1. Issue / gap identified

On the admin live-ride page (`/dashboard/rides/live/[id]`), the driver marker teleported to each new position every 5 s poll. The monitoring map (#5888) and the public `/track` page (#5892) already glide.

## 2. Root cause

W4.1 shipped the `marker-interpolation` util and wired only the monitoring map. The live-ride map was left out because `rides/**` was fenced off while other work ran in parallel, not for a technical reason. Its marker still moved with one direct `setLngLat` call in the position effect.

## 3. Fix / remediation

`live-map.tsx` now moves the driver marker through the util, using the same pattern as `monitoring-map.tsx` and `/track`:

- The first placement is exact, as before.
- Later moves glide over `MARKER_ANIMATION_MS` (1 s) on one `requestAnimationFrame` loop that stops when the marker arrives.
- The position is set directly, as before, when:
  - the jump is over 500 m;
  - the previous move is over 30 s old;
  - Reduce Motion is on (read at each update);
  - nothing moved.
- A marker created at the pickup/dropoff midpoint placeholder (no driver position yet) takes its first real position directly instead of gliding from the midpoint.
- The loop is cancelled on unmount and when the map re-initialises.
- The trail line is still redrawn as soon as a new position arrives.

**Alternatives considered (gate 10):**
- *Extract a shared `useMarkerGlide` hook and move all three maps onto it.* Rejected for this change: it would edit the monitoring map (on a merge-blocking visual baseline) and `/track`, both merged and tested, to finish a one-file item. The three copies are about 40 lines each. A shared hook is a reasonable later refactor.
- *Leave the live-ride map as is.* Rejected: W4.1's row names the live-ride map explicitly.

## 4. Risk & impact on existing functionality

- **Blast radius: one component, one page.**
  - `live-map.tsx` is imported only by `rides/live/[id]/page.tsx`, through `next/dynamic` with `ssr: false`.
  - The util already has two other importers (`monitoring-map.tsx`, `track/[rideId]/page.tsx`) and is **unchanged**.
  - `live-map.render.test.tsx` (the WebGL guard) still passes unchanged.
- **Not a visual-baseline page.** The six baselines cover `/dashboard/rides`, the list page, not `/dashboard/rides/live/[id]`.
- **Stale-gap rule and a stationary driver.** The page re-renders the map only when the position (or the trail, which only grows when the position changes) changes. The "previous move" time is therefore the last *position change*, not the last poll. A driver who waits in place for over 30 s and then moves has that first move set directly rather than glided. That is the old behaviour, for that one update, and later moves glide.
- **Trail leads the marker by up to 1 s.** The trail line's end reaches the new position immediately while the marker glides there over 1 s.
- **Tab in the background:** the browser pauses rAF. The first frame after the tab returns lands on the latest target.
- **No interaction** with the ride state machine, dispatch, money, insurance periods or the backend. Display-only; no data written; no logging added.

## 5. User-experience effect

- **Who sees it:** internal admins on the live-ride tracking page, after deploy and a page reload.
- The driver dot slides between polls instead of jumping. Jumps over 500 m, gaps over 30 s and viewers with Reduce Motion keep the instant move.
- No copy, colour, size or layout change.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/rides/live/[id]/live-map.tsx` | Driver marker moves through `interpolateMarker`; first real placement recorded; loop cancelled on cleanup | Finish W4.1 |
| `admin-dashboard/src/app/dashboard/rides/live/[id]/live-map-marker-motion.render.test.tsx` (new) | 10 cases for the wiring | Coverage for the change |
| `docs/change-log/2026-09-26-admin-marker-interpolation.md` | The "not wired" note points here | Keep the W4.1 log accurate |
| `docs/change-log/2026-09-26-live-ride-map-marker-glide.md` (new) | This log | CLAUDE.md change-impact rule |
| `.claude/plans/2026-09-25-ux-enhancement-program.md` | Progress line; W4.1 and W5.1 row markers | Program tracking |

## 7. Before / after

Before:

```tsx
if (driverLat != null && driverLng != null && driverMarkerRef.current) {
    driverMarkerRef.current.setLngLat([driverLng, driverLat]);
}
```

After:

```tsx
if (driverLat != null && driverLng != null && driverMarkerRef.current) {
    moveDriverMarker(driverMarkerRef.current, { lat: driverLat, lng: driverLng });
}
```

`moveDriverMarker` either calls `setLngLat` directly (every snap case) or starts or retargets the glide.

## 8. Rollback plan

- **Per viewer, no deploy:** turning on Reduce Motion restores the old instant move exactly.
- **Whole surface:** no feature flag, the same as W4.1's "Gate: none". Revert the commit and redeploy admin-dashboard, or promote the previous Vercel deployment. Nothing is written to live data, so no data cleanup is needed.

## 9. Verification performed

- **New tests fail on the old code.** The 5 glide cases fail against the pre-change `live-map.tsx`:
  - glide and land;
  - retarget mid-glide;
  - trail updated while the marker glides;
  - a glide cancelled on unmount;
  - a glide cancelled on map re-init.

  The exact-placement, 500 m snap, 30 s stale-gap, Reduce Motion and placeholder cases pass on both.
- **Mutation checks.**
  - Removing `stopDriverMotion()` from the init effect's cleanup fails the unmount and re-init cases.
  - Removing the `gapMs` input fails the 30 s case.
- **Checks:** `npx tsc --noEmit` exit 0. ESLint on the two files: 0 errors, and the one warning (`exhaustive-deps` on the init effect's `driverLat`/`driverLng`) is pre-existing and unchanged.
- **Full admin suite:** `npx vitest run`: 107 files and 913 tests passed (the 903 on `main` plus these 10).
- **Production build:** `npm run build` (`next build`) exit 0, "Compiled successfully", 80/80 static pages. It ran before the review's two extra test cases were added; `live-map.tsx` has not changed since.
- **Edge-case review** (`spinr-edge-case-reviewer`, on the first version of the commit). No blockers. It traced the unmount and re-init paths and found both safe: the cleanup cancels the frame before the marker and map are removed, and each frame re-reads the marker ref.
  - It asked for a regression test for re-init mid-glide. It is added (the re-init case above), together with a 30 s stale-gap case that it noted was untested.
  - It flagged the trail leading the marker by up to 1 s as cosmetic, a product call rather than a bug. It stays as is here and is recorded in §4.

## 10. What was NOT verified

- **No real browser.** The glide was exercised with a mocked MapLibre and a manual rAF queue in jsdom, not watched on a real map.
- **No real ride.** Poll cadence and positions come from the test, not from a live driver.
- **No profiling.** It is one marker, so none was expected to be needed.
