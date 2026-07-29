# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (spinr platform session) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (added on push) |
| Related issue or gap ID | Epic #2785 Phase 2; backlog #2803 (superseded finding, see below); new backlog #2816 |

## 1. Issue / gap identified

#2803 hypothesized `color-contrast` (43/64 of all a11y violations, present on 41/41 routes) was likely concentrated in 1-2 shared-token sources rather than 41 independent bugs. Confirmed by diagnosis: `src/components/sidebar.tsx` (rendered on every dashboard route) contained 4 distinct contrast failures, all traced to the same underlying pattern — Phase 0's brand token port (#2786) chose colors safe for one contrast context (solid fill + white text) but the sidebar uses those same tokens in different contexts (text directly on a tinted or raw dark background) that need different values.

## 2. Root cause

Four distinct instances in `sidebar.tsx`, all consuming the app-wide `--primary`/`--destructive` tokens outside the context those tokens were tuned for in Phase 0:

1. **Sidebar section labels** ("Operations", "Configuration") and **user role text** used `text-sidebar-foreground/40` — a 40%-opacity modifier that mathematically resolves to only 3.66-3.7:1 contrast against the sidebar background, below WCAG AA's 4.5:1. (`/50` and `/60`, used elsewhere in the same file, were verified to already pass — only `/40` was broken.)
2. **Active nav-link text + user avatar badge** used `bg-primary/10 text-primary` — `--primary` (`#d32f2f`, Phase 0's fill-safe "primaryDark") as *text* against its own 10%-opacity tint measures 3.74:1 (dark theme) / 4.29:1 (light theme), both below 4.5:1. Phase 0 only verified `--primary` for the "white text on solid fill" case (buttons), not "colored text on a tinted background" (this case).
3. **Sign Out / Sign Out Everywhere** used `text-destructive` as plain text on the sidebar's raw background — `--destructive` (`#dc2626`, Phase 0's fill-safe destructive value) measures 4.09:1 in dark theme as text-on-raw-bg (light theme's `#dc2626` already passes at 4.83:1 in this same role, so only dark was broken).

All three are the same underlying lesson: **a color tuned for "white text on a solid fill" is not automatically safe for "colored text on a tint/raw background"** — they're different contrast pairs requiring independent verification, which Phase 0 didn't do because it only touched fill-role usages directly.

## 3. Fix / remediation

Added three new sidebar-scoped tokens (light/dark) in `globals.css`, distinct from the app-wide `--primary`/`--destructive`, each independently contrast-verified against the *actual rendered background* (not just the color in isolation):

- `--sidebar-foreground-muted`: light `#6b7280` (reuses the existing `--muted-foreground` light value, 4.69:1), dark `#828283` (5.15:1) — replaces the broken `/40` opacity modifier with a real token.
- `--sidebar-primary`: light `#c62828` (4.82:1 against its own 10%-tint-over-white — verified darker than app-wide `--primary` #d32f2f, which only reaches 4.29:1 in this specific context), dark `#ff453a` (5.34:1 — the brand's own vibrant hue, verified brighter than app-wide `--primary`, which only reaches 3.74:1 here; the vibrant hue that Phase 0 explicitly avoided for *fill* contexts turns out to be correct for this *text-on-dark-tint* context).
- `--sidebar-destructive`: light `#dc2626` (unchanged — already 4.83:1 in this role), dark `#ff453a` (5.80:1, same vibrant-hue reasoning as above).

Updated `sidebar.tsx`'s 7 affected class strings (3 active-nav-link states + 1 avatar badge + 2 opacity-muted text spots + 2 Sign Out states) to consume the new tokens instead of the generic ones/opacity modifiers.

**Verified with real axe re-runs, not just hand math**: before the fix, `/dashboard` and `/dashboard/drivers` (sampled) each had 4 color-contrast violations tracing to exactly these 4 sidebar issues; after, both have **zero**. Ran the full 41-route suite: **total a11y violations dropped from 64 to 34 (47%)** — 21 of 41 routes now have zero violations. Updated `e2e/a11y-baseline.json` to lock in the improvement (per the ratchet gate's own rule: a fix must lower the baseline or it isn't "locked in").

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `sidebar.tsx` + `globals.css`.** Grepped the whole `src/` tree for any other consumer of `sidebar-primary`/`sidebar-destructive`/`sidebar-foreground-muted` — none found; these are new tokens only `sidebar.tsx` uses.
- `sidebar.tsx` renders on every one of the 34 dashboard routes (highest blast-radius component in the app) — this is exactly the shared-component scenario `CLAUDE.md`'s pre-merge gate #3 asks about. Assessed as safe to ship directly (not behind a flag) for the same reasoning as Phase 0 (#2786): purely a color-value change within the same visual language (still reads as "brand red," same layout/hierarchy), no structural/interaction change, fully `git-revert-safe`.
- Verified the visual result is still recognizably on-brand, not a jarring departure: `#c62828`/`#ff453a` are both still clearly within the established red family (Material red-800-ish darker shade for light mode's specific tint context; the brand's own vibrant hue for dark mode), not an arbitrary new color.
- `npm run build` and `npm run lint` both clean.

## 5. User-experience effect

- **Internal-admin-facing only.** Sidebar section labels, the active-page indicator, the user avatar badge, and both Sign Out controls are all slightly more legible (higher contrast), especially in dark mode. No layout, spacing, or interaction change — purely color-value adjustments within the same visual style.
- Visible on next page load/refresh, not mid-session-disruptive (no ride/dispatch/payment state involved).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/globals.css` | Added `--sidebar-foreground-muted`, changed `--sidebar-primary`, added `--sidebar-destructive` (light + dark), mapped all three in `@theme inline` | New sidebar-scoped, contrast-verified-for-their-actual-use-context tokens |
| `admin-dashboard/src/components/sidebar.tsx` | 7 class-string changes: 3 active-nav-link states, 1 avatar badge, 2 opacity-muted text spots, 2 Sign Out states | Consume the new tokens instead of the generic `--primary`/`--destructive`/opacity modifiers |
| `admin-dashboard/e2e/a11y-baseline.json` | Lowered 29 routes' violation counts (64→34 total) | Lock in the verified improvement per the ratchet gate's own rule |

## 7. Before / after

```tsx
// Before (sidebar.tsx, active nav link)
active ? "bg-primary/10 text-primary" : "..."
// text-primary (#d32f2f) as TEXT on its own 10%-tint bg: 3.74:1 (dark) / 4.29:1 (light) — fails AA

// After
active ? "bg-sidebar-primary/10 text-sidebar-primary" : "..."
// --sidebar-primary: dark #ff453a (5.34:1), light #c62828 (4.82:1) — both pass AA
```

```css
/* Before (globals.css, dark theme) */
--sidebar-primary: #d32f2f;   /* same value as app-wide --primary */

/* After */
--sidebar-primary: #ff453a;   /* brand's vibrant hue — correct for this text-on-dark-tint context,
                                  the opposite of Phase 0's fill-context conclusion */
```

## 8. Rollback plan

`git-revert-safe` — pure CSS custom-property + className value changes, no data/migration/Stripe state, fully isolated to one component + its tokens.

## 9. Verification performed

- [x] Diagnosed with real axe output (not guesswork): captured full violation node detail (target selector, HTML, computed fgColor/bgColor/contrastRatio) for 4 sampled routes before the fix, confirming the exact 4 sidebar-sourced issues.
- [x] Hand-verified every new token value against WCAG's relative-luminance contrast formula, **against the actual rendered background** (e.g. the self-generated 10%-tint, not just the raw token in isolation) — all 6 new light/dark values pass 4.5:1.
- [x] Re-ran axe after the fix on the same 4 sampled routes: `/dashboard` and `/dashboard/drivers` dropped from 4 violations to 0; `/dashboard/rides` and `/dashboard/staff` correctly retained only their non-sidebar, out-of-scope violations (error boundary heading; hardcoded off-token colors — see #2816).
- [x] Ran the full 41-route suite: 64→34 total violations, 21 routes now zero. Updated `a11y-baseline.json` accordingly.
- [x] `npm run lint` clean, `npm run build` clean.
- [x] Grepped for other consumers of the new/changed tokens — none, confirming isolation to `sidebar.tsx`.
- [ ] Feature-flagged: not applicable, same low-risk reasoning as Phase 0 (#2786) — pure color-value change, no structural change.

## What was NOT verified

- Did not fix the remaining 34 violations — `heading-order`/`page-has-heading-one` (moderate, structural), `button-name`/`label`/`aria-valid-attr-value` (critical, icon-buttons/form-labels on specific pages), and the error-boundary `text-destructive` heading are all still open, tracked in #2803.
- Discovered — but explicitly did NOT fix — a much larger, separate problem: 91 files across the app use hardcoded Tailwind color classes (`text-gray-900`, `bg-red-500`, etc.) instead of theme tokens, including one page (`/dashboard/staff`) with a heading at 1.12:1 contrast (essentially invisible in dark mode). This is a different shape of problem (page-by-page, unknown-scope) than the shared-sidebar fix here, and is tracked as its own backlog item, #2816, rather than attempted here.
- Did not visually screenshot the after-state — no committed visual-regression baselines exist yet (#2809 is still pending), so there's no automated before/after comparison; relied on axe's numeric contrast output and reasoning about the specific hex values, not a rendered screenshot diff.
