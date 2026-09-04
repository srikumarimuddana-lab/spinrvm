# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude, at user request — "the north south compass icon is placed wrong it is current showing on the top left hand corner of the screen ... please design this properly user friendly without impeding vision" + "check other screens for similar compass/status bar overlay issues" |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/admin-portal-heatmaps-audit-gm8fbn` |
| Related issue or gap ID | Follow-up to the same-session speed-chip fix; found by re-investigating the compass report after initially being unable to locate a matching in-app element |

## 1. Issue / gap identified

The main driver dashboard (`app/driver/(tabs)/index.tsx`) showed an unexpected, misplaced compass icon near the top of the map. My first pass found no app-rendered element matching that description at that position and tentatively attributed it to the OS status bar — the user asked me to keep looking and check other screens for the same class of issue, which surfaced the real cause.

## 2. Root cause

`react-native-maps`' `showsCompass` prop was never explicitly set anywhere in this codebase (confirmed by grep — zero matches across driver-app and rider-app). Left unset, it defaults to the native SDK's own adaptive behavior: Google Maps (Android) and Apple MapKit (iOS) automatically show their own built-in compass button whenever the map camera's bearing rotates away from north — and the dashboard's course-up follow-camera (`animateCamera({ heading })`, driven by the driver's live GPS heading) does exactly that continuously while online and moving.

The app already has its own deliberately-designed compass/course-up toggle in `components/dashboard/MapControls.tsx`, correctly positioned bottom-right alongside the zoom and my-location controls. The native SDK's own compass was a second, redundant control appearing automatically in the SDK's own default corner — on top of, not instead of, the app's real one. This is exactly the same class of gap the app had already closed for two other pieces of native map chrome on the same `MapView`: `showsUserLocation={false}` (replaced by the custom `CarMarker`) and `showsMyLocationButton={false}` (replaced by `MapControls`'s own button) — both already explicit; only the compass was missed.

## 3. Fix / remediation

