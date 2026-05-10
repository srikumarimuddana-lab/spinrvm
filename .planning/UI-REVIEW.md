# Spinr Mobile — UI Review (Adversarial Audit)

**Audited:** 2026-05-09
**Branch:** `fix/rider-app-expo-sdk55`
**Baseline:** Abstract 6-pillar standards (no UI-SPEC.md exists; no GSD phase)
**Method:** Code-only audit. No dev server, no screenshots. All findings derived from source.
**Scope:** `rider-app/`, `driver-app/`, `shared/` (mobile RN, Expo SDK 54, RN 0.85.2, New Arch)

---

## Executive Summary

**Overall: 13 / 24** — Needs work. Production-quality code where individual screens are concerned, but a systemic absence of cross-cutting infrastructure (responsive layout, keyboard handling, theme tokenization) means every input-heavy and small-screen experience is at risk. The user's reported symptoms (keyboard occlusion, alignment breakage across phones) are **real and correctly diagnosed** — they are direct, predictable consequences of the patterns below.

| Pillar | Score | One-line verdict |
|---|---|---|
| 1. Copywriting | 3/4 | Mostly solid voice; a few generic "Please try again" patches |
| 2. Visuals | 3/4 | Clean, consistent visual system; iconography disciplined |
| 3. Color | 2/4 | Theme exists, but heavy hardcoded hex bypassing it (esp. semantic colors) |
| 4. Typography | 2/4 | No scale system; 8+ fontSize values, mixed `fontWeight` strings vs custom font families across screens |
| 5. Spacing | 2/4 | No spacing scale used; arbitrary values (10/12/14/18/26/28/34/36) sprinkled |
| 6. Experience Design | 1/4 | **BLOCKER** keyboard handling, no responsive layer, diagnostic logs in prod, frozen `Dimensions.get` |

### Top 3 BLOCKERs (must fix before next ship)

1. **Keyboard occludes the verify/continue button on small Androids.** `rider-app/app/login.tsx`, `driver-app/app/login.tsx`, `rider-app/app/otp.tsx`, `driver-app/app/otp.tsx` all use `<KeyboardAvoidingView>` with **no `keyboardVerticalOffset`, no `ScrollView` fallback, and content vertically centered with `justifyContent: 'center'`**. On a 320×640 Galaxy A series with the soft keyboard up (≈45 % of viewport), the centered content is pushed off the top of the safe area while the CTA still sits in the keyboard's path. The rider OTP screen specifically *had* a `ScrollView` and it was removed in commit `0d3cdc91`, replaced with `<View style={{flex:1}}>` (line 252) — the driver OTP imports `ScrollView` (line 14) but renders a `<View>` (line 246). The two diverged.
2. **`Dimensions.get('window')` is read at module-load time and frozen for the lifetime of the JS context.** `shared/components/CustomAlert.tsx:20` uses this `SCREEN_WIDTH` value in the modal's `width: SCREEN_WIDTH - 56` style. After device rotation, foldable unfold (Galaxy Z Fold), tablet split-screen, or RN re-mount the alert renders at the *previous* screen width — visibly clipped or floating in dead space. The driver `login.tsx:27` and `otp.tsx:28` also do this but never actually use the captured value (dead code with the wrong intent baked in).
3. **`shared/utils/responsive.ts` was authored but is dead code.** `useResponsive` is exported, fully-formed with breakpoints, spacing, and font scales, and has **zero call sites in production code** (only graphify reports reference it). Every screen reinvents responsive logic ad hoc — `rider-app/app/(tabs)/index.tsx:62` does `width >= 768`, `rider-app/app/ride-in-progress.tsx:55` does the same comparison locally, hard-coded `paddingHorizontal: 24` and `paddingHorizontal: 20` are scattered across siblings (rider home: 20, login: 24, profile-setup: 24, ride-in-progress: variable). The contract exists; nobody signed it.

### Top 3 WARNINGs

1. **Production console.log statements ship to release builds** — `rider-app/app/otp.tsx:226`, `driver-app/app/otp.tsx:220`, `shared/components/CustomAlert.tsx:25` (`[OtpScreenDiag]`, `[CustomAlertDiag-module]` blocks), `shared/components/OfflineBanner.tsx:60` (unconditional log on every network state change). These are gated by neither `__DEV__` nor a logger abstraction. Per project TS rule: "No `console.log` statements in production code."
2. **Hardcoded hex colors bypass the theme system.** `shared/components/CustomAlert.tsx:73-95` defines variant `iconColor`/`iconBg`/`buttonColor` with hex literals (`#3B82F6`, `#F59E0B`, `#EF4444`, `#10B981`, `#FFFBEB`, …). These are the *semantic colors* — info/warning/danger/success — the exact thing a dark-mode theme is supposed to remap. Result: the entire alert system is invariant to dark mode. `SOSButton.tsx:230-265` and `driver-app/app/driver/(tabs)/index.tsx:933-967` (offer-panel decline/accept) follow the same pattern.
3. **No shared `<FormScreen>` wrapper.** Every input screen re-implements the KAV + safe-area + dismiss-on-tap + scroll-fallback pattern with subtle drift: `rider-app/login.tsx` has no scroll fallback, `rider-app/profile-setup.tsx:311-317` wraps in KAV only on iOS, `driver-app/profile-setup.tsx:239-249` wraps unconditionally and uses `automaticallyAdjustKeyboardInsets`, `rider-app/manage-cards.tsx` uses `KeyboardAvoidingView` but no `ScrollView` at all (the card form will get occluded). Inconsistent application directly produces inconsistent UX. This is a **WARNING** because no single screen is broken, but the cumulative drift is what produces the "every phone behaves a bit differently" report.

