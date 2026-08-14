# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-10 |
| Author | Claude (agent) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | 5b0d6b5 |
| Related issue or gap ID | #2816 |

## 1. Issue / gap identified

Nine form inputs/selects/a textarea in `service-areas/page.tsx` used a
hardcoded `bg-white` class with no `dark:` variant — a more severe bug
shape than the text-color spots fixed in earlier #2816 batches, since
these are interactive controls a user types into, not static text. All
nine sit inside conditionally-rendered "add new X" sub-panels: the "New
Airport Zone" panel (General tab, lines 435/442), the surge-justification
textarea (Pricing tab, line 791), and the "New Incentive" panel
(Incentives tab, lines 2005/2011/2018/2024/2032/2038).

## 2. Root cause

`service-areas/page.tsx` went through four dedicated remediation PRs
(#3119, #3129, #3135, #3138) that ported the file's headings, cards, and
badges to semantic theme tokens. These nine inputs live inside
conditionally-rendered sub-panels (only visible when "Add airport zone" /
"Add incentive" is clicked, or when surge > 2.5x triggers the
justification field) — a per-tab pass that didn't expand every
conditional branch would miss them, which is consistent with what
happened.

## 3. Fix / remediation

Removed `bg-white` from all nine, rather than adding a `dark:bg-X`
pairing. This matches an established, already-shipped convention in the
exact same file: every other `<input>`/`<select>`/`<textarea>` in
`service-areas.tsx` (including in sections the four prior PRs did fix,
e.g. the Area Name/City/Province fields in the General tab a few hundred
lines above the airport-zone panel) uses the identical
`"w-full border rounded-lg px-3 py-2 text-sm"` idiom with no explicit
background class, letting it inherit the themed default. These nine spots
were the only ones in the file that deviated from that pattern.

## 4. Risk & impact on existing functionality

- `className`-only change (removing one utility class); no logic, state,
  validation, or markup structure touched. Confirmed via `git diff`
  review — 9 lines changed, each removing exactly `" bg-white"`.
- Blast radius: isolated to one file, nine form fields inside three
  conditionally-rendered sub-panels. No other file imports or reads this
  file's JSX.
- Verified via `grep -c` that each of the three distinct className
  strings touched (`border-blue-200`, `border-amber-300`,
  `border-amber-200` variants) appeared in the file exactly as many times
  as intended (6 + 1 + 2 respectively, matching the 9 total) before
  applying a scripted replacement — no risk of an unintended match
  elsewhere in this 2,000+ line file.

## 5. User-experience effect

- Internal-admin-facing only (service-areas configuration: airport
  zones, surge justification, incentives).
- These fields previously rendered as solid white boxes against the dark
  theme's page background — a visually jarring, "doesn't respect dark
  mode" bug on a form the admin actively types into, arguably more
  disruptive than a static-text contrast issue. After this fix they
  inherit the same themed background every other input in this file
  already uses. No change in light mode (white input on white-ish page
  background either way).
- Not a mid-session change to already-open forms — the fix applies on
  next render of these (already conditionally-hidden-by-default)
  sub-panels.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | Removed `bg-white` from 9 form inputs/selects/textarea (lines 435, 442, 791, 2005, 2011, 2018, 2024, 2032, 2038) | Match established in-file convention; fix unhandled-dark-mode form controls |

## 7. Before / after

```tsx
// Before
<input className="w-full border border-blue-200 rounded-lg px-3 py-2 text-sm bg-white" ... />
<select className="w-full border border-amber-200 rounded-lg px-3 py-2 text-sm bg-white" ... />

// After
<input className="w-full border border-blue-200 rounded-lg px-3 py-2 text-sm" ... />
<select className="w-full border border-amber-200 rounded-lg px-3 py-2 text-sm" ... />
```

## 8. Rollback plan

`git revert` is sufficient — pure styling diff (one class removed per
line), no data/state touched, no migration, no flag.

## 9. Verification performed

- [x] `npx tsc --noEmit` — no new errors in the touched file
- [x] `npx eslint` on the touched file — 0 new errors (58 pre-existing
      warnings, all unrelated: `react-hooks/set-state-in-effect`,
      `react-hooks/exhaustive-deps`, `jsx-a11y/label-has-associated-control`)
- [x] `npm run build` — real production build, completed clean, the
      `/dashboard/service-areas` route compiled
- [ ] Manual repro in staging — not performed (no staging access this
      session); reasoned from the identical, already-shipped sibling
      inputs in the same file rather than a live screenshot
- [x] Blast-radius grep performed: confirmed exact match counts before
      the scripted replacement, confirmed no other file references these
      lines

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated: isolated, 1 file, 9 lines, no shared
      component
- [x] No silent behavior change — this fixes a visible rendering bug
      (white box on dark page); the UX effect field above states this
      explicitly rather than treating it as a no-op

## What was NOT verified

Not screenshotted in either theme — no staging/authenticated admin
session available from this session. The "matches the file's own
established convention" claim was verified by direct comparison of the
className strings in this same file (the General tab's Area Name/City/
Province fields, already shipped and presumably already confirmed correct
by the four prior service-areas PRs), not by an independent visual check
of these specific nine fields post-fix. No visual regression tooling
exists in this repo for admin-dashboard (standing gap, see
`ACTION_ITEMS.md`).
