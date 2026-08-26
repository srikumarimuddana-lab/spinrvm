# Change Impact & Risk Log — #2816 Batch 7 sub-batch 27: driver distance logs, expiring docs, corporate member status, incident evidence photos

## Issue/gap identified
Four more admin-dashboard files still used raw Tailwind color utilities for status/severity maps
and single-signal indicators instead of the semantic tokens.

## Root cause
Pre-dates the token migration; written against literal Tailwind palette classes.

## Fix/remediation
- `drivers/_components/driver-distance.tsx`: `PHASE_TINT` (insurance periods 1/2/3 — available /
  en route / passenger aboard) → `bg-muted text-muted-foreground` / `bg-warning/15 text-warning` /
  `bg-success/15 text-success`, mirroring the identical P1/P2/P3 neutral/warn/good treatment
  already used in `components/analytics/supply-panel.tsx`.
- `drivers/expiring/page.tsx`: the document-expiry urgency ladder (a genuine 3-tier fit: <7 days /
  <14 days / further out) → `bg-destructive/15 text-destructive border-destructive/30` /
  `bg-warning/15 text-warning border-warning/30` / `bg-success/15 text-success border-success/30`;
  the matching "urgent"/"within 14d" header-count icons → `text-destructive`/`text-warning`.
- `dashboard/corporate-accounts/[id]/members/page.tsx`: `STATUS_COLORS` (invited/active/suspended/
  removed) → warning/success/destructive/muted tokens; `REQUEST_STATUS_COLORS` (pending/approved/
  auto_approved/denied — `auto_approved` mapped to the same `success` tier as `approved` since both
  are the same approved outcome, only differing in who triggered it) → warning/success/success/
  destructive tokens; the "Invite created" confirmation box → success tokens; the
  "Suspend member"/"Reactivate member" outline-style action buttons →
  `text-destructive`/`text-success` (not solid-fill, so no contrast-risk exclusion applies). Left
  the solid-fill Approve/Reject and pending-count-badge elements untouched (contrast-risk
  exclusion).
- `safety/_components/incident-evidence-photos.tsx`: a single warning-severity "photo unavailable"
  placeholder tile (border/background/icon/label, all amber) → `--warning` tokens throughout.

Verified, no change needed: `dashboard/monitoring/ride-panel.tsx` — its `STATUS_COLORS` (ride
progress badge) was already documented with a block `eslint-disable`/`eslint-enable` comment in an
earlier pass (solid-fill white-text badge, yellow/purple have no token equivalent); no edits made.

## Risk & impact on existing functionality
Color-only class swaps; no logic, props, or data flow changed.
- `PHASE_TINT` is local to `driver-distance.tsx`'s own per-log-row rendering.
- The expiry-urgency function and its header counts are local to `expiring/page.tsx`.
- `STATUS_COLORS`/`REQUEST_STATUS_COLORS` and the action buttons are local to the corporate member
  management page — grepped for a sibling `company-portal` copy of the same status maps (an
  earlier sub-batch already converted that customer-facing mirror's equivalent colors); this
  sub-batch only touches the admin-side file.
- The incident-evidence placeholder tile is local to that component.

## User experience effect
All four files are internal-admin-only screens (driver distance-log audit trail, expiring-document
queue, corporate member management, safety incident evidence review). Purely cosmetic color
change — the underlying insurance-period classification, expiry-urgency thresholds, member/request
status logic, and evidence-availability detection are unchanged.

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-distance.tsx` | `PHASE_TINT` (P1/P2/P3) → muted/warning/success tokens | #2816 |
| `admin-dashboard/src/app/dashboard/drivers/expiring/page.tsx` | Expiry-urgency ladder + header-count icons → destructive/warning/success tokens | #2816 |
| `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/members/page.tsx` | `STATUS_COLORS`/`REQUEST_STATUS_COLORS` + invite-confirmation box + 2 action buttons → tokens | #2816 |
| `admin-dashboard/src/app/dashboard/safety/_components/incident-evidence-photos.tsx` | Photo-unavailable placeholder → warning token | #2816 |

## Before/after snippet
```tsx
// corporate-accounts/[id]/members/page.tsx STATUS_COLORS — before
invited: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300",
active: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
suspended: "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300",
removed: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400",
// after
invited: "bg-warning/15 text-warning",
active: "bg-success/15 text-success",
suspended: "bg-destructive/15 text-destructive",
removed: "bg-muted text-muted-foreground",
```

## Rollback plan
Pure CSS class revert — `git revert` this commit; no data migration, flag, or config change.

## Verification performed
- `npx eslint` on all 4 changed files: 0 errors, 8 warnings (pre-existing unrelated advisories,
  plus the deliberately-left raw-color warnings on the solid-fill buttons and pending-count badge).
- `npx tsc --noEmit`: clean.
- `npx vitest run`: 35 files / 339 tests passed.
- `npm run build`: **production build completed successfully**.

## What was NOT verified
No visual-regression tooling exists in this repo (standing gap). Colors were reasoned about
against the existing token definitions, not screenshotted. Not tested against a live
Supabase-backed admin session.
