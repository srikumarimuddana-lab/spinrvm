# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 57

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

## Issue/gap identified
- `ride-stats-cards.tsx`'s "Platform Net (post-promo)" card used a fixed
  red/emerald pair to indicate whether the platform's post-promo net is negative or
  positive — a genuine two-state financial signal, not decorative.
- `ride-flag-form.tsx`'s "Flag" submit button used a fixed `bg-red-600 text-white`
  solid destructive style instead of the standard `bg-destructive`/`text-destructive-foreground`
  pattern.
- `ride-invoice.tsx`'s "Download PDF" outline button used fixed emerald shades instead
  of the `--success` token.

## Root cause
Same as prior sub-batches: these components predate the shared semantic tokens.

## Fix/remediation
- `ride-stats-cards.tsx`: the conditional `platformAfterNeg` color/background pair
  converted from `text-red-600 dark:text-red-400`/`bg-red-100 dark:bg-red-900/30` and
  `text-emerald-600 dark:text-emerald-400`/`bg-emerald-100 dark:bg-emerald-900/30` to
  `text-destructive`/`bg-destructive/15` and `text-success`/`bg-success/15` — a genuine
  negative/positive financial-state signal.
- `ride-flag-form.tsx`: submit button → `text-destructive-foreground bg-destructive
  hover:bg-destructive/90`, matching the standard shadcn destructive-button pattern
  used repeatedly elsewhere in this migration.
- `ride-invoice.tsx`: "Download PDF" button → `border-success/40 text-success
  hover:bg-success/10` — an outline-style button (not solid-fill white-text), so the
  dark-mode `--success` contrast risk that blocks *solid* success buttons elsewhere in
  this migration does not apply here.

Left untouched (established exclusions, consistent with prior sub-batches):
- `ride-stats-cards.tsx`'s remaining ~9 fixed hues (blue/emerald/teal/amber/pink/slate/
  violet/indigo/blue) across `StatCard`/`RevenueCard` — the established money-category-
  differentiation exclusion: distinct arbitrary hues distinguishing unrelated revenue/
  count categories (rides, driver revenue, tips, incentives, GST, promo, area fees,
  platform sales) in a stat grid, not a good/bad signal.
- `ride-flag-form.tsx`/`ride-complaint-form.tsx` dialog-title icons (red/amber) —
  decorative icon accents next to a heading.
- `ride-complaint-form.tsx`'s "Submit Complaint" solid `bg-amber-600 text-white` button
  — left untouched out of the same contrast-risk caution applied to solid `bg-success`
  buttons elsewhere: no prior sub-batch has verified a solid white-text `bg-warning`
  button's dark-mode contrast, so converting it here would be an unverified assumption
  rather than a confirmed-safe substitution.
- `create-ride-modal.tsx`'s blue pickup-pin / red dropoff-pin icon, border, and ring
  colors — the established fixed pickup(blue)/dropoff(red) UI convention, same as the
  map-pin dot colors excluded elsewhere.
- `ride-lost-found.tsx`'s `STATUS_ICONS` map — already fully converted in earlier work
  (reported=warning, resolved=success, unresolved=destructive, driver_notified
  documented as a blue exception); no changes needed.
- `ride-list.tsx`'s rider/driver avatar icon colors (blue/emerald) — decorative
  entity-type differentiation, and its emerald "+tip" / "Time" figure accents — decorative
  stat highlighting, not a dual-state signal.

## Risk & impact on existing functionality
All three edited files are standalone leaf components under `rides/_components/`
(no shared-component blast radius — each is used only by its own parent ride-detail
surface). No props, state shape, or exported symbols changed.

## User experience effect
Purely color-token substitutions to visually equivalent (already-approved,
contrast-verified) tokens. The `ride-stats-cards.tsx` conversion preserves the exact
same negative/positive threshold behavior, just re-themed. No layout, copy, or
behavior change. Admin-portal-facing only.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/rides/_components/ride-stats-cards.tsx` | "Platform Net" negative/positive indicator → `--destructive`/`--success` | #2816 token migration (genuine financial signal) |
| `src/app/dashboard/rides/_components/ride-flag-form.tsx` | "Flag" submit button → standard destructive pattern | #2816 token migration |
| `src/app/dashboard/rides/_components/ride-invoice.tsx` | "Download PDF" outline button → `--success` | #2816 token migration |

## Before/after snippet
```tsx
// ride-stats-cards.tsx — before
color={platformAfterNeg ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}
bg={platformAfterNeg ? "bg-red-100 dark:bg-red-900/30" : "bg-emerald-100 dark:bg-emerald-900/30"}
// after
color={platformAfterNeg ? "text-destructive" : "text-success"}
bg={platformAfterNeg ? "bg-destructive/15" : "bg-success/15"}
```
```tsx
// ride-flag-form.tsx — before
className="px-4 py-2 text-sm font-semibold text-white bg-red-600 rounded-lg hover:bg-red-700 disabled:opacity-50"
// after
className="px-4 py-2 text-sm font-semibold text-destructive-foreground bg-destructive rounded-lg hover:bg-destructive/90 disabled:opacity-50"
```

## Rollback plan
Pure CSS class-string revert — `git revert` this commit restores the prior hardcoded
classes with no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on all three edited files: 0 errors. Remaining warnings are all
  pre-existing/expected: the documented money-category-differentiation exclusion on
  `ride-stats-cards.tsx`'s untouched cards, the decorative dialog-icon exclusion on
  `ride-flag-form.tsx`, and unrelated pre-existing `react-hooks`/`jsx-a11y` warnings —
  no new `no-restricted-syntax` warnings introduced by this change.
- `npx vitest run`: 339/339 tests passing across all 35 test files.
- `npm run build` (Turbopack) not re-run — the pre-existing, diff-unrelated
  `@spinr/shared` "Unknown module type" Turbopack failure was already root-caused
  against unmodified `origin/main` in sub-batch 31/PR #4371; this sub-batch is plain
  Tailwind class-string edits with no import/module changes.

## What was NOT verified
- No visual regression tooling exists in this repo for the admin dashboard (standing
  gap, `ACTION_ITEMS.md`) — token substitutions were reasoned about against previously
  contrast-verified token values, not screenshotted.
- Not tested against a live Supabase/staging deployment — only against the existing
  mocked `vitest` fixtures.
