# UI Fix Handoff — Mobile Apps

**Date:** 2026-05-09
**Branch:** `fix/rider-app-expo-sdk55`
**Source audit:** `.planning/UI-REVIEW.md` (overall score 13/24)
**Author:** Claude Code session — read-only audit + 1 file drafted, no other commits to shared surfaces

---

## TL;DR for the next contributor

The mobile app's UX problems (keyboard occluding CTAs on small Androids, layout breakage on rotation/foldables, diagnostic logs in production builds) are **systemic, not screen-specific**. They trace to two pieces of infrastructure that were authored but never adopted, and one debugging investigation that left instrumentation in production code.

Three things to ship, in this order:

1. **PR-1 `ui-foundations`** — Adds `<FormScreen>` shared wrapper. **Already drafted at `shared/components/FormScreen.tsx`** (this session). Pure new file, zero collision risk.
2. **PR-2 `cleanup-diagnostics`** — Strip 4 diagnostic `console.log` blocks + remove `@ts-nocheck` on `CustomAlert`. Mechanical.
3. **PR-3 `live-window-dimensions`** — Replace module-level `Dimensions.get('window')` with `useWindowDimensions()` (or `useResponsive()`) in 3 files.
4. **PR-4 `theme-semantic-tokens`** *(proposed below — diffs not yet drafted as of this handoff)* — Add `info`, `*Bg` semantic-color tokens to the theme; refactor `CustomAlert.VARIANT_CONFIG` to read them. Restores dark-mode parity for alerts.
5. **PR-5+** — Per-screen migration to `<FormScreen>`. Needs phone testing.

PR-1 → PR-3 are individually 10–30 min review. They unblock the per-screen UX work that the audit flagged across ~8 input screens.

---

## Current state of the branch (as of 2026-05-09)

```
HEAD: 4780a4d1 (vms) — Metro alias for @tanstack/react-query + VirtualView stub
      84ca8929 (vms) — patch RN ScrollView codegen for Bridgeless / New Arch
      7b1e72d8       — Merge origin/main into fix/rider-app-expo-sdk55
      0b48733a (Kiran) — QueryClientProvider at driver layout/tab levels
      22ddc165 (Kiran) — wrap loading branch with QueryClientProvider
      c76d4feb (Kiran) — patch VirtualViewExperimentalNativeComponent codegen
      aa61d669 (Kiran) — inline VirtualView event type
      cca8ad98 (Kiran) — patch VirtualView codegen
      0e2987fe (Kiran) — patch RefreshControl crash + responsive layouts
```

⚠️ **Two contributors editing same branch today.** The session that produced this handoff observed at least one prior commit (`0d3cdc91 — bypass broken Bridgeless ScrollView in OtpScreen`) get rebased away during the merge. Coordinate before rebase or new commits.

OTAs shipped this session against `runtimeVersion: 1.0.0`, branch `preview`:
- Driver: update group `632c5b1e-674a-4334-b0c2-4431124891a1`, Android update `019e0d94-5259-7212-ae24-575789811676`
- Rider: update group `d68cd21c-b59a-49d6-9bdf-7ab5da3db90d`, Android update `019e0d7e-44bd-7f09-9553-cd6a53bf59db` (and a redundant Android-only `e008c06b-…`)

Driver OTA was tagged commit `7b1e72d8…*` (asterisk = uncommitted changes on top — those changes have since been committed by the other vms session in `4780a4d1`). The shipped OTA bundles MAY be older than what's currently on disk depending on when each was actually built. Verify with `eas update:list` before testing further.

---

## What this session drafted

**`shared/components/FormScreen.tsx`** — single source of truth for input-screen layout. Owns `KeyboardAvoidingView` + `ScrollView` + `keyboardShouldPersistTaps='handled'` + `keyboardDismissMode='on-drag'` + dismiss-on-tap + safe-area insets + minimum bottom padding for home-gesture clearance.

