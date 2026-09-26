# Change Impact & Risk Log: document reviewer shortcuts can't approve or reject by accident

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
- **Typing in the reject-reason menu:** the template picker jumps to an item when you type a letter, and the shortcuts saw those letters too. Typing "a" twice while choosing a reason switched to approve and submitted.
- **Quick repeated presses:** a third A while the first request was still in flight sent the review again. The on-screen buttons were disabled while busy, but the keyboard path wasn't. The backend has no "still pending" check, so repeats wrote duplicate audit rows and sent duplicate driver notifications.

## 2. Root cause

The reviewer's document-level `keydown` handler (`document-reviewer.tsx`) acts on J, K, A and R.
- It checked the pressed key but never checked Ctrl, Cmd or Alt, so `Ctrl+A` counted as `A`.
- It also didn't check `event.repeat`, so a held key counted as two presses.
- It skipped only `input`, `textarea` and `select`, not the dropdown's combobox trigger or its options. Radix Select doesn't stop those key events, so they reached the document-level handler.
- `submit()` had no in-flight guard. `busy` is state, so it doesn't update between two quick keypresses.
- A, then A again, is the designed two-step approve, so both slips reached `submit("approved")`.

## 3. Fix / remediation

The handler now:
- returns early when Ctrl, Cmd or Alt is held;
- ignores auto-repeat for A and R. J and K keep auto-repeat, so holding J still steps through documents;
- returns early while focus is on a combobox, listbox, option or editable text, so the reject-reason picker's typing stays in the picker.

`submit()` now ignores a call while a request is in flight. The guard is a ref set synchronously, so it holds between two quick keypresses.

Plain A-then-A approve and R-then-R reject work exactly as before.

**Alternative considered:** drop the keyboard approve entirely and require a click. Rejected because staff rely on the A/R flow for queue review, and the bug is only in how keys are recognised.

## 4. Risk & impact on existing functionality

- **Blast radius:** this one component's keyboard handler.
  - Three pages render the reviewer: Drivers (`drivers/page.tsx`), the approval queue (`drivers/queue/page.tsx`) and the licence backfill (`driver-license-backfill/page.tsx`). All three get the fix.
  - The existing guards are unchanged: a missing expiry date blocks approval, and rejection needs a reason.
  - The backend review endpoint is unchanged.
- **Behaviour change:** staff who used Ctrl/Cmd/Alt with these letters, or held A/R, no longer trigger a review, and typing in the reject-reason picker stays in the picker.
  - Ctrl/Cmd/Alt+J and +K no longer step between documents. Before, any modifier was ignored, so they did. Plain J and K still work. (Ctrl/Cmd+J is the browser's Downloads shortcut, so the old behaviour clashed anyway.)
  - Intentionally unchanged: with focus on a button, for example just after clicking Reject, the A/R shortcuts still work. That's the designed keyboard flow; A still needs a second press to confirm.
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
| `admin-dashboard/src/app/dashboard/drivers/_components/document-reviewer.tsx` | Ignore keys held with Ctrl, Cmd or Alt; ignore auto-repeat for A and R; skip while a dropdown or editable text has focus; in-flight guard in `submit()` | Stop accidental and duplicate approve/reject |
| `admin-dashboard/src/app/dashboard/drivers/_components/document-reviewer.test.tsx` | +10 tests | A-then-A still approves; Ctrl/Cmd/Alt+A twice, Ctrl+R, a held A, letters typed in a dropdown, and a quick third A don't |
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

- [x] **New tests:** 10. All 9 negative cases fail on the old handler:
  - Ctrl, Cmd and Alt+A twice, and Ctrl+R
  - a held A
  - letters typed on an option, combobox or listbox
  - a quick third A while the first is in flight

  The A-then-A regression passes on both. The reviewer suite passes: 21 tests.
- [x] **Edge-case review:** `spinr-edge-case-reviewer` on the first version found the dropdown and double-submit paths. Both are fixed here.
- [x] **Typecheck:** `tsc --noEmit` passes.
- [x] **Lint:** ESLint on both files has 0 errors. The 1 warning (line 143, `set-state-in-effect`) was already there, and isn't on a changed line.
- [ ] **Full suite and production build:** run on the PR branch before pushing (see the PR).

## 10. What was NOT verified

- **No real browser:** no check on macOS Cmd vs Windows Ctrl, and no screen reader. The tests fire synthetic key events.
- **Past approvals:** no check of whether any past approval came from this path. The audit trail can't tell a keypress from a click.
- **Follow-up, backend:** `admin_review_driver_document` (`backend/routes/admin/documents.py`) updates without a `status = 'pending'` condition, so two racing requests from anywhere, such as two tabs, still both apply. A "0 rows updated means already reviewed" guard, like ride acceptance's, belongs in its own backend PR.
