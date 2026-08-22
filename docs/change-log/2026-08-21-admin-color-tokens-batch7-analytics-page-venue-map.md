# Change Impact & Risk Log — #2816 Batch 7 sub-batch 30: analytics page, venue map

## Issue/gap identified
`dashboard/analytics/page.tsx` and `components/venue-map.tsx` still used raw Tailwind color
utilities for single-signal success/warning/destructive indicators instead of the semantic tokens.

## Root cause
Pre-dates the token migration; written against literal Tailwind palette classes.

## Fix/remediation
- `dashboard/analytics/page.tsx`: a backend fetch-error message → `text-destructive`; the
  "Analytics data unavailable" banner + its outline-style Retry button → `border-destructive
  bg-destructive/10 text-destructive`; the Completion Rate / Cancellation Rate / Avg Completion
  Rate / Low-Completion-Count headline numbers → `text-success`/`text-destructive` (their sibling
  icons were already on tokens from an earlier pass — only the number text itself was raw); the
  "Low performers only" filter badge → `text-destructive border-destructive/40`; the driver-scan
  truncation notice → `--warning` tokens; the low-performer row highlight → `bg-destructive/10`;
  the per-driver Online/Offline badge → `bg-success/15 text-success` / `bg-muted
  text-muted-foreground`; the driver-list load-error text → `text-destructive`. Left the "Revenue"
  stat's amber `DollarSign` icon untouched — an arbitrary decorative accent (revenue isn't itself a
  warning state) alongside two genuinely-signaled sibling stats — and the inline hex-color
  completion-rate progress-bar segments untouched (raw `style={{ backgroundColor }}` values, not
  Tailwind classes — out of scope for this migration).
- `components/venue-map.tsx`: the "No centre set yet" placement prompt (amber) → `text-warning`.

Verified, no change needed: `lib/utils.ts` — its ride/ticket status-color function was already
fully documented with a block `eslint-disable`/`eslint-enable` comment and an extensive
contrast-verification note (10 distinct categorical states, no clean 3-token fit) in an earlier
pass; no edits made.

## Risk & impact on existing functionality
Color-only class swaps; no logic, props, or data flow changed.
- All converted elements in `analytics/page.tsx` are local to that page's own stat cards, table
  rows, and badges — no shared component involved.
- `venue-map.tsx`'s placement prompt is local to that component.

## User experience effect
Both files are internal-admin-only screens (fleet analytics dashboard, venue pickup-point editor
map). Purely cosmetic — the underlying completion/cancellation-rate calculation, low-performer
threshold logic, driver online-status detection, and venue-center validation are unchanged.

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/analytics/page.tsx` | Fetch-error banner/text, 4 headline stat numbers, low-performer badge/row-highlight, online/offline badge → success/warning/destructive tokens | #2816 |
| `admin-dashboard/src/components/venue-map.tsx` | "No centre set" prompt → warning token | #2816 |

## Before/after snippet
```tsx
// analytics/page.tsx — before
<Badge className={d.is_online
  ? "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-200"
  : "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400"}>
// after
<Badge className={d.is_online
  ? "bg-success/15 text-success"
  : "bg-muted text-muted-foreground"}>
```

## Rollback plan
Pure CSS class revert — `git revert` this commit; no data migration, flag, or config change.

## Verification performed
- `npx eslint` on both changed files: 0 errors, 5 warnings (pre-existing unrelated advisories
  only).
- `npx tsc --noEmit`: clean.
- `npx vitest run`: 35 files / 339 tests passed.
- `npm run build`: **production build completed successfully**.

## What was NOT verified
No visual-regression tooling exists in this repo (standing gap). Colors were reasoned about
against the existing token definitions, not screenshotted. Not tested against a live
Supabase-backed admin session.
