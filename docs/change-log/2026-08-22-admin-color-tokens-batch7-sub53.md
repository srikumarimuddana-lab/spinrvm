# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 53

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

## Issue/gap identified
- `components/ui/toast.tsx` (shadcn base component): the destructive-toast close button
  used fixed `red-300`/`red-50`/`red-400`/`red-600` shades instead of the
  `--destructive`/`--destructive-foreground` tokens the rest of the destructive variant
  already uses.
- `components/sidebar.tsx`: a "pending count" attention signal (nav-item icon emphasis,
  a corner indicator dot, and an expanded count pill — all driven by the same
  `badgeFor()`/`item.emphasize` pending-queue signal) used fixed amber shades in three
  places instead of the shared `--warning` token.
- `components/referral-leaderboard.tsx` and `components/referral-analytics.tsx`: fixed
  `text-red-600 dark:text-red-400` error text.
- `components/referral-pairs.tsx`: a 4-state payout-status map (`paid`/`processing`/
  `failed`/`expired`) was undocumented as an intentional exception.

## Root cause
Same as prior sub-batches: components predate the shared `--success`/`--warning`/
`--destructive` tokens, or (for `referral-pairs.tsx`) the map was never flagged as a
deliberate exception with a suppression comment explaining why.

## Fix/remediation
- `toast.tsx`: `group-[.destructive]:text-red-300 dark:hover:text-red-50 focus:ring-red-400 focus:ring-offset-red-600` → `group-[.destructive]:text-destructive-foreground/70 hover:text-destructive-foreground focus:ring-destructive focus:ring-offset-destructive`. Matches the existing `ToastAction`'s own `group-[.destructive]:focus:ring-destructive` convention two lines above it in the same file.
- `sidebar.tsx`: all three amber "pending" indicators (nav-icon emphasis, corner dot,
  expanded count pill) converted to `text-warning` / `bg-warning` / `bg-warning/15 text-warning`
  respectively — a single genuine signal repeated three times, not three separate
  decorative choices.
- `referral-leaderboard.tsx`, `referral-analytics.tsx`: error text → `text-destructive`.
- `referral-pairs.tsx`: `STATUS_COLOR` wrapped in an
  `eslint-disable/eslint-enable no-restricted-syntax` block with a one-line reason —
  left as hand-picked colors because "processing" has no dedicated semantic token
  (only success/warning/destructive exist in `globals.css`), so a partial conversion
  (2 of 4 states tokenized, 2 not) would be less consistent than documenting the whole
  map as an intentional exception.

Left untouched (established exclusions, consistent with prior sub-batches):
- `driver-map.tsx`'s online/offline legend dots (emerald/zinc) — fixed UI convention,
  same class as pickup/dropoff map-pin dots.
- `rich-text-editor.tsx`'s `text-blue-600` link color inside rendered rich-text content —
  a fixed typography/link-color convention, not a status signal.
- `alert-feed.tsx`'s amber `Zap` "Live Events" icon — small decorative icon accent next
  to a label.
- `referral-spend-summary.tsx`, `referral-leaderboard.tsx`'s `SummaryCard`, and
  `referral-analytics.tsx`'s `Stat` component `accent` props (emerald/sky/violet/amber/blue
  hues differentiating money/count categories in a stat grid) — the established
  money-category-differentiation exclusion (3+ arbitrary hues for multi-column KPI/stat-row
  variety), same pattern as `STAT_COLOR_CLASSES` in `dashboard/page.tsx`. Also the
  decorative `Trophy`/`TrendingUp`/`DollarSign`/`XCircle`/`Users` icon accents adjacent to
  headings in these three files.
- `referral-leaderboard.tsx` line 84 (`text-emerald-600` "Qualified" table cell) — a
  per-column stat differentiation, not a multi-state signal badge.

