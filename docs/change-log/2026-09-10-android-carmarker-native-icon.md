# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Cursor Grok 4.6 |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (uncommitted) |
| Related issue or gap ID | Live-testing: Android driver car marker visible on app open, gone after Drive → Profile → Drive |

## 1. Issue / gap identified

On Android, the driver's own car marker is visible when the Drive tab first mounts, then disappears after navigating to Profile and back. The map itself remains. iOS is unaffected.

## 2. Root cause

Android Google Maps does not keep custom Marker children as a live view. `react-native-maps` snapshots those children to a bitmap when `tracksViewChanges` is true, then this component froze that snapshot (`tracksViewChanges=false`) for perf. Expo Tabs keep the Drive React tree mounted but detach the native `MapView` on blur. Coming back recreates the native marker against a still-mounted `CarMarker` whose snapshot is already frozen and whose `ExpoImage` does not fire `onLoad` again — so the car stays a blank bitmap. iOS Apple Maps renders children live, so the same navigation cannot blank the icon.

This is the same interruption class previously patched with `mapKey` remounts (ride-end, offline→online) and snapshot re-arms (ring, image load). Tab blur was never in that list.

## 3. Fix / remediation

Android now uses the native `Marker.image` path (Google Maps `BitmapDescriptor`) for the car PNG — a GPU texture the map SDK owns, the same class of icon Uber/Lyft use. No custom-view snapshot, so tab detach cannot wipe it. The presence ring cannot sit as a child of that Marker (children force the snapshot path), so it is a sibling Marker at the same coordinate, animated in lockstep, with `tracksViewChanges` left on (one extra Marker, cheap). iOS is unchanged (child `ExpoImage` + view-transform rotation).

Bundled assets are already 44×44 @1x / 88 @2x / 132 @3x, matching the previous 40dp `Image` size, so the old "native image is far too large" reason no longer holds for the built-in cars.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface (driver-app `CarMarker`).** Callers: `app/driver/(tabs)/index.tsx` (own car) and `lib/androidAuto/carSurface.tsx` (no `ring`). rider-app uses a separate `shared/components/CarMarker.tsx` and is untouched.
- **Custom admin-uploaded `marker_image_url`:** native `image={{uri}}` renders at the PNG's pixel size, not the React `size` prop. Upload is PNG/WebP ≤500 KB with "tight crop" art direction, but there is **no pixel-dimension cap**. A huge custom upload can draw a giant car on Android. Bundled variants will not.
- **Presence ring** is a second native Marker. If tab-detach blanks custom views, the ring can disappear while the car stays — better than today's missing car. Ring keeps `tracksViewChanges` true to reduce that.
- No ride-state, money, or dispatch change. `size` is unused on the Android car bitmap; no caller passes `size`.
- Android Auto gets the same native icon (no ring).

## 5. User-experience effect

- **Driver-facing, Android only.** Car should remain after Profile → Drive. Ring may still be a custom view. iOS unchanged. Visible mid-session to a driver already on Drive.
- No copy change. Not feature-flagged: this is the Android marker draw path itself; a flag would still need a working native icon or we'd ship the bug.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/CarMarker.tsx` | Android: `Marker.image` for the car; sibling Marker for `ring`; animate both | Stop snapshot freeze on tab reattach |
| `driver-app/__tests__/components/CarMarker.test.tsx` | Native-icon + sibling-ring tests; drop obsolete snapshot re-arm cases | Cover the new path |
| `docs/change-log/2026-09-10-android-carmarker-native-icon.md` | This log | Live-tested driver map |

## 7. Before / after

```tsx
// Before — Android custom-view snapshot (tab return blanks a frozen bitmap)
<Marker tracksViewChanges={effectiveTracksViewChanges} rotation={androidRotation}>
  <View>…ring…<ExpoImage … /></View>
</Marker>
```

```tsx
// After — Android native icon; ring is a sibling if present
<>
  {ring ? <Marker tracksViewChanges coordinate={androidCoord}>{ringView}</Marker> : null}
  <Marker image={androidMarkerImage} rotation={androidRotation} tracksViewChanges={false} />
</>
```

