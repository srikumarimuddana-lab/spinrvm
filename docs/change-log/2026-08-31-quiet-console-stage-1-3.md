# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude, at user request — a minimalist/Japanese-influenced "Quiet Console" design direction, proposed as an artifact after the 2026-08-28 admin portal audit, approved with two constraints (no OLED-true-black deviation; logo must be theme-adaptive) and "start building Stage 1 now and others if they can run in parallel without conflicts" |
| Surface(s) | admin-dashboard, driver-app (one shared logo asset) |
| Domain (Sentry tag) | admin |
| PR / commit link | PR following this log, branch `claude/admin-quiet-console-stage1` |
| Related issue or gap ID | "Quiet Console" design-direction artifact (published this session); items 03.2/03.3 of the 2026-08-28 audit (Theme v2 dormant, six inconsistent page-header treatments) |

This is Stages 1–3 of a 4-stage rollout plan (Stage 4 — canary, then flip the flag on — is explicitly a human decision, not part of this PR). All three stages ship inside the existing `admin_theme_v2_enabled` flag, currently `false` in production, **retargeted** from an abandoned 2026-08-21 restyle to this direction rather than adding a second flag. 13 independent pieces of work (1 direct + 2 parallel agents for Stage 1/2, 3 parallel agents for Stage 3, plus 2 direct follow-up fixes) were implemented in isolated git worktrees and merged sequentially — every merge was clean, zero manual conflict resolution, including several that touched the same files across stages (`sidebar.tsx` alone: logo swap + active-rule change; `badge.tsx`: 3 separate additions across Stage 1 and the info-gap fix; `staff/page.tsx`, `drivers/page.tsx`, `safety/page.tsx` each touched once more here on top of the prior audit-fix PRs).

## 1. Issue / gap identified

The prior audit (2026-08-28) fixed structural/accessibility gaps but left the admin portal's *visual* character untouched: five-plus simultaneous saturated hues across chart palettes, role pickers, and ~122 ad-hoc `bg-{color}-100` status pills scattered across ~24 files with no shared treatment; every `<Card>` shadowed and heavily-rounded by default; six inconsistent page-header weights; a sidebar active-state and a placeholder-not-real logo. The user asked for a calmer, more restrained direction, explicitly modeled on Japanese minimalist design principles.

## 2. Root cause

Not a broken design system — an under-used one. `Badge` and the semantic `--success`/`--warning`/`--destructive`/`--info` tokens were already WCAG-verified and disciplined; ~24 files independently reached for their own ad-hoc Tailwind color classes instead of the shared component, which is what produced the visual noise. Separately: `sidebar.tsx`'s brand mark was never wired to the real logo asset at all (a static "S" in a colored square), and the one real logo PNG that exists has a dark-charcoal wordmark that is close to unreadable against the OLED-true-black dark background.

## 3. Fix / remediation

**Stage 1 — tokens, Card, Badge foundation** (`globals.css`, `card.tsx`, `badge.tsx`):
- `.theme-v2`'s neutral palette (background/foreground/card/border/muted/sidebar-*) now shifts to a warmer, quieter paper/ink set — **scoped `html:not(.dark) .theme-v2`, light mode only**. Dark mode is untouched: true OLED black stays exactly as-is, per explicit instruction not to deviate from the brand's mobile-battery-driven decision.
- `.theme-v2`'s `--radius` retargeted from the old (abandoned) direction's `1rem` (softer/rounder) to `0.375rem` (tighter/quieter) — same flag, reversed value.
- New `--shadow-card` token: defaults to Tailwind's own `--shadow-sm` (pixel-identical to today), `none` under the flag. `Card`'s static `shadow-sm` class swapped for `shadow-[var(--shadow-card)]` so it flows automatically, same pattern `--radius` already used.
- Brand accent (`--primary` and all semantic tokens) **untouched in value** — only how often the UI reaches for it changes, which is a component-level decision, not a token one.
- New `Badge` variants: `outline-accent`, `outline-success`, `outline-warning`, `outline-destructive`, and (added after Stage 3 surfaced a real gap) `outline-info` — all reuse **existing, already-WCAG-verified** semantic tokens, no new colors invented. All are pure additions; zero effect until a call site opts in.

