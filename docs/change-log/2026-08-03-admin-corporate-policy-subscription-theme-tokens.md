# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-03 |
| Author | Claude Code |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | corporate |
| PR / commit link | (this PR) |
| Related issue or gap ID | #2785 (admin-dashboard visual-refresh epic), Phase 5; #2816 (91-file hardcoded-color backlog) |

## 1. Issue / gap identified

Found via the post-service-areas #2816 triage — same systemic outline-button/no-`dark:` pattern as batch 7 (driver domain), recurring in the corporate-accounts admin pages. `corporate-accounts/[id]/policy/page.tsx` has a validation-error border/label and a destructive delete icon button using raw `red-500`/`red-600` with no `dark:` handling. `corporate-accounts/[id]/subscription/page.tsx` has an amber notice paragraph and a "Cancel subscription" button using raw `red-700` with no `dark:` handling.

## 2. Scope of this batch

Two corporate-accounts files (first of two planned batches for this domain):

- `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/policy/page.tsx`
- `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/subscription/page.tsx`

## 3. Fix / remediation

Unlike batch 7 (driver domain), these two spots are genuine validation/destructive-action states with the app's `text-destructive`/`border-destructive` semantic tokens already available — mapped directly to those instead of adding raw `dark:` pairs, matching the convention established for validation hints in the original `service-areas` batch 1:

- `policy/page.tsx` work-hours-window row: `border-red-500` (invalid end-time input) → `border-destructive`; `text-red-600` (validation message "End must be after start") → `text-destructive`; `text-red-600` (remove-window ghost button, a destructive action) → `text-destructive hover:text-destructive/80`
- `subscription/page.tsx`: `text-amber-700` (informational "Cancels at period end" notice) → `+ dark:text-amber-400` (a plain informational notice, not a validation/destructive state, so kept the amber hue with a `dark:` pair rather than remapping to a semantic token); `text-red-700` ("Cancel subscription" button) → `text-destructive`

**No logic touched** — the work-hours-window validation (`invalid`), remove-window handler, cancel-at-period-end toggle, and the subscription-cancel handler are all unchanged. Verified via `git diff | grep -viE "className"` returning empty.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** — both are page-local sub-components/markup, not shared components.
- No prop, state, or handler change; the actual cancel/remove/validation behavior is unchanged, only the color classes.

## 5. User-experience effect

- Internal-admin facing only (corporate-account detail pages, Policy and Subscription tabs). The validation border/message, remove-window button, cancellation notice, and "Cancel subscription" button now render correctly in dark mode.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/policy/page.tsx` | Validation/destructive states mapped to `text-destructive`/`border-destructive` | #2816 remediation |
| `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/subscription/page.tsx` | Amber notice got a `dark:` pair; Cancel button mapped to `text-destructive` | #2816 remediation |

## 7. Before / after

```
# Before
className={`w-32 ${invalid ? "border-red-500" : ""}`}
...
<span className="text-xs text-red-600">End must be after start</span>
```

```
# After
className={`w-32 ${invalid ? "border-destructive" : ""}`}
...
<span className="text-xs text-destructive">End must be after start</span>
```

## 8. Rollback plan

- `git revert` is fully safe — pure `className` changes, no data/config/logic touched.

## 9. Verification performed

- [x] `npm run build` — clean.
- [x] `npm run lint` — 0 new warnings in either file, before or after.
- [x] `git diff | grep -viE "className"` — empty, confirming styling-only.
- [x] `git diff --stat` — 10 lines changed across 2 files.

## What was NOT verified

- Not live-axe-verified in a browser — `text-destructive`/`border-destructive` are pre-existing, already-verified semantic tokens.
- The remaining corporate-accounts files with the same pattern (`members`, `[id]` detail, `kyb-queue`) are explicitly out of scope for this batch — planned as the next batch.
