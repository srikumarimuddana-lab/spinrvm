# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude, at user request — "start on tier 2 of the audit findings" (tier 2 of the prioritized 59-page admin-dashboard UI/UX + accessibility audit findings: the systemic Label/htmlFor + icon-button-aria-label gap, scoped by me as "~30 files" and confirmed by a fresh grep at ~34 files) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | 12 commits (batches 1–12) on branch `claude/admin-portal-heatmaps-audit-gm8fbn` |
| Related issue or gap ID | Audit finding, tier 2: unassociated `Label`/form-control pairs and unlabeled icon-only buttons, found across the majority of admin-dashboard's dialog/detail screens during the same pass that produced the safety-queue fix (item #1, already shipped in commit `d1c20d463`) |

## 1. Issue / gap identified

Across ~34 admin-dashboard files, a `<Label>` (from `@/components/ui/label`) sitting next to a real form control (`Input`, `Textarea`, `SelectTrigger`, or `Switch`) had no `htmlFor`, and the control had no matching `id` — so a screen reader announces the control with no accessible name, and clicking the label text does nothing (no implicit or explicit association). Separately, ~10 files had icon-only `<Button size="icon">`/`"icon-sm"` action buttons (edit, delete, view, activate/deactivate, resolve, remove) with no `aria-label`, so a screen reader announces them only as "button" with no indication of what they do.

## 2. Root cause

Same systemic pattern flagged app-wide by the 59-page audit: `Label` has consistently been used as a styled sibling of its control rather than a real associated label, and icon-only `Button`s have inconsistently carried `aria-label` — some screens got it right (already-fixed instances found during the scan), most didn't. No single root cause beyond incremental page-by-page development without an accessibility-pass step baked into the review process.

## 3. Fix / remediation

Dispatched 5 parallel review/fix passes (by file area: support pages, corporate/company-portal, misc dashboard pages, `settings/page.tsx` alone given its size, and small stray files), each scoped to exactly two mechanical, narrow fixes:

