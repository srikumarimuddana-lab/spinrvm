# Rider-App UI Richness Pass — Design Spec

**Date:** 2026-04-05
**Status:** Draft — awaiting user approval
**Scope:** rider-app (not driver-app, not admin-dashboard, not frontend)

---

## 1. Problem

The rider-app `login.tsx` screen is visibly less rich than the driver-app equivalent: bare black button, no brand strip, no focus states, no validation feedback, no theme tokens. The user wants the rider-app to feel as polished as the driver-app across **all** screens.

A direct comparison of `driver-app/app/login.tsx` (453 lines, uses `SpinrConfig` theme, `CustomAlert`, `Ionicons`, safe-area, animated focus states) vs `rider-app/app/login.tsx` (121 lines, hardcoded black, native `Alert.alert`, no brand, no theme) confirms the gap.

However, an audit of all 30 rider-app screens shows the rider-app is **not** uniformly bare. Most screens (e.g. `settings.tsx`, `legal.tsx`, `(tabs)/*`, `ride-*`, `become-driver.tsx`, `chat-driver.tsx`) already use `SpinrConfig.theme`, `Ionicons`, `CustomAlert`, safe-area insets, and card/section patterns. The bare screens are the exception, not the rule.

**The real task is not a rewrite — it's a targeted upgrade plus consistency pass.**

## 2. Goals

1. Bring every rider-app screen up to a shared visual language extracted from driver-app `login.tsx`.
2. Eliminate hardcoded colors (`#000`, `#333`, `#fff` outside of containers) in favor of `SpinrConfig.theme.colors`.
3. Replace all `Alert.alert` with `CustomAlert`.
4. Ensure every screen respects safe-area insets and has a coherent header/footer treatment.
5. Do this in three clearly-bounded phases that each ship standalone.

## 3. Non-Goals

- Do **not** touch driver-app, admin-dashboard, frontend, or backend.
- Do **not** change navigation structure, routes, or screen inventory.
- Do **not** change business logic, API calls, auth flow, or state management.
- Do **not** introduce new dependencies. Everything used here already exists in rider-app.
- Do **not** rewrite screens that already meet the design language — only adjust where they deviate.
- Do **not** add dark mode, i18n, or accessibility work beyond what's already present.

## 4. The Design Language

Extracted from `driver-app/app/login.tsx`. Every screen should honor these patterns to the extent they apply (a list screen has a header but no "welcome block"; an input screen has the input patterns but may not have a terms footer). This is a checklist, not a template — apply selectively.

### 4.1 Brand & Header

- **Top strip** (auth/onboarding screens only): logo circle (42×42, radius 14, `THEME.primary`, `car-sport` or app icon) + "Spinr" wordmark (24px, weight 800, letter-spacing −0.5) + role badge (`Rider` for rider-app, tinted `${THEME.primary}14` chip).
- **Stack header** (all other screens): 44×44 back button with `arrow-back` icon, centered title (18px, weight 700, `THEME.text`), 44×44 right spacer for symmetry, 1px hairline border `#F0F0F0` at bottom.
- Safe-area insets via `useSafeAreaInsets()` on every screen.

### 4.2 Welcome / Section Blocks

- **Greeting line** (optional): 16px, `THEME.textDim`, weight 500 — e.g. "Welcome back 👋"
- **Title**: 28px, weight 800, `THEME.text`, letter-spacing −0.5, margin-bottom 8.
- **Subtitle**: 15px, `THEME.textDim`, line-height 22.
- **Section label** (above grouped cards): 11–13px, weight 700, `THEME.textDim`, letter-spacing 0.5–1, all-caps.

### 4.3 Inputs

- **Wrapper**: flex row, `#F8F9FA` background, 16px radius, 60px height, 1.5px border `#F0F0F0`.
- **Focus state**: border → `THEME.primary`, background → `#fff`, shadow `{color: primary, offset: {0,0}, opacity: 0.1, radius: 8}`, elevation 3.
- **Label above** (uppercase tracked): 11px, weight 700, `THEME.textDim`, letter-spacing 1, margin-bottom 8.
- **Inline validation check** (when valid): `checkmark-circle` icon, `THEME.success`, right-aligned.
- **Country flag / prefix clusters** (phone inputs): flag emoji + code + chevron, 1px divider to input.
- **Input text**: 18px, weight 600, `THEME.text`.

### 4.4 Buttons

- **Primary CTA**: `THEME.primary` fill, 16px radius, 58px height, colored shadow `{color: primary, offset: {0,6}, opacity: 0.25, radius: 12}`, elevation 6.
- **Content**: label (16px, weight 700, white) + `arrow-forward` icon (20px), gap 8.
- **Disabled**: `#F0F0F0` background, `#999` text, no shadow.
- **Loading**: `THEME.primaryDark` background + `ActivityIndicator`.
- **Secondary button**: transparent, 1.5px `THEME.primary` border, primary text.
- **Tertiary link**: primary color, weight 600.

