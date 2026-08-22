# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 61 (partial pass)

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

This is a **partial pass** on `drivers/page.tsx` (3,500 lines, ~140 raw-color matches
before this sub-batch) — the file is too large to safely convert in one sitting, so
this sub-batch covers the driver-list status badge and the document-compliance
summary/expiry-card sections. The remaining sections (ride-status history, payment
methods, KYC banners, referral badges, doc-review action buttons, and the duplicate
detail-panel status badge) are deferred to future sub-batches, consistent with how
this same file was previously handled in partial passes earlier in this migration
(sub-batch 40).

## Issue/gap identified
- The driver-list row status badge (a 6-state driver-lifecycle ternary: deleted/
  active/needs_review/suspended/banned/pending, plus an online/offline badge) was
  undocumented as an intentional categorical exception.
- The document-compliance summary banner (pending/missing/expired/all-clear counts)
  and the per-document-row status config (approved/pending/expired) used fixed
  amber/red/emerald shades instead of `--warning`/`--destructive`/`--success`.
- The document-expiry card's `styles` palette map (neutral/emerald/amber/red) used
  fixed shades for the same three semantic states plus a neutral default.

## Root cause
Same as prior sub-batches: these sections predate the shared semantic tokens. The
driver-lifecycle badge specifically mirrors an already-established exclusion pattern
(`driver-action-bar.tsx`'s `STATUS_CONFIG`, `driver-stats-cards.tsx`'s stat-tile set)
that had not yet been applied to this file's own copy of the same ternary.

## Fix/remediation
- Driver-list row status ternary (lines ~975-990): wrapped in
  `eslint-disable`/`eslint-enable no-restricted-syntax` — documentation only, no color
  values changed, mirroring the established driver-lifecycle-status exclusion.
- Document-compliance summary banner (pending/missing/expired/all-clear) → `text-warning`
  / `text-destructive` / `text-destructive` / `text-success` — a genuine 3-signal
  compliance status.
- Per-document-row `cfg` (approved/pending/expired) → `text-success`/`text-warning`/
  `text-destructive` icon and text colors — same genuine signal.
- Document-expiry card `styles` map (emerald/amber/red) → `--success`/`--warning`/
  `--destructive` equivalents (background, border, dot, primary, and secondary text) —
  the `neutral` entry was already token-based and untouched.

Left untouched for this sub-batch (deferred, not decided against): the duplicate
detail-panel driver-status badge (~line 1129), ride-status history map (~line 1936),
payout-status maps (~lines 2041, 2729), payment-method verified/pending/failed badges
(~lines 2224-2298), KYC warning/error banners (~lines 2395-2421), subscription active/
expired cards (~lines 1450-1473), referral qualified/pending badges (~lines 2640-2884),
and the doc-review approve/reject action buttons (~lines 3480-3495). These need the
same careful per-section classification as this sub-batch's sections and are picked up
in a following sub-batch rather than rushed here.

## Risk & impact on existing functionality
All edits are within `drivers/page.tsx`, a single large page component — no
shared-component blast radius (this file is not imported elsewhere). No props, state
shape, or exported symbols changed; only class strings inside JSX and object literals.

## User experience effect
Purely color-token substitutions to visually equivalent (already-approved,
contrast-verified) tokens for the document-compliance sections; the driver-lifecycle
badge documentation change is annotation-only with zero visual effect. No layout,
copy, or behavior change. Admin-portal-facing only.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/drivers/page.tsx` | Driver-list status badge documented as a categorical exception; document-compliance banner, per-row status config, and expiry-card palette → `--success`/`--warning`/`--destructive` | #2816 token migration + documentation (partial pass) |

## Before/after snippet
```tsx
// document-compliance summary — before
{missing > 0 && <span className="inline-flex items-center gap-1 text-red-600 dark:text-red-400"><AlertTriangle className="h-3 w-3" />{missing} missing</span>}
// after
{missing > 0 && <span className="inline-flex items-center gap-1 text-destructive"><AlertTriangle className="h-3 w-3" />{missing} missing</span>}
```
```tsx
// expiry-card styles map — before
emerald: { bg: "bg-emerald-50 dark:bg-emerald-900/10 border-emerald-200 dark:border-emerald-800", dot: "bg-emerald-500", primary: "text-emerald-700 dark:text-emerald-300", secondary: "text-emerald-600/70 dark:text-emerald-400/70" },
// after
emerald: { bg: "bg-success/10 border-success/30", dot: "bg-success", primary: "text-success", secondary: "text-success/70" },
```

## Rollback plan
Pure CSS class-string revert (plus removing the `eslint-disable`/`eslint-enable` block
around the driver-list status ternary, which changes no runtime behavior) — `git
revert` this commit restores the prior classes with no data migration, feature flag,
or config involved.

## Verification performed
- `npx eslint` on the file: 0 errors. 124 `no-restricted-syntax` warnings remain —
  all in the sections explicitly deferred above (not touched by this sub-batch), so
  none are regressions; no new warnings on any line this sub-batch edited.
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
- This is explicitly a partial pass — the remaining ~124 warnings in this file (listed
  above under "Left untouched") still need classification and are tracked as
  continuing backlog for this migration, not silently dropped.
