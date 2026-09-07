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

Both apps still have hardcoded hex colors outside the `ThemeColors` palette,
in similar proportion — this is a shared inconsistency, not a rider-vs-driver
split:

- rider-app: hardcoded hex literals appear in ~46 files under `app/` and
  `components/` (most frequently `#10B981`, `#EF4444`, `#F59E0B`, `#3B82F6`,
  `#8B5CF6` — Tailwind-palette-shaped colors that aren't in `ThemeColors`).
- driver-app: same pattern, ~33 files, same top offending values.

These aren't necessarily bugs (some may be legitimate one-offs — shadow
colors, third-party map styling, etc.) but they mean "grep for `useTheme`"
is a better test of whether a given screen is on-token than assuming the
whole app is copy-clean. Do not add new hardcoded hex values in either app;
extend `ThemeColors` in `shared/theme/index.ts` instead so both apps pick it
up.

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