**Logo fix (unconditional, not flag-gated — a real bug fix)**:
- Generated a dark-mode variant of the canonical Spinr wordmark (`driver-app/assets/images/spinr-logo.png` source) by recoloring its ink pixels (identified via palette analysis, not a blanket filter) to the brand's own dark-mode text token (`#F2F2F7`) while leaving the red bullseye mark's pixels untouched — verified pixel-level correctness by compositing the result against true black before use, not just eyeballing a preview. `sidebar.tsx`'s fake "S"-in-a-box placeholder replaced with the real, theme-adaptive wordmark via `next/image` + `next-themes`' `resolvedTheme`, matching the same theme-detection pattern already used elsewhere in the app (no new hydration-guard invented).

**Stage 2 — sidebar active-indicator, PageHeader weight** (flag-gated):
- Active nav link: filled `bg-sidebar-primary/10` pill → thin left-edge rule (`shadow-[inset_2px_0_0_0_var(--sidebar-primary)]`, chosen specifically because an inset box-shadow draws without touching the box model — no padding/margin compensation needed, nothing shifts on flag toggle).
- `PageHeader`'s `<h1>`: `font-bold` → `font-semibold` under the flag.
- Both verified byte-identical to today when the flag is off.

**Stage 3 — badge consolidation across 21 dashboard files** (flag-gated, 3 parallel batches):
- Every ad-hoc `bg-{color}-100`/hardcoded color-map status or category pill in `drivers/`, `staff/`, `bulk-operations/`, `monitoring/`, `quests/`, `rides/`, `safety/`, `sentry-logs/`, `service-areas/`, `support-tickets/` now has a flag-gated Badge-variant alternate, mapped by real semantic meaning (positive→`outline-success`, pending/attention→`outline-warning`, negative→`outline-destructive`, genuinely-neither→`outline-info`, the one standout in an otherwise-flat group→`outline-accent`, purely categorical→plain `outline`) — not a blind find-replace. 17 of 21 files needed real changes; 4 were verified to have zero genuine status-pill markup (pure data-viz KPI tiles or structural status banners) and were left untouched, stated explicitly rather than silently skipped.
- A handful of multi-state color maps (7-state ride hero status, 5-state driver insurance-period phase) were **deliberately left unconverted** — both already carry a prior-audit comment explaining a 3–5-token system can't express that many distinct states without losing real information; collapsing them would have been a regression, not a simplification.

## 4. Risk & impact on existing functionality

- **Blast radius: `globals.css`/`card.tsx` are used by every route (~90); `badge.tsx` likewise.** The entire mechanism protecting against a production-visible regression is the `.theme-v2` / `html:not(.dark) .theme-v2` scoping and the per-component `themeV2Enabled` ternary pattern — both verified directly against the *compiled* CSS/JS output (not just source-reading) at each stage: confirmed `--shadow-card` resolves to `var(--shadow-sm)` by default and only `none` under the flag; confirmed the quiet-palette block only applies under the light+flag selector; confirmed every flag-gated component's `false` branch is byte-identical to its pre-change classes.
- **A real, self-caught bug**: the CSS comment for the light-mode neutral block originally contained a literal `--chart-*/` — the `*` immediately followed by `/` closed the CSS comment early, corrupting everything after it and breaking the production build. Caught by running a real build (not just `tsc`), root-caused via bisection against the raw CSS parser rather than guessed at, fixed before merge.
- **No other consumer of these files does anything the flag would break**: `Badge`'s new variants are inert until referenced; `Card`'s shadow change is token-driven with a verified default; the logo swap is the only *unconditional* visual change in this PR, and it fixes a fake placeholder that was never gated by anything to begin with.
- **Nothing here touches ride state, dispatch, payments, wallets, or insurance-period logic.**

## 5. User-experience effect

