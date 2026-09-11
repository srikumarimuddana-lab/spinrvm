---
name: spinr-rider-driver-design-system
description: The actual, current rider-app/driver-app design direction — shared brand foundation, per-app tone divergence, and known adoption gaps. Load this before making any visual/design judgment about rider-app or driver-app, before running /design-review on either, or before styling a new rider-app/driver-app component. Companion to spinr-admin-design-system (admin-dashboard's own direction) — the two are deliberately different, don't cross-apply one to the other's surface.
---

# Spinr rider-app / driver-app design system

Canonical sources, in priority order — read these, don't re-derive values from memory:
1. `shared/theme/index.ts` — the real, current color token values (`lightColors`/`darkColors`)
2. `docs/design/rider-driver-app-design-system.md` — how the shared theme plumbing works (import path, `useTheme()`, per-app adoption mechanics) and the color-token adoption history; this doc doesn't repeat that, it covers what that one doesn't
3. This doc — direction/tone, per-app divergence, typography/spacing/motion/loading standards, known gaps
4. `.claude/context/brand-spinr.md` — cross-app brand reference (palette table, typography, logo) for surfaces that don't import `shared/theme/` directly

Built 2026-09-10 from a two-pass, file:line-cited codebase inventory (not
assumption) of both apps' actual current patterns, plus explicit user
decisions on direction (recorded below with their date). If you find a claim
here that no longer matches the code, the code wins — flag the drift, don't
silently trust this doc over what you can verify.

## The direction: warmer than admin-dashboard's "Quiet Console"

Decided 2026-09-10 (user decision, this session). admin-dashboard's Quiet
Console direction (`spinr-admin-design-system`) — calm, neutral-by-default,
color reserved for real signal — is **not** the target for rider-app/
driver-app. These are personal-use apps (a rider booking a trip on their own
phone; a driver working, often one-handed, sometimes mid-shift) in a
different context from an internal enterprise dashboard. The direction here
should read warmer and more energetic than admin-dashboard's restraint —
don't import Quiet Console's "most of the screen should be neutral" premise
into judgments about these two surfaces.

## Rider-app vs. driver-app: related, not identical

Decided 2026-09-10 (user decision). The two apps are **not** meant to share
one visual/motion identity. Both draw from the same brand palette
(`shared/theme/index.ts`) and the same intended typeface (Plus Jakarta Sans)
— that part is genuinely shared. But driver-app is deliberately punchier and
faster-feeling *throughout*, not just in its real-time elements, reflecting
a different use context: rider-app is a slower, considered booking decision;
driver-app is fast, often one-handed/glanceable, high-frequency, and
sometimes safety-relevant (the go-online moment, live location, an
in-progress ride). When critiquing either app, judge it against its own
tone, not the other's.

## Loading-state standard: spinners, not skeletons

Decided 2026-09-10 (user decision). `ActivityIndicator`-style spinners stay
the standard loading pattern for both apps — this was an explicit choice,
not a default. Two rider-app screens (`app/(tabs)/activity.tsx`,
`app/ride-options.tsx`) use row-shaped `SkeletonBox` placeholders instead;
this is a **pre-existing exception**, not the direction to extend. Don't
propose converting more screens to skeletons as an "improvement," and don't
flag those two screens as inconsistent with the (spinner) standard — they
predate this decision and are being left as-is, not undone.

## Shared foundation (both apps)

### Color
Source of truth: `shared/theme/index.ts`. See `.claude/context/brand-spinr.md`
for the full table — don't duplicate it here, it drifts if two docs both
carry the numbers (that's exactly what happened before this doc existed:
`brand-spinr.md` had stale success/warning/danger values until 2026-09-10).
One recurring idiom worth knowing: components append a 2-hex-digit alpha
suffix directly to a token string (`colors.primary + '15'`) to make a
translucent variant, rather than using `rgba()` — this is established
practice, not a pattern to "fix" into `rgba()`.

### Typography
Plus Jakarta Sans (weights 400/500/600/700), loaded via
`@expo-google-fonts/plus-jakarta-sans` in both apps' root `_layout.tsx`.
**Intended rule**: every `Text` element should resolve to one of the four
registered `PlusJakartaSans_*` family names. There is currently no
`Text.defaultProps` override or themed `Text` wrapper enforcing this in
either app — see Known Gaps (UX1) below. When reviewing a new component,
flag a `Text`/`TextInput` style that sets `fontWeight` without also setting
`fontFamily` — it's rendering the OS system font, not the brand typeface,
regardless of which app it's in.

### Icons
`@expo/vector-icons`, with `Ionicons` as the default glyph set in both apps.
Occasional `MaterialCommunityIcons`/`FontAwesome`/`FontAwesome5` for a
specific glyph not in `Ionicons` is an accepted, existing exception (seen in
`rider-app/app/manage-cards.tsx`, `driver-app/app/driver/(tabs)/profile.tsx`,
`driver-app/components/activity/ActivityView.tsx`) — still the same
package, not a second icon library. Don't flag these as inconsistent; do
flag a genuinely different icon package if one shows up.

## Rider-app direction

Warmer and more considered than admin-dashboard, calmer than driver-app.
Riders are making a booking decision, not glancing at a live status — motion
and color can afford to be a little more generous here than in driver-app's
fast-glance context, without tipping into driver-app's urgency.

**Existing patterns worth keeping as the model, not replacing:**
- `components/Toast.tsx` — mounted once globally (`app/_layout.tsx`),
  triggered via a single `showToast()` call used 131 times across the app.
  This is the most consistently-adopted shared UI pattern found in the whole
  inventory — use it as the reference for what "actually shared" looks like
  when proposing a new cross-screen pattern.
- `components/CustomToggle.tsx` — reused across 6 screens; a real shared
  primitive, not a per-screen reinvention.
- The distinct-error-vs-empty pattern in `app/loyalty.tsx` and
  `app/notifications.tsx` — both have a code comment explicitly noting the
  error state was added *because* a failed fetch was previously
  indistinguishable from "genuinely nothing here." `app/wallet.tsx` and
  `app/saved-places.tsx` don't have this distinction yet (see UX-adjacent
  follow-up potential, not yet filed as its own item — a `ux-ideate` pass on
  rider-app would find this).

