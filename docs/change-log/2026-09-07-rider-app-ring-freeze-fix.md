# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-07 |
| Author | Claude Code (session on behalf of ittalenthire.ca@gmail.com) |
| Surface(s) | rider-app (shared component consumed by rider-app only) |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/vehicle-icon-movement-animation-8tys8o`, follow-up commit to srikumarimuddana-lab/spinrvm#5086 |
| Related issue or gap ID | Third parity gap found while auditing driver-app for the reverse of the gap fixed in `docs/change-log/2026-09-07-rider-app-marker-parity-fix.md`; root fix originally shipped driver-app-only in `docs/change-log/2026-09-05-driver-car-marker-ring-freeze-fix.md` |

## 1. Issue / gap identified

`driver-app/components/CarMarker.tsx` was fixed on 2026-09-05 for a live-testing report: "only a green circle, no car icon" — a frozen Android marker snapshot showing just the state-colored presence ring, never the car. `shared/components/CarMarker.tsx` (rider-app's copy of the same component) never got this fix, and a 2026-09-07 audit confirmed rider-app's own screens can hit the identical race: `driver-arriving.tsx`, `driver-arrived.tsx`, and `ride-in-progress.tsx` all mount `<CarMarker ring={...} />` with the `ring` prop already non-null on the very first render (e.g. a fresh mount when navigating between ride-stage screens, or reopening the app mid-ride).

## 2. Root cause

Android's `react-native-maps` `Marker` snapshots its child view to a bitmap when `tracksViewChanges` is true, then freezes that bitmap once it flips false — and a frozen snapshot ignores every subsequent prop change. On a fresh mount, the ring (a plain colored `View`) paints instantly while the car icon (`expo-image`) decodes asynchronously. The existing settle logic (`handleImageLoaded` + a 5s hard-cap timer) only re-arms `tracksViewChanges` around the *image's own* load event — it has no way to know a *ring* appeared, changed color, or disappeared, so if the snapshot froze before/without the car ever winning that race, it stays frozen (ring-only) through every later prop change, including the ring later disappearing.

This is the same root cause driver-app hit via its own `mapKey`-forced remount on offline→online; rider-app hits the identical race via ordinary screen-to-screen navigation instead.

## 3. Fix / remediation

Ported driver-app's 2026-09-05 fix into `shared/components/CarMarker.tsx` exactly: track whether the car image has ever decoded (`hasLoadedImageRef`), and re-arm `tracksViewChanges` on any change to the ring's own identity (`ringChangeKey = ring ? \`${ring.color}:${ring.pulsing}\` : null`) — appearance, color change, or disappearance. If the image already loaded, the fresh snapshot re-freezes on the same 350ms schedule as normal; if not, `tracksViewChanges` is left true so the eventual real `onLoad` (or the existing 5s hard cap) does the freezing instead, never before the car has had a chance to actually render.

No new utility logic — this is a direct, unmodified port of driver-app's own already-proven effect.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface (rider-app), same 5 call sites as the two prior ports** (`ride-options.tsx`, `ride-in-progress.tsx`, `driver-arrived.tsx`, `driver-arriving.tsx`, `(tabs)/index.tsx`) — only 3 of the 5 (`driver-arriving.tsx`, `driver-arrived.tsx`, `ride-in-progress.tsx`) actually pass the `ring` prop per the component's own doc comment restricting `ring` to single-marker screens; the effect is a no-op (`ringChangeKey` stays `null`) on the other two.
- **No change to `driver-app`** — this only edits the rider-app copy; driver-app's own copy (already running this fix since 2026-09-05) is untouched.
- **No prop/type/export change** — purely internal (two new refs + one new effect), same as the two prior ports in this PR.
- **Extra re-render risk**: the new effect calls `setTracksViewChanges(true)` on every ring-identity change, which is exactly the same state the existing `handleImageLoaded` path already sets — no new interaction with the playback ticker, rotation animation, or position state.

## 5. User-experience effect

