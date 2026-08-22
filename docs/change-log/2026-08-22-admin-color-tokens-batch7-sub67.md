# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 67 (partial pass)

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

`service-areas/page.tsx` (2,886 lines, 62 raw-color matches before this sub-batch) —
this is a **partial pass**: it covers the genuine warning/success signals (surge
justification, Spinr Pass enable/require toggles, incentive-active toggle, heatmap
truncation notice, config-warning severity tiers, platform-wide-settings notice,
allowlist invalid-entries notice, unsaved-changes indicator, subscription active
badge, and the vehicle-types empty state). The airport-zone feature section (blue
branding), the quests/incentives feature section (amber branding), and the document-
requirement attribute badges are left as decorative/feature-branding and are not
touched — see below. Remaining raw-color matches after this sub-batch are all in
those confirmed-decorative sections.

## Issue/gap identified
Multiple genuine signal groups used fixed Tailwind shades instead of the shared
`--success`/`--warning`/`--destructive` tokens: the surge-justification warning box
(whose message text was already on `text-warning` from earlier work, but its
border/background/textarea border were not), the Spinr Pass enabled/required toggle
cards, the incentive active/inactive toggle button, the heatmap-truncation notice, the
lower-severity ("info") tier of a two-tier warning list, the platform-wide-heatmap-
settings notice, the allowlist invalid-entries notice, the "Unsaved changes" indicator,
the subscription-transaction active-status badge, and the vehicle-types empty state.

## Root cause
Same as prior sub-batches: these sections predate the shared semantic tokens, or (for
the surge-justification box) were only half-converted in earlier work.

## Fix/remediation
- Surge-justification box: border/background/textarea border → `border-warning/40
  bg-warning/10`.
- Spinr Pass "enabled" toggle card → `bg-success/10 border-success/30`; "required"
  toggle card → `bg-warning/10 border-warning/30`.
- Incentive active-toggle button hover → `hover:bg-success/10` (was mixing an
  already-converted `text-success` with a raw `hover:bg-green-50`).
- Heatmap-truncation notice, platform-wide-settings notice, allowlist invalid-entries
  notice, and "Unsaved changes" indicator → `text-warning` (with `border-warning/40
  bg-warning/10` on the two box variants).
- Two-tier warning list's lower-severity branch: documented with
  `eslint-disable-next-line` — the "warning" tier already uses `--destructive`
  (pre-existing), and the lower tier has no dedicated semantic token (only success/
  warning/destructive exist), so it stays a hand-picked amber.
- Subscription-transaction active badge → `bg-success/15 text-success`.
- Vehicle-types empty state → `bg-warning/10 border-warning/30`/`text-warning`.

Left untouched (decorative feature-branding, confirmed not a status signal):
- The airport-zone section (AIRPORT badges, zone-creation form, zone cards) — a
  consistent blue accent for this specific feature across badges, form fields,
  buttons, and cards, the same pattern as "Spinr Pass" using violet elsewhere.
- The quests/incentives management section (create-quest form, quest cards, bonus-
  amount badge) — a consistent amber accent for this feature, same reasoning.
- The document-requirement attribute badges (Required/Has Expiry/Both Sides) — these
  tag configuration properties, not a live good/bad status.
- The "Saved" button state (`bg-green-500 text-white`) — the established dark-mode
  solid-button contrast-risk exclusion.

## Risk & impact on existing functionality
All edits are within `service-areas/page.tsx`, not imported elsewhere — no
shared-component blast radius. No props, state shape, or exported symbols changed.

## User experience effect
Purely color-token substitutions to visually equivalent (already-approved,
contrast-verified) tokens. No layout, copy, or behavior change. Admin-portal-facing
only.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/service-areas/page.tsx` | Surge-justification box, Spinr Pass toggles, incentive-toggle hover, heatmap/platform-wide/allowlist notices, unsaved-changes indicator, subscription badge, vehicle-types empty state → `--success`/`--warning`; lower-severity warning tier documented as an exception | #2816 token migration + documentation (partial pass) |

## Before/after snippet
```tsx
// Spinr Pass enabled toggle — before
<div className={`... ${enabled ? 'bg-green-50 border border-green-200 dark:bg-green-900/20 dark:border-green-800' : 'bg-muted border border-border'}`}>
// after
<div className={`... ${enabled ? 'bg-success/10 border border-success/30' : 'bg-muted border border-border'}`}>
```

## Rollback plan
Pure CSS class-string revert (plus removing the `eslint-disable-next-line` comment,
which changes no runtime behavior) — `git revert` this commit restores the prior
classes with no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on the file: 0 errors. Remaining warnings are all in the confirmed
  decorative feature-branding sections listed above, plus unrelated pre-existing
  `react-hooks` warnings — no new warnings on any line this sub-batch edited.
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
- This is explicitly a partial pass — the airport-zone and quest/incentive
  feature-branding sections and the document-requirement attribute badges were
  reviewed and classified as decorative, not silently skipped; no further raw-color
  matches remain that need classification in this file for Stage 1.