---

## Pillar Scores (with detailed evidence)

### Pillar 1 — Copywriting (3/4)

**Strong points:**
- Auth flow voice is warm and specific: "Welcome back 👋", "We'll send you a verification code", "Your number is secured and only used for verification". CTAs are intent-named ("Send Verification Code", "Verify & Continue", "Create Profile"), not generic "Submit".
- 429 OTP rate-limit handling parses `Retry-After` + detail string and shows actual remaining seconds (`rider-app/app/otp.tsx:199-208`). Tone is constructive: "Too Many Attempts — Please wait 60 seconds…".
- Promo banner copy is on-brand ("Ride local. Support local. We take 0% commission.") — directly aligned with "What Spinr Is NOT" in CLAUDE.md.
- Accessibility hints written, not just labels (`rider-app/app/(tabs)/index.tsx:419-440` — search bar, AI button, quick actions all have `accessibilityHint`).

**WARNINGs:**
- `'Please try again'` recurs 14× across the codebase (search-destination, payment-confirm, wallet, ride-in-progress, otp, ride-options, account, loyalty). It is the exact "generic recovery copy" pattern flagged by the audit checklist. Replace with the action-specific recovery instruction wherever the system actually knows what failed (e.g. "Couldn't reach the payment server — check your connection or pick another card").
- `'Something went wrong'` is the title in the global `ErrorBoundary.tsx:37`. For a ride-share product where errors interrupt actual rides in progress, the error boundary's job is to give the user a *next action* (retry, call support, return to home) — not narrate the existence of an error.
- `rider-app/app/(tabs)/index.tsx:432` shows AI Ride Booking as `'Coming Soon'` but it ships in production today. Either remove the button or gate the whole feature behind a feature flag with the button hidden when off — a permanent "Coming Soon" tile is template UI.
- The `__DEV__` "Dev mode — OTP is 1234" hint (`rider-app/app/login.tsx:185-190`, `driver-app/app/login.tsx:217-222`) is correct, but should additionally surface the dev backend URL so internal testers know which environment they're hitting.
- Inconsistent variant capitalization on the gender input — `rider-app/profile-setup.tsx:136` uses `['Male', 'Female', 'Other']` (TitleCase), backend likely normalizes; if so, surface the canonical value not the display value. Minor.

### Pillar 2 — Visuals (3/4)

**Strong points:**
- Iconography is consistent: a single Ionicons family across both apps with deliberate size choices (12/14/16/20/22/24/28/32/40). No mixing of icon libraries.
- Visual hierarchy in OTP screens is genuinely good: the 4 code boxes use scale animation + active/filled states + cursor indicator; the shake-on-error animation is well-tuned (60 ms × 5 phases, `rider-app/app/otp.tsx:113-119`).
- Driver dashboard ride-offer panel is high-density information-dense without being cluttered: countdown circle, fare prominence, route timeline, distance-to-pickup badge, driver-name + rating badge, accept/decline weighted 2:1 (offer panel is a clear focal point, lines 395-491).
- Trip route polyline interpolates color across 20 segments (`driver-app/.../index.tsx:617-646`) — proper editorial treatment, not a generic blue line.

**BLOCKERs:** none.

**WARNINGs:**
- The CustomAlert modal width formula is `SCREEN_WIDTH - 56` (line 270) — this lays out a 28 px margin on each side. On a 320 px-wide Galaxy A small screen the alert is 264 px — on a 430 px iPhone Pro Max it's 374 px. For comparison, on an iPad in portrait (768 px) the alert balloons to 712 px before the `maxWidth: 360` cap kicks in. The formula doesn't represent a deliberate design system choice; it's "a margin we picked and froze on first launch."
- `rider-app/app/(tabs)/index.tsx` mixes `fontFamily: 'PlusJakartaSans_500Medium'` (custom) with `fontWeight: '700'` (system fallback) in adjacent styles (e.g. greeting vs avatar text). On Android the system font stack picks DroidSans for the latter; the visual mismatch shows up as inconsistent x-heights. Either commit to PlusJakartaSans across the board (and delete `fontWeight`) or commit to platform default.
- `rider-app/app/(tabs)/index.tsx:766` `quickActionIcon` background is hard-coded `'#FFF0F0'`, `rider-app/.../(tabs)/index.tsx:779` promo banner is `'#FFF8F0'`, `:787` promo icon container is `'#FFE8E8'`. Three different "primary tint" surfaces, none from the theme. Same screen.
- Avatar fallback color `'#D4C4A8'` (`rider-app/app/(tabs)/index.tsx:557`) — a tan tone — is brand-foreign and not in `lightColors`/`darkColors`. Mystery hex.
- `OfflineBanner` uses `📡` emoji as its icon (line 95) where every other surface uses Ionicons. Visually jarring break.
- The "Sheet handle" pattern (`rider-app/(tabs)/index.tsx:411`) is hand-rolled (a 40×4 dim view) — same screen also uses `@gorhom/bottom-sheet` (`ride-in-progress.tsx`). Pick one bottom-sheet abstraction.

### Pillar 3 — Color (2/4)

**Theme exists.** `shared/theme/index.ts` defines `lightColors` and `darkColors` palettes, both indexed by 18 named keys, with iOS-system-level dark colors (true black, iOS label hierarchy). `ThemeContext` follows OS preference unless overridden, persists via AsyncStorage, and `isDark` is exposed. The architecture is correct.

