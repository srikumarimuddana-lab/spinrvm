# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 64 (partial pass, cont.)

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan,
and sub-batches 61-63 for prior partial passes on this same file.

Continues the partial pass on `drivers/page.tsx` — this sub-batch covers the
document-requirement status badges (Missing/pending/Approved/Re-upload needed), the
doc-review dialog's Reject confirm button, and documents `RIDE_STATUS_STYLE` as an
intentional categorical exception. Remaining sections (KYC requirements banners,
referral badges) are still deferred to a following sub-batch.

## Issue/gap identified
- The per-document-requirement status badges (Missing/pending/Approved/"Approved ·
  expiry not recorded"/Re-upload needed — a genuine document-compliance signal) used
  fixed red/amber/emerald shades.
- The doc-review dialog's confirm button used fixed shades for both branches
  (approve/reject).
- `RIDE_STATUS_STYLE` (a 7-state ride-status map) was undocumented as an intentional
  categorical exception, despite being a known third copy of the same ride
  state-machine colors already documented elsewhere (`lib/utils.ts`'s `statusColor()`,
  `ride-ui-helpers.tsx`'s `STATUS_CONFIG`).

## Root cause
Same as prior sub-batches: these sections predate the shared semantic tokens, and
`RIDE_STATUS_STYLE` was never flagged as the established exclusion it actually is.

## Fix/remediation
- Document-requirement badges → `bg-destructive/15 text-destructive` (Missing,
  Re-upload needed), `bg-warning/15 text-warning` (pending, expiry-not-recorded), and
  `bg-success/15 text-success` (Approved) — a genuine multi-state compliance signal.
- Doc-review dialog's **Reject** confirm button → the standard `bg-destructive
  hover:bg-destructive/90 text-destructive-foreground` pattern (a safe conversion,
  matching prior sub-batches' destructive-button work).
- `RIDE_STATUS_STYLE`: wrapped in `eslint-disable`/`eslint-enable
  no-restricted-syntax` — documentation only, no color values changed, since this is
  a third copy of the same ride state-machine display colors already documented as an
  exclusion on the other two implementations.

Left untouched (established contrast-risk exclusion): the doc-review dialog's
**Approve** confirm button (`bg-emerald-600 hover:bg-emerald-700 text-white`) —
converting to `bg-success` would introduce the dark-mode WCAG AA contrast failure the
fixed emerald shade currently avoids, the same reasoning already applied elsewhere in
this migration (sub-batch 55, sub-batch 60).

## Risk & impact on existing functionality
All edits are within `drivers/page.tsx`, not imported elsewhere — no shared-component
blast radius. No props, state shape, or exported symbols changed.

## User experience effect
Purely color-token substitutions to visually equivalent (already-approved,
contrast-verified) tokens for the destructive-signal conversions; the `RIDE_STATUS_STYLE`
documentation change is annotation-only with zero visual effect. No layout, copy, or
behavior change. Admin-portal-facing only.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/drivers/page.tsx` | Document-requirement badges → `--destructive`/`--warning`/`--success`; Reject confirm button → standard destructive pattern; `RIDE_STATUS_STYLE` documented as a categorical exception | #2816 token migration + documentation (partial pass, continued) |

## Before/after snippet
```tsx
// document-requirement badges — before
{matchingDocs.length === 0 && <Badge className="bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 text-[10px]">Missing</Badge>}
{counts.pending > 0 && <Badge className="bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 text-[10px]">{counts.pending} pending</Badge>}
// after
{matchingDocs.length === 0 && <Badge className="bg-destructive/15 text-destructive text-[10px]">Missing</Badge>}
{counts.pending > 0 && <Badge className="bg-warning/15 text-warning text-[10px]">{counts.pending} pending</Badge>}
```

## Rollback plan
Pure CSS class-string revert (plus removing the `eslint-disable`/`eslint-enable`
block, which changes no runtime behavior) — `git revert` this commit restores the
prior classes with no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on the file: 0 errors. Remaining warnings are in sections either
  already fixed in still-open sub-batch 61-63 PRs (which this fresh checkout doesn't
  yet include) or explicitly still deferred (KYC requirements banners, referral
  badges); no new warnings on any line this sub-batch edited.
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
- This remains a partial pass — remaining sections are tracked as continuing backlog,
  not silently dropped. This PR and sub-batches 61-63's PRs touch disjoint lines of
  the same file and should merge cleanly independently.
