# Change Impact & Risk Log — @iternio/react-native-auto-play 0.4.7 → 0.5.13

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-26 |
| Author | srikumarimuddana (via Claude Code session) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/iternio-android-auto-upgrade-nfwrfr` |
| Related issue or gap ID | — (dependency currency; `docs/carplay-android-auto.md` "still unvalidated on hardware" items) |

## 1. Issue / gap identified

The Android Auto library was pinned 26 releases / one minor version behind (0.4.7,
2026-05-19 vs 0.5.13, 2026-07-30). Three upstream native fixes target exactly the
behaviours our hardware-validation doc lists as unproven: a head-unit freeze of map
animation + RN timers, missing reflow when a head unit resizes the surface, and
phone-density-derived (i.e. wrong) `window.scale`/insets on the car screen.

## 2. Root cause

Pre-1.0 dependency deliberately pinned exact after the 2026-08-16 hardware validation;
no upgrade pass since. The three defects are in the library's `VirtualRenderer.kt`
(display released before its replacement drew → Choreographer stall; layout/measure
specs never recomputed on resize; density read from the phone context instead of
`surfaceContainer.dpi`).

## 3. Fix / remediation

Bump the dependency to 0.5.13. No Spinr call-site changes — verified by diffing the two
published tarballs' Nitro specs: every API `register.ts` uses is byte-identical; the only
spec removals are voice methods (moved to the new `HybridVoice` object) we never called.
Two accompanying config changes in `app.config.ts`: `runtimeVersion` 2.6.0 → 2.7.0
(native hybrid objects changed — OTA fence), and iternio's documented ProGuard keep rule
via `expo-build-properties` → `android.extraProguardRules` (inert while minification is
off; prevents a release-only breakage class if it's ever enabled).

## 4. Risk & impact on existing functionality

- Blast radius: **single-surface, single import site.** The package is required in exactly
  one place, `driver-app/lib/androidAuto/register.ts:135`, inside the existing try/catch
  guard; `index.js` calls `registerAutoPlay()` and the tests mock the module. rider-app,
  admin-dashboard, backend: no consumers (grepped `@iternio` repo-wide).
- No backend, ride-state, money, or background-loop interaction. Car actions still call
  the same `useDriverStore` actions; nothing in the dispatch/insurance/settlement paths
  changes.
- Real risk is native: `VirtualRenderer.kt` (the class hosting our whole map surface) is
  substantially rewritten upstream (~159 changed lines), and Nitro codegen runs again in
  the next EAS build. A codegen or rendering regression would take out the Android Auto
  surface — not the phone app (the require guard degrades to "no car support" +
  Crashlytics non-fatal).
- OTA: without the `runtimeVersion` bump, shipping this JS OTA onto an existing 0.4.7
  binary would silently disable car support for that install (guard path). The bump to
  2.7.0 closes that. Pre-launch, no production users on OTA.
- The new `WindowInformationWrapper` the library inserts around our root component is one
  extra pass-through React element above `CarMapSurface`; props are forwarded unchanged
  (`componentProps`), and the jest suite (which exercises the component contract via
  mocks) plus tsc pass.

## 5. User-experience effect

- Driver-facing only, Android Auto head units only, and only on internal-test builds —
  the car app has not shipped publicly (Play car-app review not yet done). Nobody sees a
  mid-session change from this merge; behaviour changes materialize in the **next EAS
  build**, not OTA.
- Expected effect when built: no more frozen marker/countdown after display churn,
  correct fill after head-unit resize, correctly scaled trip card/insets on
  non-phone-density units.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/package.json` | `@iternio/react-native-auto-play` 0.4.7 → 0.5.13 | the upgrade |
