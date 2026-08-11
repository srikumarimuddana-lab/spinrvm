# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-10 |
| Author | Claude (agent) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin, corporate |
| PR / commit link | 442213f |
| Related issue or gap ID | #2816 |

## 1. Issue / gap identified

Three more files with bare accent-color text and no `dark:` pairing,
found by re-running the survey with a pattern that also catches multi-line
JSX (className on one line, text content on the next — which the earlier
single-line grep missed):

- `earnings/payouts/page.tsx`: three spots (`failure_reason` inline span,
  dialog error paragraph, payout-row `failure_reason` paragraph). This
  file was already partially fixed in #3378 (alert banners), but that
  batch's scope didn't include these three.
- `company-portal/[id]/policy/page.tsx`: the `invalidWindow` validation
  message was missed in the #3538 batch that fixed this exact file's
  other two spots — its `<p className="...">` and text sit on separate
  lines, so the single-line grep used at the time didn't catch it.
- `support-tickets/_components/zoho-config-card.tsx`: three "(saved)"
  credential-status labels next to form field labels, bare
  `text-emerald-600`.

## 2. Root cause

Same root cause as every prior batch in this remediation effort — these
spots predate the `--destructive`/`dark:` token migration and were missed
by whichever prior pass's grep pattern didn't happen to match their exact
line shape. The `payouts` and `company-portal/[id]/policy` misses are
specifically a methodology gap (single-line grep vs. multi-line JSX), not
a new category of bug — same fix, same reasoning as every other spot
fixed in #3534/#3538.

## 3. Fix / remediation

- `earnings/payouts/page.tsx`: all three `text-red-500`/`text-red-600` →
  `text-destructive`. Two of the three (`text-red-500`) are real contrast
  fixes (documented 3.76:1 dark-mode failure); the third (`text-red-600`)
  is a no-op color swap.
- `company-portal/[id]/policy/page.tsx`: `text-red-600` → `text-destructive`
  (no-op color swap, same file as the #3538 fix, now fully closed out).
- `zoho-config-card.tsx`: `text-emerald-600` → `text-emerald-600 dark:text-emerald-400`
  (3 occurrences), matching the base-600 emerald pairing already used in
  `referral-analytics.tsx`, `referral-leaderboard.tsx`,
  `driver-stats-cards.tsx`, and others.

## 4. Risk & impact on existing functionality

- `className`-only change across 3 files, 7 lines; no logic, state, or
  markup structure touched. Confirmed via per-hunk review.
- Blast radius: isolated. Each spot is a standalone display element with
  no shared component or cross-file reader.
- `text-destructive` and `emerald-600 dark:emerald-400` are both patterns
  already read by dozens of other files in this app; no new consumer risk.

## 5. User-experience effect

- `earnings/payouts/page.tsx`: internal-admin-facing only (payout list
  and detail dialog). Two spots now meet WCAG AA contrast in dark mode
  (3.76:1 → 4.83:1); the third is a visual no-op.
- `company-portal/[id]/policy/page.tsx`: corporate-admin-facing (company
  self-service portal). Visual no-op — closes out the last unfixed spot
  in this file after #3538.
- `zoho-config-card.tsx`: internal-admin-facing (support-tickets Zoho
  integration settings). The three "(saved)" labels now dim correctly in
  dark mode instead of staying full-saturation.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/earnings/payouts/page.tsx` | `text-red-600`/`text-red-500` → `text-destructive` (3 spots: lines 118, 123, 386) | Contrast fix (2 of 3) + token consistency |
| `admin-dashboard/src/app/company-portal/[id]/policy/page.tsx` | `text-red-600` → `text-destructive` (line 265, `invalidWindow` message) | Token consistency; closes out file fully after #3538 |
| `admin-dashboard/src/app/dashboard/support-tickets/_components/zoho-config-card.tsx` | `text-emerald-600` → `text-emerald-600 dark:text-emerald-400` (3 spots: lines 195, 199, 203) | Dark-mode pairing |

## 7. Before / after

```tsx
// Before
<span className="text-red-600 text-xs max-w-[200px] text-right">{payout.failure_reason}</span>
{error && <p className="text-sm text-red-500">{error}</p>}
<p className="text-xs text-red-500 mt-0.5">{p.failure_reason}</p>

<p className="text-xs text-red-600">Every window end must be after start.</p>

<span className="text-xs text-emerald-600">(saved)</span>

// After
<span className="text-destructive text-xs max-w-[200px] text-right">{payout.failure_reason}</span>
{error && <p className="text-sm text-destructive">{error}</p>}
<p className="text-xs text-destructive mt-0.5">{p.failure_reason}</p>

<p className="text-xs text-destructive">Every window end must be after start.</p>

<span className="text-xs text-emerald-600 dark:text-emerald-400">(saved)</span>
```

## 8. Rollback plan

`git revert` is sufficient — pure styling diff, no data/state touched, no
migration, no flag.

## 9. Verification performed

- [x] `npx tsc --noEmit` — no new errors in the 3 touched files
- [x] `npx eslint` on the 3 touched files — 0 new errors (pre-existing
      warnings only, unrelated to this change)
- [x] `npm run build` — real production build, completed clean; all three
      routes compiled
- [ ] Manual repro in staging — not performed (no staging access this
      session)
- [x] Blast-radius grep performed: confirmed each spot is standalone with
      no shared component or cross-file reader

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated: isolated, 3 files, 7 lines
- [x] No silent behavior change — 2 real contrast fixes documented with
      before/after ratios, rest are visually-identical or dark-mode-only
      color changes, both called out explicitly

## What was NOT verified

Not screenshotted in either theme — no staging/authenticated admin or
corporate-portal session available from this session. No visual
regression tooling exists in this repo for admin-dashboard or the
company-portal surface (standing gap, see `ACTION_ITEMS.md`).
