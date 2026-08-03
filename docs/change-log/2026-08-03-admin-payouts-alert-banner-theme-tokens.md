# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-03 |
| Author | Claude Code |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | payments (UI-only; no fare/payout logic touched) |
| PR / commit link | (this PR) |
| Related issue or gap ID | #2785 (admin-dashboard visual-refresh epic), Phase 5; #2816 (91-file hardcoded-color backlog) |

## 1. Issue / gap identified

Found via the post-service-areas #2816 triage. `/dashboard/earnings/payouts` (list page) and its `[id]` detail page both have success/error/failure-reason alert boxes that use hardcoded `bg-green-50`/`bg-red-50`/`text-red-700` with zero `dark:` handling — full-width solid-background containers rendered directly on the page, not small badges.

## 2. Scope of this batch

Two files in the driver-payouts admin surface:

- `admin-dashboard/src/app/dashboard/earnings/payouts/page.tsx` — success toast + "N failed payouts" banner
- `admin-dashboard/src/app/dashboard/earnings/payouts/[id]/page.tsx` — "Failure Reason" alert box, plus a smaller text-only failure line inside the retry-confirmation dialog

## 3. Fix / remediation

Applied the `dark:` idiom already established and used repeatedly elsewhere in this codebase (`drivers/page.tsx`, `earnings/page.tsx`, `cloud-messaging/page.tsx`) for exactly this "solid pastel-bg alert box" pattern — `bg-{color}-50 dark:bg-{color}-900/10`, `border-{color}-200 dark:border-{color}-800`, `text-{color}-700/800 dark:text-{color}-300`:

- `payouts/page.tsx` success toast: `bg-green-50 border-green-200 text-green-800` → `+ dark:bg-green-900/10 dark:border-green-800 dark:text-green-300`
- `payouts/page.tsx` failed-payouts banner: `bg-red-50 border-red-200 text-red-800` → `+ dark:bg-red-900/10 dark:border-red-800 dark:text-red-300`
- `payouts/[id]/page.tsx` "Failure Reason" box: `bg-red-50 border-red-200` / `text-red-700` (×2) → `+ dark:bg-red-900/10 dark:border-red-800` / `+ dark:text-red-300`
- `payouts/[id]/page.tsx` retry-dialog failure line: `text-red-600` → `+ dark:text-red-400` (a smaller, text-only instance of the same gap, fixed alongside since it's the same one-line pattern)

**No payout/retry logic touched** — this is a UI-only fix to alert-box styling. `load()`, `retryPayout()`, `failedCount` computation, and all API calls are unchanged. Verified via `git diff | grep -viE "className"` returning empty.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to these two files' presentational markup.** Grepped for other consumers of this exact JSX — none; these are page components, not shared components.
- Domain note: this surface is payments-adjacent (driver payouts), so flagging per CLAUDE.md's money-touching convention even though nothing in the actual payout/settlement/retry code path changed — only the CSS classes on containers that display already-computed state (`toast`, `failedCount`, `payout.failure_reason`).
- No prop, state, or data-flow change.

## 5. User-experience effect

- Internal-admin facing only (`/dashboard/earnings/payouts` and its detail page). The success toast, failed-payout count banner, and failure-reason alert box now render legibly in dark mode instead of as a bright, high-contrast light box against the dark page background. No change to what triggers these alerts or their copy.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/earnings/payouts/page.tsx` | Added `dark:` variants to success/failed alert banners | #2816 remediation |
| `admin-dashboard/src/app/dashboard/earnings/payouts/[id]/page.tsx` | Added `dark:` variants to failure-reason alert box and retry-dialog failure line | #2816 remediation |

## 7. Before / after

```
# Before
<div className="rounded-md bg-red-50 border border-red-200 text-red-800 text-sm px-4 py-3 flex items-center gap-2">
```

```
# After
<div className="rounded-md bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-800 text-red-800 dark:text-red-300 text-sm px-4 py-3 flex items-center gap-2">
```

## 8. Rollback plan

- `git revert` is fully safe — pure `className` changes, no data/config/logic touched, and this is not applied to any historical Stripe/wallet state.

## 9. Verification performed

- [x] `npm run build` — clean.
- [x] `npm run lint` — 0 new warnings; all warnings present are pre-existing and on unrelated lines.
- [x] `git diff | grep -viE "className"` — empty, confirming styling-only.
- [x] `git diff --stat` — 12 lines changed across 2 files.
- [x] Cross-checked the `dark:` idiom against 3 existing usages elsewhere in the codebase before applying, rather than inventing a new pattern.

## What was NOT verified

- Not live-axe-verified in a browser — reuses a `dark:` pairing convention already established and used repeatedly elsewhere in this codebase (`drivers/page.tsx`, `earnings/page.tsx`), not a novel combination.
- Not run against real Supabase dev / real payout data — no backend or data-fetching path touched, so not applicable.
