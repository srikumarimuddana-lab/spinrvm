# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-10 |
| Author | Claude (agent) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | 4bd7028 |
| Related issue or gap ID | #2816 |

## 1. Issue / gap identified

Three more raw-Tailwind-color spots found by the same never-touched-file
survey: an error banner in `support-tickets/page.tsx` and
`support-tickets/tickets/page.tsx` using bare `text-red-600` with no
`dark:` variant, and a ticket-number link in the same tickets table using
bare `text-blue-600` with no `dark:` variant.

## 2. Root cause

Unlike the `subscriptions/page.tsx` fix in the companion commit, these
`text-red-600` spots are not a live contrast bug — Tailwind's `red-600`
(#dc2626) is numerically identical to `--destructive` in both themes, so
it already renders correctly. The gap is purely that it isn't using the
shared token, which every other file fixed in this remediation effort
(#2847, #3119-3138, #3378) already does for the same pattern — a
consistency/maintainability gap, not a rendering bug.

The `text-blue-600` link is a real (milder) inconsistency: every other
blue-accent usage across `admin-dashboard/src/app/dashboard/` pairs it with
`dark:text-blue-400` (`driver-stats-cards.tsx`, `ride-list.tsx`,
`ride-stats-cards.tsx`, `ride-detail-modal.tsx`, `promotions/page.tsx`) —
this one link never got that pairing, so it stays full-saturation blue in
dark mode instead of dimming to match the rest of the app's link color.

## 3. Fix / remediation

- `support-tickets/page.tsx:124` and `support-tickets/tickets/page.tsx:321`:
  `text-red-600` → `text-destructive` (token consistency, no visual change).
- `support-tickets/tickets/page.tsx:347`: `text-blue-600` →
  `text-blue-600 dark:text-blue-400` (matches established app-wide pairing).
- `disputes/page.tsx:338`: `text-red-600` → `text-destructive` (same
  consistency fix, third file with the identical pattern).

## 4. Risk & impact on existing functionality

- `className`-only change across 3 files; no logic, state, or markup
  structure touched. Verified via `git diff --stat` (4 insertions/4
  deletions across 3 files) and manual review of each hunk.
- Blast radius: isolated. Each spot is a single, unshared JSX element —
  none of these three files import from each other or share a component
  that renders this text; no other file reads these lines.
- The `text-destructive` swaps are a no-op color-wise (#dc2626 == #dc2626
  in both themes) — zero visual risk.
- The `text-blue-600 dark:text-blue-400` change is the only line with any
  visual delta: the ticket-number link will render dimmer blue in dark
  mode. This is a one-way brightness change on a single link, matching an
  established, already-shipped pattern used in 5+ other files — same
  category of change already reviewed and merged in the prior #2816
  batches.

## 5. User-experience effect

- Internal-admin-facing only (support-tickets list/detail pages, disputes
  resolution dialog).
- `text-destructive` swaps: no visible change (identical color values).
- `text-blue-600 dark:text-blue-400` swap: visible immediately on next
  render of the tickets table in dark mode — the ticket-number link column
  dims slightly to match the app's other link-colored elements. Not a
  mid-session change to an open dialog/table row, just a static color on
  the next paint.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/support-tickets/page.tsx` | `text-red-600` → `text-destructive` (line 124) | Token consistency |
| `admin-dashboard/src/app/dashboard/support-tickets/tickets/page.tsx` | `text-red-600` → `text-destructive` (line 321); `text-blue-600` → `text-blue-600 dark:text-blue-400` (line 347) | Token consistency + dark-mode link-color parity |
| `admin-dashboard/src/app/dashboard/disputes/page.tsx` | `text-red-600` → `text-destructive` (line 338) | Token consistency |

## 7. Before / after

```tsx
// Before
<Card><CardContent className="p-4 text-sm text-red-600">{error}</CardContent></Card>
<Link ... className="font-mono text-sm text-blue-600">{t.ticketNumber}</Link>
<p className="text-sm text-red-600">{resolveError}</p>

// After
<Card><CardContent className="p-4 text-sm text-destructive">{error}</CardContent></Card>
<Link ... className="font-mono text-sm text-blue-600 dark:text-blue-400">{t.ticketNumber}</Link>
<p className="text-sm text-destructive">{resolveError}</p>
```

## 8. Rollback plan

`git revert` is sufficient — pure styling diff, no data/state touched, no
migration, no flag.

## 9. Verification performed

- [x] `npx tsc --noEmit` — no new errors (pre-existing, unrelated test-tooling
      type errors only, none in the 3 touched files)
- [x] `npx eslint` on the 3 touched files — 0 new warnings (pre-existing
      `react-hooks/set-state-in-effect` warning in `support-tickets/page.tsx`,
      unrelated to this change)
- [x] `npm run build` — real production build, completed clean; all three
      routes (`/dashboard/support-tickets`, `/dashboard/support-tickets/tickets`,
      `/dashboard/disputes`) compiled
- [ ] Manual repro in staging — not performed (no staging access from this
      session)
- [x] Blast-radius grep performed: confirmed each of the 4 changed lines is
      a single, unshared JSX element with no other reader

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated: isolated, 3 files, 4 lines
- [x] No silent behavior change — 3 of 4 spots are visually identical
      (token swap only); the 1 spot with a visible delta (blue link dimming
      in dark mode) is called out explicitly in the UX field above

## What was NOT verified

Not screenshotted in either theme — no staging/authenticated admin session
available from this session. The `text-destructive` no-op claim rests on
comparing the literal hex values (Tailwind `red-600` = `#dc2626`, and
`--destructive` = `#dc2626` in both themes per `globals.css`), not a pixel
diff. The `text-blue-600 dark:text-blue-400` change was reasoned by pattern-
matching against 5 other already-shipped files using the identical pairing,
not independently contrast-checked against this specific table's actual
background token at render time. No visual regression tooling exists in
this repo for admin-dashboard (standing gap, see `ACTION_ITEMS.md`).
