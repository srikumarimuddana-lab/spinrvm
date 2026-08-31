# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude (session on behalf of vikas@ngitservices.com) |
| Surface(s) | shared, driver-app, rider-app |
| Domain (Sentry tag) | drivers / rides |
| PR / commit link | (branch `claude/map-vehicle-tracking-animation-3e85y2`, commits `52d5052`, `11f5d26`, `510d35b`) |
| Related issue or gap ID | Round 9 — implements the approved CR-2026-030 (#4722) and the state-colored pulsing ring the user asked for as a follow-up to round 8 |

This round bundles two independent pieces of work that both landed together because they were both explicitly requested in the same message: (1) the approved fix for a pre-existing CI gate, and (2) the deferred round-8 feature.

## 1. Issue / gap identified

1. **CR-2026-030**: `rider-app-test` was red on `main` — 5 assertions in `rideDetailsScreen.test.tsx` expected a route-quality caption ("Actual route · <quality>", "Actual route unavailable", "Planned route · Planned route preview", "Actual route processing") and an "Imported from the previous app — no GPS was recorded" disclaimer that no longer exist on screen.
2. **State-colored pulsing ring**: round 8's PR left this as an explicitly deferred follow-up (a state-colored pulsing ring was considered but not built, because it needed either a perf tradeoff on rider-app's multi-marker screen or new call-site scoping). The user asked to build it now.

## 2. Root cause

1. Commit `327d3e8` (2026-08-30, earlier same day) deliberately removed the on-screen route-quality caption and the disclaimer text folded into it — an owner directive that this diagnostic (GPS coverage %, reconstruction status) belongs on the admin panel only, not the rider screen. That commit updated its sibling contract test (`ride-details-route.test.tsx`) but missed `rideDetailsScreen.test.tsx`, which still asserted the removed text was present.
2. N/A — this is a new feature, not a bug.

## 3. Fix / remediation

1. Updated the 5 stale assertions in `rideDetailsScreen.test.tsx`: the "Imported" badge test no longer expects the removed disclaimer sentence; the four caption-text assertions were replaced with one test that pins the caption's absence across every geometry-status branch that used to drive it (matching the pattern in `ride-details-route.test.tsx`'s "keeps route-provenance diagnostics off the rider screen"). The underlying map/polyline rendering assertions (Polyline count, `fitToCoordinates` calls) are unchanged — that logic itself wasn't touched by `327d3e8` and isn't touched here.
2. Added an optional `ring?: { color: string; pulsing: boolean }` prop to `CarMarker` (both the `shared/` copy and driver-app's near-duplicate): a static low-opacity circle behind the car icon, plus — only when `pulsing` — a looping scale+fade "radar" pulse. `CarMarker` stays state- and theme-agnostic by design; the caller resolves `color` from its own `useTheme()` and decides `pulsing` from its own ride-state model. Wired for driver-app's own vehicle marker and rider-app's three single-assigned-driver screens (`driver-arriving.tsx`, `driver-arrived.tsx`, `ride-in-progress.tsx`), using colors that mirror CLAUDE.md's insurance-period grouping (Period 1 idle/available → static success-green; Period 2 en route to pickup → pulsing warning-amber; Period 3 trip in progress → static primary). Deliberately **not** wired on `ride-options.tsx`, which renders one `CarMarker` per nearby driver via `.map()`.

## 4. Risk & impact on existing functionality

- **CR-2026-030 fix**: isolated to one test file; no product code changed. Full rider-app suite confirms no other test relies on the removed caption text.
- **Ring feature — blast radius**: `CarMarker` is shared across driver-app's map screen and 5 rider-app screens. The `ring` prop is optional and additive — every existing call site that doesn't pass it renders byte-for-byte the same tree as before (the new outer wrapper `View` collapses to the exact same size as the old one when `ring` is absent; confirmed via diff review, not just reasoning, since the same code path was already exercised by the pre-existing CarMarker tests that don't pass `ring`).
- **Perf — the actual risk this design had to avoid**: a continuous pulse forces Android's `tracksViewChanges` to stay `true` for as long as it loops. Scoped by construction to single-marker screens (driver's own vehicle; one assigned driver per rider screen) — never wired on `ride-options.tsx`'s multi-marker nearby-drivers map. Within the single-marker screens, idle (which can last a driver's whole online shift) deliberately renders a **static** ring, not a pulsing one, specifically to avoid hours of continuous re-snapshotting; only the bounded-duration "en route to pickup" phase pulses.
- `React.memo`'s `_propsAreEqual` was extended to compare `ring.color`/`ring.pulsing` by value (not reference), since callers pass a fresh object literal each render — without this, memoization would have been silently defeated for every ring-using call site (not a functional bug, but would have undone the round-8 perf-conscious memo entirely for these 4 call sites).
- No change to ride state, dispatch, money, or any backend path — purely client-side, and the ring is visual-only (no new data dependency; color/pulsing are derived from state already available at each call site).

## 5. User-experience effect

