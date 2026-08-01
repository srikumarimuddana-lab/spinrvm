# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this PR) |
| Related issue or gap ID | #2785 (admin-dashboard visual-refresh epic), Phase 5; #2816 (91-file hardcoded-color backlog) |

## 1. Issue / gap identified

Continuing #2816's backlog after `/dashboard/staff` (PR #2847): `/dashboard/forecast`'s hour-by-hour breakdown cards used a hardcoded `bg-gray-50 border-gray-200` card background with `text-gray-700` value text for non-peak hours — neither respects the dark theme (the app's default). In dark mode this rendered as a light-gray card with dark text floating on the near-black page background, inconsistent with the surrounding `Card`/`text-muted-foreground` elements in the same component that already use the theme system correctly.

## 2. Root cause

Same root cause class as #2816's other findings: written before/without the Phase 0 (#2786) semantic-token system, never migrated.

## 3. A broader investigation this round, not just this one fix

Given #2816's own caution ("unknown how many of the 91 files actually have a real contrast problem... needs its own investigation pass, not a blind find-replace"), I surveyed the remaining backlog files rather than mechanically substituting tokens everywhere a `text-gray-*`/`bg-red-*`-style class appears. That survey found the grep pattern used to build the original 91-file list produces a lot of **false positives** — worth documenting here since it changes the real remaining scope:

- **Standalone, theme-independent pages**: `/track/[rideId]` (the public, unauthenticated ride-tracking share link) uses `text-gray-900`/`bg-white`/etc. throughout, but has zero CSS-custom-property usage and zero `dark:` variants anywhere — it's a deliberately separate, fixed-light design (like an Uber tracking link), not participating in the admin dashboard's theme toggle at all. Porting it to semantic tokens would actually **introduce** a bug (if the root `<html>` carries the `.dark` class, `text-foreground` would resolve to a light color while `bg-gray-50` stays hardcoded-light, or vice versa, depending on which classes get migrated).
- **Overlay UI on top of arbitrary image/photo content**: e.g. `drivers/page.tsx`'s document-preview "zoom" icon and "open original" button — both are `bg-white/90`-style translucent pills rendered *over* an arbitrary photo/document image, deliberately theme-independent by design (a white button needs to stay legible regardless of what photo it's floating over).
- **Self-contained badges/pills**: e.g. `quests/page.tsx`, `disputes/page.tsx`, `analytics/page.tsx`'s status badges (`bg-gray-100 text-gray-500` for "Expired"/"Paused"/offline states) — a light pastel background paired with dark text is internally well-contrasted regardless of the surrounding page's theme, unlike plain body text that inherits the page's actual (theme-toggled) background. These match the same "hardcoded but fine" category #2816 explicitly flagged as needing per-pair verification, not a blind swap — and on inspection, most examined so far are fine as-is.
- **Files that already handle dark mode, just via Tailwind's own `dark:` variant instead of the app's semantic tokens**: e.g. `ride-detail-modal.tsx`, `driver-timeline.tsx` (`"bg-gray-100 dark:bg-gray-800/30"`). Not broken — just a different, valid mechanism, lower priority than genuinely-unhandled cases.

The one file that clearly **does** have the same severe, unhandled bug class as the original `/dashboard/staff` fix is `/dashboard/service-areas` — but at 2,101 lines and 203 raw occurrences (by far the largest in the backlog), it needs its own dedicated multi-PR effort rather than a rushed pass; flagging it explicitly as the next real target rather than attempting it here.

## 4. Fix / remediation (this PR)

- `bg-gray-50 border-gray-200` → `bg-muted border-border` (the non-peak-hour card background)
- `text-gray-700` → `text-foreground` (the non-peak-hour value text, matching the sibling peak-hour `text-amber-600` treatment)
- Left `bg-amber-50 border-amber-200`/`text-amber-600` (the peak-hour highlight) untouched — a deliberate colored semantic indicator, not a neutral-gray bug.

## 5. Risk & impact on existing functionality

- **Blast radius: isolated** — one `className` expression in one component, no shared component touched.
- No data flow, prop, or interaction change — grepped the file, confirmed no other code references these specific class strings.

## 6. User-experience effect

- Internal-admin facing only (`/dashboard/forecast`). Non-peak hour cells in the hour-by-hour breakdown now render with the app's actual card/foreground styling instead of a hardcoded light-gray box, visible in dark mode (the app's default).

## 7. Before / after

```
# Before
className={`text-center p-2 rounded-lg border ${
    f.is_peak ? "bg-amber-50 border-amber-200" : "bg-gray-50 border-gray-200"
}`}
...
<p className={`text-sm font-bold mt-1 ${f.is_peak ? "text-amber-600" : "text-gray-700"}`}>
```

```
# After
className={`text-center p-2 rounded-lg border ${
    f.is_peak ? "bg-amber-50 border-amber-200" : "bg-muted border-border"
}`}
...
<p className={`text-sm font-bold mt-1 ${f.is_peak ? "text-amber-600" : "text-foreground"}`}>
```

## 8. Rollback plan

- `git revert` is fully safe — pure `className` change, no data/config touched.

## 9. Verification performed

- [x] `npm run build` — clean, all routes compile.
- [x] `npm run lint` — 0 new warnings from the changed file (the two pre-existing warnings on this file — `fetchData`/`areas` exhaustive-deps and setState-in-effect — predate this diff and are on unrelated lines).
- [x] Read-through investigation of ~15 other backlog files before touching any of them, to distinguish genuine bugs from the false-positive categories in section 3 — avoided applying an unverified blind substitution.

## What was NOT verified

- Not live-axe-verified in a browser like `/dashboard/staff` was — this is a single, small, mechanical class swap reusing tokens already proven safe in that exact "card + value text" context (`bg-muted`/`text-foreground`), not a new pairing, so the incremental risk is low enough that a full live-browser check wasn't run for this one line. If a future audit wants to double check, the pairing is identical to `staff/page.tsx`'s already-live-verified card treatment.
- `/dashboard/service-areas`'s 203 occurrences were surveyed but not fixed — explicitly deferred, not silently dropped (see section 3).
- The false-positive categories in section 3 were spot-checked on a sample of files (~10), not exhaustively re-verified against every one of the remaining ~85 backlog files — a future pass should still confirm each file individually rather than assuming the categories generalize perfectly.