- **Rider-facing.** Fixes a case where a rider could see only a colored ring on the map with no car icon at all — the more severe failure mode of this whole audit (the two earlier fixes were smoothness/jitter; this one is a fully missing icon).
- **Visible mid-session**: yes, in the sense that it prevents a bug that would otherwise persist for the rest of that screen's lifetime once triggered (a frozen native snapshot never self-corrects without this fix).
- **No copy or notification change.**

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/components/CarMarker.tsx` | Added `hasLoadedImageRef`; added a `ringChangeKey`-keyed effect that re-arms `tracksViewChanges` on any ring identity change | Port driver-app's 2026-09-05 ring-freeze fix, closing the third parity gap found auditing rider-app against driver-app |
| `rider-app/__tests__/carMarkerPositionChange.test.tsx` | Added a new `describe` block (4 tests) ported from `driver-app/__tests__/components/CarMarker.test.tsx`'s "Android ring-change re-arms the frozen snapshot" suite | Give this fix actual regression coverage in rider-app rather than relying solely on driver-app's own test suite for shared logic now duplicated into a second component file |
| `docs/change-log/2026-09-07-rider-app-ring-freeze-fix.md` | New Change Impact Log entry (this file) | Required for any behavior change to a live-tested surface per `CLAUDE.md` |

## 7. Before / after

```tsx
// Before
const [tracksViewChanges, setTracksViewChanges] = useState(true);
const settleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
useEffect(() => {
    const cap = setTimeout(() => setTracksViewChanges(false), 5000);
    return () => { clearTimeout(cap); if (settleTimerRef.current) clearTimeout(settleTimerRef.current); };
}, []);
const handleImageLoaded = () => {
    setTracksViewChanges(true);
    if (settleTimerRef.current) clearTimeout(settleTimerRef.current);
    settleTimerRef.current = setTimeout(() => setTracksViewChanges(false), 350);
};
// No re-arm on ring change at all — a frozen ring-only snapshot stays frozen.
```

```tsx
// After
const hasLoadedImageRef = useRef(false);
// ...
const handleImageLoaded = () => {
    hasLoadedImageRef.current = true;
    setTracksViewChanges(true);
    // ...unchanged...
};

const ringChangeKey = ring ? `${ring.color}:${ring.pulsing}` : null;
const prevRingChangeKeyRef = useRef<string | null>(null);
useEffect(() => {
    const changed = prevRingChangeKeyRef.current !== ringChangeKey;
    prevRingChangeKeyRef.current = ringChangeKey;
    if (!changed) return;
    setTracksViewChanges(true);
    if (settleTimerRef.current) clearTimeout(settleTimerRef.current);
    if (hasLoadedImageRef.current) {
        settleTimerRef.current = setTimeout(() => setTracksViewChanges(false), 350);
    }
    // else: leave tracksViewChanges true until the image actually loads or the hard cap fires.
}, [ringChangeKey]);
```

## 8. Rollback plan

Same as the two prior ports in this PR: plain `git revert` of this commit. No persisted/server state, no feature flag, no migration — every new ref and the effect are component-local and re-initialize on mount. Reverting restores the pre-fix behavior (the ring-freeze race remains possible, as it already was before this change).

## 9. Verification performed

- [x] Automated tests run — ran `npx jest __tests__/carMarkerPositionChange.test.tsx` in `rider-app/`: **1 suite passed, 6/6 tests passed** (2 pre-existing + 4 new, ported from driver-app's own proven suite). Ran the full affected group again (`vehicleTracking.test.ts`, `markerPlayback.test.ts`, `carMarkerPositionChange.test.tsx`): **3 suites passed, 50/50 tests passed**.
- [x] `npx tsc --noEmit -p tsconfig.json` run — zero errors referencing `CarMarker.tsx`.
- [x] Blast-radius grep performed — same 5 call sites as the prior two ports in this PR (§4); confirmed via the earlier audit which of the 5 actually pass `ring`.
- [x] Reviewed against relevant `CLAUDE.md` convention — frontend rendering path, not state machine/money/RLS/PIPEDA; no such convention applies directly.
- [ ] Manual repro steps followed in staging / on-device — **NOT performed.** No device/simulator was available in this session.
- [ ] Feature-flagged — **not flagged**, same justification as the prior two ports: pure client-rendering fix, ported from logic proven live in driver-app since 2026-09-05, trivially revertible.

## What was NOT verified

- **No on-device or simulator confirmation that the ring-freeze bug actually reproduces or is actually fixed on a real Android device** — verified by code reading, the ported test suite (which exercises the exact `tracksViewChanges` state transitions, not a real native snapshot), and driver-app's own live-tested history of the identical fix.
- **rider-app has no automated visual-regression tooling** — same disclosure as the prior two ports in this PR; this remains "reasoned about, not screenshotted."
- **iOS is unaffected by design** (this is an Android-native-snapshot-specific bug; `tracksViewChanges` has no equivalent freeze behavior on iOS's `Marker.Animated` path) but this was not independently re-confirmed on iOS hardware, only reasoned from the existing code path split.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert; no persisted/server state touched)
- [x] Blast radius is stated, not assumed (same 5 rider-app screens as the prior two ports, 3 of which actually use `ring`)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 above)
