# Change Impact & Risk Log — Skia TurboModule crash on pre-Skia binaries

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (agent session) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | claude/driver-app-crash-update-gax81x |
| Related issue or gap ID | 100% crash on driver-app 2.0.03 iOS: `Invariant Violation: TurboModuleRegistry.getEnforcing(...): 'RNSkiaModule' could not be found` |

## 1. Issue / gap identified

Driver app crashes immediately on iOS with `TurboModuleRegistry.getEnforcing(...): 'RNSkiaModule' could not be found`. 100% repro on version 2.0.03.

## 2. Root cause

Commit `746af5f` (HM-32, Skia gradient heatmap) added `@shopify/react-native-skia` as a dependency and `HeatmapGradientOverlay.tsx` uses a lazy `require()` inside a `try/catch`. However, Skia's own module initialization calls `TurboModuleRegistry.getEnforcing('RNSkiaModule')` — the **enforcing** variant, which throws an `Invariant Violation` that crashes the Hermes runtime before JavaScript's try/catch can intercept it. This happens because the current native binary was built before the Skia native module was added (OTA JS-only update pushed code that references a native module the binary doesn't include).

## 3. Fix / remediation

Pre-check for the native module's existence via `globalThis.__turboModuleProxy('RNSkiaModule')` — the non-throwing JSI binding that `TurboModuleRegistry.get()` wraps — BEFORE calling `require('@shopify/react-native-skia')`. If the proxy returns null (module not in binary), `loadSkia()` returns null immediately and the `require()` is never reached.

Also added module-level caching (`_skiaCache`) so the probe runs at most once per app session, avoiding the overhead of repeated `require()` calls on every component render.

## 4. Risk & impact on existing functionality

Blast radius:

- `driver-app/components/dashboard/HeatmapGradientOverlay.tsx` — the only file modified. Only caller of `loadSkia()`.
- Grepped callers: `HeatmapGradientOverlay` is imported from `components/dashboard/index.ts` (barrel) and used in `app/driver/(tabs)/index.tsx`. No other callers.
- `__tests__/components/HeatmapGradientOverlayMissingNativeModule.test.tsx` — updated to simulate the `__turboModuleProxy` pre-check.

Could regress:

- On a binary that DOES include Skia: `__turboModuleProxy('RNSkiaModule')` returns the native module (non-null), the pre-check passes, and `require()` proceeds normally. No behavior change.
- The `_skiaCache` caching means `loadSkia()` is called once rather than on every render — this is strictly better (avoids repeated `require()` calls) but means if a hot-reload somehow changes the Skia module availability mid-session, the cached result wouldn't update. This is a non-issue in production (native modules don't appear/disappear at runtime).

No ride state machine, money, auth, or backend changes.

## 5. User-experience effect

- **Driver, iOS, immediate:** the app no longer crashes on launch. Instead, the Skia gradient heatmap degrades to the existing iOS fallback (opacity-layered circles in `HeatmapCells.tsx`). The heatmap still renders, just with the pre-Skia visual quality.
- Android: unaffected (Android uses the native `<Heatmap>` layer, not Skia).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/dashboard/HeatmapGradientOverlay.tsx` | `loadSkia()`: added `__turboModuleProxy` pre-check before `require()`; added `_skiaCache` module-level cache | Prevent fatal `getEnforcing` crash on binaries without Skia |
| `driver-app/__tests__/components/HeatmapGradientOverlayMissingNativeModule.test.tsx` | Added `beforeEach`/`afterEach` to mock `__turboModuleProxy` returning null for `RNSkiaModule` | Cover the actual crash scenario (proxy exists but module missing) |
| `docs/change-log/2026-09-10-skia-turbomodule-crash-fix.md` | This log | Live-tested surface |

## 7. Before / after

```typescript
// Before — try/catch alone, getEnforcing crashes Hermes before catch runs
function loadSkia(): SkiaModule | null {
  try {
    return require('@shopify/react-native-skia');
  } catch {
    return null;  // never reached — app already crashed
  }
}
```

```typescript
// After — pre-check native module via non-throwing proxy, then require
let _skiaCache: SkiaModule | null | undefined;
function loadSkia(): SkiaModule | null {
  if (_skiaCache !== undefined) return _skiaCache;
  const proxy = (globalThis as any).__turboModuleProxy;
  if (typeof proxy === 'function' && proxy('RNSkiaModule') == null) {
    _skiaCache = null;
    return null;  // never reaches require() — no crash
  }
  // ... try/catch require kept as second safety net
}
```

## 8. Rollback plan

Client-only JS change. No migration, no live data, no `app_settings` flag. Rollback is an OTA revert to the previous JS bundle. The rollback re-introduces the crash, so it should only be used if the fix itself regresses something else — in which case reverting to the pre-746af5f bundle (before Skia was added) is the correct target.

## 9. Verification performed

- [x] Code review: `loadSkia()` pre-check logic verified against React Native TurboModule internals
- [x] Blast-radius grep: `loadSkia`, `HeatmapGradientOverlay`, `@shopify/react-native-skia`
- [x] Test updated to cover `__turboModuleProxy` pre-check path
- [ ] Tests not runnable in this environment (npm registry blocked) — CI will validate
- [ ] No production `eas build` / device test in this environment

## 10. What was NOT verified

- Real iOS device: crash-to-no-crash confirmation on a 2.0.03 binary receiving this OTA. The fix is high-confidence from the stack trace analysis, but the actual crash-avoidance needs on-device confirmation.
- Whether `__turboModuleProxy` is available on ALL React Native New Architecture versions (it's the JSI primitive that `TurboModuleRegistry` wraps — should be present wherever TurboModules exist, which is the only context where `getEnforcing` crashes).
- driver-app has no visual-regression tooling — reasoned about, not screenshotted.
