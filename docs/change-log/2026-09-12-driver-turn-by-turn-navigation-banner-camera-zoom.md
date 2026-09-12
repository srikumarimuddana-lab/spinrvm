# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Author | Claude Code |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (branch `claude/vehicle-icon-movement-animation-8tys8o`) |
| Related issue or gap ID | PR C of Phase 1, `docs/proposals/2026-09-01-driver-in-app-turn-by-turn-navigation.md` §7.3 — first-party in-app turn-by-turn navigation. |

## 1. Issue / gap identified

Even after PR A (backend endpoint) and PR B (step-tracking utility), nothing in driver-app actually calls the endpoint or shows the driver anything — there is no UI, and the follow-camera has no way to react to an upcoming turn.

## 2. Root cause

Not a regression — new capability. Included per CLAUDE.md's gate for a change to a live-tested surface (driver-app's map/camera screen).

## 3. Fix / remediation

- **`driver-app/components/dashboard/NavigationStepBanner.tsx`** (new): a small, presentation-only component — an instruction + distance-to-maneuver pill near the top of the map, with a generic directional icon (`iconForManeuver`, mapping Google's `maneuver` enum to a small fixed Ionicons set — left/right/straight/u-turn/roundabout/merge, no bespoke turn-arrow icon set, matching the proposal's "not lane guidance" Phase 1 scope) and a distance formatter (`formatManeuverDistance`, "80 m" / "1.2 km"). `pointerEvents="none"` so it never blocks map gestures underneath. Follows the same `useTheme()`/`createStyles(colors)` pattern as the existing `MapControls.tsx`.
- **`driver-app/app/driver/(tabs)/index.tsx`**:
  - New state (`navSteps`, `currentNavStep`) and two effects: one fetches `GET /rides/{id}/navigation-steps` once per ride+leg (keyed by a ref, not polled — the endpoint is already leg-scoped-cached server-side per PR A), the other re-derives `trackStepProgress()` (PR B) on every GPS tick to know which step the driver is on and how far to its maneuver.
  - Follow-camera effect (the existing speed-adaptive zoom/course-up effect): added an **additive** zoom boost — `+1` zoom level, capped, active only when `currentNavStep` is non-null AND within 150m of the maneuver (`MANEUVER_ZOOM_THRESHOLD_M`). Added `currentNavStep` to that effect's dependency array so it re-runs on step-progress changes, not just on `location`/`rideState`/`courseUp`.
  - Renders `<NavigationStepBanner>` conditionally: only during `navigating_to_pickup` / `arrived_at_pickup` / `trip_in_progress` AND only once `currentNavStep` is non-null.

No client-side flag check anywhere in this PR — PR A's endpoint already returns `{"steps": [], "destination": null}` while `app_settings.driver_turn_by_turn_enabled` is off, so `navSteps` stays empty, `currentNavStep` stays null, the banner never renders, and the camera zoom boost's `if (currentNavStep && ...)` guard is never true. Same shape either way, by PR A's own design, so nothing here needs to know about the flag.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface, but touches a shared, live-tested code path.** `NavigationStepBanner.tsx` is a brand-new, unreferenced-elsewhere component — zero risk on its own. The real blast-radius concern is the **follow-camera effect edit in `index.tsx`**, which is not new code — it's the same effect that runs the speed-adaptive zoom/course-up camera for *every* active ride today. Grepped: `FOLLOW_ZOOM_TIERS` (the tier table itself, untouched) is only consumed by this one effect in this one file — no Android Auto or other screen duplicates this camera logic, so there is exactly one call site to reason about.
- **Why this is safe despite touching shared code**: the zoom-boost branch is purely additive and its guard (`currentNavStep &&  distanceToManeuverMeters <= 150`) can only be true once `navSteps` is non-empty, which requires the backend flag to be on AND a successful fetch AND the driver within 150m of a real maneuver point. While the flag is off (default, today), `currentNavStep` is always `null` and the camera computes the exact same zoom value as before this PR — verified by reading the added `if` block: it's strictly `zoom = Math.min(zoom + BOOST, MAX)`, never reached when the guard is false, so the pre-existing `FOLLOW_ZOOM_TIERS[tier].zoom` value is the only value ever applied today.
- **Existing regression coverage**: `driver-app/__tests__/app/driverDashboardScreen.test.tsx` exercises this screen's camera/zoom behavior and passed unchanged (full suite: 147 suites / 1662 tests, all green) — this is real coverage of the touched effect, not a stubbed-out double.
- **Network cost**: one new fetch per ride+leg (not per GPS tick, not polled) — bounded exactly as PR A's Change Impact Log already describes for the endpoint itself; this PR doesn't add any new call pattern beyond "call it once when a leg starts."

## 5. User-experience effect