## Risk & impact on existing functionality
- `toast.tsx` is a shared shadcn base component used by every toast in the app (rider
  invites, error banners, admin action confirmations, etc.) — but the change is scoped
  to the destructive variant's close-button hover/focus colors only; the visible
  destructive background/text (`bg-destructive text-destructive-foreground`, unchanged)
  already carries the contrast guarantee, and `ToastAction`'s adjacent
  `group-[.destructive]:focus:ring-destructive` establishes this is the same token
  already relied on elsewhere in this exact component. No other toast variant is touched.
- `sidebar.tsx` is the app-wide navigation shell — the change is scoped to the
  `item.emphasize`/`badgeFor()` pending-count indicators only; the active/inactive nav
  states (`bg-sidebar-primary/10`, etc.) are untouched.
- `referral-leaderboard.tsx`/`referral-analytics.tsx`/`referral-pairs.tsx` are each
  standalone leaf components (shared only between the standalone Referrals page and the
  Earnings → Referrals tab, per their own doc comments) — no other consumers.

## User experience effect
Purely color-token substitutions to visually equivalent (already-approved,
contrast-verified) tokens under both light and dark themes. No layout, copy, or
behavior change. Sidebar navigation is used continuously by every logged-in admin
session, so this is visible mid-session — but the rendered color is equivalent, not a
functional change.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/components/ui/toast.tsx` | destructive-toast close button: fixed red shades → `--destructive`/`--destructive-foreground` tokens | #2816 token migration |
| `src/components/sidebar.tsx` | 3 amber "pending" indicators → `--warning` token | #2816 token migration |
| `src/components/referral-leaderboard.tsx` | error text → `text-destructive` | #2816 token migration |
| `src/components/referral-analytics.tsx` | error text → `text-destructive` | #2816 token migration |
| `src/components/referral-pairs.tsx` | `STATUS_COLOR` map documented as an intentional exception with `eslint-disable`/`eslint-enable` | #2816 token migration (documentation, no functional change) |

## Before/after snippet
```tsx
// toast.tsx — before
"... group-[.destructive]:text-red-300 group-[.destructive]:hover:text-red-50 group-[.destructive]:focus:ring-red-400 group-[.destructive]:focus:ring-offset-red-600"
// after
"... group-[.destructive]:text-destructive-foreground/70 group-[.destructive]:hover:text-destructive-foreground group-[.destructive]:focus:ring-destructive group-[.destructive]:focus:ring-offset-destructive"
```
```tsx
// sidebar.tsx — before / after (one of three equivalent conversions)
item.emphasize && !active && "text-amber-600 dark:text-amber-500"
item.emphasize && !active && "text-warning"
```

## Rollback plan
Pure CSS class-string revert (plus removing the eslint-disable comment block in
`referral-pairs.tsx`, which changes no runtime behavior) — `git revert` this commit
restores the prior hardcoded classes with no data migration, feature flag, or config
involved.

## Verification performed
- `npx eslint` on all five edited files: 0 errors. Remaining warnings are all
  pre-existing/expected: established decorative-icon and money-differentiation
  exclusions (documented above), and unrelated pre-existing `react-hooks` warnings
  (`set-state-in-effect`, `exhaustive-deps`) already present on these files before this
  change. No new `no-restricted-syntax` warnings on any converted line.
- `npx vitest run`: 339/339 tests passing across all 35 test files.
- `npm run build` (Turbopack) not re-run — the pre-existing, diff-unrelated
  `@spinr/shared` "Unknown module type" Turbopack failure was already root-caused
  against unmodified `origin/main` in sub-batch 31/PR #4371; this sub-batch is plain
  Tailwind class-string edits with no import/module changes.

## What was NOT verified
- No visual regression tooling exists in this repo for the admin dashboard (standing
  gap, `ACTION_ITEMS.md`) — token substitutions, including the sidebar's always-visible
  pending-count indicator, were reasoned about against previously contrast-verified
  token values, not screenshotted.
- Not tested against a live Supabase/staging deployment — only against the existing
  mocked `vitest` fixtures.