## 8. Rollback plan

No live data, no flag. Redeploy/revert the `CarMarker.tsx` change. A `git revert` is sufficient.

## 9. Verification performed

- [x] `npx jest __tests__/components/CarMarker.test.tsx --no-coverage` — 26/26 passed
- [ ] Manual repro on a physical Android device (Drive → Profile → Drive)
- [x] Blast-radius grep: `CarMarker` in driver-app (`index.tsx`, `carSurface.tsx`); rider-app uses `shared/components/CarMarker.tsx` (untouched)
- [x] No state-machine / money / RLS / PIPEDA surface
- [x] Not feature-flagged (draw-path change; flag would not hide a broken snapshot)

## 10. What was NOT verified

- No physical Android device or EAS build in this session. rider-app/driver-app have no visual-regression tooling — appearance of native-icon size vs the old 40dp `Image` is reasoned from the 44×44 density assets, not screenshotted.
- Custom `marker_image_url` pixel size was not tested against a large admin upload.
- Android Auto hardware not re-tested (JS-only; same component, no `ring`).

---

## 11. Follow-up review — two findings fixed (2026-09-10, session `01Ro32rhyxmV2ws4MaPhPUSX`)

A review pass over this change found two issues. Both are fixed in the same working tree; the core design above is unchanged and was independently confirmed sound.

### Confirmed correct on review

- Bundled assets really are `44×44 / 88 / 132` — the oversized `256×527` files live only in `shared/assets/marker-src/` and are not bundled, so the old "native image is far too large" reason genuinely no longer applies.
- `size` is not passed by either caller (`app/driver/(tabs)/index.tsx:1064`, `lib/androidAuto/carSurface.tsx:615`), so ignoring it on the Android bitmap causes no visual change.
- Remote URIs **are** supported by `Marker.image` on Android in `react-native-maps@1.27.2` — `MapMarker.setImage()` routes `http(s)://`, `file://`, `asset://` and `data:` through Fresco (`MapMarker.java:414-426`). Custom admin markers are not broken by the switch.

### Finding 1 — CI blocker (fixed)

`yarn tsc --noEmit` reported **4 errors** in `__tests__/components/CarMarker.test.tsx`. §9 above ran `jest` but not `tsc`, so this was missed. `mobile-dep-check.yml`'s `driver-app-dep-check` job runs `yarn tsc --noEmit` with **no `continue-on-error`** on the TypeScript step (only the Expo dep audit above it is tolerated), so this would have hard-failed the PR.

| Line | Error | Cause | Fix applied |
|---|---|---|---|
| 503, 516 | TS2345 | `carAndRing`'s param typed `{ findAllByType: (t: unknown) => any[] }` does not accept a `ReactTestInstance` | widened to `(t: any)` at `:488` |
| 613, 615 | TS18048 | `car` / `ringMarker` come from `.find()` so they are `T \| undefined`; a preceding `expect(...).toBeTruthy()` does not narrow for TS | `car!.props` / `ringMarker!.props` |

`npx tsc --noEmit` now reports **0 errors** project-wide.

### Finding 2 — silent failure on Android (fixed)

The Android early return is at `CarMarker.tsx:840`; `onError={handleImageError}` is at `:932`, inside the **iOS-only** branch. So on Android the entire custom-image error path had become unreachable:

- a failed `marker_image_url` never set `imageFailed`, so it **never fell back to the bundled car** — the driver would have no visible vehicle for the rest of the session;
- the one-shot `captureException` at `:754-759` never fired, so the failure was invisible in production monitoring;
- `MAX_IMAGE_RETRIES` and `imageRetryTimerRef` were dead code on Android.

This is the class CLAUDE.md forbids outright — "never replace a failing call with a generic fallback path that hides the symptom". §4 above flagged the *sizing* risk of a large custom upload but not the *failure* path. `react-native-maps` exposes no JS error callback for `image`, so the check has to happen before the Marker sees the URI.

