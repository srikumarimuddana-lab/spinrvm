# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude, at user request — "we need to remove the heatmap pill on the top of the screen... change the presentation of the heatmap which currently is a square box... mature presentation of heat without affecting the riders or drivers experience" + follow-up: speedometer "should be zero when still" |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/admin-portal-heatmaps-audit-gm8fbn`, commits `dfdd82656` (speed) + `726681a99` (heatmap) |
| Related issue or gap ID | Follow-up to the same-session compass fix; heatmap rework scoped via two prior AskUserQuestion exchanges ("True native gradient layer", "Split by platform") |

## 1. Issue / gap identified

Two independent driver-dashboard UI issues, both live-reported:

1. The demand heatmap rendered as a permanent pill sitting over the top-center of the map (color-ramp legend + layer picker, always open), and the heat data itself rendered as flat, hard-edged square `Polygon` cells — described as looking like "a square box," not a mature heat visualization.
2. The speed chip hid entirely while stationary (a separate same-session fix for the reported "8 km/h while parked" GPS-noise bug) — the user's follow-up preference is that it should stay visible and read a literal "0" rather than disappear.

## 2. Root cause

1. `DemandLegend` was always rendered in its fully-expanded pill form with no collapsed state, positioned `top-center` — there was no toggle, so it was permanently competing with the driver's view of the top of the map. Separately, `HeatmapCells` only ever rendered `react-native-maps`' `Polygon` component per grid cell (a literal rectangle matching the server's demand-bucket grid), which is why it visually read as square boxes rather than a smooth heat gradient — there was no branch using `react-native-maps`' actual `Heatmap` (gradient) component at all.
2. The prior fix (same session) hid the whole chip below `MIN_DISPLAYED_SPEED_MPS` to suppress the noisy non-zero reading; the visibility condition and the displayed value were the same expression, so "suppress the noise" and "hide the chip" were coupled when they didn't need to be.

## 3. Fix / remediation

1. **`DemandLegend`**: added a collapsed-by-default state — a single small icon button (44px, same visual language as `MapControls`' round buttons) that expands to the existing pill/legend/layer-picker on tap, and collapses back via a chevron in the expanded pill. Moved from top-center to top-right (confirmed free of collision — the SOS shield/button use the same corner but only in ride states mutually exclusive with `idle`, which is the only state this renders in).
2. **`HeatmapCells`**: split the renderer by platform.
   - **Android**: renders `react-native-maps`' native `<Heatmap>` — a true density-gradient layer — fed weighted cell centers, with the gradient stops set to the same 5-step brand ramp (`colors.heatmapRamp`) the legend swatches already use, so the two can never show inconsistent colors for the same intensity.
   - **iOS**: `react-native-maps`' `Heatmap` is documented as Google-Maps-only on iOS, and this app deliberately runs Apple Maps on iOS (see `app.config.ts`'s existing comment on why — a Google Maps migration there is a much larger, out-of-scope change). iOS instead gets two concentric, low-opacity `Circle` rings per cell (radius derived from the server's own grid-cell size, not a hardcoded constant) in place of one hard-edged square — a closer visual approximation of "heat" without a native gradient module.
3. **Speed chip**: decoupled visibility from the noise-floor clamp. The chip now renders at all times while online (no more pop-in/out at the threshold); only the *displayed number* is clamped to a literal `0` when the raw GPS speed is below `MIN_DISPLAYED_SPEED_MPS`.

## 4. Risk & impact on existing functionality

- **Blast radius**: isolated to driver-app's idle-state dashboard UI. No backend, no shared/rider-app code touched. Grepped both `DemandLegend` and `HeatmapCells` for other importers — only `app/driver/(tabs)/index.tsx` renders either; `lib/androidAuto/carSurface.tsx` (Android Auto head unit) reads the same underlying `useDemandHeatmap`/`demandHeatmapShared` data but has its own separate rendering, unmodified here.
- `HeatmapCells`' exported prop signature (`cells`, `region`, `cellLatDeg`, `cellLngDeg`) is unchanged, so the only call site needed no prop-shape changes.
- The Android `<Heatmap>` branch is purely additive rendering — it doesn't touch `useDemandHeatmap`'s polling, layer-selection, or hotspot logic, all of which are unmodified and still feed both platform branches identically.
- `DemandLegend`'s collapsed-by-default behavior is a pure UI-state change (local `useState`, no prop/API change) — every existing consumer of `status`/`visible`/`isV2`/`layer`/`onLayerChange` still works exactly as before, just behind one extra tap.
- Speed chip: removing the visibility gate means the chip now also renders for the brief moment right after going online before the first GPS fix updates `coords.speed` — it will show `0` in that window, which is the correct/intended reading, not a regression.

## 5. User-experience effect

Driver-facing only, both changes:

- The heatmap legend no longer sits open over the top of the map by default — a driver who wants the ramp/layer picker taps a small icon to reveal it (one extra tap versus before, in exchange for a materially less cluttered default view).
- The heat visualization itself looks meaningfully different: a real gradient on Android, softer overlapping blobs on iOS, instead of a grid of flat colored rectangles. This is visible only in `idle` state (the only state the heatmap ever rendered in) and does not change what data is shown, only how it's drawn.
- The speed chip is now always visible while online (previously invisible below ~10.8 km/h) and reads `0 km/h` at rest instead of disappearing — a driver glancing at the dashboard while stopped now sees a persistent, correct readout rather than an intermittently-appearing one.
- None of this is visible mid-session in a way that could confuse an already-online driver — the map continues to function identically (zoom, recenter, follow-camera, route drawing) for both the heatmap and speed changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/(tabs)/index.tsx` | Speed chip always renders while online, value clamped to 0 below noise floor; `DemandLegend` wrapper moved top-center → top-right | User-requested behavior + pill relocation |
| `driver-app/components/dashboard/DemandLegend.tsx` | Added collapsed/expanded state; collapsed = small icon button; expanded pill gained a collapse control | Remove the permanent top-of-screen pill |
| `driver-app/components/dashboard/HeatmapCells.tsx` | Split rendering by platform: native `Heatmap` gradient (Android) vs. soft `Circle`-ring blobs (iOS), replacing the flat `Polygon` grid on both | "Mature presentation," not a square-box grid |
| `driver-app/i18n/en.json`, `fr.json`, `es.json` | Added `heatmap.legend.expand` / `heatmap.legend.collapse` keys | Accessibility labels for the new toggle button, all 3 supported locales |

## 7. Before / after

```tsx
// Before — DemandLegend.tsx: always rendered fully expanded, no toggle
return (
  <>
    <View style={styles.container}>
      <View style={styles.pill}>
        {/* ramp + layer picker, always visible */}
      </View>
    </View>
  </>
);

// After — collapsed by default, expands on tap
if (!expanded) {
  return (
    <TouchableOpacity style={styles.toggleBtn} onPress={() => setExpanded(true)}>
      <Ionicons name={collapsedIcon} size={20} color={collapsedTint} />
    </TouchableOpacity>
  );
}
// ...expanded pill unchanged in content, gained a collapse control
```

```tsx
// Before — HeatmapCells.tsx: one Polygon rectangle per cell, every platform
<Polygon
  coordinates={cellToCorners(cell.lat, cell.lng, cellLat, cellLng)}
  fillColor={hexToRgba(color, 0.4)}
  strokeColor={isTop ? hexToRgba(color, 0.7) : 'transparent'}
/>

// After — platform-split
if (USE_NATIVE_GRADIENT) { // Android
  return <Heatmap points={points} radius={45} opacity={0.75} gradient={{...}} />;
}
// iOS — two concentric low-opacity Circle rings per cell instead of a square
```

```tsx
// Before — index.tsx: chip hidden entirely below the noise floor
{isOnline && (location.coords.speed ?? 0) >= MIN_DISPLAYED_SPEED_MPS && (
  <View style={styles.speedChip}>
    <Text>{Math.round((location.coords.speed ?? 0) * 3.6)}</Text>
  </View>
)}

// After — always visible while online, value clamped instead of chip hidden
{isOnline && (
  <View style={styles.speedChip}>
    <Text>
      {(location.coords.speed ?? 0) >= MIN_DISPLAYED_SPEED_MPS
        ? Math.round((location.coords.speed ?? 0) * 3.6)
        : 0}
    </Text>
  </View>
)}
```

## 8. Rollback plan

Plain `git revert` on both commits — no data, no migration, no API/schema change. All four files are pure client-side rendering/state; reverting restores the prior pill/grid/hide-below-threshold behavior exactly.

## 9. Verification performed

- [x] `tsc --noEmit` — clean, no errors in any changed file.
- [x] `expo lint` (full `app/` + `components/` tree) — 0 errors/warnings in any changed file; the 8 pre-existing errors reported are all in `app/driver/(tabs)/profile.tsx`, untouched by this change (same baseline noted in this session's earlier compass-fix log).
- [x] **Real production build run**: `npx expo export --platform android` AND `npx expo export --platform ios` — both completed successfully (3217/3211 modules bundled, valid `.hbc` bundles produced), not just a dev server or type-check. This is the actual production-bundle equivalent for an Expo/React Native app (there is no `npm run build` script in this app; `build:web` in `package.json` follows the same `expo export` pattern for the web target). Chosen deliberately to validate BOTH platform branches of the new `HeatmapCells` split, since the two branches use different native components (`Heatmap` vs. `Circle`) that only diverge per-platform at runtime, not at bundle time — a single-platform export wouldn't have exercised both code paths' imports.
- [x] Confirmed `react-native-maps` exports both `Heatmap` and `Circle` as named exports (checked `node_modules/react-native-maps/src/index.ts` directly) before using them, rather than assuming the API.
- [x] Confirmed via `MapHeatmap.tsx`'s own prop doc comments that `Heatmap` is "iOS: Google Maps only / Android: Supported" — the basis for the platform split, not a guess.
- [x] Grepped for every other consumer of `HeatmapCells` and `DemandLegend` (only `index.tsx`) and confirmed `lib/androidAuto/carSurface.tsx` (the one other surface reading the same heatmap data) has its own independent rendering, unaffected by this change.
- [x] Validated all 3 edited i18n JSON files (`en.json`, `fr.json`, `es.json`) parse as valid JSON via `node -e "JSON.parse(...)"` after editing.
- [x] Confirmed top-right placement for the collapsed heatmap toggle doesn't collide with the SOS shield/button in the same corner, by checking their render conditions are mutually exclusive ride states (`idle` vs. `navigating_to_pickup`/`arrived_at_pickup`/`trip_in_progress`).

## What was NOT verified

- **No live device or EAS build/visual confirmation.** This sandbox has no device/emulator and no EAS credentials (same disclosed gap as this session's compass fix) — the gradient Heatmap's actual on-screen appearance on a real Android device, and the Circle-ring blobs' appearance on a real iOS/Apple Maps device, have not been visually confirmed. The production Metro/Hermes bundle builds cleanly and the native components are correctly imported per `react-native-maps`' own type/prop documentation, but "does it actually look good" is a visual judgment call not exercised here.
- **No visual-regression tooling exists for driver-app** (same standing gap noted throughout this session and in `ACTION_ITEMS.md`) — this change is reasoned about from the library's documented behavior, not screenshotted before/after.
- **Jest suite could not run** — same pre-existing, unrelated sandbox failure (`TypeError: _lruCache is not a constructor` from `babel-preset-expo`) noted throughout this session; neither changed file has an existing unit test exercising `MapView`/`Heatmap`/`Circle` rendering directly, so this gap has no test-coverage loss beyond what already existed.
- **The Heatmap `radius`/`opacity` values (45px / 0.75) and the Circle blob radius multiplier (0.62 / 0.5 of the grid cell) are reasoned defaults, not tuned against real demand data** — real driver feedback after this ships may want these adjusted; flagging so a follow-up tuning pass isn't mistaken for a bug report.
- **"Service area boundaries" from the original request were not added as a new always-visible-while-idle feature.** The existing `service_area_polygon` boundary only renders when there's an active/incoming ride carrying that data (a different, pre-existing surge-boundary feature, untouched here) — adding a new "always show my operating boundary while idle" feature was not part of the two AskUserQuestion exchanges that scoped this specific rework (native gradient layer, platform split) and was deliberately left out rather than silently expanding scope; call it out explicitly if still wanted as a separate follow-up.
