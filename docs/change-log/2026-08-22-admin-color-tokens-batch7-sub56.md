# Change Impact & Risk Log — Admin color-token migration, batch 7 / sub-batch 56

Part of the #2816 hardcoded-Tailwind-color-token migration (Stage 1). See
`docs/change-log/2026-08-21-admin-color-token-migration-plan.md` for the overall plan.

## Issue/gap identified
`sentry-logs/page.tsx` (the Sentry error-triage viewer) had: an undocumented 4-state
categorical surface-identity badge map (`surfaceBadgeClass`), three genuine warning
signals (partial-load banner, "Sentry not configured" panel, per-issue stacktrace-load
failure) using fixed yellow shades, and a genuine all-clear success icon using a fixed
emerald shade.

## Root cause
Same as prior sub-batches: this page predates the shared `--success`/`--warning`/
`--destructive` tokens, and `surfaceBadgeClass` was never flagged as a deliberate
exception with a suppression comment.

## Fix/remediation
- `surfaceBadgeClass` (backend/rider-app/driver-app/admin, 4 states): wrapped in
  `eslint-disable`/`eslint-enable no-restricted-syntax` with a one-line reason — this
  badges which app surface an error came from, not a good/bad signal, so no semantic
  token applies. Documentation only, no color values changed.
- Partial-load warning card (`border-yellow-500/50`, `text-yellow-600 dark:text-yellow-400`)
  and the "Sentry API not configured" panel (same classes) → `border-warning/50`,
  `text-warning` — both are genuine operational warnings on a triage screen.
- The per-issue "Could not load the latest event" stacktrace-failure message →
  `text-warning` — same genuine warning signal.
- The all-clear "No issues in this window" `CheckCircle2` icon (`text-emerald-500`) →
  `text-success` — a genuine success/all-clear signal, not decorative.

## Risk & impact on existing functionality
`sentry-logs/page.tsx` is a standalone super-admin-only page (gated by
`isSuperAdmin`) with no other consumers — all edits are local to this one file. No
props, state shape, or exported symbols changed.

## User experience effect
Purely color-token substitutions to visually equivalent (already-approved,
contrast-verified) tokens. No layout, copy, or behavior change. Admin-portal-facing
only, and only for super-admin staff who use this triage view.

## Files modified
| File | What changed | Why |
|---|---|---|
| `src/app/dashboard/sentry-logs/page.tsx` | `surfaceBadgeClass` documented as a categorical exception; partial-load/not-configured/stacktrace-failure warnings → `--warning`; all-clear icon → `--success` | #2816 token migration + documentation |

## Before/after snippet
```tsx
// before
<Card className="border-yellow-500/50">
  <CardContent className="flex flex-col gap-1 pt-6 text-sm">
    <span className="flex items-center gap-2 font-medium text-yellow-600 dark:text-yellow-400">
// after
<Card className="border-warning/50">
  <CardContent className="flex flex-col gap-1 pt-6 text-sm">
    <span className="flex items-center gap-2 font-medium text-warning">
```
```tsx
// all-clear icon — before / after
<CheckCircle2 className="h-8 w-8 text-emerald-500" />
<CheckCircle2 className="h-8 w-8 text-success" />
```

## Rollback plan
Pure CSS class-string revert (plus removing the `eslint-disable`/`eslint-enable` block
around `surfaceBadgeClass`, which changes no runtime behavior) — `git revert` this
commit restores the prior classes with no data migration, feature flag, or config
involved.

## Verification performed
- `npx eslint` on the edited file: 0 errors, 0 `no-restricted-syntax` warnings (fully
  clean on that rule). Two remaining warnings are unrelated pre-existing
  `react-hooks/set-state-in-effect` warnings already present before this change.
- `npx vitest run`: 339/339 tests passing across all 35 test files.
- `npm run build` (Turbopack) not re-run — the pre-existing, diff-unrelated
  `@spinr/shared` "Unknown module type" Turbopack failure was already root-caused
  against unmodified `origin/main` in sub-batch 31/PR #4371; this sub-batch is plain
  Tailwind class-string edits with no import/module changes.

## What was NOT verified
- No visual regression tooling exists in this repo for the admin dashboard (standing
  gap, `ACTION_ITEMS.md`) — token substitutions were reasoned about against previously
  contrast-verified token values, not screenshotted.
- Not tested against a live Supabase/staging deployment or a real Sentry API
  connection — only against the existing mocked `vitest` fixtures.
