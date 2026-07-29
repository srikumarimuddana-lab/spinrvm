# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (spinr platform session) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (added on push) |
| Related issue or gap ID | Epic #2785 — admin-dashboard visual refresh, Phase 0 |

## 1. Issue / gap identified

`admin-dashboard` does not use Spinr's brand colors. `src/app/globals.css` was built from raw shadcn/ui "new-york" defaults (Tailwind `red-600`/`red-500` for primary/destructive/ring/chart-1), unrelated to the brand red (`#FF3B30`/`#FF453A`) already used consistently by rider-app and driver-app via `shared/theme/index.ts`.

## 2. Root cause

The admin-dashboard scaffold was generated from shadcn's default theme and never updated to reference `shared/theme/index.ts` — there is no cross-surface enforcement that admin-dashboard consume the shared token file (it's a separate Next.js app, not sharing a build with rider/driver-app).

## 3. Fix / remediation

Ported the brand palette from `shared/theme/index.ts` (`lightColors`/`darkColors`) into `admin-dashboard/src/app/globals.css`'s `:root`/`.dark` CSS custom properties — color values only, no structural or component changes. Deviated from a literal 1:1 port in two places for WCAG AA compliance (see §7):
- `--primary`/`--sidebar-primary` use the brand's own `primaryDark` (`#D32F2F`) rather than the vibrant `primary` (`#FF3B30`), because `#FF3B30` with white button-label text measures 3.55:1 contrast — below WCAG AA's 4.5:1 normal-text minimum. `#D32F2F` measures 4.98:1.
- Dark-mode `--destructive` changed from Tailwind `red-500` (`#ef4444`, 3.76:1 with white text — already failing AA before this change) to `#dc2626` (4.83:1), the same value as light-mode destructive and equal to the brand's own light `error`/`danger` token.
- The vibrant brand hues (`#FF3B30` light / `#FF453A` dark) are used for `--ring` and `--chart-1`, which only need the 3:1 non-text-contrast threshold (WCAG 1.4.11) and pass it.
- `--chart-2`/`--chart-3` aligned to brand `success`/`info` tokens; `--chart-4` was already numerically identical to brand `warning`; `--chart-5` (violet) has no brand equivalent and is unchanged, kept for categorical chart variety.

Typography (Geist → PlusJakartaSans) and border-radius (10px → 16px) are explicitly **out of scope** for this change — tracked as later Phase-0 follow-up in #2785, kept separate to stay within CLAUDE.md's batch-size convention.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface, single-file.** Only `admin-dashboard/src/app/globals.css` changed — no component, route, or logic file touched.
- Grepped `admin-dashboard/src` for `bg-primary|text-primary|border-primary|ring-primary|bg-destructive|text-destructive`: **183 occurrences across 75 files**, all consuming the Tailwind semantic utility classes that resolve through the CSS custom properties edited here — none hardcode a raw hex value that would bypass this change or need separate updating.
- No other surface reads this file — `admin-dashboard` is a standalone Next.js app; rider-app/driver-app consume `shared/theme/index.ts` directly and are unaffected.
- Purely a color-value swap within the same token *roles* (primary is still primary, destructive still destructive) — no renamed/removed/repurposed CSS variables, so nothing can silently mis-resolve.

## 5. User-experience effect

- **Internal-admin-facing only.** Every admin staff member sees the new brand red instead of generic Tailwind red on primary buttons, focus rings, active sidebar item, and chart series 1–3, in both light and dark mode.
- Visible on next page load/refresh — not a mid-session behavior change to an in-progress workflow (no ride/wallet/dispatch state involved), but any admin with the dashboard open across a deploy will see the color change without re-authenticating.
- No copy, layout, or interaction changes — purely a color-value swap.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/globals.css` | `--primary`, `--sidebar-primary`, `--ring`, `--sidebar-ring`, `--chart-1`, `--chart-2`, `--chart-3` (light + dark); `--destructive` (dark only) reassigned to Spinr brand values | Brand parity with rider-app/driver-app; two values chosen for WCAG AA contrast rather than a literal port (see §3) |

## 7. Before / after

```css
/* Before — :root (light) */
--primary: #dc2626;
--ring: #dc2626;
--chart-1: #dc2626;
--chart-2: #059669;
--chart-3: #2563eb;
--sidebar-primary: #dc2626;
--sidebar-ring: #dc2626;

