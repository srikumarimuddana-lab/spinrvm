# rider-app / driver-app design system

**There is no separate design doc per app because there is nothing separate to
document.** rider-app and driver-app pull their color tokens and theme
resolution logic from one shared source, `shared/theme/`, with no per-app
token overrides in either app. This doc records that fact and how it works;
for the actual color values, brand rationale, and typography, see
`.claude/context/brand-spinr.md` rather than duplicating them here.

## What's actually shared

- **Source of truth**: `shared/theme/index.ts` (color tokens: `lightColors`,
  `darkColors`, `ThemeColors` type) and `shared/theme/ThemeContext.tsx`
  (`ThemeProvider` / `useTheme()` — resolves light/dark, persists the user's
  preference to `AsyncStorage`, exposes `{ colors, isDark, colorScheme,
  setTheme }`).
- **Import path**: both apps alias `@shared` → `../shared` in both
  `babel.config.js` (`module-resolver` plugin) and `metro.config.js`
  (`resolver.extraNodeModules` + `watchFolders`), so the bundler — not just
  TypeScript — resolves it. Both `app/_layout.tsx` files import identically:
  ```ts
  import { ThemeProvider, useTheme } from '@shared/theme/ThemeContext';
  ```
  Note: `shared/theme` is **not** listed in `shared/package.json`'s `exports`
  map (that map only covers `@spinr/shared/api`, `/config`, `/store`, etc.).
  The apps never import it as `@spinr/shared/theme` — only via the `@shared/*`
  relative-path alias above. If you add a new theme export, the alias picks it
  up automatically; the npm `exports` map does not need updating for this.
- **Usage in components**: 52 files in rider-app and 53 in driver-app call
  `useTheme()` (checked via grep, excluding tests) — comparable adoption in
  both apps, confirming this isn't a rider-only or driver-only pattern.
- **No per-app override exists.** driver-app previously had its own
  `driver-app/styles/index.ts` color file; it's now an empty stub reading
  `// Driver app styles (colors.ts removed — use useTheme() instead)`. Neither
  app has any other `*theme*`/`*colors*` file outside `shared/theme/`.
- **Typography**: both apps load the same font package
  (`@expo-google-fonts/plus-jakarta-sans`, weights 400/500/600/700) via their
  own `useFonts()` call in `app/_layout.tsx` — this is the standard per-app
  Expo font-loading mechanism (fonts must be registered in each app's own
  bundle), not a design divergence. Both load the identical weight set.

## Known gap: adoption isn't total

**Largely closed in two passes (2026-09-07).** Pass 1: every hex literal
byte-identical to an existing `ThemeColors` value was replaced with a
`colors.<token>` reference (62 replacements / 21 files in rider-app, 37
replacements / 15 files in driver-app) — value-identical in light mode, and
now theme-correct in dark mode where the literal previously ignored it.

Pass 2 found something bigger than leftover debt: `#10B981`/`#EF4444`/
`#F59E0B` (Tailwind emerald/red/amber-500) were being used in exactly the
same semantic spots (`success`/`error`/`warning`) as the official tokens,
just with different shades — not one-offs. Actual usage was split and
inconsistent between the apps (rider-app: 150 Tailwind-shade uses vs. 58
token uses; driver-app: 51 vs. 141 the other way), so this was escalated
to the user rather than resolved unilaterally. **Decision: the Tailwind
shades became the official tokens** (`shared/theme/index.ts`'s
`error`/`success`/`warning`/`danger` updated to `#EF4444`/`#10B981`/
`#F59E0B` light, with new Tailwind-family dark counterparts
`#F87171`/`#34D399`/`#FBBF24`; `info` was untouched — never actually
contested). Both apps were then swept again onto the new values: 26
replacements / 16 files in driver-app, 123 replacements / 34 files in
rider-app.

What's deliberately still hardcoded, and why it's not simply "remaining
debt" to sweep the same way:

- **No exact token match.** A hex value with no equal in `lightColors` isn't
  a missed reference — it's either a genuine one-off (shadow colors,
  third-party map styling) or a color that hasn't been promoted to a token
  yet. Don't guess a "closest" token; if a real semantic need exists, extend
  `ThemeColors` in `shared/theme/index.ts` first, then reference it.
- **Module-level static color maps** defined outside any component (e.g.
  status/tier/relationship-icon lookups) — `useTheme()` isn't in scope
  there; these would need a structural refactor (turn the map into a
  function of `colors`), not a literal swap.
- **Fixed-contrast foreground on a colored/gradient surface** — text or
  icons on a surface that intentionally stays one hue regardless of theme
  (a brand-colored button, a card-face gradient). Swapping in a theme token
  here would *break* contrast in dark mode, not fix consistency.
- **Outside `ThemeProvider`'s tree** — `BrandSplash` (rendered before the
  provider mounts) and the iOS Live Activity / car-display surfaces (no
  React tree at all) have no theme context to read.

So "grep for `useTheme`" is still a better test of whether a given screen is
on-token than assuming either app is now fully clean — the remaining
hardcoded values are the ones that don't mechanically reduce to a token
swap, not an overlooked backlog.

## If you're changing a token

Edit `shared/theme/index.ts` (or `ThemeContext.tsx` for resolution behavior)
once — both apps and their Metro bundlers pick it up on next build, no
per-app change needed. Metro `watchFolders` in both apps' `metro.config.js`
already includes `../shared`, so edits trigger a rebuild in dev.

admin-dashboard does **not** consume this module directly — its Tailwind
`globals.css` has its own port of the same values (see
`docs/change-log/2026-07-29-admin-dashboard-brand-token-port.md`), so a token
change here does not automatically reach admin-dashboard.

For actual color values, dark-mode notes, and typography rationale, see
`.claude/context/brand-spinr.md`.