- **Internal admin-facing only**, plus one asset shared with driver-app's canonical logo folder (no driver-app code changed — the new dark-variant PNG is unused there today, available for a future fast-follow).
- **With the flag off (current production state): the only visible change anywhere is the sidebar logo** — it now shows the real Spinr wordmark instead of a fake "S" placeholder, adapting correctly to light/dark mode. Everything else (palette, radius, shadows, active-nav treatment, header weight, badge colors) is invisible until a super-admin flips `admin_theme_v2_enabled` on.
- **With the flag on** (not enabled by this PR): quieter neutrals in light mode only, tighter corners, flat cards, a thin nav rule instead of a filled pill, slightly lighter header weight, and every status/category badge across 17 files reading through one consistent, muted 6-variant vocabulary instead of ~122 independent color choices.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/globals.css` | Quiet neutral palette (light-mode-only), retargeted `--radius`, new `--shadow-card` token | Stage 1 foundation |
| `admin-dashboard/src/components/ui/card.tsx` | Static `shadow-sm` → token-driven | Stage 1 |
| `admin-dashboard/src/components/ui/badge.tsx` | 5 new outline variants (accent/success/warning/destructive/info) | Stage 1 + gap fix |
| `admin-dashboard/src/components/sidebar.tsx` | Real adaptive logo (unconditional); active-rule (flag-gated) | Logo bug fix + Stage 2 |
| `admin-dashboard/src/components/page-header.tsx` | Flag-gated font-weight | Stage 2 |
| `admin-dashboard/public/spinr-logo-{light,dark}.png`, `driver-app/assets/images/spinr-logo-dark.png` | New assets | Logo bug fix |
| 17 files under `dashboard/{drivers,staff,bulk-operations,monitoring,quests,rides,safety,sentry-logs,service-areas,support-tickets}/**` | Flag-gated Badge-variant alternates for ad-hoc status/category pills | Stage 3 |

## 7. Before / after

```css
/* Before — globals.css, a real bug caught before merge */
--primary/--destructive/--warning/--success/--ring/--chart-*/
--sidebar-primary are deliberately NOT touched here...
/* the `*` + `/` in "--chart-*/" closes the CSS comment early — everything
   after this point was silently parsed as invalid CSS, breaking the build */

/* After */
--primary/--destructive/--warning/--success/--ring/--chart-1..5/
--sidebar-primary are deliberately NOT touched here...
```

```tsx
// Before — sidebar.tsx
<div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary shrink-0">
  <span className="text-sm font-bold text-primary-foreground">S</span>
</div>
{!collapsed && <span className="...">Spinr</span>}

// After
<Image
  src={resolvedTheme === "dark" ? "/spinr-logo-dark.png" : "/spinr-logo-light.png"}
  alt="Spinr" width={384} height={156} priority
  className={collapsed ? "h-[18px] w-auto" : "h-7 w-auto"}
/>
```

## 8. Rollback plan

- **Everything flag-gated** (all of globals.css's palette/radius/shadow work, Stage 2, Stage 3's 17 files): flipping `admin_theme_v2_enabled` back to `false` (its current default — nothing to do) reverts every visible effect instantly, no deploy needed. `git revert` on top of that is also safe and complete — no data, no migration.
- **The unconditional logo fix**: `git revert` is sufficient — pure static-asset + component change, no data.

## 9. Verification performed

- [x] Real production build (`npm run build`) run and passing at every stage boundary (after Stage 1 alone, after Stage 1+2, after the full Stage 1+2+3 merge) — not just `tsc --noEmit`.
- [x] Compiled CSS output directly inspected (not just source) to confirm: `--shadow-card` resolves to `var(--shadow-sm)` by default / `none` under `.theme-v2`; the quiet palette is correctly scoped to `html:not(.dark) .theme-v2`; all 5 new Badge variant classes and the active-nav inset-shadow rule compile as expected.
- [x] `npx eslint` run on every changed file at every stage — 0 errors throughout; warnings cross-checked line-by-line against pre-change state to confirm none were newly introduced.
- [x] Each Stage 3 batch explicitly verified its flag-off rendering path is unchanged, not just its flag-on path.
- [x] The CSS-comment-closes-early bug was caught by an actual failing build, root-caused via bisection (isolated the exact file/line with `lightningcss` directly), not guessed at or patched around.

## What was NOT verified

- **No visual screenshot/browser check of the flag-ON state** — admin-dashboard has no committed visual-regression baselines (`ACTION_ITEMS.md` B38, a standing gap, not new here). Every visual claim above is verified via compiled-CSS inspection and source-level reasoning, not a rendered screenshot. Recommend a manual look-through (flag on, in a real browser, both themes) before Stage 4's canary.
- **Company-portal's 3 files with the same ad-hoc badge pattern were deliberately excluded from Stage 3** — that's the external corporate-customer-facing surface, a different audience than internal admin; flagged as a scope question for the user rather than silently included or dropped.
- **Two known multi-state color maps left unconverted** (ride hero status, 7 states; driver insurance-period phase, 5 states) — both predate this PR's own reasoning (already commented as "too many states for the token system") and were correctly left alone rather than forced into a 5-variant vocabulary that would have lost real distinctions.
- **rider-app/driver-app were not checked for the same dark-mode-logo-legibility gap** — the fix here is scoped to admin-dashboard (the audit's subject); the mobile apps use the same underlying asset and likely share the issue, but that's each app's own asset-loading convention and wasn't investigated here.
