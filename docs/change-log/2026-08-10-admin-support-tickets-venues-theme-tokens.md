# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-10 |
| Author | Claude (agent) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | 2fe83d0 |
| Related issue or gap ID | #2816 |

## 1. Issue / gap identified

`support-tickets/trends/page.tsx`, `support-tickets/tickets/[id]/page.tsx`,
and `venues/page.tsx` each render their load-error state with a bare
`text-red-600` class and no `dark:` variant — the exact same idiom already
fixed in `support-tickets/page.tsx` and `support-tickets/tickets/page.tsx`
in PR #3534, just in sibling files that weren't in that batch's file list.

## 2. Root cause

`{error && <Card><CardContent className="p-4 text-sm text-red-600">{error}</CardContent></Card>}`
is a copy-pasted idiom across the support-tickets feature's pages
(list, tickets list, ticket detail, trends) — one occurrence was fixed as
part of #3534's tickets-page batch, the other two (trends, ticket detail)
were missed because that batch's scope was the never-touched-file survey
at the time, and these two files use `Card`/`CardContent` (a different
literal wrapper than the plain `<div>` that most other error banners use),
which didn't get caught by every prior grep pass. `venues/page.tsx` has
the same color gap in a different wrapper shape (a `<div>`, not a `Card`).

## 3. Fix / remediation

`text-red-600` → `text-destructive` in all three files. No visual change:
Tailwind's `red-600` (#dc2626) is numerically identical to `--destructive`
in both light and dark themes (per `globals.css`).

## 4. Risk & impact on existing functionality

- `className`-only change; no logic, state, or markup structure touched.
  Confirmed via `git diff` review of each hunk (3 files, 3 lines).
- Blast radius: isolated — each of these three lines is a standalone error
  display block in its own page component; none share a component or are
  read by any other file.
- `text-destructive` is the same globally-defined token already read by
  dozens of other files across `admin-dashboard/src/app/dashboard/`; this
  adds no new consumer risk, it's a pure read.

## 5. User-experience effect

- Internal-admin-facing only (support-ticket trends dashboard, ticket
  detail page, venues list).
- No visible change — the color is numerically identical in both themes.
  Purely a token-consistency fix, matching every other file in this
  remediation effort that already made the same swap.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/support-tickets/trends/page.tsx` | `text-red-600` → `text-destructive` (line 121) | Token consistency |
| `admin-dashboard/src/app/dashboard/support-tickets/tickets/[id]/page.tsx` | `text-red-600` → `text-destructive` (line 331) | Token consistency |
| `admin-dashboard/src/app/dashboard/venues/page.tsx` | `text-red-600` → `text-destructive` (line 197) | Token consistency |

## 7. Before / after

```tsx
// Before
{!loading && error && <Card><CardContent className="p-4 text-sm text-red-600">{error}</CardContent></Card>}
<div className="py-12 text-center text-sm text-red-600">{error}</div>

// After
{!loading && error && <Card><CardContent className="p-4 text-sm text-destructive">{error}</CardContent></Card>}
<div className="py-12 text-center text-sm text-destructive">{error}</div>
```

## 8. Rollback plan

`git revert` is sufficient — pure styling diff, no data/state touched, no
migration, no flag.

## 9. Verification performed

- [x] `npx tsc --noEmit` — no new errors in the 3 touched files
- [x] `npx eslint` on the 3 touched files — 0 new warnings (pre-existing
      `react-hooks/set-state-in-effect`, `jsx-a11y`, and
      `react/no-unescaped-entities` warnings, all unrelated to this change)
- [x] `npm run build` — real production build, completed clean, all three
      routes compiled
- [ ] Manual repro in staging — not performed (no staging access this
      session)
- [x] Blast-radius grep performed: confirmed each line is a standalone,
      unshared error display with no other reader

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated: isolated, 3 files, 3 lines
- [x] No silent behavior change — pure color-value-identical token swap

## What was NOT verified

Not screenshotted in either theme — no staging/authenticated admin session
available from this session. The no-op-color claim rests on comparing the
literal hex values (Tailwind `red-600` = `#dc2626` = `--destructive` in
both themes per `globals.css`), not a pixel diff. No visual regression
tooling exists in this repo for admin-dashboard (standing gap, see
`ACTION_ITEMS.md`).
