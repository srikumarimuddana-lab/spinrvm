# Change Impact & Risk Log — driver heatmap soft falloff (shipped dark)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-09 |
| Author | mkkreddy52@gmail.com (via Claude Code) |
| Surface(s) | driver-app (phone iOS + Android, and the Android Auto car surface) |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/pr-5142-review-5dagsf` — `3ce93a1`, `4cc353b`, `946fdd1`, `42df2b9` |
| Related issue or gap ID | PR #5142 findings HM26-05 (iOS ring geometry) and HM26-06 (per-surface scale divergence) |

## 1. Issue / gap identified

The demand heatmap does not read as one product across the three driver surfaces, and none
of them reads like the soft continuous overlay the request asked for. Android draws a real
native gradient; iOS draws two concentric circles per cell (the reported "bullseye"); Android
Auto draws hard-edged squares on its own hardcoded ramp.

## 2. Root cause

Architectural, not a bug. `react-native-maps`' native `<Heatmap>` is backed only on Google
Maps, and this app deliberately runs Apple Maps on iOS (`app.config.ts:48-54` documents why —
setting `ios.config.googleMapsApiKey` injects the obsolete `react-native-google-maps` pod and
`pod install` then fails on `react-native-maps` 1.x). So `HeatmapCells.tsx` branches on
`USE_NATIVE_GRADIENT = Platform.OS === 'android'` and hand-rolls a stand-in everywhere else.

The stand-in's two circles use fixed alphas (0.14 outer, 0.32/0.5 inner). Stacked under the
painter's algorithm that steps from 0.14 straight to ~0.57 at half the radius. A 0.43 jump in
a single step is exactly what the eye reads as a ring rather than a fade.

**Not** established here: the heavy *black* outlines in the reported screenshot. Both existing
circles pass `strokeWidth={0}` but leave `strokeColor` undefined, which is the leading
candidate, but it has not been reproduced on a native build. Treated as candidate containment.

## 3. Fix / remediation

A shared `lib/heatFalloff.ts` defines one radial falloff, and all three renderers use it.
N nested circles whose *stacked* opacity follows a Gaussian, solved through the
painter's-algorithm relation `α_k = (T_k − T_k−1) / (1 − T_k−1)` so the stack lands on the
target instead of compounding past it. Largest single alpha step drops **0.430 → 0.206**.

Colour ramp is deliberately untouched on every surface — this changes shape only. Android Auto's
`CAR_RAMP` was a hand-copied literal of `darkColors.heatmapRamp`; verified byte-identical and
repointed at the shared token, so it is a dedupe with no colour change.

Everything is behind `SOFT_HEAT_RENDER_ENABLED`, which **ships `false`**.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (driver-app), and inert until the flag flips.**

Greps performed:
- `heatmapRamp` across `driver-app/`, `rider-app/`, `admin-dashboard/`, `shared/` — five
  consumers, all in driver-app: `HeatmapCells`, `DemandLegend`, `HotspotChips`, `ForecastStrip`,
  and the surge polygon at `app/driver/(tabs)/index.tsx:1188`. **No consumers outside
  driver-app.** `shared/theme/index.ts` is imported by rider-app, but the `heatmapRamp` key is
  not read there. No ramp values changed, so none of these five is affected either way.
- `carCellCenter` — confirmed zero remaining duplicates after unification.
- `METERS_PER_LAT_DEG`, `cellCenter` — were duplicated per renderer; now single-sourced.

No backend, no database, no migration, no background loop, no ride-state transition, no
money/wallet path, no insurance-period write is touched. The heatmap is read-only presentation
downstream of `GET /drivers/demand-heatmap`; nothing here changes what is fetched, when it is
fetched, or what is stored.

Residual risk, flag-on only: native view count rises. Phone iOS 60×2 = 120 shapes → 45×4 = 180.
Android Auto 80 squares → 30×4 = 120. Both caps were reduced to hold the count in the same
order, but neither was profiled (see §9).

Untouched by design, still open from PR #5142: both renderers still normalise intensity by the
*visible* maximum (HM26-06), so panning still changes what a given shade means. That is a data
-semantics fix, not a shape fix, and is out of scope here.

## 5. User-experience effect

**Nobody sees any difference from this change as shipped.** The flag is `false`, so every
driver — mid-shift, online, or on a car head unit — keeps the exact renderer they have today.

When the flag is flipped (a separate commit + OTA), the affected party is drivers only: the
demand overlay becomes a soft fade instead of rings/squares, Android's overlay becomes slightly
more translucent (0.75 → ~0.557 peak), and fewer cells are drawn at once on each surface. No
copy changes, no notification changes, no change to going online/offline, ride offers, or
navigation. Riders, corporate admins and internal admins see nothing.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/lib/heatFalloff.ts` | New. Ring stops, Gaussian falloff solver, `cellCenter`, `METERS_PER_LAT_DEG`, `paintedPeakAlpha()`, the flag | One definition of the shape so three renderers cannot disagree |
| `driver-app/components/dashboard/HeatmapCells.tsx` | iOS 4-ring soft path; Android opacity from `paintedPeakAlpha()`; `MAX_SOFT_BLOBS` 45; radius factor 0.62 → 0.70; local `cellCenter`/`METERS_PER_LAT_DEG` removed | Kill the bullseye; make the two phones agree on peak translucency |
| `driver-app/lib/androidAuto/carSurface.tsx` | Soft circle path via `Maps.Circle`; `CAR_MAX_SOFT_BLOBS` 30; `CAR_RAMP` → `darkColors.heatmapRamp`; `rampIndex` extracted; shared `cellCenter` | Car stops looking like a different product; ramp cannot drift from the phone legend |
| `driver-app/__tests__/lib/heatFalloff.test.ts` | New. 14 assertions on the falloff and grid snap | The maths is the part that is actually verifiable here |
| `driver-app/__tests__/components/HeatmapCellsSoft.test.tsx` | New. Renderer assertions with the flag forced on | The soft path is unreachable in tests otherwise |

