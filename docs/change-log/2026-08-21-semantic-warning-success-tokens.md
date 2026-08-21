# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | Admin Portal UX Audit P2/§06 Stage 1 |

## 1. Issue / gap identified

`globals.css` defines a real design-token system (`--color-primary`,
`--color-destructive`, `--color-muted`, 5 chart colors) but has no
semantic "warning" or "success" token. `--destructive` is the only
status-semantic color that exists. Every "pending" or "active" badge in
the codebase hardcodes raw Tailwind classes (`bg-amber-500/15
text-amber-600`, `bg-emerald-100 text-emerald-800`, etc.) instead, because
there's no first-class alternative to reach for.

## 2. Root cause

The token system was built (epic #2785 Phase 0/2) around the colors that
existed in `shared/theme/index.ts` at the time, which itself has no
semantic warning/success entries — only chart-series colors that happen to
be amber/green-ish (`--chart-4`, `--chart-2`) but were never intended for
text-on-background usage and fail WCAG AA text contrast in light mode
(verified below).

## 3. Fix / remediation

Added `--warning` / `--success` tokens to both themes in `globals.css`,
plus `--color-warning` / `--color-success` in the `@theme inline` block so
Tailwind utilities (`bg-warning`, `text-warning`, `border-success`, etc.)
become available everywhere, matching how `--destructive` already works.

Colors picked for real WCAG AA contrast, computed via the same
relative-luminance formula as the file's existing `--primary`/
`--destructive` picks (not eyeballed):

| Token | Light | Dark | Contrast (light, on `--background`/`--card`) | Contrast (dark, on `--background`/`--card`) |
|---|---|---|---|---|
| `--warning` | `#b45309` | `#f59e0b` (= existing `--chart-4` dark) | 4.81:1 / 5.02:1 | 9.26:1 / 9.21:1 |
| `--success` | `#15803d` | `#30d158` (= existing `--chart-2` dark) | 4.80:1 / 5.02:1 | 9.84:1 / 9.78:1 |

Dark-mode values reuse the existing `--chart-4`/`--chart-2` hex values
directly (they already clear AA contrast by a wide margin on `--card`) for
one fewer near-duplicate color in the palette. Light-mode values are new,
dedicated hex — the existing chart-4/chart-2 light values fail AA as text
(`#d97706` is 3.19:1, `#34c759` is 2.22:1 on white) which is exactly why
`--primary` also couldn't reuse the vibrant brand hue for text and needed
its own `primaryDark` pick — same reasoning applied here.

## 4. Risk & impact on existing functionality

- **Blast radius: zero today.** Nothing in the codebase reads
  `--color-warning`/`--color-success`/`bg-warning`/`text-warning`/etc. yet
  — this PR only adds the tokens, it doesn't migrate any of the 113 files
  identified in the UX audit's hardcoded-color backlog (#2816). That
  migration is a separate, later PR per the audit's staged roadmap.
- Purely additive CSS custom properties — cannot regress any existing
  rendering since nothing currently depends on these variable names.

## 5. User-experience effect

**None yet, deliberately.** No visible change until a follow-up PR
migrates a specific badge/component to use these tokens. This PR exists
so that follow-up work has a real semantic token to reach for instead of
hardcoding another one-off amber/green pair.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/globals.css` | Added `--warning`/`--success` to `:root` and `.dark`, plus `--color-warning`/`--color-success` to `@theme inline` | New semantic status tokens |

## 7. Before / after

```
# Before — no warning/success token existed
--destructive: #dc2626;
--border: #e5e7eb;

# After
--destructive: #dc2626;
--warning: #b45309;
--success: #15803d;
--border: #e5e7eb;
```

## 8. Rollback plan

`git-revert-safe` — pure CSS addition, nothing consumes it yet.

## 9. Verification performed

- [x] Real production build (`npm run build`) — succeeded.
- [x] `npx vitest run` — 339/339 passed.
- [x] Contrast ratios computed via the WCAG relative-luminance formula
      (not estimated) for both themes against both `--background` and
      `--card` — all four combinations clear 4.5:1.
- [ ] Not visually screenshotted — nothing renders differently yet since
      no component consumes these tokens (stated explicitly rather than
      implied by omission).

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated, not assumed — zero, nothing consumes these yet.
- [x] No silent behavior change — nothing currently renders differently.
