# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "staff management breaks the portal's own pattern" |

## 1. Issue / gap identified

`dashboard/staff/page.tsx` was the one admin list page not built on the
shared `Table`/`SortableHead`/`Card`/`Button`/`Input` components every
other list page (audit-logs, safety, drivers, users) uses — it rendered
staff as a hand-styled card list with raw `<div>`/`<button>`/`<input>`
elements and Tailwind classes duplicated inline instead of the shared
component library. While auditing it, a real (not just cosmetic) bug
turned up: a failed `getStaff()` load only did `console.error` — no
`toast`, unlike every other list page — so an admin whose staff list
failed to load saw an empty page with zero feedback.

## 2. Root cause

This page predates the shared component library's adoption across the
rest of the portal and was never migrated when the other list pages were.

## 3. Fix / remediation

- Staff list: card-list → `Table` with `SortableHead` (Name/Email/Role/
  Last Login sortable, matching every other list page's convention),
  same row actions (edit, enable/disable, delete, MFA reset) unchanged.
- Add/Edit form: raw `<div>` wrapper → `Card`/`CardContent`; raw
  `<input>`/`<label>` for text fields → `Input`/`Label`; raw Submit/Cancel
  `<button>` → `Button`. Left the role-preset and module-access toggle
  buttons as custom elements — they're a toggle-group pattern, not a form
  field, and don't have a shared-component equivalent elsewhere in the
  portal to converge on.
- `loadStaff`'s failure path now calls `toast(...)` like every other list
  page's load handler, instead of only `console.error`.

## 4. Risk & impact on existing functionality

- **Blast radius: one file, purely presentational + one error-handling
  fix.** No API calls, no state shape, no prop contracts changed —
  `getStaff`/`createStaff`/`updateStaff`/`deleteStaff`/`resetStaffMfa` are
  called identically to before, with the same arguments.
- Grepped every consumer of this page: none — it's a top-level route, not
  imported by any other component.
- All existing behaviors preserved: edit/enable-disable/delete/MFA-reset
  actions, the role-preset → module-checkbox cascade, the show/hide
  password toggle, the empty-state message, the "Access Denied" gate for
  non-super-admins.
- The one behavior CHANGE is the load-failure path: previously silent
  (console only), now shows a toast — strictly additive user-facing
  feedback, matching every sibling list page's existing convention, not a
  new failure mode.

## 5. User-experience effect

**Internal admin-facing only** (requires the `staff` module grant, i.e.
super_admin only per this page's own access gate). Visually, the staff
list now renders as a sortable table instead of stacked cards — same
information, same actions, consistent with the rest of the portal. A
staff-list load failure is now visible to the admin instead of silent.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/staff/page.tsx` | Card-list → Table; raw form elements → shared `Card`/`Input`/`Label`/`Button`; load-failure now toasts | Match the portal's established list-page pattern; close a real silent-failure gap found while doing so |

## 7. Before / after

```tsx
// Before
} catch (e) {
  console.error("Failed to load staff:", e);
}
```

```tsx
// After
} catch (e: any) {
  toast({ title: "Failed to load staff", description: e?.message, variant: "destructive" });
}
```

```tsx
// Before — card list
<div className="space-y-3">
  {staff.map((s) => <div key={s.id} className="bg-card rounded-2xl border p-5 ...">...</div>)}
</div>

// After — Table, matching every other list page
<Table>
  <TableHeader><TableRow>
    <SortableHead column="first_name" sort={sort} onSort={toggle}>Name</SortableHead>
    ...
  </TableRow></TableHeader>
  <TableBody>{sorted.map((s) => <TableRow key={s.id}>...</TableRow>)}</TableBody>
</Table>
```

## 8. Rollback plan

`git revert` the commit. No migration, no data written — purely
presentational plus one toast call.

## 9. Verification performed

- [x] Bracket-balance check on the touched `.tsx` file (no TS/JS
      toolchain run, per this round's instruction) — balanced.
- [x] Traced every prop/argument passed to `getStaff`/`createStaff`/
      `updateStaff`/`deleteStaff`/`resetStaffMfa` before and after — all
      identical; only the surrounding JSX changed.
- [x] Confirmed `TableHead` (renders `<th>`) vs `TableCell` (renders
      `<td>`) usage is correct in the new header row — initially used
      `TableCell` for non-sortable header columns by mistake, caught and
      fixed to `TableHead` before committing (semantically a `<td>` inside
      `<thead>` would have been wrong, and inconsistent with how
      `SortableHead` itself wraps `TableHead`).
- [x] Blast-radius grep performed (see §4): this page has no consumers to
      break.

## 10. Sign-off

- [x] Rollback plan is concrete — `git revert`, no data involved
- [x] Blast radius is stated, not assumed — single top-level route file,
      confirmed no importers
- [x] No silent behavior change to a working flow — every action button's
      handler and every API call is unchanged; the one behavior delta
      (load-failure toast) is stated as the fix's own secondary purpose

## What was NOT verified

Did not run `eslint`/`tsc --noEmit`/`vitest` or a production build — per
this round's explicit instruction, deferred to a single pass at the end.
Did not manually click through the page in a browser — reasoned through
the existing `Table`/`SortableHead`/`Card` usage already proven working
on the audit-logs and safety pages (same components, same import paths,
same call shape) rather than screenshotted; no visual-regression tooling
exists in this repo for this surface (a standing, previously-flagged
gap). Did not add a dedicated component test for this page — none
existed before this change either; only the generic
`pages.smoke.test.tsx` renders it (with a mocked `super_admin` role), and
that test's assertions are render-only (no crash), not DOM-structure
specific, so it doesn't need updating for this change.