## 7. Before / after

```tsx
// Before — iOS: two circles, one hard step from 0.14 to ~0.57 at 0.5R
<Circle radius={outerRadiusM}     fillColor={hexToRgba(color, 0.14)} strokeWidth={0} />
<Circle radius={outerRadiusM*0.5} fillColor={hexToRgba(color, idx === 4 ? 0.5 : 0.32)} strokeWidth={0} />

// After — four rings solved so the STACK follows a Gaussian; max step 0.206.
// strokeColor set explicitly (candidate containment for the reported black outline).
const alphas = ringAlphas(maxWeight > 0 ? cell.weight / maxWeight : 0);
HEAT_RING_STOPS.map((stop, i) => (
  <Circle radius={outerRadiusM * stop} fillColor={hexToRgba(color, alphas[i])}
          strokeColor="transparent" strokeWidth={0} />
))
```

```tsx
// Before — Android Auto: flat squares on a hand-copied ramp
<MapsPolygon coordinates={cellCorners(...)} fillColor={rc.fill} strokeColor={rc.stroke} strokeWidth={rc.sw} />

// After (flag on) — same falloff as the phone, ramp from the shared token
<MapsCircle center={cellCenter(...)} radius={outer * stop}
            fillColor={`rgba(${rv},${gv},${bv},${alphas[ring]})`} strokeColor="transparent" strokeWidth={0} />
```

## 8. Rollback plan

`SOFT_HEAT_RENDER_ENABLED` in `driver-app/lib/heatFalloff.ts` is already `false`, so the
shipped state *is* the rolled-back state. If the flag is flipped on and needs reverting, set it
back to `false` and publish an OTA update — no store review, no backend change, no data
remediation, because nothing here writes anything.

**Honest limitation:** this is a build-time constant, not a remote kill switch. A driver on a
build with the flag on cannot be switched off server-side; they need the next OTA. A remote
toggle would mean a new `app_settings` column plumbed through `GET /drivers/demand-heatmap` —
a reasonable follow-up, not needed to ship dark. Flagged here rather than left implicit.

No live data is touched, so `git revert` genuinely is sufficient at the code level.

## 9. Verification performed

- [x] **Falloff module: 14/14 assertions pass.** Compiled `lib/heatFalloff.ts` with `tsc` and
      executed every assertion from `heatFalloff.test.ts` directly in node.
- [x] **Syntax check**: `tsc --noEmit` over all changed files — zero `TS1xxx` syntax errors.
- [x] **Blast-radius grep**: listed in §4.
- [x] **Reviewed against `CLAUDE.md`**: no money arithmetic, no state machine, no RLS, no PIPEDA
      surface (no coordinates logged — the renderer only draws already-suppressed aggregates).
      Pre-commit hook passed on all four commits.
- [x] **Feature-flagged**, defaults false.

## 10. What was NOT verified — read this before flipping the flag

- **`jest` was never run.** `registry.npmjs.org` returns **403 both through the agent proxy and
  on a direct connection** in this environment, so `yarn install` cannot complete and
  `driver-app/node_modules` does not exist. `__tests__/components/HeatmapCellsSoft.test.tsx`
  has therefore **never been executed** — it is written but unproven. The falloff assertions
  were run by compiling the module and executing them in node, which is real but is not a jest run.
- **Full typecheck not run** — `tsc` without `node_modules` cannot resolve `react`,
  `react-native` or `react-native-maps`. Only syntax was checked.
- **No production build.** No `eas build`, no simulator, no device, no DHU, no head unit.
- **Nothing was seen rendered on any of the three surfaces.** Not on Apple Maps, not on Google
  Maps, not on Android Auto. The Gaussian stack is verified as *numbers*; how it composites on
  each native SDK is unverified. This is exactly the M0 native capability spike PR #5142 calls
  a prerequisite, and it has not been done.
- **No visual regression tooling exists for driver-app at all** (confirmed: no visual specs in
  `driver-app/e2e/`, no driver-app visual job in `ci.yml`), so per `CLAUDE.md` gate 6 this
  change is **reasoned about, not screenshotted**. admin-dashboard's Playwright baselines are
  irrelevant here — no admin surface is touched.
- **No performance measurement.** The 180-shape phone and 120-shape car budgets are reasoned
  from the previous counts, not profiled. A head unit is the likeliest place for this to hurt.
- **The black-outline symptom is not reproduced or fixed.** `strokeColor="transparent"` is a
  candidate containment on the new path only.
- **This does not achieve Uber/Lyft parity.** Stacked translucent circles compound where
  neighbouring blobs overlap; it is a closer approximation, not a continuous scalar field. The
  genuine-parity path remains PR #5142's server-rendered raster tiles (B4–B6 / M0, M4).

## 11. Sign-off

- [x] Rollback plan is concrete and testable (flag already false; OTA to revert)
- [x] Blast radius is stated, not assumed (greps listed, consumers named)
- [x] No silent behavior change to an already-shipped flow — flag-off is byte-for-byte the
      current renderer on all three surfaces, and §5 covers the flag-on case
- [ ] **Not cleared for flag-on.** Requires native screenshots on iOS, Android and a real head
      unit, plus a jest run in an environment that can reach the npm registry.
