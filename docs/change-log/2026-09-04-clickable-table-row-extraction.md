# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code (session 01Sspqro7zzjKdTbUh6D61wQ), design-audit follow-up |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/extract-keyboard-row-shared-component` |
| Related issue or gap ID | `/design Spinr Apps` audit, cross-cutting recommendation #2 |

## 1. Issue / gap identified

PR #4935 (2026-09-04) made 12 admin table rows keyboard-accessible by hand-rolling the same `onClick`/`tabIndex`/`aria-label`/`onKeyDown` boilerplate independently on each page. The design audit's own recommendation was explicit about the risk of that shape of fix:

> "Right now it's implemented correctly exactly once out of 9+ places that need it — the fix is to make correct the path of least resistance, not to ask 8 more pages to copy it by hand."

Leaving 12 independent copies means the next new clickable row is 13 more copy-paste operations away from correct, and any bug in the pattern (see §2) has to be found and fixed in every copy separately.

## 2. Root cause

Structural: no shared component existed for "a `<TableRow>` that's also a keyboard-operable control," so each page reimplemented it from scratch. Auditing all 12 copies while building the shared version surfaced one genuine, live bug that the original PR #4935 sweep missed:

**`safety/page.tsx`'s incident row had no `e.target !== e.currentTarget` bubbling guard on its `onKeyDown`**, unlike the other 11 rows (PR #4935 added that guard everywhere else after `spinr-accessibility-reviewer` caught the same class of bug). This row contains a nested `<button>` (the ride-id link, `onClick={(e) => { e.stopPropagation(); router.push(...) }}`). A keyboard user tabbing to that button and pressing Enter would trigger the browser's native button-click behavior (navigating to the ride) **and** have the keydown bubble up to the row's unguarded handler, which also called `openSelected(it.id)` — a real double-activation, not a hypothetical one. `stopPropagation()` on the button's `onClick` only stops the click from bubbling; it does nothing for the keydown that triggered it.

Six of the 12 migrated rows (`users`, `disputes`, `support/_tabs/{complaints,tickets,flags}`, `quests`, `cloud-messaging`, `venues`) have the same shape of nested interactive control (icon/text buttons with `stopPropagation()`-only click guards) — all were already correctly guarded by PR #4935's fix, so only `safety/page.tsx` had the live gap.

## 3. Fix / remediation

- Added `admin-dashboard/src/components/ui/clickable-table-row.tsx` — a `ClickableTableRow` component wrapping the existing `TableRow` primitive with `onActivate`/`ariaLabel` props, the target-origin keydown guard (always on, so it's a no-op for rows with no nested controls and load-bearing for rows that have them), `tabIndex={0}`, `cursor-pointer`, and a `focus-visible` outline (lifted from `safety/page.tsx`'s already-correct version — most of the other 11 rows had no visible focus ring at all, a secondary WCAG 2.4.7 gap this closes as a side effect). An `active={false}` escape hatch renders a plain, non-interactive `TableRow` for conditionally-clickable rows (`audit-logs/page.tsx`'s "only long rows expand" case).
- Migrated all 12 rows from PR #4935 to use it: `users`, `disputes`, `drivers/appeals`, `support/_tabs/{complaints,tickets,flags}`, `quests`, `cloud-messaging`, `venues`, `drivers` (the original `drivers/page.tsx:1047` gold-standard example), `safety` (bug fix), `audit-logs` (conditional).
- Did **not** change `TableRow`/`ui/table.tsx` itself, and did **not** make row-level keyboard behavior the default for every `TableRow` in the app — most tables in this codebase have non-clickable rows, and defaulting `tabIndex`/keydown handling onto all of them would pollute tab order across the whole dashboard for no reason. `ClickableTableRow` is opt-in per row, which is the correct scope: "make the correct pattern the default" means default *when a row is meant to be clickable*, not unconditionally.

## 4. Risk & impact on existing functionality

- Blast radius: `admin-dashboard/src/components/ui/clickable-table-row.tsx` (new file) plus the 12 files above — each edit is a like-for-like swap (`TableRow` → `ClickableTableRow`, `onClick`/`tabIndex`/`aria-label`/`onKeyDown` → `onActivate`/`ariaLabel`) with no change to the handler logic itself. No other file imports or depends on any of these 12 pages' row markup.
- `TableRow` and `ui/table.tsx` are untouched, so every other table in admin-dashboard (the vast majority, which have no row-click behavior) is unaffected.
- The `safety/page.tsx` fix is a genuine behavior change (removes the double-activation), but it's a bug fix, not a new capability — the row's intended behavior (open the incident on Enter, without also navigating away) is what the other 11 rows already did.
- `focus-visible` outline is new for 11 of the 12 rows (previously only `safety/page.tsx` had it) — additive CSS-only change, no layout shift, only visible on keyboard focus (never for mouse clicks).

## 5. User-experience effect

Admin, keyboard/screen-reader users only. No change for mouse users. Two effects:
1. A latent double-activation bug on the safety/incidents page (keyboard-only) is fixed.
2. 11 rows that previously had no visible focus indicator now show one when tab-navigated to — a genuine accessibility improvement, not just a refactor.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/ui/clickable-table-row.tsx` | New shared component | Single source of truth for the keyboard-row pattern |
| `admin-dashboard/src/app/dashboard/users/page.tsx` | Migrated to `ClickableTableRow` | — |
| `admin-dashboard/src/app/dashboard/disputes/page.tsx` | Migrated | — |
| `admin-dashboard/src/app/dashboard/drivers/appeals/page.tsx` | Migrated | — |
| `admin-dashboard/src/app/dashboard/support/_tabs/complaints.tsx` | Migrated | — |
| `admin-dashboard/src/app/dashboard/support/_tabs/tickets.tsx` | Migrated | — |
| `admin-dashboard/src/app/dashboard/support/_tabs/flags.tsx` | Migrated | — |
| `admin-dashboard/src/app/dashboard/quests/page.tsx` | Migrated | — |
| `admin-dashboard/src/app/dashboard/cloud-messaging/page.tsx` | Migrated | — |
| `admin-dashboard/src/app/dashboard/venues/page.tsx` | Migrated | — |
| `admin-dashboard/src/app/dashboard/drivers/page.tsx` | Migrated the one clickable row (line ~1047); the file's other 10 `onKeyDown` handlers are unrelated sortable-column-header behavior, untouched | — |
| `admin-dashboard/src/app/dashboard/safety/page.tsx` | Migrated **and fixed** the missing bubbling guard | Real double-activation bug (§2) |
| `admin-dashboard/src/app/dashboard/audit-logs/page.tsx` | Migrated using `active={isLong}` for its conditional-clickability case | — |

## 7. Before / after

```tsx
// before (repeated ~verbatim across 12 files)
<TableRow
  key={row.id}
  className="cursor-pointer hover:bg-muted/50"
  onClick={() => setSelected(row)}
  tabIndex={0}
  aria-label={`${row.name}, view details`}
  onKeyDown={(e) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      setSelected(row);
    }
  }}
