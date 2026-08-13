# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-10 |
| Author | Claude (agent) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | 2c50adb |
| Related issue or gap ID | #2816 |

## 1. Issue / gap identified

`service-areas/page.tsx` and `support/_tabs/legal-documents.tsx` both
render an "unsaved changes" indicator with a bare `text-amber-600` class
and no `dark:` variant — the identical idiom independently present in two
otherwise-unrelated editor surfaces.

## 2. Root cause

Two separate editor UIs (the service-area document-type list and the
legal-document markdown editor) implement the same "dirty state" indicator
convention, both missing the `dark:` pairing that the rest of the app's
amber-accent usages already carry (e.g. `referral-analytics.tsx` uses
`text-amber-600 dark:text-amber-400` for the same base-600 tone). Neither
file references or imports the other, so this is parallel duplication of
the same gap rather than one root cause propagating through shared code.

## 3. Fix / remediation

`text-amber-600` → `text-amber-600 dark:text-amber-400` in both files,
matching the established base-600 amber pairing used elsewhere in the app.

## 4. Risk & impact on existing functionality

- `className`-only change across 2 files, 2 lines; no logic, state, or
  markup structure touched.
- Blast radius: isolated. Each spot is a standalone `dirty &&` conditional
  indicator local to its own component; no shared component between the
  two files.
- `dark:text-amber-400` is a pattern already used elsewhere in the app
  (`referral-analytics.tsx`, `sidebar.tsx`); no new consumer risk.

## 5. User-experience effect

- Internal-admin-facing only (service-areas document-type editor, legal
  documents editor).
- Visible immediately on next render when either editor has unsaved
  changes in dark mode — the indicator now dims to the established amber
  accent tone instead of staying full-saturation. Not a mid-session change
  to already-rendered state; the indicator only appears when `dirty` is
  true, same trigger condition as before.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | `text-amber-600` → `text-amber-600 dark:text-amber-400` (line 1199) | Dark-mode pairing |
| `admin-dashboard/src/app/dashboard/support/_tabs/legal-documents.tsx` | `text-amber-600` → `text-amber-600 dark:text-amber-400` (line 173) | Dark-mode pairing |

## 7. Before / after

```tsx
// Before
{dirty && <span className="text-xs text-amber-600 font-medium">Unsaved changes</span>}
{dirty && <span className="ml-2 text-amber-600">· unsaved changes</span>}

// After
{dirty && <span className="text-xs text-amber-600 dark:text-amber-400 font-medium">Unsaved changes</span>}
{dirty && <span className="ml-2 text-amber-600 dark:text-amber-400">· unsaved changes</span>}
```

## 8. Rollback plan

`git revert` is sufficient — pure styling diff, no data/state touched, no
migration, no flag.

## 9. Verification performed

- [x] `npx tsc --noEmit` — no new errors in the 2 touched files
- [x] `npx eslint` on the 2 touched files — 0 new errors (pre-existing
      `react-hooks/set-state-in-effect` warnings only, unrelated)
- [x] `npm run build` — real production build, completed clean; both
      routes compiled
- [ ] Manual repro in staging — not performed (no staging access this
      session)
- [x] Blast-radius grep performed: confirmed no shared component between
      the two files despite the identical UI idiom

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated: isolated, 2 files, 2 lines
- [x] No silent behavior change — same trigger condition (`dirty`), only
      the rendered color in dark mode changes, called out explicitly above

## What was NOT verified

Not screenshotted in either theme — no staging/authenticated admin session
available from this session. No visual regression tooling exists in this
repo for admin-dashboard (standing gap, see `ACTION_ITEMS.md`).