**Theme is partially bypassed.** Six classes of bypass found:

1. **Status / variant colors hardcoded.** `CustomAlert.tsx:73-95` hardcodes `info: #3B82F6 / #EFF6FF`, `warning: #F59E0B / #FFFBEB`, `danger: #EF4444 / #FEF2F2`, `success: #10B981 / #ECFDF5`. The theme already has `error`, `success`, `warning` for the foreground tones — the missing piece is the *bg tint* token, but instead of adding `errorBg` / `successBg` / `warningBg` / `infoBg` to the theme the colors got inlined. Result: the alert renders with light-mode tint colors on a dark-mode surface. **Visible regression on dark mode.** This affects every alert in the app since `CustomAlert` is the universal modal.
2. **SOS button.** `SOSButton.tsx:232-265` uses `#DC2626` (red), `#B91C1C` (pressing), `#D97706` (sending), `#10B981` (sent), `#92400E`/`#FCD34D` (failed). None go through the theme. The "amber" failure state is intentional and brand-signal — it should be a token.
3. **Driver dashboard offer panel.** `driver-app/.../index.tsx:933` `tripInfoBadge` background is `#F0FDF9` border `#D1FAE5`; `:957` `declineBtn` is `#FEF2F2` border `#FECACA`; `:967` `declineText` is `#FF4757`. All hardcoded.
4. **Rider home tinted surfaces.** As above — three tints (`#FFF0F0`, `#FFF8F0`, `#FFE8E8`) on one screen.
5. **Notification badge.** `rider-app/.../(tabs)/index.tsx:608` `notifBadge` is `'#EF4444'` — same red used everywhere but committed to literal.
6. **Map markers.** `driver-app/.../(tabs)/index.tsx:335` pickup `#10B981`, `:352` dropoff `#EF4444`, `:425-427` route dots, `:626` polyline interpolation `rgb(238, 43, 43)` etc. Map markers reasonably stay outside the theme (they need to be readable on map tiles regardless of dark/light), but they should still come from a `mapPalette` constant, not be inlined.

**60/30/10 distribution is fine.** Brand red `#FF3B30` is restrained — used on CTA, focused borders, primary icons, badges. Surfaces are predominantly white/grey. No overuse.

**Score 2/4** because the bypass is systemic, not incidental: it happens in shared components (`CustomAlert`, `SOSButton`) that propagate the bypass to every consumer. Dark-mode parity is broken in alerts and SOS — meaning the dark-mode surface area the user actually sees is not 100 % theme-driven. With the theme architecture already in place, this is fixable by token addition (no architectural change), so a 2 not 1.

### Pillar 4 — Typography (2/4)

**No scale system in use.** `shared/utils/responsive.ts` defines a `FONT` constant with 7 levels (h1/h2/h3/bodyLg/bodyMd/bodySm/label = 32/26/22/16/15/13/11). It is not imported anywhere in the apps.

**Font sizes actually in use** (from a single representative screen, `rider-app/app/login.tsx`): 11, 12, 13, 14, 15, 16, 18, 24, 28. Nine sizes on one screen. `rider-app/app/otp.tsx` adds 17 and 26 and 28 → eleven distinct sizes between two adjacent screens.

**Font weights**: across `login.tsx` alone — `'500'`, `'600'`, `'700'`, `'800'`. Then in `(tabs)/index.tsx` add `fontFamily: 'PlusJakartaSans_400Regular'` / `_500Medium` / `_600SemiBold` / `_700Bold` (4 custom-font weights). The driver dashboard uses `fontWeight: '800'` for fare and countdown. Some screens use system weights; some use the loaded PlusJakartaSans family — sometimes both on the same screen.

**Letter spacing** is hand-tuned per component (`-0.5`, `-1`, `0.5`, `1`). No baseline. The very-large fares use `-1` for tight density (correct for display type); the section labels use `+1` for caps treatment (correct). Other usages look picked at random.

**Line heights** are inconsistent: subtitle line-height of 22 paired with fontSize 15 (1.46×) on login, 18 paired with fontSize 12 (1.50×) on terms, and several Text elements have no `lineHeight` at all so they fall back to font defaults (which differ between iOS and Android — directly produces alignment drift across platforms).

**Font scaling response not handled.** `allowFontScaling={false}` appears once (`driver-app/.../(tabs)/index.tsx:512`, on the WS error banner). Everywhere else the OS Dynamic Type / Android font-size-up-to-1.3× will scale text. That's the right *default*, but the app has hardcoded heights everywhere (`height: 60` on input containers, `height: 58` on buttons, `height: 56` on inputs) — so when the OS scales text up, text overflows or clips. No `flexShrink`, no `numberOfLines` on most fields. Try the login screen with Android "Largest" font setting and the "Send Verification Code" button text will be either clipped or push the icon out of frame.

**Score 2/4.** The infrastructure (PlusJakartaSans family loading, `FONT` constant) is half-done; the discipline isn't there.

### Pillar 5 — Spacing (2/4)

**No spacing scale used.** `responsive.ts` defines `SPACING = { xs: 4, sm: 8, md: 16, lg: 24, xl: 32, xxl: 48 }`. Zero call sites.

**Spot-check of unique spacing values across audited screens** (`paddingHorizontal`, `paddingVertical`, `marginBottom`, `gap`):