>

// after
<ClickableTableRow
  key={row.id}
  className="hover:bg-muted/50"
  onActivate={() => setSelected(row)}
  ariaLabel={`${row.name}, view details`}
>
```

```tsx
// safety/page.tsx before — missing guard, real bug
onKeyDown={(e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    openSelected(it.id);
  }
}}

// after — guard is built into ClickableTableRow, applied automatically
```

## 8. Rollback plan

`git revert` — no data touched, no migration, no coordinated deploy. Each of the 4 commits in this branch reverts cleanly (component addition, then 3 migration batches); reverting all 4 restores the exact PR #4935 state, bug included.

## 9. Verification performed

- [x] `npx tsc --noEmit` — clean (exit 0) after every batch of edits, not just at the end.
- [x] **Real production build**: `npm run build` — succeeded, all 12 touched routes (plus every other admin route) built without error. Not just `tsc`/dev server, per CLAUDE.md's explicit requirement.
- [x] Full test suite: `npm run test` (vitest, the actual configured runner — `npx jest` fails in this repo with unrelated ESM config errors, confirmed as a tooling-invocation mismatch, not a real failure) — 59 test files / 562 tests, all passing.
- [x] Read every one of the 12 migrated rows' surrounding markup before editing, specifically checking for nested interactive controls and their existing `stopPropagation()` guards, to confirm `ClickableTableRow`'s target-guard was compatible with (and in `safety/page.tsx`'s case, fixed a gap in) each one.
- [x] Confirmed `drivers/page.tsx`'s other 10 `onKeyDown` handlers (sortable column headers, not rows) were untouched and out of scope.

## 10. What was NOT verified

- No visual-regression tooling exists for the specific pages touched here beyond `dashboard-drivers` (one of admin-dashboard's 5 seeded baselines) — a visual diff on `dashboard-drivers` from this change is possible only if the `focus-visible` styling or `cursor-pointer` placement is visible in a non-focused, non-hovered static screenshot, which it should not be (both are interaction-state-only styles). The other 11 pages (`users`, `disputes`, `venues`, etc.) have no seeded baseline at all — no visual regression coverage exists for them, stated explicitly rather than assumed clean.
- Did not manually drive a screen reader (VoiceOver/NVDA) against the migrated rows — reasoned about via the `aria-label`/`role`/`tabIndex` contract (same contract PR #4935 already shipped and that a real accessibility reviewer pass approved), not screenshotted or recorded.
- Did not audit whether other, non-`TableRow`-based clickable elements elsewhere in admin-dashboard have the same guard gap `safety/page.tsx` had — this fix is scoped to the 12 rows already identified as "clickable TableRow," not a repo-wide keyboard-bug sweep.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data involved)
- [x] Blast radius is stated, not assumed (13 files total, each a like-for-like swap, verified via `tsc`+build+tests after each batch)
- [x] No silent behavior change to an already-shipped flow — the one real behavior change (`safety/page.tsx`'s bug fix) is called out explicitly in §2/§3/§5, not buried in a "just a refactor" framing
