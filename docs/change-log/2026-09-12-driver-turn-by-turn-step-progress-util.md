# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Author | Claude Code |
| Surface(s) | shared (used by driver-app; rider-app unaffected, doesn't import it yet) |
| Domain (Sentry tag) | drivers |
| PR / commit link | (branch `claude/vehicle-icon-movement-animation-8tys8o`) |
| Related issue or gap ID | PR B of Phase 1, `docs/proposals/2026-09-01-driver-in-app-turn-by-turn-navigation.md` §7.3 — builds on PR A (#5289, backend `navigation-steps` endpoint). |

## 1. Issue / gap identified

PR A's backend endpoint returns a flat list of turn-by-turn steps, but nothing on the frontend can determine which step the driver is currently on or how far remains to the next maneuver — the raw list alone isn't enough to drive a banner UI or camera behavior.

## 2. Root cause

Not a regression — new capability. No frontend code anywhere (grepped, confirmed in the earlier research pass) has any concept of step/maneuver progress.

## 3. Fix / remediation

Added `shared/utils/navigationSteps.ts`, a pure utility (no React/native imports, same discipline as `vehicleTracking.ts`, which it reuses `distanceMeters`/`TrackingLatLng` from):

- `NavigationStep` interface — matches PR A's endpoint response shape exactly (`instruction`, `maneuver`, `distanceMeters`, `startLocation`, `endLocation`).
- `trackStepProgress(steps, position, prevIndex)` — given the step list and the driver's current position, returns `{stepIndex, distanceToManeuverMeters, step}`. Uses the same continuity-hint pattern `snapToRoute`'s `preferredFromIndex` already establishes: a caller passes its own last-known step index so a normal tick's search starts there instead of from the beginning.
- `STEP_ADVANCE_THRESHOLD_M = 30` — how close to a step's `endLocation` counts as "maneuver reached," advancing to the next step. Deliberately generous (matching GPS accuracy realities right at an intersection, the worst place for GNSS accuracy) rather than tight.
- Advances past any number of already-completed short steps in one call (handles a GPS-jittery or infrequent-poll tick that skips several steps at once) but never moves backward — a driver nudged slightly behind an already-passed maneuver point by GPS noise keeps their already-advanced progress.

No existing file was modified — this is a wholly new, isolated module.

## 4. Risk & impact on existing functionality

- **Blast radius**: zero. This is a new file with no existing importers. Grepped the whole `driver-app/` and `shared/` tree — nothing references `navigationSteps.ts` yet; PR C wires it into the actual UI/camera.
- **No change to `vehicleTracking.ts`** — only reads its existing exported `distanceMeters`/`TrackingLatLng`, doesn't modify them.
- **Pure function, no side effects** — cannot affect rendering, network, or state until a caller (PR C) actually uses it.

## 5. User-experience effect

**None.** This PR ships no UI change — it's an unused (until PR C) pure utility.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/utils/navigationSteps.ts` | New file: `NavigationStep`, `StepProgress`, `trackStepProgress()`, `STEP_ADVANCE_THRESHOLD_M` | Pure step-progress tracking, reusable by driver-app's banner UI and follow-camera (PR C) |
| `driver-app/__tests__/navigationSteps.test.ts` | New: 10 tests (empty list, initial index, distance-decreasing, single-step-advance, no-advance-below-threshold, multi-step-skip, never-past-final-step, no-backward-movement on noisy continuity hint, out-of-range hint clamped, missing-endLocation fallback) | Proves the exact mechanism PR C will depend on; lives in `driver-app/__tests__` per this repo's established convention (no CI jest run collects `shared/utils/__tests__` directly — see `rider-app/__tests__/vehicleTracking.test.ts`'s own header comment) |

## 7. Before / after

```ts
// Before — no frontend concept of step/maneuver progress existed at all.
```

```ts
// After — a new pure utility
export function trackStepProgress(
  steps: readonly NavigationStep[],
  position: TrackingLatLng,
  prevIndex: number | null,
): StepProgress | null {
  // ... continuity-hint search, advance-past-completed-steps loop ...
  return { stepIndex, distanceToManeuverMeters, step };
}
```

## 8. Rollback plan

`git-revert-safe` — new, unused file; nothing else in the codebase references it yet.

## 9. Verification performed

- [x] New unit tests: `driver-app/__tests__/navigationSteps.test.ts` — 10/10 passing.
- [x] `npx tsc --noEmit` clean on both driver-app and rider-app (the shared file is on rider-app's compile path too, via `@shared/*`, even though rider-app doesn't import it yet).
- [x] Blast-radius grep performed: confirmed zero existing importers of the new file.

**What was NOT verified:** on-device behavior — this is a pure math utility with no rendering; PR C's own device-adjacent testing (via the existing `driverDashboardScreen.test.tsx`-style component test harness) is where a more end-to-end check happens. No visual-regression tooling exists for driver-app (per CLAUDE.md) and this PR has no visual surface to check anyway.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (new, unreferenced file — trivial revert)
- [x] Blast radius is stated, not assumed (grepped: zero existing importers)
- [x] No silent behavior change to an already-shipped flow (§5: no UI change, nothing calls this yet)