### 4.5 Cards & Rows

- **Card**: `#F9F9F9` or `#F8F9FA` background, 16px radius, 14px horizontal padding, group related rows inside.
- **Row**: flex row, 14px vertical padding, 1px `#F0F0F0` bottom border (except last), 40×40 icon tile with 12px radius + tinted background, title (15px, weight 600) + optional subtitle (12px, `#999`), `chevron-forward` trailing icon.

### 4.6 Footer / Terms

- **Secure footer** (auth screens): lock icon + micro-copy (12px, `THEME.textDim`), centered.
- **Terms block** (auth screens): 12px muted text, primary-colored links with weight 600, inside safe-area bottom padding.

### 4.7 Alerts

- **Always use `CustomAlert`** from `@shared/components/CustomAlert`. Never `Alert.alert`. Use variant `info`/`warning`/`danger`/`success` appropriate to context.

### 4.8 Color Discipline

- **All colors route through `SpinrConfig.theme.colors`** — `THEME.primary`, `THEME.primaryDark`, `THEME.text`, `THEME.textDim`, `THEME.success`, `THEME.background`, `THEME.surfaceLight`, etc.
- Hardcoded neutrals allowed **only** for UI-chrome greys that aren't in the theme (`#F0F0F0`, `#F8F9FA`, `#F9F9F9`, `#E5E5E5`, `#999`, `#CCC`). Never hardcode primary-family colors, never hardcode pure black (`#000`) as text or button fills.

## 5. Screen Treatment Plan

Screens are grouped by the work required. Each phase is a standalone shippable unit.

### Phase 1 — Auth Flow (high-value, biggest gap)

These are the first-impression screens and the ones most visibly behind the driver-app.

| Screen | Current state | Action |
|---|---|---|
| `app/login.tsx` | Bare: no theme, hardcoded black, native Alert, no brand strip, no focus states | **Full rewrite** to match `driver-app/app/login.tsx` structure — with "Rider" badge in place of "Driver", and preserving rider-app's `useFocusEffect` logout-on-reentry logic (lines 13–22 of current file). |
| `app/otp.tsx` | Bare: hardcoded black, single underlined input, native Alert, no brand strip | **Full rewrite** mirroring `driver-app/app/otp.tsx`. Preserve rider-app's dual-mode logic (`isBackendMode` with 4-digit code vs Firebase with 6-digit code, lines 29–30), and the user-profile-routing effect (lines 36–46). |
| `app/profile-setup.tsx` | Already uses `SpinrConfig` — needs consistency audit only | **Audit + adjust** only: confirm it honors §4 patterns; fix any hardcoded colors or stray `Alert.alert`. No rewrite. |

**Deliverable of Phase 1:** Rider-app auth flow visually matches driver-app auth flow, logic unchanged.

### Phase 2 — Consistency Audit of the Rest

Every remaining rider-app screen gets a **mechanical** consistency pass against the checklist in §4, not a rewrite. For each screen:

1. Replace any `Alert.alert` → `CustomAlert`.
2. Replace any hardcoded primary-family color (`#000` as fill/text, `#007AFF`, `#FF3B30`, etc.) → `THEME.*`.
3. Confirm safe-area insets are used; add if missing.
4. Confirm stack header pattern (§4.1) on non-tab screens; align back button size and title weight.
5. Confirm primary CTAs match §4.4 (radius 16, shadow, arrow icon where applicable).
6. Confirm input styling matches §4.3 where inputs exist.
7. Confirm `CustomAlert` import replaces any native Alert.

**Scope of Phase 2:**

| Screen | Size | Notes |
|---|---|---|
| `app/index.tsx` | 113 | Splash — minimal; ensure logo/tagline use theme. |
| `app/(tabs)/_layout.tsx` | 59 | Tab bar — theme the active/inactive tint. |
| `app/(tabs)/index.tsx` | 622 | Home. Check hardcoded colors only. |
| `app/(tabs)/activity.tsx` | 402 | Activity list. Audit. |
| `app/(tabs)/account.tsx` | 308 | Account. Audit. |
| `app/legal.tsx` | 120 | Already good. Minor tweaks only. |
| `app/settings.tsx` | 185 | Already good. Minor tweaks only. |
| `app/privacy-settings.tsx` | 150 | Audit. |
| `app/support.tsx` | 215 | Audit. |
| `app/promotions.tsx` | 186 | Audit. |
| `app/report-safety.tsx` | 187 | Audit. |
| `app/saved-places.tsx` | 327 | Audit. |
| `app/manage-cards.tsx` | 366 | Audit. |
| `app/emergency-contacts.tsx` | 524 | Audit. |
| `app/become-driver.tsx` | 494 | Audit. |
| `app/chat-driver.tsx` | 456 | Audit. |
| `app/ride-details.tsx` | 274 | Audit. |