/* After — :root (light) */
--primary: #d32f2f;      /* brand primaryDark — AA-safe (4.98:1) */
--ring: #ff3b30;          /* brand primary — vibrant, non-text 3:1 use */
--chart-1: #ff3b30;
--chart-2: #34c759;       /* brand success */
--chart-3: #3b82f6;       /* brand info */
--sidebar-primary: #d32f2f;
--sidebar-ring: #ff3b30;
```

```css
/* Before — .dark */
--primary: #ef4444;
--destructive: #ef4444;   /* 3.76:1 with white text — was already below AA */
--ring: #ef4444;
--chart-1: #ef4444;
--chart-2: #10b981;
--chart-3: #3b82f6;
--sidebar-primary: #ef4444;
--sidebar-ring: #ef4444;

/* After — .dark */
--primary: #d32f2f;       /* same AA-safe value as light — brand's own primaryDark is theme-invariant */
--destructive: #dc2626;   /* 4.83:1 — fixes a pre-existing AA failure */
--ring: #ff453a;           /* brand dark primary — vibrant, non-text 3:1 use */
--chart-1: #ff453a;
--chart-2: #30d158;        /* brand dark success */
--chart-3: #0a84ff;        /* brand dark info */
--sidebar-primary: #d32f2f;
--sidebar-ring: #ff453a;
```

## 8. Rollback plan

`git revert` is sufficient and complete here: this is a pure CSS custom-property value change with no data, migration, or third-party (Stripe/wallet) state involved. Reverting restores the exact prior visual state with no follow-up cleanup required.

## 9. Verification performed

- [x] Real production build run: `npm run build` (Next.js 16.2.4, Turbopack) — compiled successfully in 22.5s, TypeScript checked clean, all 67 pages generated. (Not just dev server / `tsc --noEmit`.)
- [ ] Automated tests run — not applicable; no unit/e2e test asserts on color values, and this repo has zero visual-regression coverage (`admin-dashboard/e2e/*` has no `toHaveScreenshot()` usage) — this is the standing gap tracked as Phase 1 of #2785, not yet closed.
- [ ] Manual repro / staging screenshot check — **not performed**. No visual-regression tooling exists yet to compare before/after, and this session has no way to render the app in a browser with a live backend to eyeball it. Contrast values were computed analytically (WCAG relative-luminance formula), not screenshotted.
- [x] Blast-radius grep performed: `bg-primary|text-primary|border-primary|ring-primary|bg-destructive|text-destructive` across `admin-dashboard/src` — 183 occurrences / 75 files, all via Tailwind semantic classes resolving through the edited CSS variables (see §4).
- [x] Reviewed against relevant CLAUDE.md conventions: WCAG 2.1 AA (contrast computed and enforced per value, see §3), pre-merge gate on shared-component blast radius (assessed as low-risk/isolated — pure color swap, no structural change, git-revert-safe — see §4, so shipped directly rather than behind an `app_settings` flag; larger Phase 2+ changes in #2785 will be flagged).
- [ ] Feature-flagged — **not flagged**, justified above as a low-risk, purely-cosmetic, single-file, fully-revertible change; not the kind of behavior/structural change the flagging gate targets.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (grep evidence in §4)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 states exactly what changes and for whom)

## What was NOT verified

- No visual/screenshot confirmation that the new colors render as intended in a real browser — this repo currently has no visual-regression tooling (tracked as #2785 Phase 1) and no way to run a live-backend-connected browser session from here.
- Contrast ratios were computed by hand against the WCAG 2.1 relative-luminance formula for the specific "white text on solid fill" and "color against page background" cases actually used by shadcn's button/ring/chart components — not verified with an automated contrast-checker tool or `@axe-core/playwright` (not yet wired into CI; also tracked in #2785 Phase 1 / `ACTION_ITEMS.md` E11).
- Did not audit every one of the 183 grep hits individually for context (e.g. a `text-primary` used at small font size against an unusual background could still have a marginal contrast issue not captured by the two representative cases computed here).
