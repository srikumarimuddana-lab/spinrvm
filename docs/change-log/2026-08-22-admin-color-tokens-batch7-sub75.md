# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 75

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

## Issue/gap identified
`corporate-accounts/page.tsx` had a "delete corporate account" confirm button on a
fixed red, a solid-fill "Active" status badge lacking the standard contrast-risk
documentation, and a "KYB re-verification due" reminder banner on a fixed sky-blue
instead of a warning signal. `corporate-accounts/kyb-queue/page.tsx`'s "Approve" button
had the same undocumented contrast-risk gap.

## Root cause
These predate the shared tokens; the two solid-fill badges/buttons were already
deliberate contrast-risk exceptions but lacked the inline reason.

## Fix/remediation
- `corporate-accounts/page.tsx`: "Are you sure?" delete-confirmation button → standard
  shadcn destructive pattern.
- `corporate-accounts/page.tsx`: added a documenting `eslint-disable-next-line` to the
  existing solid-fill "Active" badge (`bg-emerald-500`, white text) — no color change.
- `corporate-accounts/page.tsx`: "N companies due for KYB re-verification" reminder
  banner (border/bg/text/link-hover, all sky-blue) → `--warning` tokens — the code's own
  comment describes this as "a reminder for an admin to manually re-run the KYB review
  flow," the same reminder-nudge pattern already converted for KYC due/past-due banners
  elsewhere in this migration.
- `corporate-accounts/kyb-queue/page.tsx`: added a documenting `eslint-disable-next-line`
  to the existing solid-fill "Approve" button (`bg-emerald-600`, white text) — no color
  change.

Left untouched (decorative, confirmed by review not silently skipped):
- `kyb-queue/page.tsx`'s "Preview" link (blue) — decorative link-styling convention, not
  a status signal. Its "Reject" button was already tokenized (`text-destructive`).
- `heatmap/page.tsx`'s remaining stat-tile icons ("Active Demand"/orange, "Surge
  Active"/amber — the other two tiles in the same set, "Idle Supply"/"Demand Pressure",
  were already converted to success/destructive in a prior sub-batch) and its
  demand-forecast bar-chart fill (orange, a "heat" visualization brand color) — none of
  these three are a genuine success/warning/destructive signal.
- `analytics/page.tsx`'s page-header and "Revenue" section-header icons (blue/amber) —
  decorative section-icon accents, not status signals.

## Risk & impact on existing functionality
All edits are within `app/dashboard/corporate-accounts/page.tsx` and `app/dashboard/
corporate-accounts/kyb-queue/page.tsx`. Grepped for other importers: both are leaf
route pages, not imported elsewhere. No shared-component blast radius. No props, state
shape, or exported symbols changed.

## User experience effect
The delete-confirmation button changes from a fixed red to the standard destructive
token (visually near-identical). The KYB-due reminder banner changes from sky-blue to
amber/warning — a more accurate signal for an "admin attention needed, not urgent"
reminder, consistent with the KYC due/past-due banners already using this pattern
elsewhere. The two badge/button documentation edits have zero visual change. No layout
or copy change to any element. Admin-portal-facing only.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/corporate-accounts/page.tsx` | Delete-confirm button → standard destructive pattern; KYB-due banner → `--warning`; documented "Active" badge exception | #2816 token migration |
| `src/app/dashboard/corporate-accounts/kyb-queue/page.tsx` | Documented "Approve" button contrast-risk exception (no color change) | #2816 token migration |

## Before/after snippet
```tsx
// corporate-accounts/page.tsx KYB-due banner — before
<Card className="border-sky-300/50 bg-sky-50/40 dark:bg-sky-950/10">
  <div className="... text-sky-800 dark:text-sky-400 ...">
// after
<Card className="border-warning/40 bg-warning/10">
  <div className="... text-warning ...">
```

## Rollback plan
Pure CSS class-string revert — `git revert` this commit restores the prior classes with
no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on both files: 0 errors, 5 pre-existing warnings (all unrelated
  `react-hooks` warnings on `corporate-accounts/page.tsx`; `kyb-queue/page.tsx`
  produced 0 warnings after the edit — no raw-color warnings remain in either file).
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
- Re-classifying the KYB-due banner from sky-blue to warning is a judgment call (the
  code comment frames it as a low-urgency reminder, not a hard warning) — flagged for
  visibility rather than asserted as unambiguous.