`login.tsx`: 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 28, 36, 60.
`otp.tsx`: 4, 6, 8, 10, 12, 14, 16, 18, 24, 26, 28, 36, 48, 56, 64.
`profile-setup.tsx` (rider): 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 28, 32, 36, 40, 90.
`profile-setup.tsx` (driver): 2, 4, 6, 8, 10, 12, 13, 14, 16, 20, 22, 24, 32, 36, 50, 56, 58.

**Adjacent screens use different gutters.** `rider-app/(tabs)/index.tsx` uses `paddingHorizontal: 20`, `login.tsx` uses `24`, `profile-setup.tsx` uses `24`, `ride-in-progress.tsx` varies. No declared system.

**Inset compensation is correct where it appears.** `rider-app/app/login.tsx:234` `paddingBottom: insets.bottom + 16`, `rider-app/app/otp.tsx:254` `paddingTop: insets.top + 16, paddingBottom: insets.bottom + 24`. So safe-area awareness exists — it's not centralized.

**Tap targets**: button heights 58/60 are good (≥ 44 pt). Code-box height 64 good. But `mapControlButton: width 44, height 44` (`rider-app/(tabs)/index.tsx:667`) is exactly the iOS minimum — the divider then visually splits it making each control look smaller. The `notificationButton:44/44` (`:592`) is fine. The `aiButton:56/56` (`:725`) good. The custom alert buttons `paddingVertical: 14` ≈ 48 px hit area — fine.

**Negative finding — gestural overlap on driver dashboard.** The map controls + SOS button + bottom panels all live in `position: 'absolute'` with hardcoded offsets. On a 320×640 screen with `insets.bottom + 34` (`rideOfferContent: paddingBottom: 34`), the offer panel's accept button can sit very close to the home gesture indicator on Android navigation-gesture devices. No `Math.max(insets.bottom, 16)` clamp.

**Score 2/4.** Hand-rolled values *mostly* land on a 4-grid (rare 6/10/22/26 outliers), but the absence of a tokenized system means every refactor or feature add starts the drift over. The `SPACING` token literally exists and is unused.

### Pillar 6 — Experience Design (1/4)

This is where the user's reported pain is concentrated. **Pillar score 1 because the keyboard story breaks task completion on small Android in the auth flow** — the entry point of the app.

**BLOCKERs (each justifies the 1-score):**

#### B-1 — Keyboard occlusion on auth flow

**Pattern:** `rider-app/app/login.tsx:108-251`, `driver-app/app/login.tsx:139-285`.
- Root: `<KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={styles.container}>`
- No `keyboardVerticalOffset` (so on Android with `adjustResize` + a translucent status bar, the offset is wrong by `StatusBar.currentHeight`).
- Inside: `<View style={styles.content} justifyContent: 'center'>`. **Vertically centered content + KAV + small viewport = top of content scrolls out of safe area when the keyboard pushes the layout up.**
- No `<ScrollView>` wrapping the form. There is no overflow path.
- The `<Text>` "Send Verification Code" button is at the bottom of the centered block. On a 320×640 device with the keyboard up (≈ 290 px keyboard), the available content height is 350 px. The header (top strip + `paddingTop: insets.top`) eats 80–100 px. The welcome section is `marginBottom: 36` + content. The button + footer + terms eat another 120 px. There is **no room** — and the `behavior: 'height'` on Android forcibly compresses, which on RN 0.85 with New Arch produces inconsistent results across OEMs (Samsung One UI vs Pixel vs Xiaomi keyboard insets all differ).

**Pattern:** `rider-app/app/otp.tsx:247-388`, `driver-app/app/otp.tsx:242-396`.
- Same KAV, same centered flex, **but rider OTP uses `<View style={[styles.scrollContent, { flex: 1 }]}>` (line 251)** while driver OTP imports `ScrollView` (line 14) and uses the same `<View>` wrapper at the same line range. The `ScrollView` import in driver OTP is *unused*. Both screens lack a scroll fallback. **Confirmed regression** from commit `0d3cdc91` per session context.
- The 4 code boxes are 56×64 each + 12 px gaps = 260 px wide. On 320 px width with `paddingHorizontal: 24` (= 48 px gutters total), there's 272 px → fits with 12 px slack. On 280 px-wide foldable inner-screen-fold? Overflows.

**Pattern:** `shared/components/CustomAlert.tsx:163-167`.
- Uses `<KeyboardAvoidingView behavior="padding">` unconditionally (no Platform branch). `behavior="padding"` on Android with `windowSoftInputMode="adjustResize"` is wrong — the keyboard already triggers a resize so KAV adds duplicate offset. Result: when an alert has `showInput: true` (e.g. promo code entry), the input + title + message can be pushed **above the top of the screen** on Android.

**Pattern:** `rider-app/app/profile-setup.tsx:308-317`.
- Wraps in KAV **only on iOS** (`Platform.OS === 'ios' ? <KAV>… : contentNode`). On Android the form relies entirely on `windowSoftInputMode="adjustResize"`. That actually works for the ScrollView inside — but it's an inconsistent decision rule.
- `driver-app/app/profile-setup.tsx:239` uses KAV always plus `automaticallyAdjustKeyboardInsets={true}` (which is iOS-only — silently ignored on Android). The two profile-setup screens implement keyboard handling **differently**.

**User's specific complaint about field-regaining-focus-when-keyboard-returns:** RN's default behavior is that returning to the screen does NOT auto-refocus a previously focused input. The OTP screens use `autoFocus` (line 290 rider, line 290 driver) which fires on mount. But if the keyboard is dismissed mid-entry (e.g. user taps an alert) the input loses focus and there is **no `useFocusEffect` re-focus on return**. This is the user's reported symptom.

