import type { CSSProperties } from 'react';

/**
 * Light-mode values of the admin design tokens this page uses, re-declared on
 * the page's own root element.
 *
 * /track is the public link riders share, and it is fixed-light on purpose:
 * it must look the same whatever the visitor's system theme or the admin
 * app's theme toggle. But the root layout's ThemeProvider defaults to "dark",
 * so <html> carries `.dark` for nearly every visitor and globals.css then
 * points `--background`, `--foreground`, … at the dark palette. That is why
 * the #2816 token migration skipped this page (2026-07-29 and 2026-08-21
 * change logs) and it kept raw Tailwind grays.
 *
 * Tailwind's `@theme inline` compiles `bg-card` to `var(--card)`, so
 * re-declaring the variables on an ancestor retargets every semantic class
 * under it (the same mechanism `.theme-v2` uses). With this scope on the
 * page root, the markup can use bg-card / text-foreground / text-success
 * like the rest of admin and still render light.
 *
 * The values MUST equal globals.css `:root`; track-brand-tokens.render.test
 * .tsx fails if either side drifts.
 */
export const TRACK_LIGHT_TOKENS = {
  '--background': '#f9fafb',
  '--foreground': '#111827',
  '--card': '#ffffff',
  '--muted': '#f3f4f6',
  '--muted-foreground': '#6b7280',
  '--border': '#e5e7eb',
  '--ring': '#ff3b30',
  '--success': '#15803d',
  '--warning': '#b45309',
  '--info': '#1d4ed8',
  '--destructive': '#dc2626',
} as CSSProperties;