- **Label/control association**: added `htmlFor="<kebab-case-id>"` to the `Label` and a matching `id="<same-id>"` to its `Input`/`Textarea`/`SelectTrigger`/`Switch`. IDs are scoped to be unique within each file (verified by grep post-fix — zero duplicates in any touched file, including the ~68-id `settings/page.tsx`).
- **Icon-button `aria-label`**: added a short action description to every icon-only `Button` that lacked one, reusing existing `title` wording where present rather than inventing new phrasing; one dynamic case (promotions' activate/deactivate toggle) gets a value-dependent `aria-label` matching its current state.

**Deliberately left unchanged** (same rule applied consistently across every file, and verified in my own review of each diff):
- A `Label` used as a section/subsection/card heading with no single adjacent control (e.g. titling a whole repeating list, a photo grid, or a group of settings) — forcing an `id` onto a non-existent single control would misrepresent the relationship, not fix it.
- A `Label` next to a plain read-only `<p>`/`<div>`/`<Badge>` display value inside a "view details" dialog — not a form control.
- A `Label` next to a `Switch`, `Tabs`, or custom `MultiSelect` in the files scoped to "Input/Textarea/SelectTrigger only" batches — flagged by the agents as out of that batch's literal scope; `settings/page.tsx`'s own batch *did* include `Switch` since it was scoped separately and has many toggle-style settings.
- Icon-only actions using `size="sm"` (with a visible `title` but no `size="icon"`/`"icon-sm"`) — out of this sweep's literal scope; not claiming these are fully covered elsewhere.
- One `Label` in `settings/page.tsx` captioning an `<audio>` preview element — not one of the four control types in scope.

## 4. Risk & impact on existing functionality

- **Blast radius**: 34 files, each independently self-contained (a page or a dialog component with a single default export/render function) — no shared component was modified, no prop signatures changed, no imports added or removed anywhere in the sweep (grepped: every `Label`/`Input`/`Select`/`Switch`/`Button` used was already imported in every touched file). Every added line in every file is exclusively an `htmlFor=`, `id=`, or `aria-label=` JSX attribute — independently verified via `git diff | grep` across the full batch before committing, not just taken from the fixing agents' self-reports.
- **No id collisions**: every file's new + pre-existing `id`s were grepped for duplicates before commit; none found, including in `settings/page.tsx` which now carries 68 distinct ids across ~30 settings fields.
- **No logic touched**: none of these files had a conditional, handler, or data-fetching path changed — confirmed by reviewing every full diff (not just the fixing agents' reports) before each commit.
- This is admin-dashboard only; no rider-app, driver-app, or backend files are part of this sweep.

## 5. User-experience effect

Admin/staff-facing only. No visible layout or styling change — `htmlFor`/`id`/`aria-label` are non-rendering attributes. The observable effect is exclusively for screen-reader and keyboard users: form controls across ~30 dialogs/pages (support tickets, corporate accounts, company portal, users, vehicle types, promotions, cloud messaging, settings, and more) now announce a real accessible name instead of "edit box, blank" / "combobox, blank"; icon-only action buttons announce what they do instead of just "button". Clicking label text (a standard browser behavior once `htmlFor`/`id` exist) now correctly focuses the associated control — previously a no-op.

## 6. Files modified

34 files across 12 commits. Full list (grouped by commit):

| Batch | Files |
|---|---|
| 1 | `documents/requirements/page.tsx`, `register/driver/page.tsx`, `monitoring/ride-panel.tsx` |
| 2 | `corporate-accounts/[id]/policy/page.tsx`, `corporate-accounts/[id]/members/allowance-dialog.tsx`, `corporate-accounts/[id]/members/page.tsx` |
| 3 | `corporate-accounts/page.tsx`, `company-portal/[id]/policy/page.tsx`, `company-portal/[id]/allowances/page.tsx` |
| 4 | `company-portal/[id]/members/page.tsx`, `company-portal/[id]/settings/page.tsx` |
| 5 | `support-tickets/tickets/[id]/page.tsx`, `support-tickets/tickets/page.tsx`, `support/_tabs/tickets.tsx` |
| 6 | `support/_tabs/lost-and-found.tsx`, `support/_tabs/complaints.tsx`, `support/_tabs/flags.tsx` |
| 7 | `settings/page.tsx` |
| 8 | `ai-console/page.tsx`, `cloud-messaging/page.tsx`, `compliance/page.tsx` |
| 9 | `disputes/page.tsx`, `drivers/appeals/page.tsx`, `earnings/payouts/page.tsx` |
| 10 | `heatmap/page.tsx`, `promotions/page.tsx`, `quests/page.tsx` |
| 11 | `rides/_components/create-ride-modal.tsx`, `staff/page.tsx`, `subscriptions/page.tsx` |
| 12 | `users/page.tsx`, `vehicle-types/page.tsx` |

(All paths relative to `admin-dashboard/src/app/dashboard/` unless under `admin-dashboard/src/app/company-portal/`, `admin-dashboard/src/app/register/`, or `admin-dashboard/src/app/dashboard/rides/_components/`.)

## 7. Before / after

```tsx
// Before — no association; screen reader announces "combobox, blank"
<Label>Status</Label>
<Select value={ticket.status} onValueChange={...}>
    <SelectTrigger><SelectValue /></SelectTrigger>
    ...

// After
<Label htmlFor="ticket-status">Status</Label>
<Select value={ticket.status} onValueChange={...}>
    <SelectTrigger id="ticket-status"><SelectValue /></SelectTrigger>
    ...
```

```tsx
// Before — icon-only, no accessible name
<Button variant="ghost" size="icon" onClick={() => handleOpenDelete(account)}>
    <Trash2 className="h-4 w-4 text-destructive" />
</Button>

// After
<Button variant="ghost" size="icon" onClick={() => handleOpenDelete(account)} aria-label="Delete account">
    <Trash2 className="h-4 w-4 text-destructive" />
</Button>
```

## 8. Rollback plan

Plain `git revert` on any batch commit — no data, no migration, no feature flag, no shared-component change. Each of the 12 commits is independently revertible without affecting the others (they touch disjoint files).

## 9. Verification performed

- [x] Every fixing agent's diff was independently reviewed by me — not just their self-report — before staging: `git diff` read in full for every one of the 34 files.
- [x] Cross-file sanity check: `git diff | grep -viE 'htmlFor=|id="|aria-label='` on every added line across all 14 files in the largest batch returned zero results — confirms no unrelated change slipped in anywhere in that batch (spot-checked equivalently for the other batches via full-diff review).
- [x] `id` uniqueness independently grepped per file (not trusted from agent reports) — zero duplicates in any of the 34 files.
- [x] `tsc --noEmit` — clean (zero output) after every batch, both filtered to the touched files and as a full project-wide run.
- [x] `eslint` — 0 errors across every batch (known pre-existing eslint 10.9.1/eslint-plugin-react workaround: linted with a local unsaved `eslint@9.39.5`, then restored the pinned version, per this session's established pattern). Every warning's line number was cross-checked against the diff's own hunk ranges and confirmed to fall outside them — not just assumed pre-existing.
- [x] Real production build (`npm run build`) — exit code 0, confirmed via full-log grep for "error", run once after all 12 commits landed (not just per-batch).

## What was NOT verified

- **No live browser/screen-reader check.** Same standing gap as this session's other admin-dashboard a11y work — no visual-regression tooling exists for admin-dashboard, and this sandbox cannot run the app live with real assistive tech (VoiceOver/NVDA). The htmlFor/id/aria-label wiring was verified structurally (correct attribute pairing, correct target element per each library's forwarding behavior — e.g. `id` on `SelectTrigger` not `SelectValue`), not by listening to an actual screen reader announce each field.
- **Coverage is not exhaustive of every accessibility gap on these pages** — this sweep targeted exactly two named patterns (Label/htmlFor and icon-button aria-label on `size="icon"`/`"icon-sm"` specifically). Deliberately out of scope and not claimed fixed: icon-only actions using `size="sm"`, `Label`s paired with `Switch`/`Tabs`/custom multi-selects (except in `settings/page.tsx`'s dedicated pass, which did cover `Switch`), and any other accessibility dimension (contrast, focus order, heading hierarchy) not part of this specific finding.
- **Not tested against a real backend** — these are attribute-only JSX changes with no data-flow implications, so this was judged unnecessary; the risk this class of change could pose to actual runtime behavior is effectively zero given the line-by-line diff review performed instead.