**Fix:** a new Android-only effect probes an `http(s)` `imageUri` with **react-native's** `Image.prefetch` — deliberately not `expo-image`'s, because on Android RN's goes through Fresco, the same pipeline `MapMarker.setImage()` uses. A pass therefore also warms the exact cache the marker is about to read; a failure routes into the **existing** `handleImageError`, so Android and iOS now share one fallback, one retry policy and one Sentry report rather than diverging.

Two deliberate narrowings, both commented at the call site:
- **`http(s)` only.** A `file://` / `asset://` / `data:` source is resolved locally by the SDK, and a prefetch miss there would be a false negative that hid a perfectly good icon behind the fallback.
- **Non-thenable return is skipped, not treated as failure.** A real Android build always has `Image.prefetch`; Jest's `react-native` Image mock does not. Throwing inside a passive effect would cost the whole marker, which is worse than the unprobed icon this guards.

### Files modified by this follow-up

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/CarMarker.tsx` | `Image as RNImage` import; new Android-only prefetch probe routing into `handleImageError` | Restore the fallback + retry + Sentry report the native-icon switch made unreachable |
| `driver-app/__tests__/components/CarMarker.test.tsx` | 4 type errors fixed; 3 tests added (failed custom URL falls back to the bundled car; a good custom URL survives; a non-http source is not probed) | Unblock CI and pin the restored error path |

### Verification performed

- `npx jest __tests__/components/CarMarker.test.tsx --no-coverage` — **29/29 passed** (26 original + 3 new).
- `npx tsc --noEmit` — **0 errors** project-wide (was 4).
- `npx eslint components/CarMarker.tsx` — the added range is clean, 0 errors and 0 warnings. 6 errors remain elsewhere in the file (`react-hooks/refs` at `:411-413`/`:941`, `react-hooks/immutability` at `:427`, `react-hooks/set-state-in-effect` at `:731`); all predate this follow-up and none is in its hunks. No CI job lints driver-app, so they are not blocking — but the misplaced `eslint-disable-next-line react-hooks/set-state-in-effect` at `:729` (it covers the `useEffect(` line, not the `setImageFailed` on `:731`) is worth a one-line fix by whoever owns that code.

### What was NOT verified by this follow-up

- Everything in §10 still stands — no device, no EAS build, no visual-regression tooling.
- **The prefetch probe itself has never run against a real Fresco pipeline.** The three new tests spy on `Image.prefetch`, so they pin the component's branching, not Android's actual image loading. Whether a 404 resolves `false` versus rejecting on a real device is unverified — both route to `handleImageError`, so the outcome is the same either way, but the path taken is untested.
- **Double fetch is accepted, not measured.** The probe and the native Marker each fetch the URL. They share Fresco's cache so the second should hit warm, but this was reasoned from `MapMarker.java`, not timed on a device.
- A pre-existing oddity, deliberately left alone: after the first failure `imageFailed` stays `true` until `imageUri` changes, so the retry that bumps `imageAttempt` re-mounts the `<Image>` against the **bundled** source and never re-attempts the custom URL. Retries therefore only ever exhaust toward the Sentry report. Unchanged on both platforms by this follow-up; out of scope here.

---

## 12. The native-icon approach did NOT fix it — reverted (2026-09-11)

**Shipped via OTA after PR #5219 merged, confirmed running on a physical Android device (two+ cold starts), and the car still disappears after Drive → Profile → Drive.** The §2 root cause was right about the *mechanism* (Expo Tabs detach the native MapView on blur) but the §3 remedy swapped one non-surviving render path for another.

### The evidence that settles it

With the car missing, **the presence ring is still visible, and there is no red default pin.** That is decisive:

- No red pin means `getIcon()` did not fall through to `BitmapDescriptorFactory.defaultMarker` (`MapMarker.java:580-600`), so `iconBitmapDescriptor` was non-null — the marker existed and drew, with an empty bitmap.
- The ring survived. The ring was a **custom-view Marker with `tracksViewChanges` left on**; the car was a **native `Marker.image`**. Same coordinate, same re-attach, same component — one redrew itself and one did not.

So the working example was inside the component the whole time. Both failures share a single cause: **the car stopped re-rendering itself.** Freezing the snapshot stops it; handing the icon to the map SDK also stops it.

Why the native path is not the synchronous drawable §3 assumed: under `expo-updates`, a `require()`d asset is not an Android drawable — it resolves to a `file://` URI in the update's cache, and `MapMarker.setImage()` routes `file://` through Fresco's Drawee holder (`MapMarker.java:414-426`), the same lifecycle-sensitive path as a remote URL. react-native-maps already has to prop that path up with a reflection call to `onAttachedToWindow` (`:481`). A bundled asset in an OTA build is therefore *not* immune to a detach.

Also worth recording: this could never have been a custom-upload problem. All four rows in `vehicle_types` have `marker_image_url = null` (checked against production), so `imageUri` was never set and the car was always on the bundled-asset path.

### Fix

Revert to **one Marker, car and ring as child views, `tracksViewChanges` permanently true on both platforms** — the configuration the ring demonstrably survives on. The long comment at the `tracksViewChanges` prop now records both failed approaches so the next person does not re-try either.

Consequently removed, because the reason for each is gone:
- the sibling ring Marker and its `ringMarkerRef` lockstep `animateMarkerToCoordinate`;
- `androidMarkerImage`;
- the §11 `Image.prefetch` probe and its `RNImage` import — `<ExpoImage onError>` is reachable on Android again, so the native fallback, retry and one-shot Sentry report work without a stand-in. The probe would now only double-fetch and duplicate the error path.

iOS is unchanged in behaviour and keeps tracking on for its own independent reason: Apple Maps ignores `Marker.rotation`, so heading is a view transform and a frozen snapshot would pin the PNG north.

### Accepted cost

One marker re-snapshots per frame. Accepted deliberately: this renders the driver's own single vehicle, the `ring` prop's docblock already forbids wiring it on a multi-marker screen, and **the ring was already paying exactly this cost unconditionally** under §3 — so the per-frame work is not new, it is now simply one marker instead of two.

### Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/CarMarker.tsx` | single Marker with child views on both platforms; `tracksViewChanges` always true; sibling ring, `ringMarkerRef`, `androidMarkerImage`, prefetch probe and `RNImage` import removed; both docblocks rewritten to record the two failed approaches | Put the car on the only render path observed to survive a MapView re-attach |
| `driver-app/__tests__/components/CarMarker.test.tsx` | replaced both Android `Marker.image` describe blocks with "Android never freezes the marker snapshot" (5 tests); dropped the 3 prefetch tests | Guard the never-freeze rule and the no-`Marker.image` rule explicitly |

### Verification

- `npx jest __tests__/components/CarMarker.test.tsx --no-coverage` — **26/26 passed**. The new assertions (`tracksViewChanges === true`, `marker.props.image` undefined, a child `ExpoImage` present, exactly one Marker with a ring) all fail against the §3 implementation, so they are real regression guards rather than restatements.
- `npx jest` over CarMarker, driverDashboardScreen, `__tests__/hooks` and `lib/androidAuto/__tests__` — **21 suites, 346 passed**.
- `npx tsc --noEmit` — **0 errors** project-wide.
- `npx eslint components/CarMarker.tsx` — **5 errors, unchanged from the pre-existing baseline**; all `react-hooks/refs`/`immutability` on lines outside this change.

### What is NOT verified

- **This has not been confirmed on the device either.** It is grounded in the ring surviving the exact transition that blanks the car, which is strong evidence but still inference about the native layer — not an observed fix. Ship it to the `preview` channel and reproduce Drive → Profile → Drive before trusting it.
- **The per-frame re-snapshot cost was not measured.** No frame timing was taken on a real device, on any OEM. If the driver map regresses visibly on a low-end handset, the next step is re-freezing on a *focus-aware* trigger rather than reverting to a native icon.
- **Two prior theories about this bug have now been wrong in production.** Treat this one as a hypothesis under test until a device says otherwise.

---

## 13. §12 made it worse — CarMarker restored to the pre-#5219 original (2026-09-11)

**Shipped via OTA after PR #5220 merged, confirmed on device: the map is now empty — no car AND no ring.** Before #5220 the ring at least rendered. §12's "never freeze the snapshot" removed the one step that made the marker draw at all.

What the three shipped versions actually did, side by side:

| Version | `tracksViewChanges` on Android | Observed |
|---|---|---|
| Original (pre-#5219, `6c7a4fc2b`) | `true`, then `false` ~350 ms after image load | Car + ring visible on load; car lost after Drive → Profile → Drive |
| #5219 | car: native `Marker.image`; ring: sibling custom view, `true` permanent | Ring visible; car lost after tab switch |
| #5220 (§12) | one custom-view marker, `true` permanent | **Nothing renders** |

The only runtime difference between the original and #5220 is that the original eventually freezes. So the freeze is what produces a valid snapshot in this environment, and a permanently-tracking custom-view marker with the car's `rotation` prop produces an empty one. §12's reasoning — "the ring survived with tracking on, so tracking on is the surviving path" — held for the ring in isolation and did not hold for the combined marker. It was inference about the native layer, stated as such in §12's "not verified", and it was wrong.

No native error was recorded for the #5220 bundle (Sentry, `release:com.spinr.driver@2.0.0+25`, last hour); the marker fails silently.

### Action

`driver-app/components/CarMarker.tsx` and its test are restored byte-for-byte from `6c7a4fc2b` — the last version observed to show the car. This deliberately **re-introduces the original tab-switch loss** (§1), because a car that disappears after a tab switch is strictly better than a car that never appears.

Removed with it, since they only existed for the two failed approaches: the sibling ring, `androidMarkerImage`, the `Image.prefetch` probe, and the never-freeze rule. The pre-existing freeze-after-load machinery (`setTracksViewChanges`, the ring re-arm effect, `handleImageLoaded`) is back exactly as it was.

### Verification

- `npx jest __tests__/components/CarMarker.test.tsx __tests__/app/driverDashboardScreen.test.tsx` — **80 passed** (the restored test file is the original 26-test suite).
- `npx tsc --noEmit` — **0 errors**.

### What happens next — and what does NOT

Three theories about this bug have now been wrong in production, each one plausible, each one shipped on inference. **No fourth theory ships.** The next change to this component must be a diagnostic build to the `preview` channel that logs, at the moment of the tab switch and again on return: whether `CarMarker` is mounted, the Marker's `tracksViewChanges` value, whether `onLoad` fired for the current `ExpoImage` instance, the wrapper's measured layout size, and whether `mapKey` changed — so the fix is read from evidence rather than reasoned from the ring.

---

## 14. Remount the marker on Drive-tab refocus (2026-09-11)

**New evidence from live testing: going offline → online brings the car back after a tab switch.** That path bumps `mapKey` (`index.tsx:275`) and remounts the whole `MapView`; a remount creates a fresh marker whose snapshot is taken from a freshly mounted view. So a remount is *observed* to restore the marker, whereas every attempt to make the existing snapshot survive re-attach (§2, §12) failed.

This is not a fourth theory about the native snapshot. It replays an action the device has shown to work, on a different trigger.

**Fix:** `index.tsx` keys `CarMarker` on a counter that increments every time the Drive tab regains focus (`useFocusEffect`), skipping the first focus so startup does not mount twice. Marker-only, not `mapKey`: no tile reload, no camera reset. `CarMarker.tsx` is untouched — still the pre-#5219 original from §13.

**Cost:** one marker remount per return to the Drive tab — the playback buffer re-seeds from the current fix. Brief marker re-appearance is possible on return; that replaces "marker absent until offline/online" and is strictly better.

**If this does not restore the car:** the escalation is to bump `mapKey` on refocus instead — the exact mechanism observed to work — accepting the map reload. Nothing else on this component ships without that.

Test: `driverDashboardScreen.test.tsx` — the `CarMarker` mock now counts mounts; one test asserts a refocus mounts a fresh marker and the first focus does not. The suite needed an `expo-router` mock (its real module pulls an ESM dependency Jest cannot parse); other screen tests already have one.

Not verified on a device — grounded in the offline→online observation, not a reproduction of this exact change.
