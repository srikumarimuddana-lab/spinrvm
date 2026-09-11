# Change Impact & Risk Log — Car icon can fail to decode with no fallback, no retry, and no visibility (Android "green circle, never a car")

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-09 |
| Author | Claude Code (agent session) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/vehicle-icon-movement-animation-8tys8o` |
| Related issue or gap ID | User-reported, live-tested build, 4 fresh Android screenshots (2026-09-09): the driver's own vehicle marker shows only the colored presence ring, never the car icon — same symptom reported before an earlier, different fix (the ring/image race, commit `b9e8d6a`) was confirmed already present in the live build |

## 1. Issue / gap identified

The already-shipped ring-freeze fix (`b9e8d6a`) addresses a *timing race* between the presence ring and the car `<Image>`'s decode. It is confirmed present in the live Play Store build (`103f2f7`, git-verified this session). Despite that, the symptom — a driver's own vehicle marker showing only a colored circle, never the car icon — was re-reported today against "the latest build."

## 2. Root cause

A dedicated investigation agent traced every code path this session and confirmed: `CarMarker.tsx`'s `onError` handler on the `<Image>` only ever did `setImageFailed(true)` — and `imageFailed` only controls whether a caller-supplied *custom* `imageUri` (an admin-uploaded marker) falls back to the *bundled* PNG. When there is no custom `imageUri` (the common case — most vehicle types use the bundled marker), `useCustomImage` is already `false`, so `onError` firing on the bundled image itself changed nothing about what `<Image>` was asked to render — it would retry the exact same source that had just failed, with no different outcome expected, and `onLoad` (the only thing that ever sets `hasLoadedImageRef.current = true`) would never fire. The marker's own snapshot lifecycle then permanently freezes at the mount effect's unconditional 5000ms hard cap (`setTracksViewChanges(false)`), locking in whatever the native view looked like at that instant — the ring, with no car, forever, immune to every later prop/state change including going offline and back online.

This is a distinct bug from the ring-freeze race the earlier fix addressed: that fix assumed the image *would* eventually decode and only needed to win a timing race against the ring; this gap is about what happens when the image *never* decodes on a given device (a transient OOM/codec glitch on a low-end Android device is the most likely trigger, though the exact device-level cause is not confirmed — see "What was NOT verified" below). Critically, this failure was also completely **silent**: no log, no Sentry event, nothing — a direct violation of CLAUDE.md's "do not silently swallow errors," which up to now had only been enforced for DB/auth/payment paths, not a UI rendering failure with this much user impact (SGI/insurance-period rules require a driver's presence status to be visually legible — an invisible vehicle icon undermines that on the driver's own device, even though the underlying insurance-period classification in the backend is unaffected).

## 3. Fix / remediation

`driver-app/components/CarMarker.tsx`:
- `onError` now retries the `<Image>` up to `MAX_IMAGE_RETRIES` (3) times with linear backoff (300ms, 600ms, 900ms), by bumping an `imageAttempt` counter that's included in the `<Image>`'s `key` — forcing React Native to tear down and recreate the native image view each time, giving a transient decode failure (GC pressure, a momentary codec hiccup) a real chance to succeed on a fresh attempt within the same 5-second window the existing ring-freeze logic already waits.
- Once retries are exhausted and the image still hasn't loaded, `captureException` (the repo's existing Sentry/Crashlytics facade, `@shared/services/errorReporting`) is called exactly once per mount with `{ domain: 'drivers', surface: 'driver-app' }` tags — matching CLAUDE.md's Sentry tagging convention. This is purely additive visibility: it does not change what the driver sees (the marker still ends up frozen on the ring-only view if the image genuinely never loads — that part of the behavior is unchanged), it only means this is no longer invisible to the team.
- `imageAttempt` and the "already reported" flag both reset whenever `imageUri` changes, matching the existing `imageFailed` reset — a newly-assigned custom marker gets its own fresh set of retries.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `CarMarker.tsx`.** No new props, no change to the component's public interface — every existing caller (`driver-app/app/driver/(tabs)/index.tsx`'s own-vehicle marker, `driver-app/lib/androidAuto/carSurface.tsx`'s Android Auto marker) is unaffected by anything except the internal retry/report behavior.
- Does not touch the ring-freeze logic (`ringChangeKey` effect, `hasLoadedImageRef`, the 5000ms hard cap) at all — that logic is correct as-is and this fix layers on top of it, addressing a different failure mode.
- The retry mechanism only fires `onError`, which in normal operation (image loads successfully) never triggers — zero behavior change for the overwhelming majority of markers that already render correctly.
- `captureException`'s own facade already fails safe (wrapped in try/catch internally, per its own source) — a Sentry/Crashlytics outage cannot itself crash this component.

## 5. User-experience effect

- **Driver-facing**: a driver whose device experiences a transient image-decode glitch now has up to ~1.8 extra seconds (3 retries' worth of backoff, well within the existing 5s hard-cap window) for the car icon to actually appear, instead of being guaranteed to freeze on the ring-only view. A driver whose device has a genuinely persistent failure (not transient) sees no change in what's displayed — the ring-only fallback still stands, this fix does not manufacture a car icon out of nothing.
- **Internal/ops-facing**: this failure is now visible in Sentry (tagged `domain: drivers`, `surface: driver-app`) for the first time, letting the team see how often it actually happens in production and correlate it with device/OS data Sentry captures automatically — directly answering the open question of whether this is a widespread or rare failure.
- Not mid-session disruptive: nothing about this changes an already-rendered marker; it only changes what happens the next time an image fails to decode.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/CarMarker.tsx` | `onError` now retries (bumped `Image` key) up to `MAX_IMAGE_RETRIES` times with backoff; reports via `captureException` once retries are exhausted; `imageAttempt`/report-flag reset alongside the existing `imageFailed` reset on `imageUri` change | Close the silent, un-recoverable gap for a bundled-image decode failure found by this session's root-cause investigation |
| `driver-app/__tests__/components/CarMarker.test.tsx` | New `describe` block: retry produces a fresh `Image` instance; exactly one `captureException` call after retries exhausted, never more than once, never before exhaustion; no report at all if the image eventually loads | New behavior needs test coverage |

