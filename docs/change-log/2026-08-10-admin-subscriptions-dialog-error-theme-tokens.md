# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-10 |
| Author | Claude (agent) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | 4115ee3 |
| Related issue or gap ID | #2816 |

## 1. Issue / gap identified

Two inline form-error paragraphs in `subscriptions/page.tsx` (the create/edit
plan dialogs) used a raw Tailwind `text-red-500` class with no `dark:`
variant, instead of the app's `--destructive` theme token.

## 2. Root cause

`text-red-500` resolves to Tailwind's `#ef4444`. `admin-dashboard/src/app/globals.css`'s
own comment on `--destructive` documents this exact color as "the previous
Tailwind red-500 (#ef4444, 3.76:1) — that was a pre-existing contrast
failure" against the dark theme's near-black backgrounds (WCAG AA requires
4.5:1 for normal text). `--destructive` (#dc2626, 4.83:1) was introduced
specifically to fix this class of bug across the app, but these two spots
in `subscriptions/page.tsx` predated that fix and were never migrated —
found via a re-run of the grep survey from #2816 against files never
touched by any of the six prior remediation PRs (#2847, #3119, #3129,
#3135, #3138, #3378).

## 3. Fix / remediation

Swapped both `text-red-500` occurrences to `text-destructive`, matching the
convention already used for the identical "inline dialog error" pattern in
`create-ride-modal.tsx`, `heatmap/page.tsx`, `faqs/page.tsx`,
`settings/page.tsx`, and `service-areas/page.tsx` (all fixed in earlier
#2816 batches).

## 4. Risk & impact on existing functionality

- `className`-only change; no logic, state, or markup structure touched.
- Grepped `git diff | grep -viE "className"` on the commit — empty, confirming
  no non-styling lines changed.
- `text-destructive` is a globally-defined CSS custom property already used
  by dozens of other files in `admin-dashboard/src/app/dashboard/`; this
  change adds no new consumer risk to that token, it's a pure read.
- Blast radius: isolated to two `<p>` elements inside two Dialog
  components in one file. No other file imports or reads this file's JSX.

## 5. User-experience effect

- Internal-admin-facing only (subscription plan create/edit dialogs).
- Visible immediately on next render if a form validation error is showing
  — not a mid-session change to an already-open dialog's state, just a
  color correction on error text.
- Net effect: this error text now meets WCAG AA contrast in dark mode
  (previously 3.76:1, now 4.83:1); no change in light mode (both colors
  read as a similar red).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/subscriptions/page.tsx` | `text-red-500` → `text-destructive` (2 spots, lines 295 and 451) | Fix documented dark-mode contrast failure |

## 7. Before / after

```tsx
// Before
{error && <p className="text-sm text-red-500">{error}</p>}

// After
{error && <p className="text-sm text-destructive">{error}</p>}
```

## 8. Rollback plan

`git revert` is sufficient — pure styling diff, no data/state touched, no
migration, no flag.

## 9. Verification performed

- [x] `npx tsc --noEmit` — no new errors (pre-existing, unrelated test-tooling
      type errors only, none in the touched file)
- [x] `npx eslint` on the touched file — 0 new warnings (1 pre-existing
      `react-hooks/set-state-in-effect` warning, unrelated to this change)
- [x] `npm run build` — real production build, completed clean, both
      `/dashboard/subscriptions` route compiled
- [ ] Manual repro in staging — not performed (no staging access from this
      session); reasoned from the documented `--destructive` contrast
      values in `globals.css` instead of a live screenshot
- [x] Blast-radius grep performed: confirmed no other file references these
      two lines; confirmed via `git diff --stat` that only this file changed

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated: isolated, single file, two lines
- [x] No silent behavior change — this is a contrast-only color correction,
      not a behavior change; UX effect field filled in above

## What was NOT verified

Not screenshotted in either theme — no staging/authenticated admin session
available from this session. The color-correctness claim rests on the
`--destructive` token's contrast values already being documented and
verified (with citations to specific ratios) in `globals.css`'s own
comments, not on a fresh visual check of this specific page. No visual
regression tooling exists in this repo for admin-dashboard (standing gap,
see `ACTION_ITEMS.md`).
