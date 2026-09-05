# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | vikas@ngitservices.com |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (added at PR open) |
| Related issue or gap ID | Live-testing screenshots (2026-09-05): Android car marker shows only the green presence ring, no car icon, both immediately after going online and later while offline at the same spot |

## 1. Issue / gap identified

On Android, the driver's own car marker sometimes renders as just the state-colored presence ring (a plain colored circle) with **no car icon inside it** — reproduced by the user going online while stationary, and persisting even after going back offline at the same location.

## 2. Root cause

`CarMarker.tsx`'s Android path uses `tracksViewChanges` to snapshot the marker once, then freezes it (`tracksViewChanges=false`) for performance — either 350ms after the car `<Image>`'s `onLoad` fires, or via an unconditional 5-second hard cap if `onLoad` never fires. Once frozen, the native marker bitmap **ignores all further prop changes** — that's the entire point of freezing.

`index.tsx` (lines ~260–285) already documents forcing a full `MapView` remount (`key={mapKey}`) on every offline→online transition, as the fix for a *different*, earlier bug ("car icon never reappears after going offline then online again"). That remount restarts `CarMarker` from scratch — with the online-idle green ring (`ring={{color: success, pulsing: false}}`) present from the very first render. This creates a race: the ring is a plain `View` that paints essentially instantly, while the car `<Image>` still needs to decode. If the freeze (hard cap, or an unlucky early snapshot) happens before the image wins that race, the frozen bitmap bakes in as "ring only, no car" — and being frozen, it then **stays that way indefinitely**, including across the driver going offline again (`ring` prop returning to `null` in React has no effect on an already-frozen native bitmap), which is exactly what the second screenshot showed.

The existing self-heal path — `effectiveTracksViewChanges = ring?.pulsing ? true : tracksViewChanges` — only re-arms the snapshot while the ring is *pulsing* (e.g. an active ride offer). The static idle-green ring (the very first ring a driver ever sees on going online) never triggers it.

## 3. Fix / remediation

Added a new effect that re-arms `tracksViewChanges` on **any change to the ring's identity** (color or presence, not just a transition into pulsing) — covering ring appearing (offline→online), ring color changing (idle→in-trip), and ring disappearing (online→offline). To avoid introducing a *new* race, the re-arm only schedules an immediate 350ms re-freeze if the car image has already loaded at least once (tracked via a new `hasLoadedImageRef`); if the image hasn't loaded yet, `tracksViewChanges` is left `true` and the existing `handleImageLoaded`/5s-hard-cap logic is left to freeze it once the image is actually ready — never freezing ahead of the image on this new path either.

## 4. Risk & impact on existing functionality

- Blast radius: isolated to `CarMarker.tsx`'s own internal `tracksViewChanges` state machine. No prop or exported API changed; `shared/components/CarMarker.tsx` (rider-app's marker) is a separate, unmodified file.
- Does not touch the `index.tsx` `mapKey`-remount fix this bug's mechanism depends on — that fix solves a real, different problem and is left as-is.
- The new effect is a no-op on the most common case (ring identity unchanged across a re-render) — verified by a dedicated test ("no redundant re-arm on same ring identity, new object reference").
- Slight, bounded extra cost: an actual ring change now triggers one additional 350ms `tracksViewChanges=true` window (one more native snapshot) — negligible, and only on ring transitions (going online/offline, ride-state changes), not on every GPS tick.

## 5. User-experience effect

Driver-facing only: closes the gap where the driver's own car icon could vanish (leaving only the colored ring) after going online, or after going offline again following that. No new UI, no copy.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/CarMarker.tsx` | Added `hasLoadedImageRef`; added an effect that re-arms `tracksViewChanges` on any `ring` color/presence change, gated on whether the image has already loaded | Close the self-heal gap for static (non-pulsing) ring transitions |
| `driver-app/__tests__/components/CarMarker.test.tsx` | 4 new tests: re-arm on ring appearing, re-arm on ring disappearing, no premature freeze before the image has ever loaded, no redundant re-arm on an unchanged ring | Cover the exact mechanism being fixed, not just its absence of a crash |

## 7. Before / after

```tsx
// Before
const handleImageLoaded = () => {
    setTracksViewChanges(true);
    if (settleTimerRef.current) clearTimeout(settleTimerRef.current);
    settleTimerRef.current = setTimeout(() => setTracksViewChanges(false), 350);
};
// (no re-arm on ring change at all — only ring.pulsing forced tracksViewChanges via
//  effectiveTracksViewChanges = ring?.pulsing ? true : tracksViewChanges)
```

```tsx
// After
const hasLoadedImageRef = useRef(false);
const handleImageLoaded = () => {
    hasLoadedImageRef.current = true;
    setTracksViewChanges(true);
    if (settleTimerRef.current) clearTimeout(settleTimerRef.current);
    settleTimerRef.current = setTimeout(() => setTracksViewChanges(false), 350);
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
    // else: leave true — handleImageLoaded or the 5s hard cap freezes it correctly instead.
}, [ringChangeKey]);
```

## 8. Rollback plan

No feature flag — a bounded, well-tested internal-state fix with no API change. Rollback is a plain `git revert`; no live data, ride state, or money path touched.

## 9. Verification performed

- [x] `npx jest driver-app/__tests__/components/CarMarker.test.tsx` — 15/15 passed (11 pre-existing unchanged + 4 new, targeting the exact re-arm mechanism via the mocked `Marker`'s `tracksViewChanges` prop).
- [x] `npx tsc --noEmit -p tsconfig.json` for the full driver-app project — clean, 0 errors.
- [x] Blast-radius grep/review: confirmed isolated to `CarMarker.tsx`'s internal state; rider-app's separate marker component untouched.
- [x] Reviewed against relevant `CLAUDE.md` conventions: surgical, no PIPEDA concern, no state-machine/money path.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert).
- [x] Blast radius is stated, not assumed.
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 completed).

## What was NOT verified

Not confirmed on a physical Android device — driver-app has no visual-regression tooling, and this fix targets a race condition whose exact timing (image decode speed vs. remount/freeze timing) is device- and network-dependent (especially relevant if the driver's vehicle type uses a custom admin-uploaded marker image fetched over the network rather than the bundled default, which was not something this session could determine for the reporting user's account). The tests confirm the *mechanism* is now correct (re-arm fires on every ring transition, gated correctly on image-load state) via the mocked `tracksViewChanges` prop; they cannot confirm the original screenshots' exact device/build actually hits this code path rather than some other cause. Recommend the user re-test the same reproduction (go online while stationary, then offline) on the next build and report back whether the car icon now stays visible.
