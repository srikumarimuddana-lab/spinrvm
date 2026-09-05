# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | vikas@ngitservices.com |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (added at PR open) |
| Related issue or gap ID | Live-testing bug report 2026-09-05: "the zoom out whenever there is turn or speed slow down is not smooth" |

## 1. Issue / gap identified

The driver dashboard's course-up follow-camera (`index.tsx`, the `useEffect` keyed on `[location, rideState, mapRef, courseUp]`) calls `mapRef.current.animateCamera(...)` with a 700ms duration on every `location` state change. Live-testing reported the camera zoom/rotation as "not smooth" specifically on turns and speed slowdowns.

## 2. Root cause

The effect has no rate limiting: it fires a brand-new 700ms `animateCamera` call on every GPS fix, however often those arrive. Android's `watchPositionAsync` is interval-gated (`LOCATION_CONFIGS` in `useDriverDashboard.ts` — e.g. `timeInterval: 2000` while `navigating_to_pickup`/`trip_in_progress`), but iOS's `distanceFilter`-based delivery has no time floor — at driving speed a 5m `distanceInterval` can be crossed well under 700ms apart. When a new `animateCamera` call lands before the prior one's native interpolation finishes, the native map cancels the in-flight animation and restarts from wherever it happened to be, which reads as jerky zoom/rotation exactly on turns and slowdowns (where heading/zoom-tier are both changing).

## 3. Fix / remediation

Added a leading+trailing throttle around the `animateCamera` call, capped at one call per `CAMERA_ANIM_MS` (700ms, matching the animation's own duration): if enough time has passed since the last camera update, apply immediately; otherwise schedule exactly one trailing call carrying the latest computed center/zoom/heading, anchored on the last actual update time so repeated fast-arriving ticks converge on the same fire time rather than pushing it back indefinitely. No new dependency — implemented with two `useRef`s in the existing effect.

## 4. Risk & impact on existing functionality

- Blast radius: isolated to this one `useEffect` in `driver-app/app/driver/(tabs)/index.tsx`. Grepped every `animateCamera` call site in `driver-app/`: the only other call sites are (a) the same file's one-off heading-only animation on the course-up/north-up toggle button (line ~1376, unrelated call site, untouched), and (b) `lib/androidAuto/carSurface.tsx`'s independent Android Auto car-projection camera logic (separate component, separate ref, not touched).
- No other code reads `lastCameraUpdateRef`/`pendingCameraTimeoutRef` — both are new, local to this effect.
- Does not change what the camera does (same center/zoom/heading formula), only how often the underlying native call fires — so no behavior change beyond dropping the previously-thrashing intermediate camera calls.
- No ride-state, money, or dispatch path involved.

## 5. User-experience effect

Driver-facing only, visible only while online and driving (course-up follow camera active in `idle`/`navigating_to_pickup`/`trip_in_progress`). The change is a smoothness improvement to an already-shipped, always-on camera behavior — no new UI, no copy change. Visible mid-session to any driver currently online, since the follow camera runs continuously while driving.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/(tabs)/index.tsx` | Added `CAMERA_ANIM_MS` throttle (leading + trailing) around the follow-camera's `animateCamera` call | Stop overlapping `animateCamera` calls from cancelling each other's in-flight interpolation on fast-arriving GPS fixes |

## 7. Before / after

```tsx
// Before
mapRef.current.animateCamera?.(
  { center, zoom, heading: mapHeading },
  { duration: 700 },
);
```

```tsx
// After
const applyCamera = () => {
  lastCameraUpdateRef.current = Date.now();
  pendingCameraTimeoutRef.current = null;
  mapRef.current?.animateCamera?.(
    { center, zoom, heading: mapHeading },
    { duration: CAMERA_ANIM_MS },
  );
};
const elapsed = Date.now() - lastCameraUpdateRef.current;
if (elapsed >= CAMERA_ANIM_MS) {
  applyCamera();
} else {
  pendingCameraTimeoutRef.current = setTimeout(applyCamera, CAMERA_ANIM_MS - elapsed);
}
```

## 8. Rollback plan

No feature flag — this is a pure client-side timing change with no server dependency and no data written. Rollback is a plain `git revert` of this commit (no live data, no ride state, no money path touched by this diff), followed by a normal app-store/EAS release cycle (mobile builds only ship on `[build]`-tagged commits per `CLAUDE.md`'s Deployment section, so this doesn't reach drivers until the next tagged build regardless).

## 9. Verification performed

- [x] `npx tsc --noEmit -p tsconfig.json` run for the full driver-app project — clean, 0 errors (a real compile, not just editor/dev-server feedback).
- [ ] Automated tests: none added — no existing test harness exercises `animateCamera` timing in this component (it's a `MapView` ref call, not a pure function), consistent with driver-app having no visual-regression tooling at all (per `CLAUDE.md`).
- [ ] Manual repro steps followed in staging — not performed this session (no staging device/build available in this environment).
- [x] Blast-radius grep performed: searched for every `animateCamera` call site in `driver-app/` (see §4).
- [x] Reviewed against relevant `CLAUDE.md` conventions: surgical change (touches only the one effect), no PIPEDA-relevant data involved (no GPS coordinates logged), no state-machine/money path touched.
- [ ] Feature-flagged: not flagged. Justification: pure timing/smoothness change to client-only camera rendering, no behavior change a driver could rely on, and mobile changes don't ship until an explicit `[build]`-tagged EAS release regardless — additive risk is low enough that a flag would add complexity without a corresponding safety benefit.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, no live-data coupling).
- [x] Blast radius is stated, not assumed (isolated to one effect; other call sites enumerated and confirmed unaffected).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 completed — smoothness-only, no new UI/copy).

## What was NOT verified

Not tested against a real device at driving speed in this session — no physical device or simulator with live GPS movement available in this remote environment. The throttle's correctness (leading-edge fire, trailing-edge convergence to a single fire time) was verified by hand-tracing the timing math in this log and by a clean `tsc` compile, not by an on-device repro of the original "not smooth" report. Recommend the user (or a driver on the next test build) re-run the same turn/slowdown scenario from the original report to confirm the perceived smoothness improvement before treating this as fully closed.
