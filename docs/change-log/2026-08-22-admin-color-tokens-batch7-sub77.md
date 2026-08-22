# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 77

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

## Issue/gap identified
`support/_tabs/lost-and-found.tsx`'s "Delete lost item?" confirm button used a fixed
red instead of the standard destructive pattern. `rides/_components/
ride-complaint-form.tsx`'s "Submit Complaint" button (a solid-fill white-text amber
button, previously reviewed and left untouched in sub-batch 57) lacked the inline
documentation this migration uses for its no-token-equivalent exceptions.

## Root cause
The delete button predates the shared `--destructive` token. The submit button is a
genuine no-token-equivalent case — as confirmed in sub-batch 76, this design system has
no `--warning-foreground` token, so no solid-fill warning-tier button anywhere in the
admin portal has ever been safely converted; it simply lacked the inline comment.

## Fix/remediation
- `lost-and-found.tsx`: "Delete lost item?" `AlertDialogAction` → standard shadcn
  destructive pattern.
- `ride-complaint-form.tsx`: added a documenting `eslint-disable-next-line` to the
  existing solid-fill "Submit Complaint" button — no color change.

Left untouched (already-documented categorical exceptions, confirmed by review not
silently skipped):
- `lost-and-found.tsx`'s `S_CFG` and `ride-lost-found.tsx`'s `STATUS_ICONS`
  (reported/driver_notified/resolved/unresolved — both mirror the same 4-state map,
  `driver_notified` already documented as having no token equivalent) — already fully
  converted/documented from prior sub-batches.
- `support-tickets/page.tsx`, `support-tickets/tickets/page.tsx`, and
  `support-tickets/tickets/[id]/page.tsx`'s `statusClass()` ("open" branch) — already
  documented from a prior sub-batch.
- `support-tickets/tickets/[id]/page.tsx`'s message-thread accent border (Internal
  note/Agent reply/Customer — amber/blue/slate) — categorical message-role
  differentiation, not a status signal.
- Several decorative "accent" stat-card highlights and link-styling blues throughout
  `support-tickets/page.tsx`, `support-tickets/trends/page.tsx`, and
  `support-tickets/tickets/[id]/page.tsx` — not status signals.

## Risk & impact on existing functionality
All edits are within `app/dashboard/support/_tabs/lost-and-found.tsx` and
`app/dashboard/rides/_components/ride-complaint-form.tsx`. Grepped for other
importers: `lost-and-found.tsx` is a tab component used only by the Support page's tab
router; `ride-complaint-form.tsx` is used only by `ride-detail-modal.tsx` (already
migrated in sub-batch 69). No shared-component blast radius beyond those two known
call sites. No props, state shape, or exported symbols changed.

## User experience effect
The delete-confirmation button changes from a fixed red to the standard destructive
token (visually near-identical). The "Submit Complaint" edit is comment-only, zero
visual change. No layout or copy change to either. Admin-portal-facing only.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/support/_tabs/lost-and-found.tsx` | "Delete lost item?" confirm button → standard destructive pattern | #2816 token migration |
| `src/app/dashboard/rides/_components/ride-complaint-form.tsx` | Documented existing "Submit Complaint" button no-token exception (no color change) | #2816 token migration |

## Before/after snippet
```tsx
// lost-and-found.tsx delete button — before
className="bg-red-600 hover:bg-red-700">Delete</AlertDialogAction>
// after
className="bg-destructive text-destructive-foreground hover:bg-destructive/90">Delete</AlertDialogAction>
```

## Rollback plan
Pure CSS class-string revert — `git revert` this commit restores the prior classes with
no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on both files: 0 errors, 5 pre-existing warnings on
  `lost-and-found.tsx` (all unrelated `react-hooks` warnings); `ride-complaint-form.tsx`
  went from 4 warnings (including 1 raw-color) to 3 (the raw-color warning is now
  suppressed with a documented reason; the remaining 3 are pre-existing unrelated
  `jsx-a11y` warnings).
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
