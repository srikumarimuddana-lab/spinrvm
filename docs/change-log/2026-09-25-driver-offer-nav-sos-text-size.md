# Change Impact & Risk Log: offer card and nav banner follow the OS text size; SOS stays visible below the nav banner

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code session (claude.ai/code) |
| Surface(s) | driver-app, `shared/components/SOSButton.tsx` (comment only) |
| Domain (Sentry tag) | safety / dispatch / drivers (display and layout only) |
| PR / commit link | UX program W1.1b-3 — branch `claude/spinr-animations-admin-ux-x7nl5x` |
| Related issue or gap ID | clean-sheet UXA11Y-001; plan decision D1; SOS placement decided by the user on 2026-09-25 ("move SOS below banner") |

## 1. Issue / gap identified

1. **Text scaling:** the ride-offer amount and the turn-by-turn banner text ignored the OS text-size setting. Three more locks had no written justification: the SOS small-circle wordmark, the driver splash, and the Android Auto earnings hero. driver-app had no guard against new locks.
2. **SOS overlap (a pre-existing safety defect):** during a ride with turn-by-turn steps, the navigation banner (`zIndex: 150`, `top: insets.top + 8`) was drawn over the SOS button (`zIndex: 50`, `top: insets.top + 56`).
   - At default text size the banner (about 54 pt tall) already covered about 6 pt of the 44 pt button.
   - With the banner's text scaled to 1.5×, it would cover about half.
   - The banner is `pointerEvents="none"`, so SOS stayed tappable but partly hidden.

## 2. Root cause

1. **Text scaling:** the same defensive locking as the earlier text-size PRs.
2. **SOS overlap:** SOS and the banner were positioned independently with fixed offsets, and the banner's height was never accounted for.

## 3. Fix / remediation

- **Offer card:** the earnings amount and its dollar sign scale up to `MAX_FONT_SCALE`. The dollar sign previously scaled without limit. The hero row wraps the trip metrics below a large amount instead of the card's `overflow: hidden` cutting them off.
- **Justified locks:** comment-only changes; each lock keeps a `font-scale-lock:` explanation.
  - SOS wordmark: a fixed 44 pt circle.
  - Driver splash: about 2 s of decorative branding.
  - Android Auto hero: RN applies the phone's text size on the car screen, and the hero is already car-sized.
- **Navigation banner:** its text scales up to `MAX_FONT_SCALE`. It reports its rendered height through a new optional `onHeightChange` prop.
- **SOS placement:** new `sosTopOffset(insetsTop, bannerShown, bannerHeight)`.
  - While a step shows and the banner's height is known, SOS sits 8 pt below the banner.
  - Otherwise, and never higher than this, it stays in its default slot (`insetsTop + 56`).
  - This applies to both SOS variants (`SOSButton` and the flag-gated `SafetyShield`).
- **Guard:** `driver-app/__tests__/fontScalingLock.test.ts` covers driver-app `app`, `components` and `lib`, plus `shared/components`.

## 4. Risk & impact on existing functionality

- **Blast radius:** single surface (driver-app), plus a comment in shared `SOSButton`.
  - The offer-card change is display only. The offer countdown, accept/decline handlers and dispatch logic are untouched.
  - SOS press/hold behaviour, the trigger callback and the `driver_discreet_sos_enabled` gating are untouched; only the wrapper's `top` changes.
- **Default text size, what changes:** during navigation with a turn step showing, SOS moves from `insets.top + 56` to about `insets.top + 70`, just below the banner. This is intentional: it is the fix for SOS being partly hidden. It affects every driver during turn-by-turn navigation, not only drivers with large text.
- **Default text size, what doesn't change:**
  - SOS placement when no step is shown.
  - The offer card; its wrap only applies on overflow.
  - The banner itself.
- **Position change while a step appears or disappears:** SOS moves about 14 pt when a step shows or hides. A reviewer was asked to assess an in-progress hold during that move.
- **Stale height:** `navBannerHeight` is not reset when the banner unmounts, but `sosTopOffset` ignores it whenever no step is shown.
- **Consumers:**
  - `NavigationStepBanner` is used only on the driver dashboard; the new prop is optional.
  - `sosTopOffset` is used only by the dashboard screen.
  - `SOSButton` is used by both apps; its change here is comment-only.