**None today** — the flag (`driver_turn_by_turn_enabled`) is off by default, so no driver sees the banner or the zoom boost. Once enabled: a driver on `navigating_to_pickup`/`trip_in_progress` would see a small instruction+distance pill near the top of the map, and the camera would zoom in slightly (one extra level) inside 150m of a turn — this is the shipped, but dark-launched, Phase 1 behavior described in the proposal §3. No rider-facing or admin-facing change at all — driver-app only.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/dashboard/NavigationStepBanner.tsx` | New component: instruction/distance banner, `iconForManeuver`, `formatManeuverDistance` | Presentation layer for PR A/B's data |
| `driver-app/app/driver/(tabs)/index.tsx` | New state + 2 effects (fetch steps once per leg, track step progress per GPS tick); additive zoom-boost branch + new dep in the existing follow-camera effect; conditional banner render | Wires PR A/B into the live map screen, behind the flag (via empty-response shape, no explicit flag check needed) |
| `driver-app/__tests__/components/NavigationStepBanner.test.tsx` | New: 7 tests (distance formatting incl. negative-clamp, maneuver→icon mapping incl. unknown-maneuver fallback, render + accessibility label, pointerEvents) | Proves the new presentation component in isolation |

## 7. Before / after

```tsx
// Before — the follow-camera effect's zoom was purely speed-tiered.
const tier = zoomTierForSpeed(effectiveSpeedMps(...), followZoomTierRef.current);
followZoomTierRef.current = tier;
const zoom = FOLLOW_ZOOM_TIERS[tier].zoom;
```

```tsx
// After — additive boost, inert unless a real upcoming maneuver is tracked.
const tier = zoomTierForSpeed(effectiveSpeedMps(...), followZoomTierRef.current);
followZoomTierRef.current = tier;
let zoom = FOLLOW_ZOOM_TIERS[tier].zoom;
if (currentNavStep && currentNavStep.distanceToManeuverMeters <= MANEUVER_ZOOM_THRESHOLD_M) {
  zoom = Math.min(zoom + MANEUVER_ZOOM_BOOST, MAX_MANEUVER_ZOOM);
}
```

## 8. Rollback plan

`git-revert-safe` — the camera-zoom branch is dead code today (guard can't be true while the upstream flag is off), and the banner render is conditional on the same always-empty-today state. Setting `driver_turn_by_turn_enabled` back to `False` (already its default) fully disables every visible effect of this PR without touching this branch's code at all; a plain `git revert` is also safe on top of that since nothing outside this PR reads `navSteps`/`currentNavStep`.

## 9. Verification performed

- [x] New unit tests: `driver-app/__tests__/components/NavigationStepBanner.test.tsx` — 7/7 passing (pure-function distance/icon mapping + component render + accessibility + non-interactivity).
- [x] `npx tsc --noEmit` — clean, no new type errors.
- [x] Full driver-app suite re-run: `yarn test --ci --forceExit` — **147 suites / 1662 tests, all passing**, including `driverDashboardScreen.test.tsx` (real coverage of the touched follow-camera effect) — this is the production-equivalent test command this repo's CI (`driver-app-test` in `ci.yml`) actually runs (`yarn test --ci --coverage --forceExit --reporters=default`).
- [x] Blast-radius grep performed: `FOLLOW_ZOOM_TIERS` has exactly one consuming effect (this file); no Android Auto or other screen duplicates the follow-camera logic.
- [ ] `npm run build` / production Expo build — **not run in this environment**; this repo's driver-app CI job itself only runs `tsc --noEmit` + `yarn test` (confirmed by reading `ci.yml`'s `driver-app-test` job), not a production bundle build, so this matches what CI actually gates on. No EAS build was triggered by this commit (no `[build]` in the message — that's deferred to the final PR in the Phase 1 sequence per the proposal).

**What was NOT verified:** on-device behavior — no physical device or simulator run in this environment, so the banner's real-world legibility, the camera zoom transition's visual smoothness, and GPS-noise behavior near a real intersection are reasoned about (from the existing `snapToRoute`/follow-camera precedent) rather than observed. driver-app has no automated visual-regression tooling at all (per CLAUDE.md), so this "reasoned about, not screenshotted" disclosure is mandatory here. The `react-hooks/purity` ESLint rule (from `eslint-config-expo`, a bleeding-edge React Compiler check) flags 2 of the new lines as "calling setState synchronously within an effect can trigger cascading renders" — confirmed this rule is not part of this repo's CI gate for driver-app (`ci.yml`'s `driver-app-test` job runs only `tsc --noEmit` + `yarn test`, no `eslint` step), and the file already carries one pre-existing instance of the same rule family (`Date.now()` in the speed chip, line ~1670, predating this PR) — left as-is rather than restructuring working code to satisfy an unenforced, overzealous advisory rule.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (flag stays off by default; the new branches are provably dead code until it flips)
- [x] Blast radius is stated, not assumed (grepped `FOLLOW_ZOOM_TIERS`'s one consumer; existing test coverage on the touched effect confirmed green)
- [x] No silent behavior change to an already-shipped flow (§5: zero visible effect while the flag is off, which it is by default; the shared follow-camera effect's default output is unchanged, verified by reading the added guard)
