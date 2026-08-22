# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | #2816 Stage 1, Batch 7 (sub-batch 10) — see `docs/change-log/2026-08-21-admin-color-token-migration-plan.md` |

## 1. Issue / gap identified

Continuing the #2816 hardcoded-Tailwind-color-token migration into
`drivers/page.tsx` (9 of 116 total raw matches, the file's remaining
gap) and `service-areas/page.tsx` (8 of 62), plus 3 smaller files:
`drivers/_components/area-stats-table.tsx` (4),
`corporate-accounts/kyb-queue/page.tsx` (4), and `company-portal/[id]/
allowance-requests/page.tsx` (4).

## 2. Root cause / findings

- **`drivers/page.tsx`**: every one of its 9 flagged lines was
  individually checked. A fixed-convention star-rating icon and a
  decorative area-name `MapPin` icon left untouched. Two photo-review
  approve/reject buttons, a save button, and a conditional approve/
  reject button are all solid-fill white-text (contrast-risk
  exclusion). Two lines (`bg-white/95`/`bg-black/70` overlay buttons)
  are lightbox/photo-viewer chrome floating over a full-screen image
  preview with a black backdrop — deliberately theme-independent, the
  same reasoning as the public `/track/[rideId]` page and the standard
  "close" button already using fixed black/white in the same modal. One
  real finding: a solid-fill white-text document-status badge (3-state,
  computed via a ternary chain) — documented with `eslint-disable-
  next-line` (same Batch 1 contrast-risk shape as `ride-panel.tsx`'s
  `STATUS_COLORS`), rather than converted. **Net: 0 token conversions,
  1 documentation comment added** — the file's remaining raw-color
  usage was already either correct-as-is or a genuine exclusion case.
- **`service-areas/page.tsx`**: an empty-state `Car` icon (amber, inside
  an already-`dark:`-aware container whose text siblings already had
  `dark:` pairing) given matching `dark:text-amber-400`. An incentive
  toggle button already used the `text-success` token for its text but
  paired it with a raw `hover:bg-green-50` (no token, no `dark:`) —
  converted to `hover:bg-success/10` for full token consistency. Left
  untouched: 5 solid-fill white-text/primary buttons.
- **`drivers/_components/area-stats-table.tsx`**: an online-driver
  count, a verified-driver count, and a total-earnings figure (each a
  single-column positive-signal number, not a multi-category money
  legend) → `success`; an unverified-driver count → `warning`.
- **`corporate-accounts/kyb-queue/page.tsx`**: an error banner (no
  `dark:` at all) → the `border-destructive/40 bg-destructive/5
  text-destructive` house pattern; a "Preview" link button (blue, no
  `dark:`) given `dark:text-blue-400` pairing matching the established
  link-color convention. Left untouched: 2 solid-fill white-text
  buttons.
- **`company-portal/[id]/allowance-requests/page.tsx`** (corporate-
  customer-facing): `STATUS_COLORS` (pending/approved/auto_approved/
  denied) is the identical map already handled in `corporate-accounts/
  [id]/members/page.tsx`'s `REQUEST_STATUS_COLORS` in Batch 7 sub-batch
  1 — given the exact same house-convention `dark:` pairing treatment
  (was missing entirely, a real dark-mode bug on a page corporate
  customers use directly to review their own allowance requests).

## 3. Fix / remediation

7 real semantic-token fixes, 4 house-convention `dark:` pairing
additions (1 icon + 1 link color + the 4-state `STATUS_COLORS` map), 1
documented suppression (`drivers/page.tsx`'s solid-fill status badge).
`drivers/page.tsx` itself required zero conversions — every flagged
line was confirmed already correct or a genuine exclusion.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to these 5 files.** No shared component,
  hook, or utility was touched. `company-portal/[id]/allowance-
  requests/page.tsx`'s `STATUS_COLORS` is confirmed a separate,
  independently-defined map from its admin-facing counterpart (not
  shared/imported).
- All converted lines are plain text, icons inside already-dark-aware
  containers, or link buttons — none were part of the excluded
  solid-fill white-text button/badge class. The photo-viewer lightbox
  chrome and the one genuine solid-fill status-badge finding in
  `drivers/page.tsx` were correctly identified and left unconverted/
  documented rather than risking a contrast regression or a themed-
  overlay visual bug.
- Repo-wide lint warning count stayed well under the `--max-warnings`
  ratchet (1355 vs. the 1751 ceiling).

## 5. User-experience effect

**Internal admin only**, except `company-portal/[id]/allowance-
requests/page.tsx` which is corporate-customer-facing (a business user
reviewing their own team's allowance requests). That page's status
badges gain dark-mode support for the first time — a real, if minor,
dark-mode bug now fixed. No icon, label, or layout change elsewhere; no
change to which drivers/areas/requests are shown or filtered.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/page.tsx` | 1 documented suppression added (solid-fill status badge); no token conversions needed | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | Empty-state icon given dark: pairing; toggle-button hover converted to token | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/drivers/_components/area-stats-table.tsx` | Online/verified/earnings/unverified counts → success/warning tokens | #2816 Batch 7 |
| `admin-dashboard/src/app/dashboard/corporate-accounts/kyb-queue/page.tsx` | Error banner → destructive house pattern; link color given dark: pairing | #2816 Batch 7 |
| `admin-dashboard/src/app/company-portal/[id]/allowance-requests/page.tsx` | `STATUS_COLORS` (4-state) given dark: pairing, matching admin-facing sibling | #2816 Batch 7 |

## 7. Before / after

```tsx
// drivers/_components/area-stats-table.tsx (before)
<span className="text-emerald-600 font-medium">{area.online}</span>
<span className="text-green-600 font-medium">{area.verified}</span>
<span className="text-amber-600 font-medium">{area.unverified}</span>
<td className="... text-emerald-600 font-medium">{formatCurrency(area.total_earnings)}</td>

// after
<span className="text-success font-medium">{area.online}</span>
<span className="text-success font-medium">{area.verified}</span>
<span className="text-warning font-medium">{area.unverified}</span>
<td className="... text-success font-medium">{formatCurrency(area.total_earnings)}</td>
```

## 8. Rollback plan

`git-revert-safe` — 4 modified files, all `className` string literals
and one documentation comment. No data/API/schema change, no
shared-component change.

## 9. Verification performed

- [x] `npx eslint` on all 5 touched files — 0 errors, 325 warnings (the
  large majority pre-existing residual raw-color usage in the two huge
  files, `drivers/page.tsx` and `service-areas/page.tsx`, most already
  classified as legitimate exclusions in this and prior batches).
- [x] `npx tsc --noEmit` — clean, 0 errors.
- [x] `npx vitest run` — 339/339 passed.
- [x] `npm run lint` (the exact CI command) — exit 0, 1355 total
  warnings (under the 1751 ratchet).
- [x] `npm run build` (real production build) — succeeded.
- [ ] Not manually click-tested/screenshotted — same standing sandbox
  limitation as every prior change-log this session; visual-regression
  baseline still not seeded. Notable: `company-portal/[id]/allowance-
  requests/page.tsx` is corporate-customer-facing.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated per file, not assumed.
- [x] No silent behavior change — every conversion is a color-only
  visual change on an already-semantically-meaningful element; all
  click handlers, filters, and conditional rendering are unchanged.
- [x] `drivers/page.tsx` needing zero conversions was verified line-by-
  line, not assumed — including correctly distinguishing themed
  lightbox/photo-viewer chrome from an actual dark-mode gap.
