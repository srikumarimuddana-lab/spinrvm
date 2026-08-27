# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude, at user request (live-tested during a 2-driver Regina test session in this conversation) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | commit following this log |
| Related issue or gap ID | Reported live: "tested with 2 drivers in regina it had a white rectangle bar with small rectangles in it... white in color" |

## 1. Issue / gap identified

A driver in a service area with the demand heatmap freshly enabled (Regina, enabled minutes
earlier in this same session) could be left staring at an unlabeled, colorless placeholder
pill at the top-center of the map screen — no "Quiet"/"Busy" text, no color — for as long as
~4 minutes, with no indication anything was wrong.

## 2. Root cause

`driver-app/hooks/useDemandHeatmap.ts`'s `fetchHeatmap` only transitions `status` away from
its initial `'loading'` value on a *successful* response, or after **3 consecutive** failed
fetches (`MAX_CONSECUTIVE_ERRORS`). That threshold is deliberate and correct for a driver who
has *already* seen real data — tolerating one blip mid-shift avoids flickering the whole
overlay to an "unavailable" state on a transient network hiccup (see the existing test
`hides the overlay once failures become persistent` / now renamed, and its own comment).

But the same threshold also applied to a driver's **very first** fetch attempt, before any
data has ever been shown. If that first attempt fails — plausible right after a feature flag
flip (cold Redis cache, cold backend instance) or on a driver's first cold app start — nothing
protects a "was working" state from flicker, because nothing has worked yet. The driver is
left on the blank `'loading'` shimmer (`DemandLegend.tsx`'s loading branch: a pill with 5
low-opacity gray swatches and zero text) for up to 3 poll cycles at the default 90s interval
(≈4 minutes) before the first visible feedback ("Demand info unavailable") appears.

## 3. Fix / remediation

Distinguish "never yet succeeded" from "was working, now blipping" using the existing
`lastFetchRef` (only ever set on a successful fetch, so its absence is a reliable "still on
the initial load" signal). On a failure:
- If a fetch has never succeeded (`lastFetchRef.current === 0`): surface `'error'` after the
  **first** failure — there is nothing to protect from flicker, so showing "Demand info
  unavailable" beats an indefinite blank shimmer.
- If at least one fetch has already succeeded: unchanged — still requires 3 consecutive
  failures, preserving the existing blip-tolerance behavior for a driver already looking at
  real data.

No change to the steady-state (post-first-success) behavior at all; the fix is scoped
entirely to the cold-start path.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `useDemandHeatmap`'s error-threshold logic.** Grepped for other
  consumers of this hook: only `driver-app/app/driver/(tabs)/index.tsx` (the main map screen)
  and the Android Auto car-map mirror (`useCarLiveRoute.ts` reads the *published* snapshot via
  `demandHeatmapShared.ts`, not the hook directly, and inherits whatever `status` this hook
  settles on — same benefit, no separate change needed there).
- Does not touch the backend, the WS layer, ride state, or any money/dispatch path — this is a
  client-only display-state fix for a feature that is currently enabled in exactly one service
  area (Saskatoon and Regina; see prior change-logs in this same date range).
- Existing test `hides the overlay once failures become persistent` (pre-existing) implicitly
  relied on "first attempt onward, always failing" reaching `'error'` after 3 cycles — with
  this fix it now reaches `'error'` after the *first* cycle, since it never had a prior
  success. Its final assertion (`status === 'error'`, `visible === false`) still holds, so it
  still passes; I split it into two tests to keep both properties explicit and independently
  regression-tested going forward: (a) first-attempt-fails → immediate error, and (b)
  post-success blip tolerance → still requires 3 consecutive failures.

## 5. User-experience effect

**Driver-facing, visible mid-session.** A driver whose very first heatmap fetch fails now sees
"Demand info unavailable" within one poll attempt (bounded by the API client's existing 15s
request timeout) instead of an unlabeled blank pill for up to ~4 minutes. A driver who has
already seen real demand data and hits a transient blip mid-shift sees no change at all — the
existing 3-strike tolerance is untouched.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/hooks/useDemandHeatmap.ts` | Error-status threshold is now 1 failure before any success has occurred, 3 failures (unchanged) after | Stop an indefinite blank loading shimmer on a driver's first fetch attempt |
| `driver-app/__tests__/hooks/useDemandHeatmap.test.ts` | Split the single persistent-failure test into two: immediate-error-on-first-failure (new) and blip-tolerance-after-success (renamed/adjusted from the original) | Pin both properties explicitly so a future change can't silently reintroduce either the stuck-shimmer bug or the post-success flicker it was designed to avoid |
| `docs/change-log/2026-08-27-driver-heatmap-loading-shimmer-stuck-fix.md` | This log | Bug fix on a live-tested driver-facing surface |

## 7. Before / after

```ts
// Before
} catch {
  errorCountRef.current += 1;
  if (errorCountRef.current >= MAX_CONSECUTIVE_ERRORS) {
    setStatus('error');
  }
}
```

```ts
// After
} catch {
  errorCountRef.current += 1;
  const everSucceeded = lastFetchRef.current > 0;
  const threshold = everSucceeded ? MAX_CONSECUTIVE_ERRORS : 1;
  if (errorCountRef.current >= threshold) {
    setStatus('error');
  }
}
```

## 8. Rollback plan

`git revert` is complete and sufficient — pure client-side logic change, no data, no
migration, no server-side component.

## 9. Verification performed

- [x] `npx jest __tests__/hooks/useDemandHeatmap.test.ts` — 17/17 pass, including the two
      restructured failure-handling tests.
- [x] `npx eslint hooks/useDemandHeatmap.ts __tests__/hooks/useDemandHeatmap.test.ts` — the
      diff introduces zero new lint findings (confirmed by diffing against the pre-change file
      via `git stash`: the 2 `react-hooks/set-state-in-effect` errors and 1 import-order
      warning all pre-exist this change, in unrelated code this diff doesn't touch).
- [x] `npx tsc --noEmit -p .` — clean, no errors.
- [ ] Not run against a real device/simulator — reasoned from the hook's unit tests and a
      direct reading of `DemandLegend.tsx`'s render branches, not an on-device repro. No
      automated visual-regression tooling exists for driver-app (per CLAUDE.md's standing
      note), so this is a logic-level verification, not a visual one.

## What was NOT verified

- Did not reproduce the original stuck-shimmer report on a live device against production —
  diagnosed from the hook's source and its existing/updated unit tests. The original report
  (2 drivers, Regina, white rectangle bar) is consistent with this root cause but wasn't
  independently re-observed after the fix.
- Did not investigate why the *first* fetch specifically failed for the reporting drivers
  (cold Redis cache for a newly-enabled area vs. a genuine network issue vs. something else)
  — this fix makes the failure visible quickly regardless of cause; it doesn't address why a
  first fetch might fail in the first place.

## 10. Sign-off

- [x] Rollback plan is concrete (plain `git revert`, no data-layer component).
- [x] Blast radius is stated, not assumed — isolated to one hook's error-threshold logic, one
      consumer screen, verified via full test suite + lint/typecheck diffing.
- [x] No silent behavior change to an already-shipped flow for a driver who has already seen
      real data — only the never-yet-succeeded cold-start path changes.