- `app/driver/(tabs)/index.tsx`: added `showsCompass={false}` to the main dashboard's `MapView`, alongside the existing `showsUserLocation={false}`/`showsMyLocationButton={false}` — same pattern, same reasoning, closing the one native control that wasn't yet explicitly disabled.
- `app/driver/ride-detail.tsx`: added `showsCompass={false}` defensively. This screen's `MapView` already sets `rotateEnabled={false}` (a static route-preview map, bearing always 0), so the native compass was never actually visible there — but relying on the SDK's adaptive-hide behavior instead of being explicit left it one future code change (e.g. someone adding rotation later) away from silently reintroducing the same bug.
- Checked every other `MapView`-rendering screen in both apps: driver-app's `lib/androidAuto/carSurface.tsx` (Android Auto head-unit rendering — a different physical display and UX convention car head units commonly *do* want a compass for; left alone, no live report, out of scope for a phone-screen bug) and all 8 rider-app map screens (`ride-in-progress.tsx`, `driver-arriving.tsx`, `ride-completed.tsx`, `ride-details.tsx`, `pick-on-map.tsx`, `driver-arrived.tsx`, `ride-options.tsx`, `confirm-pickup.tsx`). None of the rider-app screens' `MapView`s ever rotate the camera bearing — every `heading` prop found there belongs to `CarMarker` (rotating a driver's car *icon*, not the map itself), and the two screens with a `rotateEnabled` prop already set it to `false`. So none of them can currently show the native compass regardless of `showsCompass` — a real but purely latent/defensive opportunity, not an active bug, and not touched here since no user report exists for that surface and 8 files is real scope in an app the user didn't ask about.

## 4. Risk & impact on existing functionality

- **Blast radius**: 2 files, 1 prop each, on the exact `MapView` element already carrying the same explicit-native-chrome-disable pattern for two other controls. No other prop, no map data, no rotation/follow-camera logic touched.
- `showsCompass={false}` only ever hides the SDK's own auto-appearing button — it has no effect on the map's actual bearing/rotation behavior, on `MapControls`'s own compass toggle, or on any other overlay. The app's real, deliberately-designed course-up control is completely unaffected.
- Grepped for other consumers/readers of `showsCompass` or any test asserting on its absence — none; this is a pure, additive, single-prop change with no other code depending on the native compass being present.

## 5. User-experience effect

Driver-facing. The redundant native compass no longer pops up over the map while driving in course-up mode — only the app's own deliberately-positioned, correctly-styled compass/course-up toggle (bottom-right, alongside zoom and my-location) remains. No functionality is lost: tapping that existing button still switches between course-up and north-up exactly as before.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/(tabs)/index.tsx` | Added `showsCompass={false}` to the dashboard `MapView` | Remove the redundant native compass appearing during course-up follow mode — the reported misplaced top-left icon |
| `driver-app/app/driver/ride-detail.tsx` | Added `showsCompass={false}` to the route-preview `MapView` | Defensive consistency — currently inert (map never rotates here) but closes the same latent gap |

## 7. Before / after

```tsx
// Before — index.tsx's MapView: two of three native controls explicitly
// disabled, compass left to the SDK's own adaptive default (visible)
showsUserLocation={false}
showsMyLocationButton={false}
onRegionChange={(region) => { ... }}

// After — all three explicit, matching the same "disable native chrome,
// use our own" pattern throughout
showsUserLocation={false}
showsMyLocationButton={false}
showsCompass={false}
onRegionChange={(region) => { ... }}
```

## 8. Rollback plan

Plain `git revert` — no data, no migration, no API change. A single boolean prop with no other code depending on it; reverting simply lets the native compass reappear.

## 9. Verification performed

- [x] Grepped the entire repo (driver-app + rider-app) for `showsCompass` — confirmed it was never set anywhere before this change.
- [x] Read `react-native-maps`' own `MapView.tsx` prop documentation to confirm the exact adaptive behavior ("visible only if the map is not pointing north") that explains why the icon appears specifically during course-up driving, not at rest.
- [x] Confirmed `index.tsx`'s course-up follow camera actually animates `heading` (`animateCamera({ heading }, ...)`), i.e. the map's bearing genuinely does leave north during normal use — not a theoretical trigger.
- [x] Found and matched the existing, already-established `showsUserLocation={false}`/`showsMyLocationButton={false}` precedent on the exact same `MapView`, confirming this is the same intended pattern the original developers used elsewhere, not a new one I invented.
- [x] Enumerated every `<MapView>`-rendering file in both driver-app and rider-app (4 total: `index.tsx`, `ride-detail.tsx`, `carSurface.tsx` in driver-app; 8 screens in rider-app) and checked each for actual camera-bearing rotation before deciding whether the fix applied — confirmed rider-app's `heading` usages are all on `CarMarker` (icon rotation), not the map camera, so none of those screens are live-affected.
- [x] `tsc --noEmit` — clean.
- [x] `expo lint` (full `app/` + `components/` tree) — 0 errors/warnings in either changed file; the 8 pre-existing errors reported are all in `app/driver/(tabs)/profile.tsx`, untouched by this change.

## What was NOT verified

- **No live device reproduction** — this sandbox cannot run the driver-app on a real device to confirm the native compass actually appeared before this fix or is actually gone after it. The diagnosis is grounded in `react-native-maps`' own documented adaptive-compass behavior and confirmed live camera-rotation code, not an observed screen recording.
- **Could not run the driver-app Jest suite** (same pre-existing, unrelated sandbox tooling failure noted in the earlier speed-chip fix's Change Impact Log) — not relevant here regardless, since neither changed file has an existing test exercising `MapView` props directly.
- **`lib/androidAuto/carSurface.tsx` (Android Auto head-unit) was deliberately left unexamined for this specific fix** beyond confirming it renders its own `MapView` — car head-unit UX conventions differ from a phone screen (a compass/heading indicator is often wanted there), there's no user report for that surface, and changing it without understanding that context's actual UX intent was judged out of scope.
- **The 8 rider-app map screens are unmodified** — confirmed the bug is currently inert there (no camera rotation), but did not add the same defensive `showsCompass={false}` hardening applied to `ride-detail.tsx`, since that would mean touching a different app with no live report, for a purely preventive reason, without being asked.
