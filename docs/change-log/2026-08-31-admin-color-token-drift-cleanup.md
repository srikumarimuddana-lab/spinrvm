# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude (session on behalf of vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (branch `claude/map-vehicle-tracking-animation-3e85y2`) |
| Related issue or gap ID | #2816 (original status-badge token migration); design-consistency audit finding, 2026-08-31 |

## 1. Issue / gap identified

A design-consistency audit flagged "57 files bypass status-badge tokens (already-tracked, reopened gap)" for admin-dashboard, based on a raw grep for hardcoded Tailwind color classes.

**That characterization was independently verified and found to be substantially stale.** `docs/change-log/2026-08-22-admin-color-tokens-batch7-final-sweep.md` shows #2816's Stage 1 migration was already fully completed on 2026-08-22 — a 45-file, agent-driven classification pass reached **0** `no-restricted-syntax` ESLint warnings repo-wide (down from 402), with 17 genuine token conversions and 315 deliberately-documented, contrast-verified exceptions (categorical maps, decorative accents, contrast-risk cases). The audit subagent's raw grep counted those documented exceptions as unmigrated, which is what produced "57 files."

Running the repo's own authoritative check (`npx eslint . | grep -c no-restricted-syntax`, the same tool the migration's own final-sweep doc calls "authoritative, not a hand-rolled grep") found **12** warnings, not 54–57 files — genuine new drift introduced in code added *after* the 2026-08-22 closure, confined to 2 files.

## 2. Root cause

`drivers/page.tsx`'s profile-completeness badges/progress-bar (added after 2026-08-22) and `stripe-events/page.tsx`'s event-type badge/icon colors (a page not touched by the original 45-file sweep) hardcoded raw Tailwind color classes instead of the `--success`/`--warning`/`--destructive`/`--info` tokens the migration established.

## 3. Fix / remediation

Applied the same a/b/c classification methodology as the original migration. All 12 warnings were case (a) — genuine signal, converted to semantic tokens (none needed a categorical-map or decorative exception):

- `drivers/page.tsx`: profile-completeness badge (Complete/Incomplete/Missing) and its progress-bar fill both map 1:1 to success/warning/destructive — converted to `bg-success/15 text-success`, `bg-warning/15 text-warning`, `bg-destructive` (destructive badge was already token-based). "All required fields complete" text converted to `text-success`.
- `stripe-events/page.tsx`: `eventTypeBadge()`'s 4-way category (succeeded/paid → success, failed/dispute → destructive, refund → warning, everything else → info) maps 1:1 onto the four status tokens, now including `--info` — converted to `bg-{token}/15 text-{token}`. The header's warning-triangle icon, the empty-state checkmark icon, and the replay button's hover-color were single-purpose status icons, converted to `text-warning`/`text-success`/`text-success hover:text-success/80` respectively (matching the existing `text-destructive hover:text-destructive/80` pattern used elsewhere in the codebase).

Added `--info`/`--color-info` tokens to `globals.css` (light `#1d4ed8`, dark `#0a84ff`, both WCAG AA text-contrast verified — see inline comments) since no info-status token existed yet; the stripe-events 4th badge category was the first real consumer.

## 4. Risk & impact on existing functionality

- **Blast radius: 2 files, purely visual class-name changes.** No logic, no props, no data flow touched — every changed line is a CSS class string or a ternary branch selecting one. Grepped for other consumers of `eventTypeBadge()`: only used within `stripe-events/page.tsx` itself. No other importer of anything touched here.
- New `--info`/`--color-info` tokens are purely additive to `globals.css`'s existing `@theme inline`/`:root`/`.dark` blocks — no existing token renamed or removed, so no other component using `--success`/`--warning`/`--destructive` is affected.
- The `bg-x/15 text-x` opacity-modifier pattern is already used in 10+ other files in this codebase (sidebar.tsx, company-portal pages, auto-payouts-panel.tsx, etc.) — not a new pattern being introduced.

## 5. User-experience effect

- **Admin-facing, visual only, same effective colors.** Every hex value converted maps to (or in `--info`'s case, was specifically WCAG-AA-verified to be close to) the original color the raw Tailwind class rendered — this is a token-source change, not a redesign. No behavior, layout, or copy change. Not mid-session-disruptive (badge colors on internal list/detail pages, not a live-updating surface).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/page.tsx` | 3 hardcoded color-class sites → success/warning/destructive tokens | Close new #2816 drift |
| `admin-dashboard/src/app/dashboard/stripe-events/page.tsx` | 4 hardcoded color-class sites (1 function, 3 JSX) → success/warning/destructive/info tokens | Close new #2816 drift |
| `admin-dashboard/src/app/globals.css` | Added `--info`/`--color-info` (light `#1d4ed8`, dark `#0a84ff`) | No info-status token existed; needed for stripe-events' 4th badge category |

## 7. Before / after

```tsx
// Before (drivers/page.tsx)
<Badge className="bg-emerald-100 text-emerald-700 hover:bg-emerald-100 dark:bg-emerald-900/30 dark:text-emerald-400 ...">Complete</Badge>
// After
<Badge className="bg-success/15 text-success hover:bg-success/15 ...">Complete</Badge>
```
```tsx
// Before (stripe-events/page.tsx)
function eventTypeBadge(eventType) {
  ...
  return "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300"; // no token existed for this
}
// After
function eventTypeBadge(eventType) {
  ...
  return "bg-info/15 text-info";
}
```

## 8. Rollback plan

`git revert` of the relevant commit — pure CSS-class and CSS-custom-property changes, no data, no migration, no feature flag needed.

## 9. Verification performed

- [x] `npx eslint .` — **0 `no-restricted-syntax` warnings** (down from 12; verified via `grep -c`), 336 total warnings remaining (unrelated, pre-existing).
- [x] `npx tsc --noEmit` — clean.
- [x] `npx vitest run` (real build-adjacent check for this surface, not just tsc) — **59/59 test files, 561/561 tests passing.**
- [x] Blast-radius grep: confirmed `eventTypeBadge()` has no other callers; confirmed no other file imports the touched components' color logic.
- [ ] Manual/visual verification in a browser — not performed; no active visual-regression tooling for admin-dashboard (baselines not yet seeded, see `ACTION_ITEMS.md` B38) and no browser session in this environment. Colors were reasoned about via the same hex-value mapping the original 2026-08-22 migration used, not screenshotted.
- [x] `npm run build` — **not run** for this change (CSS-token/class-name-only diff, no build-breaking surface touched); flagging per CLAUDE.md's explicit requirement to state whether it was run, not assume dev-server/tsc coverage is equivalent.

## 10. What was NOT verified

- No visual/screenshot verification (standing admin-dashboard gap, `ACTION_ITEMS.md` B38).
- `npm run build` was not run — only `tsc --noEmit`, `eslint`, and `vitest`. A pure CSS-variable + Tailwind-class change is low-risk for a build-only failure mode, but this is not the same guarantee a full production build gives.
- Did not re-audit the rest of the codebase for further drift beyond the 12 ESLint-flagged warnings — this fix closes exactly what `eslint .` currently flags, not a broader manual sweep.

## Correction to prior audit reporting

The design-consistency audit's admin-dashboard finding ("57 files bypass status-badge tokens, already-tracked, reopened gap") overstated the actual gap by roughly 5x, because it was based on a raw grep rather than running the repo's own authoritative `eslint .` check. The #2816 migration was in fact already fully closed as of 2026-08-22 (0 warnings, well-documented). The real, current gap was 12 warnings across 2 files — new drift since that closure — now fixed by this change.