**Per-screen time budget:** audits are bounded — touch only what violates §4. If a screen already conforms, leave it alone.

### Phase 3 — Ride Flow Polish

The ride lifecycle screens are large, user-facing, and benefit most from a coherent language. Same mechanical audit as Phase 2, grouped separately because these screens share state and should be verified together end-to-end.

| Screen | Size |
|---|---|
| `app/search-destination.tsx` | 804 |
| `app/pick-on-map.tsx` | 329 |
| `app/ride-options.tsx` | 1005 |
| `app/payment-confirm.tsx` | 644 |
| `app/ride-status.tsx` | 572 |
| `app/driver-arriving.tsx` | 1034 |
| `app/driver-arrived.tsx` | 486 |
| `app/ride-in-progress.tsx` | 585 |
| `app/ride-completed.tsx` | 507 |
| `app/rate-ride.tsx` | 558 |

**Deliverable of Phase 3:** The ride flow is visually cohesive with auth and settings.

## 6. What "Done" Looks Like Per Screen

A screen is done when a reviewer can tick every box that applies:

- [ ] No hardcoded primary-family colors (`#000`, `#007AFF`, raw hex for tinted UI).
- [ ] No `Alert.alert` — all alerts go through `CustomAlert`.
- [ ] `useSafeAreaInsets()` (or `SafeAreaView`) used for top/bottom padding.
- [ ] Header follows §4.1 pattern (back button 44×44, centered 18px/700 title, symmetric spacer).
- [ ] Primary CTAs follow §4.4 (radius, shadow, icon pairing, disabled/loading states).
- [ ] Inputs follow §4.3 where present.
- [ ] Cards/rows follow §4.5 where present.
- [ ] Typography scale honored (titles 28/800, section labels 11–13/700 tracked, body 15, micro 12).
- [ ] No visual regressions: screen still renders and all existing behavior works.

## 7. Architecture / Structure

No new components, no new files under `shared/`, no refactoring of `SpinrConfig`. All work is in-file edits to `rider-app/app/*.tsx`. Phase 1 may optionally extract a small `<AuthBrandStrip>` component if the rewrite duplicates the same JSX in `login.tsx` and `otp.tsx` — but only if it emerges naturally during Phase 1 implementation; not a required upfront deliverable.

## 8. Testing & Verification

This project has no UI test suite to lean on. Verification is manual + lint:

- **Per screen:** run `npx tsc --noEmit` in rider-app to catch any typing regressions from the edits.
- **Per phase:** run `expo start`, open the modified screens, confirm they render and basic interactions work (tap buttons, focus inputs, submit forms, navigate).
- **Phase 1 specifically:** full auth flow walkthrough — open app → login → enter phone → OTP → profile-setup → home. Confirm no broken states, the logic (dev-mode OTP 1234, backend auth) still works unchanged.
- **Per phase:** visual diff against driver-app reference screens where a parallel exists.

## 9. Risks & Trade-offs

- **Risk:** Mechanical color replacement can break a screen if a deliberate non-theme color was load-bearing (e.g., illustrative icon tints in `settings.tsx`). **Mitigation:** section §4.8 explicitly preserves neutral greys and tinted category icons; only primary-family colors are replaced.
- **Risk:** Phase 3's large screens (`ride-options.tsx` 1005 lines, `driver-arriving.tsx` 1034 lines) may have buried hardcoded values hard to audit by eye. **Mitigation:** use `Grep` for `#000`, `#007AFF`, `Alert.alert` patterns per-file before touching.
- **Trade-off:** Doing all 3 phases as one spec means a longer cycle before user sees any change, vs. phased specs. Chosen deliberately per user request (`c` + option 2).
- **Trade-off:** Not extracting shared components (`<AuthBrandStrip>`, `<ScreenHeader>`, `<PrimaryButton>`) up front. This keeps the diff small and preserves the "this file owns its styles" convention already in use. Components can be extracted later if duplication becomes painful.

## 10. Out-of-Scope (explicit)

- Driver-app, admin-dashboard, frontend, backend, shared library changes.
- Navigation/route changes.
- Business logic, API, auth-flow behavior changes.
- New features, dark mode, i18n, a11y audits.
- Component extraction into `shared/components` (except optional `<AuthBrandStrip>` in Phase 1, and only if it falls out naturally).
- Test suite additions.

## 11. Plan Breakdown for Implementation

The implementation plan (written by `writing-plans` after this spec is approved) will break the work into roughly:

- **Phase 1** — 3 tasks (login rewrite, otp rewrite, profile-setup audit) + 1 verification pass.
- **Phase 2** — 17 screen-audit tasks, batched by similarity, + 1 verification pass per batch.
- **Phase 3** — 10 ride-flow audit tasks + 1 end-to-end ride walkthrough.

Each phase is a clean stopping point — the user can approve Phase 1, review, and redirect before Phase 2 begins.