- **Rider- and driver-facing**, visible mid-session — a driver's own marker now shows a colored ring reflecting online/en-route/on-trip status; a rider sees the same visual language around their assigned driver's marker on 3 of their ride-tracking screens.
- Not feature-flagged: additive, visual-only, no functional change to marker position/rotation/tracking; consistent with round 7 (hud collapse) and round 8 (mount bounce) both shipping un-flagged for the same reasons.
- CR-2026-030 fix has no user-facing effect (test-only).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/__tests__/rideDetailsScreen.test.tsx` | Fixed 5 stale assertions to match the intentional `327d3e8` removal | CR-2026-030 |
| `shared/components/CarMarker.tsx` | Added `ring` prop, static+pulsing ring rendering, `tracksViewChanges` forcing while pulsing, `_propsAreEqual` value comparison | Ring feature (rider-app + any future shared consumer) |
| `driver-app/components/CarMarker.tsx` | Same, mirrored into driver-app's own near-duplicate copy | Ring feature (driver-app) |
| `driver-app/__tests__/components/CarMarker.test.tsx` | 3 new tests: static ring, pulsing loop through several cycles + clean unmount, toggling pulsing on/off via rerender | Test coverage |
| `driver-app/app/driver/(tabs)/index.tsx` | Computed `ownMarkerRing` from `isOnline`/`rideState`, wired onto the driver's own `CarMarker` | Ring feature call site (driver-app) |
| `rider-app/app/driver-arriving.tsx`, `driver-arrived.tsx` | Wired `ring={{ color: colors.warning, pulsing: true }}` | Ring feature call site (Period 2) |
| `rider-app/app/ride-in-progress.tsx` | Wired `ring={{ color: colors.primary, pulsing: false }}` | Ring feature call site (Period 3) |

## 7. Before / after

**CR-2026-030** (`rideDetailsScreen.test.tsx`):
```tsx
// Before
expect(allText(r)).toContain('Imported from the previous app — no GPS was recorded for this ride');
// ...four separate tests each asserting a now-removed caption string
```
```tsx
// After
// (disclaimer assertion removed; badge-presence assertion kept)
// One test pins the caption's absence across every geometry-status branch:
const forbidden = ['Actual route ·', 'Actual route unavailable', 'Planned route · Planned route preview', 'Actual route processing'];
forbidden.forEach((s) => expect(allText(r)).not.toContain(s));
```

**Ring feature** (`CarMarker.tsx`, both copies):
```tsx
// Before
<Animated.View style={mountAnimatedStyle}>
  <Image source={...} ... />
</Animated.View>
```
```tsx
// After
<View style={{ width: outerSize, height: outerSize, alignItems: 'center', justifyContent: 'center' }}>
  {ring && (
    <View pointerEvents="none" style={{ position: 'absolute', width: ringMaxDiameter, height: ringMaxDiameter, ... }}>
      <View style={staticRingStyle} />
      {ring.pulsing && <Animated.View style={pulseRingAnimatedStyle} />}
    </View>
  )}
  <Animated.View style={mountAnimatedStyle}>
    <Image source={...} ... />
  </Animated.View>
</View>
```

**Driver-app call site** (`(tabs)/index.tsx`):
```tsx
// Before
<CarMarker coordinate={...} heading={...} isOnline={isOnline} variant={markerVariant} imageUri={markerImageUri} routeCoordinates={...} />
```
```tsx
// After
<CarMarker coordinate={...} heading={...} isOnline={isOnline} variant={markerVariant} imageUri={markerImageUri} routeCoordinates={...} ring={ownMarkerRing} />
```

## 8. Rollback plan

`git revert` of the three commits is a complete rollback for both pieces of work — no server state, no migration, no data touched by either. The CR-2026-030 fix and the ring feature are in separate commits, so either can be reverted independently if only one turns out to need it.

## 9. Verification performed

- [x] Automated tests: driver-app full suite **119 suites / 1337 tests, all passing** (includes 3 new ring tests + 2 existing round-8 tests, 5/5 in `CarMarker.test.tsx`). Rider-app full suite **134 suites / 1894 tests, all passing** (up from 133/134, 1891/1896 pre-CR-fix — confirms CR-2026-030 is actually resolved, not just locally reproduced).
- [x] `npx tsc --noEmit` clean on both apps, run after each of the three commits' changes.
- [x] `npx eslint` clean (0 errors) on every touched file; only pre-existing-pattern warnings (`no-require-imports` in `jest.mock()` factories) remain, matching established codebase style.
- [x] Blast-radius grep: confirmed `ring=` is not present on `ride-options.tsx` (the one multi-marker rider-app screen using `CarMarker`); confirmed no other `CarMarker` call site across either app needed updating (optional prop, additive).
- [x] Diffed `shared/components/CarMarker.tsx` against `driver-app/components/CarMarker.tsx` after the ring changes — confirmed the only differences remaining are the pre-existing intentional ones (expo-image vs RN Image, asset paths, `isOnline` compat prop), i.e. the ring feature itself is byte-identical logic in both copies.
- [ ] Manual repro on staging/device — not performed; no device/emulator in this environment. The ring's visual feel ("playful" pulse timing/scale) was reasoned about, not screenshotted — neither app has visual-regression tooling (standing gap, `ACTION_ITEMS.md`).
- [x] Reviewed against CLAUDE.md conventions: insurance-period color grouping used deliberately (not as an insurance-classification change — this is UI color only, no `driver_insurance_periods` writes or period-transition logic touched); no state-machine, money, RLS, or PIPEDA surface touched otherwise.

## 10. What was NOT verified

- No on-device verification of the ring's actual visual quality (pulse smoothness, color contrast in light/dark map styles, legibility at `size={44}` on rider-app vs `size={40}` default on driver-app) — this ships for the user's next on-device test pass, same as every prior round in this series.
- Battery/perf impact of the Period-2 pulsing ring was reasoned about (bounded duration, single marker, native-driven scale/opacity) but not measured on a real device.
- The user's separately-reported "live app navigation — still there are issues" was **not investigated or addressed in this round** — the specifics (map/course-up camera vs in-app screen navigation vs turn-by-turn directions, all three flagged as relevant) were not pinned down to concrete, reproducible symptoms before this round shipped. Flagged as an explicit open item for the next round.
