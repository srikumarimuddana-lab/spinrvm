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

Final batch of the systemic outline/ghost-button-without-`dark:` pattern found across the driver (batch 7) and corporate-accounts (batch 8) domains. This batch covers the remaining three corporate-accounts files: member Suspend/Reactivate/Remove buttons, company Suspend/Close buttons, and one KYB Reject button.

## 2. Scope of this batch

Three files, closing out the corporate-accounts portion of this systemic pattern:

- `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/members/page.tsx` — Suspend/Reactivate/Remove member buttons
- `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/page.tsx` — company Suspend/Close buttons
- `admin-dashboard/src/app/dashboard/corporate-accounts/kyb-queue/page.tsx` — Reject button

## 3. Fix / remediation

Non-destructive state-transition buttons (Suspend, Reactivate) got a `dark:` pair preserving their hue, matching the convention from batches 7-8; destructive/terminal actions (Remove, Close, Reject) were mapped to the `text-destructive` semantic token, since it's already available and used throughout this remediation for exactly this category:

- `members/page.tsx`: "Suspend member" `text-orange-700` → `+ dark:text-orange-400`/`hover:text-orange-800` → `+ dark:hover:text-orange-300`; "Reactivate member" `text-emerald-700` → `+ dark:text-emerald-400`/`hover:text-emerald-800` → `+ dark:hover:text-emerald-300`; "Remove member" `text-red-600 hover:text-red-700` → `text-destructive hover:text-destructive/80`
- `[id]/page.tsx`: "Suspend" `text-orange-700` → `+ dark:text-orange-400`; "Close" `text-red-700` → `text-destructive`. The "Reactivate" solid button (`bg-emerald-600 hover:bg-emerald-700`) was left untouched — it's a filled button with implied white text, not the outline/ghost text-color pattern this systemic fix targets, and solid-color buttons render correctly in both themes already.
- `kyb-queue/page.tsx`: "Reject" `text-red-600 hover:text-red-700` → `text-destructive hover:text-destructive/80`

**No logic touched** — `openTransition()`, `handleStatusChange()`, `handleRemove()`, and the KYB assign/reject handlers are all unchanged. Verified via `git diff | grep -viE "className"` returning empty.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** — three page-local button groups, not shared components.
- No prop, state, or handler change; suspend/reactivate/remove/close/reject behavior is unchanged.

## 5. User-experience effect

- Internal-admin facing only (corporate-account member management, company-status controls, KYB review queue). These outline/ghost buttons now render legibly in dark mode. This completes the systemic outline-button `dark:` fix across both the driver and corporate-accounts domains found during the #2816 triage.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/members/page.tsx` | Added `dark:` variants / mapped to `text-destructive` | #2816 remediation |
| `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/page.tsx` | Added `dark:` variant / mapped Close to `text-destructive` | #2816 remediation |
| `admin-dashboard/src/app/dashboard/corporate-accounts/kyb-queue/page.tsx` | Reject button mapped to `text-destructive` | #2816 remediation |

## 7. Before / after

```
# Before
<Button variant="outline" onClick={() => openTransition("close")} className="text-red-700">
```

```
# After
<Button variant="outline" onClick={() => openTransition("close")} className="text-destructive">
```

## 8. Rollback plan

- `git revert` is fully safe — pure `className` changes, no data/config/logic touched.

## 9. Verification performed

- [x] `npm run build` — clean.
- [x] `npm run lint` — 0 new warnings in any of the three files, before or after.
- [x] `git diff | grep -viE "className"` — empty, confirming styling-only.
- [x] `git diff --stat` — 12 lines changed across 3 files.

## What was NOT verified

- Not live-axe-verified in a browser — `text-destructive` is a pre-existing, already-verified semantic token; the `dark:` pairs match the convention already cross-checked in batches 7-8.
- This closes out the systemic outline-button pattern found during triage — no further files with this exact pattern remain from the original 6-agent survey.