Critical design decisions inside it:
- iOS uses `behavior='padding'`; Android passes `behavior={undefined}` and relies on `windowSoftInputMode='adjustResize'` from the manifest. KAV `behavior='height'` on Android with New Arch / Bridgeless produces inconsistent results across OEMs (Samsung, Pixel, Xiaomi all differ). `adjustResize` is the OS-native solution.
- Exposes `scrollable={false}` for chat-style screens where the input bar must pin to the bottom.
- Exposes `dismissOnTap={false}` for screens that need a sticky keyboard (none today; available for future).
- Does NOT own focus management — screens still need `useFocusEffect` + `inputRef.current?.focus()` for return-to-screen refocus (the user's specific complaint about the keyboard regaining focus). `<FormScreen>` removes the keyboard-occlusion blocker; the focus-restoration concern is per-screen.
- Does NOT own background color, StatusBar, or scroll-to-input — those are explicitly out of scope so screens can tune them.

**Usage:**

```tsx
import FormScreen from '@shared/components/FormScreen';

export default function SomeFormScreen() {
  return (
    <FormScreen>
      <Header />
      <Fields />
      <CTA />
    </FormScreen>
  );
}
```

Screens migrate to this component via PR-5+ — see migration order below.

---

## PR-2 — cleanup-diagnostics (drop-in diffs)

### Diff 1 — `rider-app/app/otp.tsx`

```diff
@@ line ~17
- // DIAG-ONLY (revert with diagnostic block): test BlurView module resolution
- import { BlurView as DiagBlurView } from 'expo-blur';

@@ line ~224
-  // DIAGNOSTIC: log typeof each imported component to find the undefined one.
-  // Remove once OtpScreen render error is fixed.
-  console.log('[OtpScreenDiag]', JSON.stringify({
-    View: typeof View, Text: typeof Text, TextInput: typeof TextInput,
-    TouchableOpacity: typeof TouchableOpacity,
-    ActivityIndicator: typeof ActivityIndicator,
-    KeyboardAvoidingView: typeof KeyboardAvoidingView,
-    Animated: typeof Animated, AnimatedView: typeof Animated?.View,
-    Ionicons: typeof Ionicons, CustomAlert: typeof CustomAlert,
-    Modal: typeof Modal,
-    ModalRender: typeof (Modal as any)?.render,
-    ModalDisplayName: String((Modal as any)?.displayName ?? 'n/a'),
-    ModalSymbolType: String((Modal as any)?.$$typeof ?? 'n/a'),
-    BlurView: typeof DiagBlurView,
-    BlurViewDisplayName: String((DiagBlurView as any)?.displayName ?? 'n/a'),
-  }));
```

Drop `Modal` from the RN import destructure if not used elsewhere in the file.

### Diff 2 — `driver-app/app/otp.tsx`

```diff
@@ line ~19
- // DIAG-ONLY (revert with diagnostic block): test BlurView module resolution
- import { BlurView as DiagBlurView } from 'expo-blur';

@@ line ~218
-  // (same OtpScreenDiag block as Diff 1, with ScrollView field)
```

Drop unused `Modal` and `ScrollView` from RN imports if no other use.

### Diff 3 — `shared/components/CustomAlert.tsx`

```diff
@@ line 1
- // @ts-nocheck

@@ line ~17
- import { BlurView } from 'expo-blur';

@@ lines ~25–41
- // DIAGNOSTIC (revert once OtpScreen render error is fixed): logs at module
- // evaluation time so we know what CustomAlert's own imports resolve to —
- // independent of OtpScreen's import surface.
- console.log('[CustomAlertDiag-module]', JSON.stringify({ ...all 14 fields... }));
```

After `@ts-nocheck` removal, `tsc` may surface `any` casts; clean those up in the same PR or a follow-up.

### Diff 4 — `shared/components/OfflineBanner.tsx`

```diff
@@ line ~58
-      console.log('[OfflineBanner] Network state changed:', {
+      if (__DEV__) console.log('[OfflineBanner] Network state changed:', {
```

Long-term: route through `shared/utils/logger.ts` per CLAUDE.md observability convention.

### Diff 5 — `rider-app/app/(tabs)/index.tsx` (lines 71, 79, 135)

```diff
- console.error('[index]', err);
+ if (__DEV__) console.error('[rider-home]', err);
```

`__filename` doesn't expand in Expo, so `[index]` is a literal (useless) tag. Better long-term: logger module.

---

## PR-3 — live-window-dimensions (drop-in diffs)

### Diff 6 — `shared/components/CustomAlert.tsx`

```diff
@@ RN imports
   import {
-    Dimensions,
+    useWindowDimensions,
     ...
   } from 'react-native';

@@ line ~20 — delete module-level capture
- const { width: SCREEN_WIDTH } = Dimensions.get('window');

@@ inside the component, before the existing useTheme call
+ const { width } = useWindowDimensions();
+ const containerWidth = Math.min(width - 56, 360);

@@ where the alert container is rendered
- <Animated.View style={[styles.container, { transform: [...], opacity: opacityAnim }]}>
+ <Animated.View
+   style={[styles.container, { width: containerWidth, transform: [...], opacity: opacityAnim }]}
+ >

@@ inside createStyles, the container rule (line ~270)
   container: {
-    width: SCREEN_WIDTH - 56,
-    maxWidth: 360,
     backgroundColor: colors.surface,
     ...
   },
```

**Behavior preserved:** previous formula was `min(screenWidth - 56, 360)` baked into `width + maxWidth`; new formula computes the same value at render time, so it now updates on rotation, foldable unfold, and split-screen.

### Diff 7 — `driver-app/app/login.tsx`

```diff
@@ line ~13 (RN imports)
   import {
     ...
-    Dimensions,
     ...
   } from 'react-native';

@@ line 27 (delete dead capture)
- const { width: SCREEN_WIDTH } = Dimensions.get('window');
```

Verified `SCREEN_WIDTH` has 1 reference (the capture itself) — pure dead code.

### Diff 8 — `driver-app/app/otp.tsx` (same pattern as Diff 7)

```diff
@@ line ~12 (RN imports)
- Dimensions,

@@ line 28
- const { width: SCREEN_WIDTH } = Dimensions.get('window');
```

---

## PR-4 — theme-semantic-tokens (proposed; diffs draft)

Restores dark-mode parity for alerts, SOS button, and any other semantic-tinted surface. Adds 5 new tokens to the theme and refactors `CustomAlert.VARIANT_CONFIG` to consume them.

### Diff 9 — `shared/theme/index.ts`

```diff
 export type ThemeColors = {
   // ...existing...
   error: string;
   success: string;
   warning: string;
+  info: string;
+  // Semantic surface tints — light bg behind status icons/badges
+  successBg: string;
+  warningBg: string;
+  dangerBg: string;
+  infoBg: string;
   // Aliases / Legacy
   ...
 };

 export const lightColors: ThemeColors = {
   // ...
   error: '#DC2626',
   success: '#34C759',
   warning: '#FFCC00',
+  info: '#3B82F6',          // iOS-system-blue
+  successBg: '#ECFDF5',
+  warningBg: '#FFFBEB',
+  dangerBg:  '#FEF2F2',
+  infoBg:    '#EFF6FF',
   // ...
 };

 export const darkColors: ThemeColors = {
   // ...
   error: '#FF453A',
   success: '#30D158',
   warning: '#FFD60A',
+  info: '#0A84FF',          // iOS-system-blue (dark)
+  // Dark tints — NOT just inverted; an elevation-2 surface with a tint
+  successBg: '#0B3D2E',
+  warningBg: '#3D2E0B',
+  dangerBg:  '#3D0B0B',
+  infoBg:    '#0B243D',
   // ...
 };
```

### Diff 10 — `shared/components/CustomAlert.tsx` (refactor VARIANT_CONFIG)

Replace the `VARIANT_CONFIG` constant (currently lines ~68–96) with a hook-driven mapper:

```diff
- const VARIANT_CONFIG: Record<AlertVariant, {
-   icon: string; iconColor: string; iconBg: string; buttonColor: string;
- }> = {
-   info:    { icon: 'information-circle', iconColor: '#3B82F6', iconBg: '#EFF6FF', buttonColor: '#3B82F6' },
-   warning: { icon: 'alert-circle',       iconColor: '#F59E0B', iconBg: '#FFFBEB', buttonColor: '#F59E0B' },
-   danger:  { icon: 'warning',            iconColor: '#EF4444', iconBg: '#FEF2F2', buttonColor: '#EF4444' },
-   success: { icon: 'checkmark-circle',   iconColor: '#10B981', iconBg: '#ECFDF5', buttonColor: '#10B981' },
- };
```

Replace inside the component:

```tsx
const variantConfig = useMemo(
  () => ({
    info:    { icon: 'information-circle', iconColor: colors.info,    iconBg: colors.infoBg,    buttonColor: colors.info },
    warning: { icon: 'alert-circle',       iconColor: colors.warning, iconBg: colors.warningBg, buttonColor: colors.warning },
    danger:  { icon: 'warning',            iconColor: colors.error,   iconBg: colors.dangerBg,  buttonColor: colors.error },
    success: { icon: 'checkmark-circle',   iconColor: colors.success, iconBg: colors.successBg, buttonColor: colors.success },
  } as const),
  [colors],
);
const config = variantConfig[variant];
```

(Note `danger` variant maps to `colors.error` — the theme already aliases `danger` to `error`; using `error` is the canonical token.)

After this lands, repeat the pattern for `SOSButton.tsx` (which also hardcodes the same tints) and the driver dashboard offer panel (`driver-app/app/driver/(tabs)/index.tsx:933+`). Those are part of PR-4 or a separate semantic-color-cleanup PR — your call.

### Verification for PR-4

Toggle the device into dark mode and trigger:
- Each alert variant (info/warning/danger/success) — should show dark-tinted bg, not the light hex
- SOS button across its states — should respect dark surface
- Driver dashboard offer panel — should show dark-tinted accept/decline buttons

---

## PR-5+ — per-screen migration to `<FormScreen>`

Migration order, by user-impact-per-effort:

| Order | Screen | Notes |
|---|---|---|
| 1 | `rider-app/app/login.tsx` | Entry point — every new user hits this. |
| 2 | `driver-app/app/login.tsx` | Drop hardcoded StatusBar `barStyle="dark-content"` while there. |
| 3 | `rider-app/app/otp.tsx` | Restores scroll fallback (regressed in `0d3cdc91`). |
| 4 | `driver-app/app/otp.tsx` | Drop unused `ScrollView` import. |
| 5 | `rider-app/app/manage-cards.tsx` | Add-card form currently has no scroll; payment-critical. |
| 6 | `shared/components/CustomAlert.tsx` (internal KAV) | Different API surface — `<FormScreen>` doesn't fit inside a Modal cleanly. Likely a separate `<AlertSheet>` pattern. Save for last. |
| 7 | `rider-app/app/saved-places.tsx` | |
| 8 | `driver-app/app/vehicle-info.tsx` | |

Each migration:
1. Wrap the existing layout in `<FormScreen>` instead of bespoke KAV.
2. Move existing `paddingHorizontal`, `paddingTop: insets.top`, `paddingBottom: insets.bottom` props off the inner View — `<FormScreen>` owns them.
3. Add `useFocusEffect` + `inputRef.current?.focus()` on screens where the user reported focus regressions (the OTP screen specifically).
4. Add a visual-regression smoke test at 320 / 375 / 768 widths via Playwright-via-Expo-web (or screenshot tests on a simulator).

Each PR migrates 2–3 screens. Phone testing required per PR.

---

## Acceptance criteria (overall)

After PR-1 through PR-4 land, on a 320×640 small Android (e.g. Pixel 4a in default mode, or a Galaxy A03):

1. **Login screen** — typing the phone number, the "Send Verification Code" button is fully visible above the keyboard. Toggling dark mode, the layout doesn't break.
2. **OTP screen** — the verify button is visible; entering wrong code triggers the shake animation; alert dismisses; input regains focus automatically.
3. **CustomAlert** — opens correctly on device rotation (rotate during open, alert resizes; close and reopen, alert is correctly sized for new orientation).
4. **Foldable inner-display** (Galaxy Z Fold or similar) — open the app on outer display, unfold, alert and forms re-render at the new width without dead space.
5. **No `console.log`** in `adb logcat ReactNativeJS:V` filter for `[OtpScreenDiag]`, `[CustomAlertDiag-module]`, `[OfflineBanner]`, or `[index]` tags.
6. **Dark mode**: every alert variant tints its background dark (not the legacy `#EFF6FF` light blue on a black surface).

---

## Coordination protocol going forward

Per the discovery in this session that two contributors are editing the same files:

1. Before starting any of PR-1 through PR-4, **`git pull --rebase`** on `fix/rider-app-expo-sdk55` and re-confirm the diffs above still apply (line numbers may shift).
2. **Open as draft PRs first**, with empty commits + a `# This PR will modify: <file list>` comment. Visible to the other contributor before any code lands.
3. **Never amend commits** authored by the other contributor without explicit confirmation. The session that produced this handoff lost a commit (`0d3cdc91`) to a rebase already.
4. If both contributors need to touch the same file in parallel, split by **screen** (one owns rider OTP, the other owns driver OTP) — not by file (both editing `CustomAlert.tsx` simultaneously).

---

## Other findings worth ticketing (not in any PR above)

From the audit's "Findings I Should Surface" section:

1. **Hardcoded Google Maps API key** in both `AndroidManifest.xml` files. Verify SHA-1 + package-name restriction on the GCP key; if absent, rotate and add. CLAUDE.md security says no hardcoded secrets.
2. **Driver login requests location permission on mount** before user signs in. PIPEDA data-minimization implies post-consent. The rider home has the correct pre-prompt pattern; backport to driver login.
3. **`fullBackupContent` set on driver-app, not rider-app.** Auth-token backup behavior is asymmetric. Verify intentionality with security-reviewer.
4. **No `accessibilityLanguage`** anywhere — screen reader voice doesn't switch for French / Cree users in Saskatchewan. Out-of-scope for the keyboard fix but ticket-worthy.
5. **Two divergent OTP implementations** (rider uses `user` watch, driver uses `hasAttemptedVerification` gate). Consolidate as a `useOtpVerification` hook in `shared/`.

---

## Files NOT touched by this handoff

- All native code (Android Java/Kotlin, iOS Objective-C/Swift)
- Backend (`backend/`)
- Admin dashboard (`admin-dashboard/`)
- Database migrations
- Patches (`*/patches/react-native+0.85.2.patch` etc.)

The audit was scoped to the mobile RN frontend; the cross-cutting infrastructure proposed here lives entirely in `shared/components/` and `shared/theme/`.

---

## Verification checklist for whoever picks this up

- [ ] Confirm `shared/components/FormScreen.tsx` exists (this session committed it as a standalone commit on the branch — verify with `git log shared/components/FormScreen.tsx`).
- [ ] Read `.planning/UI-REVIEW.md` for full audit context (BLOCKER details, evidence file:line, all WARNINGs).
- [ ] Pull latest `fix/rider-app-expo-sdk55` before starting; confirm Diffs 1–10 still apply at the listed line numbers.
- [ ] Confirm with the other vms session and Kiran that no in-flight work depends on the diagnostic logs being present.
- [ ] Sequence the PRs: PR-1 (already in branch) → PR-2 → PR-3 → PR-4 → PR-5+.
- [ ] Phone-test each PR-5+ migration on at least one small Android (320 / 360 width) and one iPhone (375 width).
- [ ] Disk: working tree on this machine was at 0 bytes free during this handoff. Free space before any large bundles or builds.
