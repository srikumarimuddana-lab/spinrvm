# Change Impact & Risk Log — #2816 Batch 7, sub-batch 31: users/promotions/disputes error & signal text

**Issue/gap identified**: `users/page.tsx`, `promotions/page.tsx`, `disputes/page.tsx`, and `disputes/chargebacks-tab.tsx` used hardcoded Tailwind color utilities (`text-red-600 dark:text-red-400`, `text-amber-600 dark:text-amber-400`, `text-emerald-600 dark:text-emerald-400`, `bg-red-50 dark:bg-red-950/30`, `bg-green-50 dark:bg-green-950/30`, `bg-amber-50 dark:bg-amber-950/30`, etc.) for genuine error/warning/success feedback instead of the `--success`/`--warning`/`--destructive` semantic tokens, per #2816.

**Root cause**: These files predate the semantic-token system introduced for #2816; error/warning/success text and banners were styled with ad hoc raw Tailwind color classes at the time they were written.

**Fix/remediation**: Converted every genuine single-signal error/warning/success instance in these four files to the semantic tokens:
- `users/page.tsx`: top-of-page load-error banner (border+text), wallet-action error text (2 occurrences), saved-cards load-error text → `text-destructive`/`bg-destructive/10`/`border-destructive/20`/`border-destructive/30`.
- `promotions/page.tsx`: "no discount cap" warning hint → `text-warning`; "Free Ride" positive label and per-row discount-applied money figure → `text-success`.
- `disputes/page.tsx`: resolved-dispute confirmation box → `bg-success/10`/`text-success`.
- `disputes/chargebacks-tab.tsx`: fetch-error and download-error banners → `bg-destructive/10`/`text-destructive`; "submit to Stripe" irreversible-action warning box → `bg-warning/10`/`text-warning`.

Left untouched (documented exclusions, consistent with prior sub-batches): role badges (`sky`/`violet` — decorative role differentiation, not a signal), avatar/wallet-balance decorative gradients (`sky`/`blue`), solid-fill white-text Credit buttons (`bg-emerald-600 hover:bg-emerald-700` — carried over from the known dark-mode `--success` contrast-risk finding, never converted), the `pending_deletion` categorical exclusion in `users/page.tsx`'s status badge (already documented with `eslint-disable-next-line` from an earlier pass), the multi-column promo/dispute stat-card icon arrays (categorical differentiation, not single signals), and the brand-accent `bg-red-500`/`border-red-500` tab-underline theme in `promotions/page.tsx` (selected-tab styling, not a feedback signal).

**Risk & impact on existing functionality**: Purely a CSS class-name substitution — no JSX structure, props, state, or logic changed in any of the four files. `--destructive`, `--warning`, `--success` are pre-existing tokens already used throughout the admin-dashboard (including in these same files' already-converted `STATUS_COLORS`/`STATUS_CONFIG` maps), so no new CSS variables were introduced. Blast radius: isolated to these four files; no shared component, hook, or utility was touched, so no other page is affected.

**User experience effect**: Internal-admin-only surfaces (Users, Promotions, Disputes/Chargebacks are all under `/dashboard`, not customer-facing). Visually near-identical in both themes — the semantic tokens resolve to the same hue family the raw classes previously hardcoded, just consistently theme-aware. No behavior change, no new user-visible states.

**Files modified**:
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/users/page.tsx` | Load-error banner, 2× wallet-error text, cards-error text → destructive tokens | #2816 |
| `admin-dashboard/src/app/dashboard/promotions/page.tsx` | No-cap warning hint → warning; Free Ride label + discount-applied figure → success | #2816 |
| `admin-dashboard/src/app/dashboard/disputes/page.tsx` | Resolved-dispute box → success tokens | #2816 |
| `admin-dashboard/src/app/dashboard/disputes/chargebacks-tab.tsx` | Fetch/download error banners → destructive; submit-warning box → warning | #2816 |

**Before/after snippet**:
```tsx
// before (users/page.tsx)
{error && (
    <Card className="border-red-200 dark:border-red-900/50">
        <CardContent className="pt-4 pb-4">
            <div className="flex items-center justify-between">
                <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
// after
{error && (
    <Card className="border-destructive/30">
        <CardContent className="pt-4 pb-4">
            <div className="flex items-center justify-between">
                <p className="text-sm text-destructive">{error}</p>
```

**Rollback plan**: `git revert` this commit — pure class-name changes, no data or migration involved.

**Verification performed**:
- `eslint` on the 4 changed files: 0 errors, 53 warnings (all pre-existing residual raw-color warnings on lines this batch did not touch — decorative role badges, avatar gradients, brand-accent tab underlines, categorical/solid-fill exclusions already covered by prior sub-batches' documentation).
- `tsc --noEmit`: pre-existing repo-wide `GeoJSON` namespace errors in unrelated map files (`monitoring-map.tsx`, `maplibre-base.ts`, etc.) — confirmed present identically on unmodified `origin/main` via `git stash` (this batch touches none of those files).
- `npm run build` (production Turbopack build): fails with a pre-existing `@spinr/shared` "Unknown module type" Turbopack error — confirmed identical on unmodified `origin/main` via the same `git stash` test, unrelated to any file this batch touches. **This build failure was NOT introduced by this change**; it is a standing environment/monorepo-config gap this session could not run a passing production build against, regardless of diff content.
- `vitest run`: 339/339 tests pass across all 35 test files.

**What was NOT verified**: No visual regression tooling exists in this repo (standing gap, flagged in `ACTION_ITEMS.md`), so the visual equivalence of the token substitutions was reasoned about (token values already used identically in these same files' pre-existing `STATUS_COLORS` maps), not screenshotted. A passing production build could not be obtained in this session due to the pre-existing `@spinr/shared` Turbopack issue described above — `tsc --noEmit` and `eslint` were run instead, plus confirmation the same failure predates this change.
