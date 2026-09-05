---
name: spinr-admin-design-system
description: The actual, current admin-dashboard design system and direction — tokens, the Quiet Console visual language, the admin_theme_v2_enabled flag, and the known open gaps. Load this before making any visual/design judgment about admin-dashboard, before running /design-review, or before styling a new admin-dashboard component. Supersedes any assumption that admin-dashboard's direction is "futuristic" — that was #2785's original framing, replaced 2026-08-31 by Quiet Console.
---

# Spinr admin-dashboard design system

Canonical sources, in priority order — read these, don't re-derive values from memory:
1. `admin-dashboard/src/app/globals.css` — the real token values (`:root`, `.dark`, `.theme-v2`, `html:not(.dark) .theme-v2`)
2. `docs/change-log/2026-08-31-quiet-console-stage-1-3.md` — the direction's own Change Impact Log, with the full rationale for every choice below
3. `.claude/context/brand-spinr.md` — the cross-app brand reference (colors/type/logo), written for surfaces that don't import `shared/theme/index.ts` directly

## The direction: "Quiet Console," not "futuristic"

`#2785` originally asked for a "professional, on-brand, futuristic feel." That framing was **explicitly superseded 2026-08-31** by a minimalist, Japanese-influenced direction the user named "Quiet Console" — calmer, more restrained, most of the screen neutral with color reserved for real signal. Do not design or critique toward "futuristic"; it's the abandoned brief, not the current one.

**Current status: shipped in code, off in production.** Everything below (except the logo fix) lives behind the `admin_theme_v2_enabled` `app_settings` flag, currently `false`. Stage 4 (canary rollout, then flip the flag) is an explicit human decision, not yet made. When reasoning about what admin-dashboard *actually looks like today* to a real user, the answer is: the pre-Quiet-Console default styling, with one exception — the sidebar logo fix (below) shipped unconditionally.

## Tokens (light mode, `:root`)

| Token | Value | Notes |
|---|---|---|
| `--primary` | `#d32f2f` | Contrast-safe brand red variant (`primaryDark` in `shared/theme/index.ts`), not the raw brand `#FF3B30` — chosen for WCAG AA on white fills |
| `--background` | `#f9fafb` | |
| `--foreground` | `#111827` | |
| `--destructive` | `#dc2626` | |
| `--success` / `--warning` / `--info` | `#15803d` / `#b45309` / `#1d4ed8` | |
| `--radius` | `0.625rem` (10px) | Base; `--radius-sm/md/lg/xl/2xl/3xl/4xl` derive via `calc()` |

## Dark mode

**True OLED black (`--background: #09090b`), not near-black — a deliberate, hard product constraint** (mobile battery profile), reaffirmed explicitly when Quiet Console was approved ("no OLED-true-black deviation"). Never propose lightening admin-dashboard's dark background toward a dark-gray "richer" look; that's the one thing this direction was told not to touch. `--primary` in dark mode stays the same contrast-safe `#d32f2f` (not the brighter brand `#FF453A`) for the same AA-on-fill reason as light mode.

## `.theme-v2` — what changes when the flag is on

Scoped `html:not(.dark) .theme-v2` (light mode only — dark mode is untouched everywhere in this block, per the OLED constraint):

- **Radius**: `0.625rem` → `0.375rem` (6px) — *tighter*, not rounder. (A now-abandoned 2026-08-21 attempt went the other way, 10px→16px; Quiet Console reversed that call. If you see a stale reference to "softer/rounder" admin-dashboard corners, it's describing the abandoned direction.)
- **Card shadow**: `Card`'s shadow flows through a `--shadow-card` token (`shadow-[var(--shadow-card)]`, not a static `shadow-sm` class) — defaults to `var(--shadow-sm)` (pixel-identical to today), becomes `none` under the flag. Flat cards, not shadowed ones.
- **Neutral palette** (light-mode-only): warmer paper/ink set — `--background: #fafaf8`, `--foreground: #1c1c1a`, `--secondary`/`--muted`: `#f2f1ec`. Brand accent tokens (`--primary`, `--destructive`, `--warning`, `--success`, `--ring`, `--chart-1..5`, `--sidebar-primary`) are **deliberately untouched in value** — only how often a screen reaches for them should change, which is a per-component decision, not a token one.
- **Sidebar active-indicator**: filled `bg-sidebar-primary/10` pill → thin left-edge inset rule (`shadow-[inset_2px_0_0_0_var(--sidebar-primary)]`) — chosen specifically because an inset shadow doesn't touch the box model, so nothing shifts layout when the flag toggles.
- **`PageHeader`**: `font-bold` → `font-semibold`.
- **`Badge`**: 6-variant vocabulary for status/category pills — `outline-success` (positive), `outline-warning` (pending/attention), `outline-destructive` (negative), `outline-info` (genuinely neither), `outline-accent` (the one standout in an otherwise-flat group), plain `outline` (purely categorical, no semantic charge). Replaced ~122 ad-hoc `bg-{color}-100`/hardcoded color-map pills across 17 files. **Exception, don't "fix" these**: multi-state color maps with more states than the vocabulary can express (7-state ride hero status, 5-state driver insurance-period phase) are deliberately left as their own color maps — a 6-variant system can't carry that many distinct states without losing real information.

## Logo (shipped unconditionally, not flag-gated)

`sidebar.tsx` previously rendered a fake "S"-in-a-colored-square placeholder. Now renders the real, theme-adaptive Spinr wordmark (`admin-dashboard/public/spinr-logo-{light,dark}.png`) via `next/image` + `next-themes`' `resolvedTheme`. This is the one visible-today change regardless of the flag.

## Known, tracked gaps — don't silently "fix" or restate these as new findings

- **Typography**: admin-dashboard still ships Geist, not the brand's actual **Plus Jakarta Sans** (used in rider-app/driver-app). `.theme-v2` overrides `--font-geist-sans` itself (not the `--font-sans` alias — Tailwind v4's `@theme inline` inlines the alias's source variable at build time, so overriding the alias has no effect; verified live before relying on this). This gap is explicitly tracked under this same epic (`#2785`), not a new finding to raise.
- **~90-file long tail**: `#2816`'s broader token-migration backlog (routes not yet on semantic tokens) is unscoped by design — needs a per-file "hardcoded-but-fine vs. hardcoded-and-broken" judgment call, not a blind sweep. Two concrete large targets (`/dashboard/staff`, `/dashboard/service-areas`) are already done.

## Reusable findings for anyone extending this system

- A reused, already-verified token in a *new* pairing is not automatically safe — verify live (axe + real rendered content, both themes) before trusting a CSS read alone; two contrast regressions were caught this way during `/dashboard/staff`'s migration, after the source looked correct.
- CSS comment hygiene: a literal `--chart-*/` inside a `/* ... */` block closes the comment early (`*/` is the CSS comment terminator) and silently corrupts everything after it — caught once already via a real production build, not `tsc`. Never write a raw `*/`-shaped substring inside a CSS comment.
- Reuse the established `ring-primary/NN` / `border-primary/NN` / `bg-primary/NN` "selected/active state" idiom (already in `staff/page.tsx`, `vehicle-types/page.tsx`, `drivers/page.tsx`, `driver-notes.tsx`) rather than inventing a new active-state treatment.
