# Dimension 05 — Android & iOS UI/UX Quality

**Question:** Does the app look and work correctly on every screen size, platform, and accessibility setting?

---

## Checklist

### Screen Sizes & Responsive Layout
- [ ] No hardcoded pixel sizes for padding/margin — use `%` or `Dimensions.get` or safe area insets
- [ ] Test on small screen (iPhone SE: 375×667) — nothing clipped or overlapping
- [ ] Test on large screen (iPhone Pro Max: 430×932) — no awkward empty space
- [ ] Test on Android small (360×640) — same checks
- [ ] Test on Android foldable (unfolded ~673pt wide) — single-column layout still works
- [ ] Safe area insets applied (`useSafeAreaInsets`) — nothing behind notch, home indicator, or status bar

### Keyboard Handling
- [ ] `KeyboardAvoidingView` wraps all screens with text input
- [ ] `behavior="padding"` on iOS, `behavior="height"` on Android
- [ ] All text inputs visible when keyboard is open (not hidden behind keyboard)
- [ ] Form scrolls to focused input if below keyboard
- [ ] Keyboard dismisses on tap outside or on "Done" / submit
- [ ] `ScrollView` wraps forms so content is accessible when keyboard is up

### Maps & GPS
- [ ] Google Maps on Android, Apple Maps on iOS — correct provider selected per platform
- [ ] Map buttons (zoom, locate) are above the map layer (z-index / `pointerEvents`)
- [ ] Map container uses `pointerEvents="box-none"` so buttons receive touches
- [ ] GPS foreground permission requested before background permission
- [ ] Background permission only requested after user confirms they need it (rationale screen)
- [ ] Map has a fallback when API key is missing (graceful error, not blank screen)

### Button Placement & Touch Targets
- [ ] Minimum touch target: 44×44pt (iOS HIG) / 48×48dp (Material Design)
- [ ] `hitSlop` applied to small buttons (e.g. rating stars, close icons)
- [ ] Primary action button at the bottom of the screen — thumb-reachable
- [ ] Android: hardware back button handled in modal screens (`BackHandler`)
- [ ] FABs (floating action buttons) positioned bottom-right per platform conventions
- [ ] Buttons not overlapping other interactive elements

### Information Hierarchy
- [ ] Most important information is visible without scrolling (above the fold)
- [ ] Driver name, rating, and earnings visible at-a-glance on dashboard
- [ ] Error and loading states shown in context — not just a spinner with no text
- [ ] Empty states have a message and a call to action (not just blank)

### Font & Typography
- [ ] `allowFontScaling` set appropriately — large accessibility font sizes don't break layout
- [ ] Minimum font size: 11pt (WCAG recommendation)
- [ ] Platform-specific status bar height handled — not hardcoded 20px fallback on Android
- [ ] Long text truncated gracefully — `numberOfLines` + `ellipsizeMode` set

### Platform-Specific
- [ ] iOS: Dynamic Island / notch area never obscured by app content
- [ ] iOS: Home indicator area free of interactive elements
- [ ] Android: Edge-to-edge display (`edgeToEdgeEnabled: true` in `app.config.ts`)
- [ ] Android: Status bar colour matches app theme
- [ ] Both: SOS / emergency button has visual feedback (pulse animation, haptics)

---

## Severity Guide

| Finding | Severity |
|---|---|
| Critical content hidden behind notch/status bar | CRITICAL |
| Text input hidden behind keyboard — unusable | HIGH |
| Touch target < 20×20pt — cannot reliably tap | HIGH |
| No BackHandler in Android modal — exits ride flow | HIGH |
| Layout completely broken on one device size | HIGH |
| Touch target 20–44pt — small but tappable | MEDIUM |
| Hardcoded pixel values — may clip on some devices | MEDIUM |
| `allowFontScaling` not set — layout breaks at 200% font | MEDIUM |
| Empty/error state missing | MEDIUM |
| Minor spacing inconsistency | LOW |
