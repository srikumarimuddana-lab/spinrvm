# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 74

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

## Issue/gap identified
`safety/page.tsx`'s incident-detail "Save" button lacked the inline documentation this
migration uses for its dark-mode contrast-risk exclusions. `cloud-messaging/page.tsx`'s
summary-stat array mixed three genuine delivery-outcome signals (Sent/Failed/Success
Rate) in with purely decorative category counts, and its "Cancel Message" confirm
button used a fixed red instead of the standard destructive pattern.

## Root cause
These predate the shared tokens; the "Save" button was already a deliberate
contrast-risk exception from earlier work but lacked the inline reason.

## Fix/remediation
- `safety/page.tsx`: added a documenting `eslint-disable-next-line` to the existing
  solid-fill "Save" button (`bg-emerald-600`, white text) in the incident detail panel
  — already a deliberate contrast-risk exception (dark-mode `--success` fails WCAG AA
  against white text); no color change.
- `cloud-messaging/page.tsx`: stat-card array's "Sent" and "Success Rate" tiles →
  `text-success`, "Failed" tile → `text-destructive` — these are literal delivery
  outcomes (not arbitrary category counts like the array's other tiles), matching the
  established precedent of converting a genuine signal within an otherwise-decorative
  stat-tile set (e.g. `ride-stats-cards.tsx`'s "Platform Net" indicator).
- `cloud-messaging/page.tsx`: "Cancel this message?" `AlertDialogAction` → standard
  shadcn destructive pattern.

Left untouched (already-documented or decorative, confirmed by review not silently
skipped):
- `safety/page.tsx`'s `severityTone()` (SEV1/SEV2/SEV3 severity map) — already fully
  documented from a prior sub-batch (SEV3 has no token equivalent).
- `safety/page.tsx`'s pickup(emerald)/dropoff(red) address-list dots — the established
  fixed-role pickup/dropoff convention, not a status signal.
- `safety/page.tsx`'s page-header gradient (`from-red-50`) — decorative page-theme
  accent for the safety/SOS surface, not a per-item status signal.
- `cloud-messaging/page.tsx`'s `STATUS_CONFIG` — already fully converted/documented
  from a prior sub-batch (`scheduled` has no token equivalent).
- `cloud-messaging/page.tsx`'s `NOTIFICATION_TYPES` — already a documented categorical
  exception (5 message-type icon colors) from a prior sub-batch.
- `cloud-messaging/page.tsx`'s remaining stat-tile entries ("Total Messages"/violet,
  "Scheduled"/blue, "Recipients Reached"/amber) — arbitrary category-count
  differentiation, not outcome signals.
- `cloud-messaging/page.tsx`'s tab underline (decorative selected-state, same pattern
  as `promotions.tsx`) and various violet/blue section-header icons (decorative,
  matching the Spinr-feature-icon convention used throughout the admin portal).

## Risk & impact on existing functionality
All edits are within `app/dashboard/safety/page.tsx` and `app/dashboard/
cloud-messaging/page.tsx`. Grepped for other importers: both are leaf route pages, not
imported elsewhere. No shared-component blast radius. No props, state shape, or
exported symbols changed — one documentary comment plus plain Tailwind class-string
substitutions.

## User experience effect
The two "Sent"/"Success Rate" stat-tile icons and the "Failed" tile icon change hue to
the app's success/destructive tokens (visually near-equivalent). "Cancel this message?"
changes from a fixed red to the standard destructive token. No layout, copy, or
behavior change. Admin-portal-facing only.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/safety/page.tsx` | Documented existing "Save" button contrast-risk exception (no color change) | #2816 token migration |
| `src/app/dashboard/cloud-messaging/page.tsx` | Sent/Failed/Success Rate stat-tile icons → `--success`/`--destructive`; "Cancel Message" confirm button → standard destructive pattern | #2816 token migration |

## Before/after snippet
```tsx
// cloud-messaging/page.tsx stat array — before
{ label: "Sent", value: stats.total_sent, icon: CheckCircle2, color: "text-emerald-500" },
{ label: "Failed", value: stats.total_failed, icon: XCircle, color: "text-red-500" },
// after
{ label: "Sent", value: stats.total_sent, icon: CheckCircle2, color: "text-success" },
{ label: "Failed", value: stats.total_failed, icon: XCircle, color: "text-destructive" },
```

## Rollback plan
Pure CSS class-string revert — `git revert` this commit restores the prior classes with
no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on both files: 0 errors (28 pre-existing warnings on `safety/page.tsx`,
  22 on `cloud-messaging/page.tsx` — all decorative/categorical exceptions reviewed
  above plus unrelated `react-hooks` warnings; none of the edited lines appear in
  either warning list).
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
- The choice to convert only 3 of the 6 `cloud-messaging` stat-tile entries (the ones
  with a literal outcome meaning) rather than the whole array is a judgment call,
  flagged for visibility rather than asserted as unambiguous.
