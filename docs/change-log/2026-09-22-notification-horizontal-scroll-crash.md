# Change Impact & Risk Log — notification horizontal-scroll crash

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-22 |
| Author | Codex |
| Surface(s) | rider-app, driver-app |
| Domain (Sentry tag) | notifications |
| PR / commit link | Branch `fix/ride-reliability-notifications` |
| Related issue or gap ID | PR5712 — Android 17 / Fabric notification-screen crash |

## 1. Issue / gap identified

PR5712 reports a native `HorizontalScrollView only one direct child` failure when the rider notification screen is opened on Android 17 with app version 2.0.0+31. A separate native child-remove error is reported for driver notifications.

## 2. Root cause

The exact native failure sequence is **not confirmed**. Both notification screens create a horizontal `FlatList` for category tabs. In React Native 0.86.3, the Android horizontal scroll uses a native horizontal view and a separate content-view wrapper. The app patches differ: `driver-app/patches/react-native+0.86.3.patch` maps both Android horizontal components to plain `View`, while `rider-app/patches/react-native+0.86.3.patch` only adds a `View` import and leaves the native horizontal view/content wrapper selected. That makes the rider's remaining `HorizontalScrollView` a strong match for PR5712's reported native view type. The driver-side child-remove report remains unexplained; its patch bypasses the native horizontal view when applied, so the error could originate elsewhere. React Native's `codegenNativeComponent` runtime still delegates to `requireNativeComponent`; the `interfaceOnly` declaration alone does not prove that the content wrapper is non-renderable or that it caused either incident. Replacing each horizontal `FlatList` also removes virtualization from the fixed category controls, which may reduce native child churn, but that is not a confirmed driver-crash cause. The exact Fabric sequence has not been reproduced on the reporting device.

## 3. Fix / remediation

Replace each horizontal category `FlatList` with a wrapping `View` that renders the same accessible tab buttons. Category filtering, labels, selected state, notification data, and list refresh behavior remain intact. The rider/driver screen fork remains deliberate for navigation and localization (see `docs/known-forks.md`); this matching structural fix is ported to both.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated, cross-app UI.** Only the rider and driver notification screens and their screen tests change. No shared hooks, API requests, native patch, persistence, or notification writes change.
- Existing inbox fetching, unread count, mark-read, delete, clear-all, navigation, and detail-modal flows are unaffected. Their current tests remain in place.
- Categories no longer require horizontal swiping. On narrow screens or with longer driver translations the tabs wrap onto additional rows, increasing header height and moving list content down. Every category stays visible and tappable.
- No interaction with rides, auth, money, backend loops, or the ride state machine.
- No runtime feature flag is added: this removes the only native horizontal scroller from the affected notification controls to mitigate a reported screen crash, while preserving the filter behavior.

## 5. User-experience effect

Riders and drivers see category pills wrap onto one or more rows as needed instead of scrolling the category strip horizontally. This is visible when the screen loads, including after an OTA bundle update. No copy or notification content changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/notifications.tsx` | Render category tabs in a wrapping view | Removes the native horizontal scroll view associated with the reported rider crash |
| `rider-app/__tests__/notificationsScreen.test.tsx` | Assert tabs are accessible, no horizontal `FlatList` exists, filtering works, and remount resets selection | Pins structure and existing filter behavior |
| `driver-app/app/driver/notifications.tsx` | Render category tabs in a wrapping view | Applies the same structural mitigation to the driver screen's child-remove error |
| `driver-app/__tests__/app/driverNotificationsScreen.test.tsx` | Assert no horizontal `FlatList`, verify category filtering/remount behavior; update refresh-test comment | Pins both behavior and the new list hierarchy |
| `driver-app/__tests__/components/ActiveRidePanel.test.tsx` | Clear `AsyncStorage.setItem` call history between automatic-navigation cases | Prevents a prior test's mock calls from leaking into the backgrounded-accept assertion |
| `docs/change-log/2026-09-22-notification-horizontal-scroll-crash.md` | This impact and risk record | Required for a live-tested mobile UI change |

## 7. Before / after

```tsx
// Before: creates a native HorizontalScrollView for the category controls
<FlatList horizontal data={CATEGORY_TABS} renderItem={renderCategoryTab} />
```

```tsx
// After: ordinary accessible controls wrap within the available width
<View style={styles.tabsContent}>
  {CATEGORY_TABS.map((tab) => (
    <TouchableOpacity key={tab.key} accessibilityRole="tab" ... />
  ))}
</View>
```

## 8. Rollback plan

This is a client-rendering-only change; it writes no data. Reverting the two screen changes restores the former horizontal category lists. If already delivered through OTA, a code rollback requires publishing a replacement OTA bundle (or rebuilding through the normal release path); no data repair or migration is needed.

## 9. Verification performed

- [x] Rider notification screen Jest suite — 23/23 tests passed (tab/filter, unread, deletion, remount).
- [x] Driver notification screen Jest suites — 27/27 tests passed across 2 suites (tab/filter, unread, deletion, remount).
- [x] Driver `ActiveRidePanel.test.tsx` baseline comparison: unchanged `origin/main` at `f3ecdfd2` fails the same deferred-navigation assertion (7 stale `AsyncStorage.setItem` calls) when the full file runs; current branch clears that mock's call history in the automatic-navigation setup, and the full file passes 33/33. This repairs test isolation only; no product behavior changed.
- [x] Rider Android production JavaScript bundle export — integrated source bundled 3,370 modules into a 10 MB Hermes bundle.
- [x] Driver Android production JavaScript bundle export — integrated source bundled 3,726 modules into an 11 MB Hermes bundle.
- Both app postinstall scripts were run before final exports: RN 0.86.3 patches applied successfully, plus rider's `@types/react` adjustment and driver's shared-module dedupe. These were Hermes JavaScript bundle exports, not APK/native production builds.
- [ ] Manual validation on Android 17 / reporting device. Device verification remains a release gate.
- [x] Blast-radius check: inspected both notification routes and their existing screen tests; confirmed category controls are the only horizontal `FlatList` in these screens. Reviewed `docs/known-forks.md` before porting the structural fix.
- [x] Reviewed against `CLAUDE.md`, the rider/driver design-system guidance, and notification/accessibility/design reviewer criteria. Tabs retain text labels, `accessibilityRole="tab"`, selected state, and a 44-point minimum height.
- No automated mobile visual-regression or screen-reader tooling exists in this repo. Layout/accessibility behavior is reasoned from JSX and test assertions, not visually screenshotted or manually read with a screen reader.

## 10. Sign-off

- [x] Rollback is data-safe and concrete.
- [x] Blast radius and user-visible layout change are stated.
- [x] The exact Fabric root cause is marked unconfirmed; device validation is not implied.