#### B-2 — Frozen `Dimensions.get('window')`

`shared/components/CustomAlert.tsx:20` is the most damaging instance — a shared component, on every screen. Already detailed above.

`driver-app/app/login.tsx:27`, `driver-app/app/otp.tsx:28` — same pattern but the captured `SCREEN_WIDTH` is never read (the destructuring is dead code).

#### B-3 — Diagnostic logs in production code path

- `rider-app/app/otp.tsx:226-244` — `[OtpScreenDiag]` log. Comment says "Remove once OtpScreen render error is fixed." Per recent commit history (`83a56584 wip: pause — OtpScreen Element type undefined; diag builds running`), the bug is being investigated; that's fine for a debug build, **not** for a production release.
- `driver-app/app/otp.tsx:220-239` — same pattern.
- `shared/components/CustomAlert.tsx:25-41` — `[CustomAlertDiag-module]` log fires **at module evaluation time**, i.e., on every cold start before any UI renders. For a shared component this fires once on mount; nonetheless it ships in release. The diag also imports `BlurView` from `expo-blur` (line 17) just to log its `displayName` — that's an extra native module dependency to support a diagnostic.
- `shared/components/OfflineBanner.tsx:60` — unconditional `console.log('[OfflineBanner] Network state changed:', …)` on every NetInfo event. On a flaky network this is one log per second. A user who travels between cells fills the device log buffer.
- `rider-app/app/(tabs)/index.tsx:71,79,135` — `console.error('[index]', err)` in catch blocks where the error is recoverable (AsyncStorage read failures — they fall through to defaults). This is using `console.error` as a logger, with no domain/surface tag. CLAUDE.md observability conventions explicitly require logger module + Sentry tags.

**WARNINGs (Pillar 6):**

