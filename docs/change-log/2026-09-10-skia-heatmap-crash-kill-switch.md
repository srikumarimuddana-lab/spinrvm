# Change Impact & Risk Log — Skia heatmap kill-switch (driver-app iOS crash)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (agent session) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch TBD, following this log |
| Related issue or gap ID | Active production incident: driver-app 2.0.03 100% crash on iOS, `Invariant Violation: TurboModuleRegistry.getEnforcing(...): 'RNSkiaModule' could not be found`. Reported via a separate PR (#5162, `claude/driver-app-crash-update-gax81x`), which proposed a `__turboModuleProxy` pre-check fix but has not been merged or device-verified. |

## 1. Issue / gap identified

`HeatmapGradientOverlay.tsx` (HM-32, merged via #5149) calls `require('@shopify/react-native-skia')` inside a `try/catch`, guarding against exactly the case where a JS-only OTA update reaches an iOS binary that predates the Skia native dependency. That OTA already went out automatically to the **production** channel the moment #5149 merged (confirmed: `eas-build.yml` run #922, `conclusion: success`), so every iOS driver phone has had this code since. #5162 reports a 100% crash on iOS matching this exact code path.

## 2. Root cause

**Not fully confirmed — this is the core problem with #5162's own fix, and why this kill-switch exists instead of just merging that PR.** I independently verified, against primary sources, that the `try/catch` *should* work:

- `TurboModuleRegistry.getEnforcing()` (`node_modules/react-native/Libraries/TurboModule/TurboModuleRegistry.js`) calls `invariant()` on failure.
- `invariant` (`node_modules/invariant/invariant.js`) does exactly `throw new Error(message)` — a plain, synchronous, catchable JS exception in both dev and production builds. Nothing native, nothing that bypasses a wrapping `try/catch`.
- `@shopify/react-native-skia`'s own `NativeSkiaModule.js` calls `getEnforcing` as an ordinary top-level module-evaluation side effect — exactly the kind of throw a `try/catch` around the triggering `require()` call catches.
- **Empirically re-verified, not just reasoned about**: this repo's own existing test (`__tests__/components/HeatmapGradientOverlayMissingNativeModule.test.tsx`, part of the original HM-32 PR) already simulates `require('@shopify/react-native-skia')` throwing and passes against the **unmodified, currently-live** code — the component catches the throw and renders `null`.

This directly contradicts #5162's stated root cause ("crashes the Hermes runtime before JS try/catch can intercept it"). #5162's own PR body also claimed its tests were "not runnable in this environment (npm registry blocked)" — I ran them myself in this session and they passed fine, which further reduces confidence in that PR's own verification rigor.

**Conclusion: the true failure mechanism is not yet identified.** Rather than merge a fix built on an unverified (and likely incorrect) theory while a claimed 100%-crash is live, this change removes the entire risk surface instead of patching a guess.

## 3. Fix / remediation

`driver-app/app/driver/(tabs)/index.tsx`:

- **Disabled `HeatmapGradientOverlay` rendering entirely** — its render condition is now `false && ...` (was `Platform.OS === 'ios' && ...`). The `require('@shopify/react-native-skia')` call inside `loadSkia()` can no longer execute on any device, regardless of the true underlying mechanism. A comment at the render site documents why and what's needed before re-enabling.
- **Restored `HeatmapCells` on iOS** — its render condition dropped the `&& Platform.OS === 'android'` restriction added by HM-32. `HeatmapCells.tsx` has its own internal `USE_NATIVE_GRADIENT = Platform.OS === 'android'` branch, so this exactly restores the pre-HM-32 iOS behavior: the original circle-based fallback heatmap, which has **zero dependency on `@shopify/react-native-skia`** and was working safely before HM-32 ever shipped.

Net effect: iOS drivers get their heatmap back (in the pre-HM-32 visual style, not the newer gradient look), and the crash's entire code path is unreachable.

Deliberately did **not**: touch `HeatmapGradientOverlay.tsx` itself (left as dead code, including whatever fix eventually lands from #5162 or further investigation — re-enabling later is a one-line revert of the `false &&`), remove the `mapViewport`/`liveHeatmapRegion` state or their `onLayout`/`onRegionChange` wiring (still harmless to compute even though nothing currently reads them for rendering — leaving them in place keeps this diff minimal and avoids touching unrelated code under incident pressure), or merge/close #5162 (its pre-check is still a reasonable defense-in-depth addition once the real mechanism is confirmed, but shouldn't be trusted alone to have fixed this).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `driver-app/app/driver/(tabs)/index.tsx`** (2 render-condition changes) and `driver-app/__tests__/app/driverDashboardScreen.test.tsx` (updated to match). No change to `HeatmapCells.tsx`, `HeatmapGradientOverlay.tsx`, `useVisibleHeatmapCells.ts`, or `lib/heatFalloff.ts` — this only changes which of the two existing, already-built renderers gets mounted on iOS.
- Grepped for other consumers of both render sites — this is the only screen that renders either component (`components/dashboard/index.ts` barrel export, single import site, confirmed via `grep -rn "HeatmapGradientOverlay\|HeatmapCells" app/ components/` before editing).
- `HeatmapCells`'s iOS path is not new or unverified code — it's the exact renderer that shipped safely before HM-32, for the full period this app has had a demand heatmap on iOS at all.
- This is strictly a risk-reduction change: it can only remove a crash, not introduce one, since it activates a previously-working code path and deactivates a currently-crashing one.

## 5. User-experience effect

- **Driver, iOS, immediate**: the app stops crashing. The demand heatmap on iOS reverts from the newer Skia gradient look to the pre-HM-32 circle-based style — a visual downgrade, not a functional loss (the heatmap itself, cell filtering, and driver-position exclusion are all unchanged).
- **Driver, Android**: no change — Android always used `HeatmapCells`' native `<Heatmap>` path, untouched by this fix.
- This ships via OTA (JS-only) to the same production channel the crash arrived on, so it reaches affected phones on next app open — no App Store/Play Store review cycle needed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/(tabs)/index.tsx` | `HeatmapGradientOverlay` render condition → `false && ...`; `HeatmapCells` render condition no longer restricted to Android | Remove the crashing code path entirely; restore the known-safe iOS fallback |
| `driver-app/__tests__/app/driverDashboardScreen.test.tsx` | Updated the 2 tests that asserted iOS renders `HeatmapGradientOverlay`/never `HeatmapCells` — now asserts the reverse, plus a new test asserting `HeatmapGradientOverlay` never renders on any platform | Match the new, correct expected behavior |
| `docs/change-log/2026-09-10-skia-heatmap-crash-kill-switch.md` | This log | Live-tested surface, real production incident |

## 7. Before / after

```tsx
// Before
{rideState === 'idle' && heatmapCells.length > 0 && Platform.OS === 'android' && (
  <HeatmapCells ... />
)}
...
{Platform.OS === 'ios' && rideState === 'idle' && heatmapCells.length > 0 && (
  <HeatmapGradientOverlay ... />
)}

// After
{rideState === 'idle' && heatmapCells.length > 0 && (
  <HeatmapCells ... />
)}
...
{false && Platform.OS === 'ios' && rideState === 'idle' && heatmapCells.length > 0 && (
  <HeatmapGradientOverlay ... />
)}
```

## 8. Rollback plan

This change *is itself* the rollback for the crashing feature — reverting this commit would re-enable the crash. If this change somehow regressed something else (it shouldn't; `HeatmapCells`'s iOS path is the pre-existing, already-proven code), the correct rollback is a further OTA that also disables `HeatmapCells` on iOS (drop the heatmap entirely) rather than reverting back to the crashing Skia path.

Re-enabling `HeatmapGradientOverlay` in the future requires, in order: (1) the real Sentry stack trace confirming the actual throw site and mechanism, (2) a real EAS native-build device test confirming no crash on both a pre-Skia and post-Skia binary, (3) flipping `false &&` back to `Platform.OS === 'ios' &&` and restoring the `&& Platform.OS === 'android'` restriction on `HeatmapCells` (or, better, gating the whole thing behind an explicit, instantly-killable flag rather than a compile-time constant — the `SOFT_HEAT_RENDER_ENABLED` pattern already used elsewhere in this codebase for exactly this "shipped dark until device-verified" situation).

## 9. Verification performed

- [x] `npx tsc --noEmit` — clean.
- [x] `npx eslint "app/driver/(tabs)/index.tsx"` — 0 errors (75 pre-existing style warnings elsewhere in the file, unrelated to this change).
- [x] Full driver-app suite: `npx jest --silent` — 139 suites / 1572 tests pass, including the updated `driverDashboardScreen.test.tsx` assertions for the new render behavior.
- [x] Grepped for every render-site consumer of `HeatmapCells`/`HeatmapGradientOverlay` before editing — confirmed single call site, no other screen affected.
- [x] Independently re-verified the *disabled* code's own claimed root cause via primary source (`invariant`, `TurboModuleRegistry.js`, Skia's `NativeSkiaModule.js`) and this repo's own pre-existing regression test — see §2. This is why the fix removes the risk surface rather than trusting #5162's unverified patch.

## What was NOT verified

- **No real device confirmation that this actually stops the crash** — this environment has no iOS device/simulator or EAS build access. The fix's safety argument is structural (the crashing `require()` call can no longer execute at all, verified by reading the render condition), not empirical-on-device. This is the strongest kind of "not verified" disclosure this repo's convention asks for: it's *why* this is a removal rather than a patch — a removed code path can't crash regardless of what the real mechanism turns out to be, whereas a patched one still could if the patch's own theory is wrong.
- The actual root cause of the original crash remains unconfirmed. This is a mitigation, not a diagnosis — pulling the real Sentry stack trace is still the right next step for whoever owns that investigation, even though it's no longer blocking driver safety.
- Whether #5162 should still be merged: its `__turboModuleProxy` pre-check is harmless and can land independently, but shouldn't be described as "the fix" for this incident given §2.
- driver-app has no visual-regression tooling — the iOS heatmap's reversion to its pre-HM-32 look was reasoned about (it's the exact code that rendered it before), not screenshotted.

## 10. Sign-off

- [x] Rollback plan is concrete: revert = re-enable the crash; correct fallback if needed = also disable `HeatmapCells` on iOS.
- [x] Blast radius is stated, not assumed: single screen, two render-condition changes, grepped for other consumers.
- [x] No silent behavior change: the User-Experience-effect section states plainly that iOS drivers get an older heatmap visual style back, not the newer one, until the real fix is confirmed.
