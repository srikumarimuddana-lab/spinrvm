# Change Impact & Risk Log: rider-app text follows the OS text-size setting (1.5× cap)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code session (claude.ai/code) |
| Surface(s) | rider-app (plus one additive `shared/` constant) |
| Domain (Sentry tag) | rides / payments (display only) |
| PR / commit link | UX program W1.1a — branch `claude/spinr-animations-admin-ux-x7nl5x` |
| Related issue or gap ID | clean-sheet UXA11Y-001; `docs/audit/2026-09-25-ux-scorecard-world-class-minimal.md` gap 1; plan decision D1 |

## 1. Issue / gap identified

30 rider-app text elements used `allowFontScaling={false}`, which ignores the OS text-size setting entirely. They include:
- fare totals and fare breakdown lines
- ETA and pickup PIN
- trip stats and ride-history fares

`maxFontSizeMultiplier` was used nowhere. A rider who enlarges text for low vision saw these values at default size, which fails WCAG 2.1 SC 1.4.4.

## 2. Root cause

The locks were added defensively to stop large system fonts from breaking tight rows. The chosen fix disabled scaling outright instead of bounding it. Nothing in CI flagged new locks.

## 3. Fix / remediation

- **Shared cap:** added `MAX_FONT_SCALE = 1.5` to `shared/utils/responsive.ts`, one shared cap per decision D1.
- **Replaced locks:** 28 of the 30 locked sites now use `maxFontSizeMultiplier={MAX_FONT_SCALE}`. Text grows with the setting up to 1.5×.
- **Kept one exception:** the 56 pt circular ETA badge in `ride-in-progress.tsx` keeps its lock (2 sites, number and unit), documented with a `font-scale-lock:` comment. Its fixed 22 pt line height would clip larger digits, and the same ETA is shown at scalable size in the trip-stats row.
- **Regression guard:** `rider-app/__tests__/fontScalingLock.test.ts` fails on any new unexplained lock in `rider-app/app` or `rider-app/components`.

## 4. Risk & impact on existing functionality

- **Blast radius: single surface (rider-app), display only.**
  - No state, network, money arithmetic or ride-state logic changes.
  - Fare values are unchanged; only the maximum rendered size of existing strings changes.
- **Default text size: no change.** `maxFontSizeMultiplier` only matters once the OS scale exceeds 1.0.
- **Layout risk, for riders with enlarged text:** text on these screens can now grow by up to 50%. Tight rows (fare breakdown lines, pills, the payment-confirm total) could wrap or truncate at the largest settings.
  - The 1.5× cap bounds this, instead of the unbounded growth that a plain removal of the lock would allow.
  - The accessibility reviewer was asked to check each site's container styles.
- **`MAX_FONT_SCALE` has no other readers yet.** It is additive.
- **Driver-app and shared components:** their remaining locks (about 56 sites) are untouched. That is plan item W1.1b, a separate PR.

## 5. User-experience effect

- **Who sees it:** riders who have increased their OS text size. On the in-trip, payment-confirm, ride-completed, driver-arriving, driver-arrived, activity and splash screens, fares, ETA, PIN, plate and trip stats now appear larger, up to 1.5×.
- **Default text size:** no change.
- **Mid-session:** the change applies on the next render after the app update is installed.
- **Copy:** no copy change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/utils/responsive.ts` | New `MAX_FONT_SCALE = 1.5` | One shared cap (D1) |
| `rider-app/app/ride-in-progress.tsx` | 5 locks → cap; ETA badge keeps a justified lock | Fare, ETA and trip stats scale |
| `rider-app/app/payment-confirm.tsx` | 6 locks → cap | Fare total and breakdown scale |
| `rider-app/app/ride-completed.tsx` | 4 locks → cap | Trip total and stats scale |
| `rider-app/app/driver-arriving.tsx` | 3 locks → cap | ETA, plate and PIN scale |
| `rider-app/app/driver-arrived.tsx` | 2 locks → cap | Arrived chip and PIN scale |
| `rider-app/app/(tabs)/activity.tsx` | 4 locks → cap | History fare and status scale |
| `rider-app/components/BrandSplash.tsx` | 2 locks → cap | Splash tagline scales |
| `rider-app/__tests__/fontScalingLock.test.ts` | New static guard | Stops unexplained locks returning |

## 7. Before / after

```tsx
// Before (payment-confirm.tsx)
<Text style={styles.totalPrice} allowFontScaling={false}>${...}</Text>
```

```tsx
// After
<Text style={styles.totalPrice} maxFontSizeMultiplier={MAX_FONT_SCALE}>${...}</Text>
```

## 8. Rollback plan

**No feature flag.** This follows the #4607/#5830 precedent for accessibility fixes: the change only takes effect for riders who enlarged OS text, it is display only, and it writes no data.

**Tuning without reverting:** change `MAX_FONT_SCALE`, for example to 1.3, in one place and ship through the normal mobile release or OTA path.

**Full rollback:** revert the commits, then ship the same way. No live data is touched.

## 9. Verification performed

- [x] **Guard test:** `fontScalingLock.test.ts` passes with the change. Against the pre-change code it lists 30 violations, so it tests the change, not itself.
- [x] **Screen tests:** the 7 affected rider screen suites pass (activity, driverArrived, driverArriving, paymentConfirm, rideCompleted, rideInProgress, BrandSplash), 287 tests.
- [x] **Typecheck:** `tsc --noEmit` passes in rider-app and driver-app.
- [x] **Lint:** ESLint on the touched files reports the same result before and after: 1 error and 406 warnings, all pre-existing. The error is on `BrandSplash.tsx` line 107, which this change did not touch.
- [x] **Blast radius:** grepped every lock site, and `MAX_FONT_SCALE` has no other consumers.
- [x] **Feature flag:** none; justified in §8.
- [ ] **Accessibility review:** `spinr-accessibility-reviewer` is running on the diff. Its outcome will be recorded here before merge.

## 10. What was NOT verified

- **No device testing.** Nothing was run with the OS text size at its largest setting on iOS or Android, so layout at 1.5× is reasoned about from the styles, not screenshotted. rider-app has no visual-regression tooling.
- **Needs a person:** a manual device pass at the largest non-bold text size on payment-confirm and ride-completed. Those are the densest money screens.
- **No production build.** No EAS or native build was run. The change adds no native dependency; `maxFontSizeMultiplier` is a core React Native `Text` prop.