- **No focus management on screen revisit.** `useFocusEffect` is only used on `rider-app/app/login.tsx:35` to clear partial auth state — not to restore field focus. The user explicitly asked for this.
- **No `keyboardShouldPersistTaps`** on most screens. `rider-app/profile-setup.tsx:173` and `driver-app/profile-setup.tsx:246` have it set correctly to `'handled'`. `rider-app/app/manage-cards.tsx` has no ScrollView at all (the card form uses a FlatList for the saved-cards list — but the add-card form is *outside* the FlatList). Tapping outside the card-name field does nothing because there's no dismiss-on-tap and no `keyboardShouldPersistTaps`.
- **Hardware back button not handled** consistently. Only `rider-app/app/ride-in-progress.tsx:14` imports `BackHandler`. On the OTP screen the user pressing system back returns to login — this is correct because `router.back()` is wired (line 257) — but then login's `useFocusEffect` triggers `logout()` which clobbers any in-flight login state. There's no warning/confirm.
- **No skeleton states** for first-paint. The driver dashboard `if (!location?.coords) return <ActivityIndicator>` (line 495) is "spinner with text" — a skeleton outline of the real layout would feel less jarring.
- **Error boundary is a placeholder.** `shared/components/ErrorBoundary.tsx:37` shows "Something went wrong" with no recovery action wired (the SOS button persists, the "go home" CTA doesn't exist). For a ride-in-progress crash, this is the wrong screen.
- **`accessibilityHint` coverage uneven.** `(tabs)/index.tsx`: 5 hints. `otp.tsx`: 0 hints (only labels). Login: 3. The OTP screen — the most important auth interaction — has labels but no hints; the verify button has no `accessibilityHint`.
- **Status bar treatment is inconsistent.** `rider-app/login.tsx:113` uses `<StatusBar barStyle={isDark ? 'light-content' : 'dark-content'} />` (correct). `driver-app/login.tsx:144` hard-codes `barStyle="dark-content"` — breaks dark mode. `driver-app/.../(tabs)/index.tsx` does not set StatusBar at all on the dashboard.
- **No "are-you-sure" confirmation on destructive actions** beyond the change-number flow. `rider-app/app/saved-places.tsx` + `manage-cards.tsx` likely allow delete; verify they confirm. (Manage-cards file shown only in part.)
- **Driver dashboard renders a full-screen MapView with overlay panels.** No `<SafeAreaView edges>` around the map; insets are read in `useSafeAreaInsets()` (line 50) but only applied to the SOS-button container (line 675). The `DriverTopBar` and `DriverIdlePanel` components must compensate internally — that's a contract assumption every panel author has to remember and easy to violate. On a foldable inner display the bottom-rounded sheet + safe-area + nav-bar gesture all collide.

---

## Mobile-Specific Deep Dives

### Keyboard Handling Audit (screen-by-screen)

| Screen | Wrapper | KVO offset | Scroll fallback | Risk |
|---|---|---|---|---|
| `rider-app/login.tsx` | KAV (iOS:padding, Android:height) | ❌ | ❌ | **HIGH** small Androids: button under keyboard |
| `driver-app/login.tsx` | KAV same | ❌ | ❌ | **HIGH** same |
| `rider-app/otp.tsx` | KAV same | ❌ | ❌ (`<View flex:1>`) | **HIGH** regressed from ScrollView in 0d3cdc91 |
| `driver-app/otp.tsx` | KAV same | ❌ | ❌ (imports ScrollView, doesn't use it) | **HIGH** same |
| `rider-app/profile-setup.tsx` | KAV iOS-only + ScrollView always | ❌ | ✅ (ScrollView, keyboardShouldPersistTaps='handled') | LOW (works) |
| `driver-app/profile-setup.tsx` | KAV always + ScrollView | ❌ | ✅ + `automaticallyAdjustKeyboardInsets` (iOS-only flag) | MED inconsistent with rider |
| `rider-app/manage-cards.tsx` | KAV unconditional | ❌ | ❌ (FlatList for list, no SV around add form) | **HIGH** add-card form occluded |
| `rider-app/saved-places.tsx` | (need to confirm) | — | — | check next sweep |
| `rider-app/chat-driver.tsx` | uses `keyboardVerticalOffset` ✅ | ✅ | likely ✅ | LOW |
| `rider-app/wallet.tsx` | uses `keyboardVerticalOffset` ✅ | ✅ | likely ✅ | LOW |
| `shared/CustomAlert.tsx` (showInput) | KAV "padding" unconditional | ❌ | ❌ | **HIGH** Android: input pushed off-screen |

**Single highest-leverage fix:** create a `<FormScreen>` component (`shared/components/FormScreen.tsx`) that wraps content in `KeyboardAvoidingView` + `ScrollView` + `keyboardShouldPersistTaps='handled'` + `keyboardDismissMode='on-drag'` + computes `keyboardVerticalOffset` from the SafeAreaView header — and replace the 8 occurrences. Estimate: ≈ 2 hours, removes 6 BLOCKER instances.

### Safe-Area / Responsive Geometry Audit

- **`useResponsive` hook is dead code.** `shared/utils/responsive.ts` exports `useResponsive()`, `BREAKPOINTS`, `SPACING`, `FONT`, `MIN_TOUCH` — zero call sites. Adopting it (delete `Dimensions.get`, replace with `useResponsive`) restores foldable / split-screen / rotation-correctness.
- **`useWindowDimensions` is used in only 3 production files**: `rider-app/app/(tabs)/index.tsx`, `rider-app/app/ride-in-progress.tsx`, `rider-app/app/ride-options.tsx`. The driver dashboard does NOT use it; its layout assumes portrait phone forever.
- **Orientation lock is `portrait`** in both AndroidManifests (line 25 / 27). This is correct policy for Spinr (avoids landscape map layout issues on mid-range Android), but **note this means foldable inner-display unfold (Galaxy Z Fold) still triggers a configChanges event** — `configChanges="keyboard|keyboardHidden|orientation|screenSize|screenLayout|uiMode|smallestScreenSize"` is the right list. That part is fine.
- **`SafeAreaView` from `react-native-safe-area-context` is used inconsistently**. `rider-app/app/(tabs)/index.tsx:233` wraps the header. `driver-app/app/driver/(tabs)/index.tsx` does not wrap anything in SafeAreaView; it reads insets manually. The `OfflineBanner` reads `insets.top` and positions itself absolute (correct).
- **Screen-by-screen padding discipline** as detailed in Pillar 5.

### Theme & Dark-Mode Parity Audit

- **Architecture: solid.** `ThemeProvider` is system-aware, persistent, no flash. `useTheme()` is the single entrypoint.
- **Dark-mode parity in audited screens**:
  - `rider-app/login.tsx`, `rider-app/otp.tsx` — uses `colors.*` everywhere. Likely correct.
  - `driver-app/login.tsx` — `<StatusBar barStyle="dark-content" />` (line 144) hardcoded. **BROKEN** in dark mode.
  - `CustomAlert` — variant colors hardcoded → all alerts wrong in dark mode.
  - `SOSButton` — entirely hardcoded → SOS button looks the same in light + dark, but the surrounding screen changes.
  - `OfflineBanner` — uses `colors.error` (line 174) ✅ but text is hardcoded white (correct since the banner is always red).
- **Dark mode is fully implementable** — the theme has all the surface/text/border tokens. The bypasses are concentrated in shared components and a handful of screen-local overrides. Estimated effort to fix: 1 day, mostly mechanical.

### Long-Content Overflow Audit

(Brief — these were not deeply read but confirmed structurally:)
- `rider-app/app/(tabs)/activity.tsx`, `notifications.tsx`, `legal.tsx`, `promotions.tsx` — should be FlatList-or-ScrollView based with empty/loading/error states. Spot check shows correct usage of useSafeAreaInsets in most. **Action:** verify each has loading skeleton + empty-state illustration + error retry, not blank views.
- The driver `earnings.tsx` and `ride-detail.tsx` have hardcoded `Dimensions.get` — same risk as CustomAlert. Foldable rotation breaks them.

### Accessibility Audit

- **Coverage is uneven** as detailed above (login: 3 hints, OTP: 0 hints).
- **`accessibilityState` is correctly applied** on the disabled login button (`rider-app/login.tsx:206`) and SOS button (`SOSButton.tsx:207-211`). Good.
- **Color-only state indication** — focused-input style adds a colored border AND a shadow, so not color-only. Tap targets adequate.
- **No `accessibilityLiveRegion`** on the OTP countdown ("Resend code in 28s"). Screen reader users won't get auto-updates.
- **Emoji used for screen-reader-relevant content** — `OfflineBanner` icon `📡` (line 95) — most screen readers will read this as "antenna with bars" or skip it. Use Ionicons + `accessibilityLabel`.
- **No reduced-motion** check. The OTP shake animation, the rider-home avatar pulse, the SOS pulse, the dot scale animations — all run regardless of `AccessibilityInfo.isReduceMotionEnabled()`. Per CLAUDE.md "What Spinr Is NOT" — accessibility is mandatory for service-area customers.

---

## Cross-Cutting Recommendations

### 1. Adopt the dead-code responsive layer (1 day)

**Action:** Replace every `Dimensions.get('window')` and ad-hoc `useWindowDimensions` + `width >= 768` check with `useResponsive()`. Delete the module-level `SCREEN_WIDTH` constants in `shared/components/CustomAlert.tsx`, `driver-app/app/login.tsx:27`, `driver-app/app/otp.tsx:28`, and any other instance.

**Concrete diff for the worst offender:**
```ts
// shared/components/CustomAlert.tsx
- import { Dimensions } from 'react-native';
- const { width: SCREEN_WIDTH } = Dimensions.get('window');
+ // (removed)

// inside component:
+ const { width } = useWindowDimensions();
+ const containerWidth = Math.min(width - 56, 360);

// styles:
- container: { width: SCREEN_WIDTH - 56, maxWidth: 360, … }
+ container: { width: '100%', maxWidth: 360, marginHorizontal: 28, … }
```
(Or pass `containerWidth` into a `useMemo`-built styles factory.)

### 2. Build `<FormScreen>` and migrate 8 screens (2 hours)

```tsx
// shared/components/FormScreen.tsx (proposed)
export function FormScreen({
  children,
  paddingHorizontal = SPACING.lg,
  scrollable = true,
  keyboardOffset = 0,
}: FormScreenProps) {
  const insets = useSafeAreaInsets();
  return (
    <KeyboardAvoidingView
      style={{ flex: 1 }}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={keyboardOffset}
    >
      {scrollable ? (
        <ScrollView
          contentContainerStyle={{
            flexGrow: 1,
            paddingHorizontal,
            paddingTop: insets.top,
            paddingBottom: Math.max(insets.bottom, SPACING.md),
          }}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="on-drag"
          showsVerticalScrollIndicator={false}
        >
          {children}
        </ScrollView>
      ) : (
        <View style={{ flex: 1, paddingHorizontal, paddingTop: insets.top }}>
          {children}
        </View>
      )}
    </KeyboardAvoidingView>
  );
}
```

**Migration order** (by user-visible impact):
1. `rider-app/app/login.tsx` (entry point — every new user hits this)
2. `driver-app/app/login.tsx`
3. `rider-app/app/otp.tsx`
4. `driver-app/app/otp.tsx`
5. `rider-app/app/manage-cards.tsx` (payment-critical)
6. `shared/components/CustomAlert.tsx` (replaces internal KAV — different API surface, do last)
7. `rider-app/app/saved-places.tsx`
8. `driver-app/app/vehicle-info.tsx`

### 3. Strip diagnostic console.logs (15 minutes)

Delete:
- `rider-app/app/otp.tsx:226-244` (`[OtpScreenDiag]`)
- `driver-app/app/otp.tsx:220-239` (`[OtpScreenDiag]`)
- `shared/components/CustomAlert.tsx:25-41` (`[CustomAlertDiag-module]`) and the `BlurView as DiagBlurView` import line 17.
- `shared/components/OfflineBanner.tsx:60-64` (network-state log)

Wrap all remaining `console.log` calls in `__DEV__` checks or migrate to a logger module per CLAUDE.md.

### 4. Tokenize semantic colors (4 hours)

Add to `shared/theme/index.ts`:
```ts
type ThemeColors = {
  …existing…
  // Semantic surface tints (bg behind icon/badge)
  successBg: string;
  warningBg: string;
  dangerBg: string;
  infoBg: string;
};

// lightColors:
  successBg: '#ECFDF5',
  warningBg: '#FFFBEB',
  dangerBg:  '#FEF2F2',
  infoBg:    '#EFF6FF',

// darkColors (NOT just inverted — use elevation-2 surface with a tint):
  successBg: '#0B3D2E',
  warningBg: '#3D2E0B',
  dangerBg:  '#3D0B0B',
  infoBg:    '#0B243D',
```

Replace `VARIANT_CONFIG` in `CustomAlert.tsx` to read from `colors`. Same for `SOSButton`.

### 5. Coordinate parallel-session work (process)

Per the user's note that another contributor is editing the same files: a UX-focused branch with the above changes will conflict heavily with the in-flight `[OtpScreenDiag]` debugging effort and any other parallel edits. Recommend:
- Open a draft PR titled "ui-foundations: FormScreen + responsive + theme tokens" with empty commits, scoped to: shared/components/CustomAlert.tsx, shared/utils/responsive.ts (already exists), shared/components/FormScreen.tsx (new), shared/theme/index.ts.
- Land that first, then the per-screen migrations are mechanical conflict-free.

---

## Findings I Should Surface That Weren't In The Prompt

These didn't fit cleanly into the 6 pillars but are real risks:

1. **The `// @ts-nocheck` directive in `shared/components/CustomAlert.tsx:1`** — TypeScript checking is fully disabled on the most-used shared component in the app. Per project TypeScript rule "Avoid `any`": this is worse — it's avoiding *all* type checking. Investigate whether the original error is fixable; if it's about the React Native Modal type incompatibility (which is what the diag log suggests), there's a documented workaround using `as React.ComponentType<any>`.

2. **`fullBackupContent` is set on driver-app but not rider-app**. `driver-app/.../AndroidManifest.xml:20` includes `android:fullBackupContent="@xml/secure_store_backup_rules"` and `android:dataExtractionRules` — the rider does not. This is a security/compliance asymmetry: rider auth tokens may be backed up to Google Drive on driver-app-protected devices but not on rider-app. Verify intentionality with security-reviewer.

3. **Hardcoded Google Maps API key in both AndroidManifests** (`AIzaSyC5i7lhtfXDoyYOB3KdyJtZ-CtKDzM5m9M`, line 18 / 21). This is a build-time secret in source control. Even if the key is restricted by package name + SHA-1 fingerprint (which I cannot verify here), CLAUDE.md security says "NEVER hardcode secrets in source code." If this is intentional (Maps Android SDK requires it), document why and pin the key restrictions.

4. **`(rider-app)/(tabs)/index.tsx:557` `backgroundColor: '#D4C4A8'`** — the avatar fallback tan. This color isn't in either palette and looks like a one-off "warm placeholder" visual. If a brand designer chose this, document it; if it was vibe-picked, replace with `colors.surfaceLight`.

5. **The driver-app login (`driver-app/app/login.tsx:50-78`) requests location permission on mount** — before the user has entered their phone number or signed in. PIPEDA: data-minimization implies you collect location only after consent. The pre-prompt pattern from `rider-app/(tabs)/index.tsx:99-112` ("Spinr uses your location to show nearby drivers…") is the correct pattern; the driver login is missing it. Also flag for security-reviewer.

6. **Inconsistent `mode === 'backend'` vs mode missing handling.** Both OTP screens contain `const isBackendMode = mode === 'backend' || !verificationId;`. The branching is structurally identical but has subtle divergences (e.g. driver OTP uses `setHasAttemptedVerification` to gate navigation, rider OTP navigates directly on `user`). Two implementations of the same flow — different bug surfaces.

7. **The `t()` translation calls in driver-app/app/login.tsx:269-272 + otp.tsx but NOT in rider-app equivalents.** The rider app has no `useLanguageStore`. So if users switch language in driver app, OTP and login translate; if they switch in rider app, nothing happens. Either drop the translation pretense or extend to rider.

8. **`expo-blur` is imported by `CustomAlert.tsx:17` but never used.** Pure import-side-effect from the diagnostic block. Removing the diag should also remove the dependency surface.

9. **No `accessibilityLanguage` set anywhere.** For a Saskatchewan-deployed product with French + Cree language considerations, screen reader voice doesn't switch. Out-of-scope for keyboard fix but worth a ticket.

10. **`driver-app/app/driver/(tabs)/index.tsx:679` SOS-trigger handler contains `console.error('[index]', err)`** — the `[index]` tag is from the `__filename` macro that Expo doesn't actually expand. So the log shows the literal string `[index]` — useless for triage. Same in `rider-app/(tabs)/index.tsx:71/79`. Replace with proper logger calls.

---

## Files Audited

**Read in full:**
- `rider-app/app/login.tsx`
- `rider-app/app/otp.tsx`
- `rider-app/app/profile-setup.tsx`
- `rider-app/app/(tabs)/index.tsx`
- `driver-app/app/login.tsx`
- `driver-app/app/otp.tsx`
- `driver-app/app/profile-setup.tsx`
- `driver-app/app/driver/(tabs)/index.tsx`
- `shared/components/CustomAlert.tsx`
- `shared/components/SOSButton.tsx`
- `shared/components/OfflineBanner.tsx`
- `shared/utils/responsive.ts`
- `shared/theme/index.ts`
- `shared/theme/ThemeContext.tsx`
- `rider-app/android/app/src/main/AndroidManifest.xml`
- `driver-app/android/app/src/main/AndroidManifest.xml`

**Read in part:**
- `rider-app/app/manage-cards.tsx` (form structure, KAV pattern)
- `rider-app/app/ride-in-progress.tsx` (snap-points, useWindowDimensions usage)

**Grep-confirmed (file-level evidence only):**
- ~40 screens across `rider-app/app`, `driver-app/app`, `shared/components` for KAV/console.log/Dimensions.get/responsive-import patterns

---

## UI REVIEW COMPLETE

**Project:** Spinr (rider-app, driver-app, shared)
**Branch:** `fix/rider-app-expo-sdk55`
**Overall Score:** 13/24
**Screenshots:** not captured (no dev server, mobile-only RN — code-only audit by design)

### Pillar Summary
| Pillar | Score |
|--------|-------|
| Copywriting | 3/4 |
| Visuals | 3/4 |
| Color | 2/4 |
| Typography | 2/4 |
| Spacing | 2/4 |
| Experience Design | 1/4 |

### Top 3 Fixes
1. Build `<FormScreen>` shared wrapper (KAV + ScrollView + insets + dismiss-on-tap + keyboardVerticalOffset) and migrate the 4 auth screens + 4 input screens. Removes the keyboard-occlusion BLOCKER on small Androids — the user's #1 reported issue.
2. Adopt `shared/utils/responsive.ts` (already authored, zero call sites). Delete every module-level `Dimensions.get('window')`. Restores correct layout on rotation, foldables, split-screen.
3. Strip 4 diagnostic `console.log` blocks from production code paths (`OtpScreenDiag` × 2, `CustomAlertDiag-module`, `OfflineBanner` network log) and re-enable TypeScript on `shared/components/CustomAlert.tsx` (remove `// @ts-nocheck`).

### Recommendation Count
- BLOCKERs: 3 (keyboard occlusion, frozen Dimensions, diagnostic logs in prod)
- WARNINGs: 12 (across pillars 1, 2, 3, 4, 5, 6 + cross-cutting)
- Cross-cutting recommendations: 5 (FormScreen, responsive adoption, log strip, color tokenization, parallel-session coordination)
- "Findings you didn't ask for": 10

### File Created
`C:/Users/TabUsrDskOff111/Documents/Spinrvm/spinrvm/.planning/UI-REVIEW.md`
