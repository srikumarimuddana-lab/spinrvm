# Change Impact & Risk Log: document reviewer shortcuts ignore Ctrl/Cmd/Alt and held keys

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code session (claude.ai/code) |
| Surface(s) | admin-dashboard, Drivers → document reviewer (staff) |
| Domain (Sentry tag) | drivers (driver document review, which feeds driver eligibility) |
| PR / commit link | Branch `claude/spinr-animations-admin-ux-x7nl5x` |
| Related issue or gap ID | Found while reviewing UX program W5.1 (keyboard shortcuts sheet) |

## 1. Issue / gap identified

Common browser shortcuts in the driver document reviewer could arm or confirm a review without the admin meaning to.
- **Ctrl/Cmd+A twice** (select all) approved a pending document.
- **Ctrl/Cmd+R** (reload) switched to reject mode instead of reloading.
- **Holding A** approved a pending document, because the key's auto-repeat supplied the "press A again" confirmation.

## 2. Root cause

The reviewer's document-level `keydown` handler (`document-reviewer.tsx`) acts on J, K, A and R.
- It checked the pressed key but never checked Ctrl, Cmd or Alt, so `Ctrl+A` counted as `A`.
- It also didn't check `event.repeat`, so a held key counted as two presses.
- A, then A again, is the designed two-step approve, so both slips reached `submit("approved")`.

## 3. Fix / remediation

The handler now:
- returns early when Ctrl, Cmd or Alt is held;
- ignores auto-repeat for A and R. J and K keep auto-repeat, so holding J still steps through documents.

Plain A-then-A approve and R-then-R reject work exactly as before.

**Alternative considered:** drop the keyboard approve entirely and require a click. Rejected because staff rely on the A/R flow for queue review, and the bug is only in how keys are recognised.

## 4. Risk & impact on existing functionality

- **Blast radius:** this one component's keyboard handler.
  - Three pages render the reviewer: Drivers (`drivers/page.tsx`), the approval queue (`drivers/queue/page.tsx`) and the licence backfill (`driver-license-backfill/page.tsx`). All three get the fix.
  - The existing guards are unchanged: a missing expiry date blocks approval, and rejection needs a reason.
  - The backend review endpoint is unchanged.
- **Behaviour change:** staff who used Ctrl/Cmd/Alt with these letters, or held A/R, no longer trigger a review. Nothing else changes.
- **Visual baselines:** none. The reviewer is a closed dialog on the `dashboard-drivers` baseline, and only its keyboard handling changed.
- **Exposure before this fix:**
  - Documents with an expiry date (licence, insurance, registration and similar) could not be approved this way, because approval refuses a missing expiry date.
  - Documents without one (for example the profile photo) could be.
  - It's unknown whether any document was approved this way; approvals are recorded, but whether a keypress was accidental isn't.

## 5. User-experience effect

- **Internal admins (live-tested):** select-all and reload behave normally inside the reviewer, and holding A or R no longer confirms. The A/R/J/K shortcuts are otherwise unchanged.
- **Mid-session:** applies on the next page load after deploy.
- **Drivers and riders:** no change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/_components/document-reviewer.tsx` | Ignore keys held with Ctrl, Cmd or Alt; ignore auto-repeat for A and R | Stop accidental approve/reject |
| `admin-dashboard/src/app/dashboard/drivers/_components/document-reviewer.test.tsx` | +6 tests | A-then-A still approves; Ctrl/Cmd/Alt+A twice, Ctrl+R, and a held A don't |
| `docs/change-log/2026-09-26-admin-doc-reviewer-single-key-shortcuts.md` | This log | Required for a live-tested surface |

## 7. Before / after

```tsx
// Before
const k = e.key.toLowerCase();
if (k === "j") { ... }
else if (k === "a" && current?.status === "pending") { ... submit("approved") ... }
```

```tsx
// After
if (e.ctrlKey || e.metaKey || e.altKey) return;
const k = e.key.toLowerCase();
if (e.repeat && (k === "a" || k === "r")) return;
if (k === "j") { ... }
```

## 8. Rollback plan

**No flag, and no data or backend change.** Revert the PR; admin redeploys through Vercel. That brings back the accidental-approval path.

## 9. Verification performed

- [x] **New tests:** 6. The 5 negative cases (Ctrl, Cmd and Alt+A twice, Ctrl+R, a held A) fail on the old handler; the A-then-A regression passes on both. The reviewer suite passes: 17 tests.
- [x] **Typecheck:** `tsc --noEmit` passes.
- [x] **Lint:** ESLint on both files has 0 errors. The 1 warning (line 143, `set-state-in-effect`) was already there, and isn't on a changed line.
- [ ] **Full suite and production build:** run on the PR branch before pushing (see the PR).

## 10. What was NOT verified

- **No real browser:** no check on macOS Cmd vs Windows Ctrl, and no screen reader. The tests fire synthetic key events.
- **Past approvals:** no check of whether any past approval came from this path. The audit trail can't tell a keypress from a click.