| `driver-app/yarn.lock` | that entry's version/resolved/integrity only (hand-minimized) | plain `yarn install` also stripped real `dependencies:` blocks from unrelated entries (`got`, `keyv`, `clone-response`) — proxy-abbreviated metadata; reverted and hand-edited, then validated with `yarn install --frozen-lockfile` |
| `driver-app/app.config.ts` | `runtimeVersion` 2.6.0 → 2.7.0; added `android.extraProguardRules` keep rule | OTA fence for the native change; Nitro classes must survive minification if ever enabled |
| `docs/carplay-android-auto.md` | version refs, upgrade-note section, corrected stale "jest can't run" note (suite is green: 12 suites / 211 tests) | keep the integration doc truthful |

## 7. Before / after

Dependency-only at JS level; the behavior-changing diff is upstream native code. The load-
bearing upstream change (release order of the car `VirtualDisplay`):

```kotlin
# Before (0.4.7, VirtualRenderer.kt — on every onSurfaceAvailable)
virtualDisplay?.release()          // old display gone; window-absent gap stops
virtualDisplay = manager.createVirtualDisplay(...)   // Choreographer → map + RN timers freeze
```

```kotlin
# After (0.5.13)
virtualDisplay?.let { pendingDisplays.add(it) }      // kept alive…
virtualDisplay = manager.createVirtualDisplay(...)
// …released in an OnDrawListener only after the new display has drawn
```

## 8. Rollback plan

- Not yet in any store build: **revert the commit and rebuild** is a complete rollback —
  no live data, no OTA cohort, no migration. `runtimeVersion` 2.7.0 must be reverted in
  the same commit (it only ever shipped with 0.5.13).
- If a bad build has gone to Play internal testing: pull the internal-track release /
  roll back to the previous internal build in Play Console; phone app is unaffected
  either way (guarded require).
- No feature flag: the surface is dark to the public by store-track gating, which is the
  stronger control here; an `app_settings` flag can't reach a car-only cold launch before
  the store gate does.

## 9. Verification performed

- [x] Automated tests: `npx jest lib/androidAuto` — 12 suites / 211 tests green against
  0.5.13 (note: suite mocks the iternio module, so it validates our contract, not the
  package internals — see §10)
- [x] `npx tsc --noEmit` clean (full driver-app)
- [x] `npx eslint app.config.ts lib/androidAuto` — no errors; one pre-existing warning in
  `carSurface.tsx` (`HeatmapCell` unused), untouched by this change
- [x] Static export check: `HybridAutoPlay`, `MapTemplate`, `Flag` (values unchanged:
  Primary=1/Persistent=2/Default=4), and every method signature we call verified present
  in the installed 0.5.13 `lib/` d.ts files; Nitro spec diff 0.4.7→0.5.13 shows
  `MapTemplate`/`ListTemplate`/`Cluster`/`Telemetry` specs byte-identical
- [x] Blast-radius grep: `@iternio` repo-wide → single guarded require site + tests +
  docs; no other surface consumes it
- [x] `yarn install --frozen-lockfile` passes on the hand-minimized lockfile
- [x] Reviewed against CLAUDE.md conventions (surgical change, no error-swallowing added,
  additive config)
- Real production build: **NOT run** — `npm run build` has no equivalent here; the gating
  build is EAS `--profile android-auto`, which this environment cannot execute.

## 10. What was NOT verified

- **The Nitro codegen build on our exact stack** (Expo SDK 57 / RN 0.86.2 / nitro 0.35.9)
  — first EAS build after merge is the gate, exactly as it was for 0.4.7.
- **Anything on a head unit.** The three fixes this upgrade exists for are, by nature,
  only observable in a car (or DHU). The next device session should re-check marker
  glide during an offer countdown, surface resize behaviour, and trip-card scale.
- The jest suite fully mocks the native module, so it gives zero coverage of the
  upstream `VirtualRenderer` rewrite — stated per the "stubbed dependency ≠ coverage"
  rule.
- No automated visual/snapshot regression tooling exists for driver-app (standing gap,
  `ACTION_ITEMS.md`); car-screen rendering was reasoned about, not screenshotted.
