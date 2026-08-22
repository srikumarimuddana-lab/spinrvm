# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 62 (partial pass, cont.)

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan,
and `docs/change-log/2026-08-22-admin-color-tokens-batch7-sub61.md` for the prior
partial pass on this same file.

Continues the partial pass on `drivers/page.tsx`. This sub-batch covers the
detail-panel's duplicate driver-status badge, the profile-photo-rejected error text,
and both the compact and card-style Spinr Pass subscription-expired indicators.
Remaining sections (ride-status history, payment-method badges, KYC banners, referral
badges, doc-review action buttons) are still deferred to a following sub-batch.

## Issue/gap identified
- The detail panel's driver-lifecycle status badge (a second copy of the same
  6-state ternary already documented on the list-row badge in sub-batch 61) was
  still undocumented in this location.
- "Profile photo rejected" text used a fixed red shade.
- The Spinr Pass subscription-expired indicator (both the compact badge and the
  larger card variant) used fixed red shades instead of `--destructive`.

## Root cause
Same as prior sub-batches: this section predates the shared semantic tokens, and is
a literal duplicate of the list-row badge already handled — this sub-batch documents
the second copy for consistency.

## Fix/remediation
- Detail-panel driver-status ternary: wrapped in `eslint-disable`/`eslint-enable
  no-restricted-syntax`, referencing the list-row badge's comment — documentation
  only, no color values changed.
- "Profile photo rejected" text → `text-destructive`.
- Subscription-expired compact badge and card variant → `bg-destructive/15
  text-destructive` (badge) and the equivalent `bg-destructive/15`/`text-destructive`/
  `text-destructive/80` set (card) — a genuine expired/error signal.

Left untouched (deliberate, not decided against — branding, not a status signal): the
subscription-**active** state (compact badge and card) keeps its violet
(`bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-400`) styling.
"Spinr Pass" is a named paid product tier, and violet is used as its brand accent
consistently across both the active badge and active card — converting only the
*active* state to `--success` while the rest of the product's branding stays violet
elsewhere would introduce inconsistency without a clear product decision to re-brand
the tier. The **expired** state, by contrast, is a genuine status signal (not a brand
identity) and was converted.

## Risk & impact on existing functionality
All edits are within `drivers/page.tsx`, not imported elsewhere — no shared-component
blast radius. No props, state shape, or exported symbols changed.

## User experience effect
Purely color-token substitutions to visually equivalent (already-approved,
contrast-verified) tokens for the destructive-signal sections; the driver-status
badge documentation change is annotation-only with zero visual effect. No layout,
copy, or behavior change. Admin-portal-facing only.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/drivers/page.tsx` | Detail-panel driver-status badge documented; profile-photo-rejected text and subscription-expired badge/card → `--destructive` | #2816 token migration + documentation (partial pass, continued) |

## Before/after snippet
```tsx
// subscription-expired card — before
<div className="w-9 h-9 rounded-xl bg-red-100 dark:bg-red-900/30 flex items-center justify-center shrink-0">
    <CreditCard className="h-4 w-4 text-red-600 dark:text-red-400" />
</div>
// after
<div className="w-9 h-9 rounded-xl bg-destructive/15 flex items-center justify-center shrink-0">
    <CreditCard className="h-4 w-4 text-destructive" />
</div>
```

## Rollback plan
Pure CSS class-string revert (plus removing the `eslint-disable`/`eslint-enable`
block, which changes no runtime behavior) — `git revert` this commit restores the
prior classes with no data migration, feature flag, or config involved.

## Verification performed
- `npx eslint` on the file: 0 errors. 180 remaining `no-restricted-syntax` warnings
  (up from 124 after sub-batch 61's pass, because this fresh `origin/main` checkout
  does not yet include sub-batch 61's un-merged PR #4402 — its 124 already-addressed
  matches are counted again here as still-raw in this checkout) are all in sections
  either already fixed in the still-open sub-batch 61 PR or explicitly still deferred;
  no new warnings on any line this sub-batch edited.
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
- This remains a partial pass — the sections listed as "still deferred" above are
  tracked as continuing backlog for this migration, not silently dropped. Once
  sub-batch 61's PR merges, this sub-batch's diff and sub-batch 61's diff are on the
  same file but touch disjoint lines, so both should merge cleanly.
