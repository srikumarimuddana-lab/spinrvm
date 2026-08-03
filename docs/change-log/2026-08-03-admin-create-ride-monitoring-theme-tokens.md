# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-03 |
| Author | Claude Code |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this PR) |
| Related issue or gap ID | #2785 (admin-dashboard visual-refresh epic), Phase 5; #2816 (91-file hardcoded-color backlog) |

## 1. Issue / gap identified

Found via the post-service-areas #2816 triage. `rides/_components/create-ride-modal.tsx` (the admin's manual ride-creation dialog) has a submit-error message using a raw `text-red-500` instead of the semantic `text-destructive` token, plus pickup/dropoff address inputs whose distinguishing blue/red borders have no `dark:` counterpart. `monitoring/ride-panel.tsx` (the live-ride monitoring side panel) has a "Searching for a driver…" info box and a "Complete Ride" outline button, both with zero `dark:` handling.

## 2. Scope of this batch

Two files:

- `admin-dashboard/src/app/dashboard/rides/_components/create-ride-modal.tsx`
- `admin-dashboard/src/app/dashboard/monitoring/ride-panel.tsx`

## 3. Fix / remediation

- `create-ride-modal.tsx` submit-error text: `text-red-500` → `text-destructive` (a direct semantic-token swap, not a `dark:` addition — this is a validation/error message, the same category mapped to `text-destructive` throughout the `service-areas` batches)
- `create-ride-modal.tsx` pickup/dropoff input borders: `border-blue-200` / `border-red-200` → added `dark:border-blue-800` / `dark:border-red-800`. **Not** converted to semantic tokens — confirmed this is a deliberate blue=pickup / red=dropoff visual convention (matching the pickup/dropoff `MapPin` icon colors, `text-blue-500`/`text-red-500`, a few lines above each input), the same "distinguishing accent color" reasoning as false-positive category 3, just needing dark-mode legibility rather than a token swap. The `focus-visible:ring-*` colors were left as-is — focus rings remain visible in both themes by design.
- `ride-panel.tsx` "Searching for a driver…" box: `border-amber-300`/`bg-amber-50/50`/`text-amber-700`/`text-amber-600/80` → added `dark:border-amber-700`/`dark:bg-amber-900/10`/`dark:text-amber-300`/`dark:text-amber-400/80`
- `ride-panel.tsx` "Complete Ride" outline button: `text-green-600 border-green-600 hover:bg-green-50` → added `dark:text-green-400 dark:border-green-700 dark:hover:bg-green-900/20`

**No logic touched** — form submission, address autocomplete, fare estimation, and the ride-complete handler are all unchanged. Verified via `git diff | grep -viE "className"` returning empty.

**Note on a new precedent set here**: no existing "outline action button with a colored border/text but no `dark:` pairing" fix existed anywhere in the codebase yet when this was written (confirmed via grep before applying) — this is the same systemic pattern flagged across `driver-action-bar.tsx` and the `corporate-accounts` pages during triage (planned as later batches). The convention applied here (`-600 → dark:-400` text, `-600 → dark:-700` border, `hover:bg-{color}-50 → dark:hover:bg-{color}-900/20`) is intended to be the template those later batches reuse, for consistency.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** — both are page/panel-local markup, not shared components.
- No prop, state, or data-flow change.

## 5. User-experience effect

- Internal-admin facing only. The ride-creation dialog's error message now uses the app's standard destructive-error color; pickup/dropoff field borders stay legible in dark mode while keeping their distinguishing blue/red hue. The live-ride monitoring panel's "searching" indicator and "Complete Ride" button now render correctly in dark mode.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/rides/_components/create-ride-modal.tsx` | Error text → `text-destructive`; pickup/dropoff border `dark:` variants | #2816 remediation |
| `admin-dashboard/src/app/dashboard/monitoring/ride-panel.tsx` | Added `dark:` variants to searching-info box and Complete Ride button | #2816 remediation |

## 7. Before / after

```
# Before
className="w-full gap-1.5 text-xs text-green-600 border-green-600 hover:bg-green-50"
```

```
# After
className="w-full gap-1.5 text-xs text-green-600 dark:text-green-400 border-green-600 dark:border-green-700 hover:bg-green-50 dark:hover:bg-green-900/20"
```

## 8. Rollback plan

- `git revert` is fully safe — pure `className` changes, no data/config/logic touched.

## 9. Verification performed

- [x] `npm run build` — clean.
- [x] `npm run lint` — 0 new warnings; all warnings present are pre-existing and on unrelated lines.
- [x] `git diff | grep -viE "className"` — empty, confirming styling-only.
- [x] `git diff --stat` — 12 lines changed across 2 files.
- [x] Confirmed via `grep` before editing that the pickup/dropoff border colors are paired with matching icon colors elsewhere in the same file (deliberate convention, not an error state) before deciding not to convert them to semantic tokens.
- [x] Confirmed via `grep` that no prior "outline action button + `dark:`" precedent existed anywhere in the codebase before choosing the convention applied here.

## What was NOT verified

- Not live-axe-verified in a browser.
- The systemic outline-action-button `dark:` gap in `driver-action-bar.tsx` and the `corporate-accounts` pages is explicitly out of scope for this batch — planned as separate follow-up batches reusing the convention established here.
