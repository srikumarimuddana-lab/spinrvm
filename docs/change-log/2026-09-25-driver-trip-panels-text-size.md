# Change Impact & Risk Log: driver trip panels follow the OS text-size setting (1.5× cap)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code session (claude.ai/code) |
| Surface(s) | driver-app (plus two comment-only rider-app corrections) |
| Domain (Sentry tag) | drivers / rides (display only) |
| PR / commit link | UX program W1.1b-1 — branch `claude/spinr-animations-admin-ux-x7nl5x` |
| Related issue or gap ID | clean-sheet UXA11Y-001; plan decision D1; follows #5837 (rider-app) |

## 1. Issue / gap identified

36 driver-app text elements ignored the OS text-size setting via `allowFontScaling={false}`:

| Component | Locked sites | What they show |
|---|---|---|
| Active-ride panel | 21 | status, earnings, route labels, PIN prompt, action buttons |
| Trip-completed panel | 10 | earnings hero, fare breakdown, rating prompt |
| Cancel-reason sheet | 5 | title, warning, reasons, buttons |

Two neighbouring texts, the live earnings figure and the PIN digits, scaled with no limit at all.

## 2. Root cause

The same defensive pattern as rider-app (#5837): scaling was disabled outright instead of bounded, to protect fixed-height buttons and tight rows.

## 3. Fix / remediation

**Scaling**
- All 36 sites now use `maxFontSizeMultiplier={MAX_FONT_SCALE}` (1.5×).
- The earnings figure and PIN digits get the same cap. They previously scaled without limit.

**Layout tweaks, so larger text wraps instead of clipping**

| Element | Change |
|---|---|
| Earnings box | Bounded to 50% width |
| Bonus breakdown | May wrap to 2 lines |
| PIN title | Can shrink beside its icon |
| Primary and secondary action buttons | `minHeight` (same 52/50) instead of a fixed height, plus vertical spacing-token padding only; labels shrink and centre |
| Fare labels and values | Labels can shrink; values get the same cap |
| Cancel sheet | Capped at 92% of the screen, with a shrinking list |

**Also:** corrected two rider-app `font-scale-lock:` comments from #5837 that gave a wrong reason (see §7).

## 4. Risk & impact on existing functionality

**Blast radius: single surface (driver-app), display only.** No handler, state, network, money arithmetic or ride-state logic changed; the diff is props and styles.

**What changes at default text size:**
- **Scaling props:** no change. `maxFontSizeMultiplier` only matters above 1.0×.
- **`minHeight` with padding:** renders the same as before, because content plus padding is shorter than the minimum.
- **`flexShrink`:** only matters when content no longer fits.
- **Needs a device check:** the 50% bound on the earnings box. At default size the figure is far narrower than half the panel; 50% rather than 45% leaves room for a large bonus line on a 320dp screen.
- **Horizontal padding:** none was added to the action buttons. Horizontal padding would narrow the label area at every text size and could make a long label wrap even at default size.

**Consumers:**
- `ActiveRidePanel`, `TripCompletedPanel` and `CancelReasonSheet` are used on the driver dashboard only. Their existing tests (6 suites) pass.
- `MAX_FONT_SCALE` already exists (from #5837) and is unchanged.

**Safety and dispatch:** not touched. SOS, the ride-offer card and the navigation banner are later PRs (W1.1b-2 and W1.1b-3).

## 5. User-experience effect

- **Who sees it:** drivers who enlarged OS text. Trip status, earnings, route labels, PIN prompt, action buttons, trip summary and the cancel sheet appear up to 1.5× larger. Long labels, French especially, wrap onto two lines instead of overflowing.
- **Default text size:** no change.
- **Mid-session:** applies after the app update is installed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/dashboard/ActiveRidePanel.tsx` | 21 locks → cap; earnings and PIN digit capped; box, title and button layout tweaks | In-trip text scales without clipping |
| `driver-app/components/dashboard/TripCompletedPanel.tsx` | 10 locks → cap; fare label can shrink; fare values capped | Trip summary scales consistently |
| `driver-app/components/CancelReasonSheet.tsx` | 5 locks → cap; sheet max height with a shrinking list | Title stays on screen |
| `rider-app/app/ride-in-progress.tsx` | Comment only | Corrects the lock reason |
| `rider-app/components/BrandSplash.tsx` | Comment only | Corrects the lock reason |

## 7. Before / after

```tsx
// Before (ActiveRidePanel.tsx)
actionPrimary: { flexDirection: 'row', height: 52, ... }
<Text allowFontScaling={false} style={styles.statusText}>...</Text>
```

```tsx
// After
actionPrimary: { flexDirection: 'row', minHeight: 52, paddingVertical: SPACING.sm, ... }
<Text maxFontSizeMultiplier={MAX_FONT_SCALE} style={styles.statusText}>...</Text>
```

**Correction to #5837.** Its lock comments said a fixed `lineHeight` would clip scaled text. That is wrong. React Native scales `lineHeight` by the same capped multiplier on both platforms:
- Android: `TextAttributes.kt`, `effectiveLineHeight` uses `toPixelFromSP(lineHeight, effectiveMaxFontSizeMultiplier)`.
- iOS: `RCTTextAttributes.mm`, `_lineHeight * self.effectiveFontSizeMultiplier`.

The two locks stay for their real reasons: the ETA badge is a fixed 56 pt circle that a 2–3 digit ETA overflows, and the splash is about 2 seconds of decorative branding.

## 8. Rollback plan

**No feature flag.** This follows the #4607/#5830/#5837 precedent for accessibility fixes: the change only takes effect for users who enlarged OS text, and it is display only.

**Tuning without reverting:** adjust `MAX_FONT_SCALE`.

**Full rollback:** revert and ship through the normal release or OTA path. No data is touched.

## 9. Verification performed

**Tests**
- [x] Driver suites for the touched components pass: 6 suites, 154 tests.
- [x] `tsc --noEmit` passes in driver-app.
- [x] ESLint on the touched files matches `main` exactly: 2 errors and 98 warnings, all pre-existing. The literal paddings were swapped to `SPACING` tokens to keep that parity.

**Research and review**
- [x] Each site was classified by a read-only analysis of its container styles before editing: 31 needed only the cap, 20 needed a layout tweak, and 5 stay locked in later PRs.
- [x] React Native `lineHeight` behaviour was verified in the installed RN source, not assumed.
- [x] `spinr-accessibility-reviewer` ran on the diff (code-read only).
  - **Blocker:** none.
  - **Should-fix, handled:**
    - Removed the action buttons' horizontal padding. It changed the label width at default size.
    - Loosened the earnings bound from 45% to 50%. At 45% a large bonus line could wrap at default size on a 320dp screen.
    - Kept the PIN digit box as is, flagged for the device check. The digit was unbounded before and is now capped; at maximum size on a narrow phone it may touch the box border, with no hard clip because overflow is visible.
  - **Nit, fixed:** the fare values are now capped like their labels.
  - **Confirmed:** no handler or ride-flow logic changed, and the cancel-sheet shrink pattern is correct.

## 10. What was NOT verified

- **No device testing.** Nothing was run at a large OS text size; driver-app has no visual-regression tooling. Layout at 1.5× was reasoned about from the styles.
- **Needs a person:** a device pass at the largest text size on the active-ride panel (PIN entry and action buttons) and the trip summary, in English and French. Include the PIN digit box on a narrow (~320dp) phone.
- **Other driver surfaces:** driver-app has no guard test yet, and the remaining driver locks (top bar, idle panel, navigation banner, offer card, SOS, Android Auto, splash) are W1.1b-2 and W1.1b-3.
