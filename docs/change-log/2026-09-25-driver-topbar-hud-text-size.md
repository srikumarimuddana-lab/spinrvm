# Change Impact & Risk Log: driver top bar and online/offline HUD follow the OS text size (1.5× cap)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code session (claude.ai/code) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers (display only) |
| PR / commit link | UX program W1.1b-2 — branch `claude/spinr-animations-admin-ux-x7nl5x` |
| Related issue or gap ID | clean-sheet UXA11Y-001; plan decision D1; follows #5837 and #5838 |

## 1. Issue / gap identified

11 driver dashboard texts ignored the OS text-size setting, and two others scaled with no limit:

| Area | Texts | Before |
|---|---|---|
| Top bar | today's earnings, trip count, surge badge, connection banner | locked |
| Online/offline HUD | status pills, GO/STOP | locked |
| Dashboard screen | connection error, speed readout | locked |
| Online/offline HUD | vehicle and plate text | scaled with no limit |

## 2. Root cause

These texts sit inside containers that animate between fixed heights, each with `overflow: hidden`:
- the top bar's connection banner animates to 28
- the online/offline HUD animates between 90 and 34

Larger text would have clipped, so scaling was disabled instead of letting the heights follow the text.

## 3. Fix / remediation

**Top bar**
- Five texts scale up to `MAX_FONT_SCALE`.
- The banner's target height is `12 + ceil(16 × min(fontScale, 1.5))`. That is 28 at default size, and it grows with its text line.
- The earnings/surge pill row wraps instead of pushing the bell off-screen.
- The 16 pt unread badge keeps a documented lock: it is a fixed dot inside the bell, and the count is in the bell's accessibility label.

**HUD**
- The status pills scale up to `MAX_FONT_SCALE`, and the vehicle and plate text are capped at the same value.
- New exported `hudHeightsFor(fontScale)` grows the expanded and collapsed heights by each pill's text line only. At a font scale of 1 or less it equals the 90/34 constants exactly.
- GO/STOP is one line, shrunk to fit its 72 pt circle, so "STOP" no longer breaks mid-word.

**Dashboard screen**
- The idle map padding uses `hudHeightsFor`, so the map and the HUD always reserve the same space.
- The connection error and speed readout scale up to `MAX_FONT_SCALE`.

## 4. Risk & impact on existing functionality

- **Blast radius: single surface, display only.**
  - No ride-state, dispatch, go-online or money logic changed.
  - `index.tsx` gains one `useWindowDimensions()` call next to the existing `useSafeAreaInsets()`, and its map-padding expression reads the helper.
- **Default text size:** everything is identical.
  - `hudHeightsFor(1)` equals `{ 90, 34 }`, which a unit test asserts.
  - The banner target equals 28.
  - `flexWrap` and `flexShrink` only act when content overflows.
  - `adjustsFontSizeToFit` only shrinks text that does not fit, and at 1× "STOP" fits.
- **Consumers of the old constants:**
  - `HUD_EXPANDED_HEIGHT_DP` and `HUD_COLLAPSED_HEIGHT_DP` remain exported.
  - Their only reader, the map padding in `index.tsx`, now uses `hudHeightsFor`.
  - The dashboard test mocks the component barrel, so the mock gains `hudHeightsFor`.
- **Re-renders:** `useWindowDimensions` re-renders the dashboard only when the window or font scale changes. It doesn't change on ordinary updates.
- **Banner animation:** the effect now also re-runs when the font scale changes. It re-animates to the new target, and has no other effect.

## 5. User-experience effect

- **Drivers with enlarged OS text:**
  - The top bar, connection banner, online/offline pills and speed readout appear up to 1.5× larger.
  - The HUD and the map's reserved space grow to fit the larger pills.
  - If the pills no longer fit on one line, the top-bar pill row wraps.
- **Default text size:** no change.
- **Mid-session:** a driver who changes the text size while online sees the HUD and banner resize on the next render.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/dashboard/DriverTopBar.tsx` | 5 locks → cap; font-scale banner height; wrapping pill row; badge lock justified | Top bar scales without clipping |
| `driver-app/components/dashboard/DriverIdlePanel.tsx` | 3 locks → cap; vehicle/plate capped; `hudHeightsFor`; GO/STOP fit | HUD grows with its text |
| `driver-app/components/dashboard/index.ts` | Re-exports `hudHeightsFor` | Barrel |
| `driver-app/app/driver/(tabs)/index.tsx` | Map padding via `hudHeightsFor`; 3 locks → cap | Map and HUD agree |
| `driver-app/__tests__/components/hudHeightsFor.test.ts` | New: 4 tests | Identical at default size, grows, stops at the cap |
| `driver-app/__tests__/app/driverDashboardScreen.test.tsx` | Barrel mock gains `hudHeightsFor` | Mock mirrors the module surface |

## 7. Before / after

```tsx
// Before (DriverTopBar.tsx)
Animated.timing(bannerHeight, { toValue: showBanner ? 28 : 0, ... })
// Before (DriverIdlePanel.tsx)
outputRange: [HUD_COLLAPSED_HEIGHT_DP, HUD_EXPANDED_HEIGHT_DP]
```

```tsx
// After
const bannerTarget = 12 + Math.ceil(16 * Math.min(fontScale, MAX_FONT_SCALE)); // 28 at 1x
Animated.timing(bannerHeight, { toValue: showBanner ? bannerTarget : 0, ... })
outputRange: [hudHeights.collapsed, hudHeights.expanded] // 34 / 90 at 1x
```

## 8. Rollback plan

**No feature flag.** This follows the accessibility precedent (#4607/#5830/#5837/#5838): it only takes effect for drivers who enlarged OS text, and it is display only.

**Full rollback:** revert and ship through the normal release or OTA path. No data is touched.

## 9. Verification performed

- [x] **New tests:** `hudHeightsFor` unit test (4 tests).
- [x] **Related suites:** 6 suites, 77 tests, including the dashboard screen and DriverIdlePanel suites.
- [x] **Full driver-app suite:** 172 suites, 2131 tests pass.
- [x] **Typecheck:** `tsc --noEmit` passes.
- [x] **Lint:** ESLint on the touched source files matches `main` exactly: 4 errors and 90 warnings, all pre-existing. The new test file is clean.
- [x] **Per-site analysis before editing:** a read-only pass over each site's container styles supplied the banner and HUD height model.
- [ ] **Accessibility review:** `spinr-accessibility-reviewer` on the diff is running. The outcome will be recorded before merge.

## 10. What was NOT verified

- **No device testing.** Nothing was run at a large OS text size; driver-app has no visual tooling. The HUD height model (16 dp per pill text line) is estimated from the styles, not measured.
- **Needs a person:** a device pass at the largest text size while online and offline (HUD expanded and collapsed), with the connection banner showing, in English and French.
- **Still to do:** the navigation banner, offer card, SOS, Android Auto, splash and the driver guard test are W1.1b-3.