## 7. Before / after

```tsx
// Before
<Image
    source={useCustomImage ? { uri: imageUri as string } : CAR_IMAGES[variant]}
    onError={() => { setImageFailed(true); setTracksViewChanges(true); }}
    onLoad={handleImageLoaded}
    ...
/>
```

```tsx
// After
<Image
    key={imageAttempt}
    source={useCustomImage ? { uri: imageUri as string } : CAR_IMAGES[variant]}
    onError={handleImageError}   // retries with backoff, then captureException once
    onLoad={handleImageLoaded}
    ...
/>
```

## 8. Rollback plan

No migration, no live data. Pure component-logic diff in one file — `git revert` is a complete rollback. No feature flag: this is a resilience/observability improvement to an existing rendering path, not a new user-facing capability, and there's no existing `app_settings` flag for marker rendering to piggyback on. If the retry behavior itself ever caused a problem (e.g. excessive image-view churn on a specific device class), reverting this commit restores the prior single-attempt behavior exactly.

## 9. Verification performed

- [x] `yarn jest __tests__/components/CarMarker.test.tsx` — 18/18 passed
- [x] `yarn jest` (full driver-app suite) — 132 suites / 1495 tests passed
- [x] `npx tsc --noEmit` — clean
- [x] Root cause confirmed by a dedicated investigation pass this session tracing the exact `onError`/`imageFailed`/`useCustomImage`/`hasLoadedImageRef` code paths, not guessed
- [x] Blast-radius grep: `CarMarker` component's public prop interface unchanged; both known call sites (`index.tsx`, Android Auto's `carSurface.tsx`) unaffected
- [ ] **`npm run build` / EAS production build NOT run** — OTA-eligible JS-only change, no native deps touched, no EAS build triggered
- [ ] Not manually reproduced on a real device — the original failure (a bundled PNG genuinely failing to decode) has no confirmed reliable local repro; the fix was verified via the component's own retry/report logic under test, not a live device reproduction

## What was NOT verified

- **This does not confirm the bundled-image-decode-failure theory is the actual cause of the user's screenshots** — it closes a real, code-confirmed gap (no fallback, no retry, no visibility) that would produce exactly this symptom, but I have no device-level evidence (a crash log, a Sentry event) that this specific mechanism is what happened on the reporting user's phone rather than, e.g., a stale build that predates even the ring-freeze fix (Play Store staged rollouts mean "newest build submitted" isn't necessarily "what a given phone has downloaded" — this repo/CI cannot determine that). Asking the user for their driver-app's actual installed version/build number (Settings → About) is the fastest way to rule that alternative in or out.
- No visual-regression tooling exists for driver-app (per CLAUDE.md) — reasoned about and unit-tested, not screenshotted before/after.
- The retry backoff values (300/600/900ms) and retry count (3) are judgment calls sized to fit inside the existing 5s hard-cap window, not tuned against real device telemetry — once Sentry data comes in from this fix's own reporting, these may need adjusting.