## 5. User-experience effect

- **All drivers during turn-by-turn navigation:** the SOS button sits just below the turn banner and is fully visible, instead of partly under it. It returns to its usual spot when no turn step shows.
- **Drivers with enlarged OS text:** the offer amount and the turn banner appear up to 1.5× larger. The offer card's trip metrics wrap below a large amount.
- **Mid-session:** a driver mid-ride sees SOS move below the banner on the next turn step after updating.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/panels/RideOfferPanel.tsx` | Amount and dollar sign capped; hero row wraps | Offer text scales without clipping |
| `shared/components/SOSButton.tsx` | Comment only (`font-scale-lock:` wording) | Justified lock, guard-readable |
| `driver-app/components/BrandSplash.tsx` | Comments only | Justified locks |
| `driver-app/lib/androidAuto/CarOfferPanel.tsx` | Comment only | Justified lock |
| `driver-app/components/dashboard/NavigationStepBanner.tsx` | Text capped; `onHeightChange`; `sosTopOffset` helper and constants | Banner scales; SOS placement is computed |
| `driver-app/app/driver/(tabs)/index.tsx` | `navBannerHeight` state; SOS wrappers use `sosTopOffset` | SOS stays visible |
| `driver-app/__tests__/components/NavigationStepBanner.test.tsx` | +6 tests | SOS offset rules and height reporting |
| `driver-app/__tests__/fontScalingLock.test.ts` | New guard | Stops unexplained locks from returning |

## 7. Before / after

```tsx
// Before (index.tsx)
<View style={{ position: 'absolute', top: insets.top + 56, right: 16, zIndex: 50 }}>
<NavigationStepBanner ... topOffset={insets.top + 8} />
```

```tsx
// After
const [navBannerHeight, setNavBannerHeight] = useState(0);
const sosTop = sosTopOffset(insets.top, !!currentNavStep, navBannerHeight);
<View style={{ position: 'absolute', top: sosTop, right: 16, zIndex: 50 }}>
<NavigationStepBanner ... topOffset={insets.top + NAV_BANNER_TOP_OFFSET} onHeightChange={setNavBannerHeight} />
```

## 8. Rollback plan

**No feature flag.**
- The SOS move fixes a safety visibility defect. Flagging it would leave SOS partly hidden for everyone the flag is off for.
- The text-scaling parts follow the accessibility precedent (#4607/#5830/#5837/#5838/#5840).

**Full rollback:** revert and ship through the normal release or OTA path. No data is touched.

## 9. Verification performed

- [x] **Tests:**
  - 6 new tests: 5 on `sosTopOffset` (no banner, height not yet known, below banner, taller scaled banner, never above the default slot) and 1 on the banner reporting its height.
  - 10 related suites pass, 153 tests.
  - Full driver-app suite passes: 172 suites, 2138 tests.
  - The rider guard still passes.
- [x] **Driver guard test:** passes, with 5 justified locks remaining.
- [x] **Typecheck:** `yarn tsc --noEmit` is clean (0 errors).
- [x] **Lint:** ESLint on the touched files matches before exactly: 5 errors and 130 warnings, all pre-existing.
- [ ] **Safety review:** `spinr-safety-sos-reviewer` is running. The outcome will be recorded before merge.
- [ ] **Accessibility review:** `spinr-accessibility-reviewer` is running. The outcome will be recorded before merge.

## 10. What was NOT verified

- **No device testing.** The banner's real rendered height, SOS's new position relative to the map controls and speed chip, and the offer card at 1.5× were reasoned about from the styles. driver-app has no visual tooling.
- **Needs a person:** a device pass during an active ride with turn-by-turn steps, at default and at the largest text size. Confirm SOS is fully visible and tappable, including a hold while a step appears or disappears. Also check an offer at the largest text size.