## Driver-app direction

Punchier and faster-feeling than rider-app throughout — not confined to the
real-time elements, but those elements are where it shows most clearly and
should be treated as the model for the rest of the app, not as isolated
exceptions to rationalize away:

- `components/dashboard/DriverIdlePanel.tsx`'s GO/STOP toggle — a large
  circular gradient button with a continuous subtle pulse animation while
  eligible-and-offline. This is the single highest-stakes tap in the app
  (going on duty) and its visual weight/motion reflects that on purpose.
- `components/CarMarker.tsx`'s live position/bearing animation — the most
  elaborate motion in either app, platform-branched for iOS/Android,
  continuously reflecting real vehicle movement.
- `components/panels/RideOfferPanel.tsx`'s accept/decline with a live
  countdown fill — time-pressured by design, matching the real offer-expiry
  window.

When extending driver-app, match this energy rather than defaulting to
rider-app's calmer pace or to a generic "clean and minimal" instinct —
driver-app being fast and immediate *is* on-direction here, not a gap to
smooth over.

**Existing pattern worth keeping as the model:** the three-way
loading/error/empty branch in `app/driver/notifications.tsx` and
`components/activity/ActivityView.tsx`, both with a code comment explaining
that an empty list and a failed fetch are not the same thing and must be
told apart. Reuse this pattern rather than reinventing a two-way
loading/content branch on a new screen.

## Known, tracked adoption gaps — don't silently "fix" or restate these as new findings

These are gaps between this doc's intended system and current code, already
filed with evidence in `ACTION_ITEMS.md`'s P3 section
("rider-app/driver-app design-system adoption gaps") — cross-check there for
current status before reporting one of these as a new finding:

- **UX1** — Plus Jakarta Sans loads but only applies in a minority of
  screens (21/64 rider-app files, 8/61 driver-app files actually set
  `fontFamily`); the rest silently render the OS system font.
- **UX2** — the shared `SPACING`/`FONT` scales (`shared/utils/responsive.ts`)
  exist but are imported in only 1–4 files per app; spacing/type elsewhere
  is ad-hoc numeric literals.
- **UX3** — `shared/components/Button.tsx` has zero consumers in driver-app;
  every driver-app button is independently hand-styled.
- **UX4** — no shared transition timing/easing constants exist anywhere;
  rider-app's OTP shake and driver-app's PIN shake are the same interaction,
  independently reimplemented with different values.
- **UX5** — (live bug, not adoption debt) `driver-app/components/toastConfig.tsx`
  hardcodes toast colors matching neither the current nor previous theme
  tokens, with no dark-mode awareness.

## Reusable findings for anyone extending this system

- **The `createStyles(colors)` factory pattern** — many driver-app files
  define `const createStyles = (colors: ThemeColors) => StyleSheet.create({...})`
  and call it with the live theme inside the component. This is the
  established idiom for theme-aware styles in both apps; prefer it over a
  static `StyleSheet.create()` block with inline `useTheme()` reads scattered
  through the render function.
- **Module-level static color/status maps can't call `useTheme()`** — a
  lookup object defined outside any component (e.g. a status-to-color map)
  has no theme context available. This is a structural reason a color is
  hardcoded, not a missed token reference — turning the map into a function
  of `colors` is a real refactor, not a one-line fix. (Same note as
  `docs/design/rider-driver-app-design-system.md`'s color-specific version
  of this; repeated here because it applies beyond just color.)
- **Fixed-contrast foreground on a colored/gradient surface is deliberate**
  — white text/icons on a brand-colored button or gradient card face should
  usually stay a fixed `#FFF`/`#000`, not a theme token; swapping in a token
  here would break contrast in dark mode, not fix consistency. Don't flag
  this as hardcoded-color drift.
- **Components rendered outside `ThemeProvider`'s tree** (e.g. each app's
  `BrandSplash.tsx`, shown before the provider mounts) genuinely have no
  theme context — this is why they have no `useTheme()` call, not an
  oversight.
