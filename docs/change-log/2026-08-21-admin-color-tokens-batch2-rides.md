# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | #2816 Stage 1, Batch 2 — see `docs/change-log/2026-08-21-admin-color-token-migration-plan.md` |

## 1. Issue / gap identified

`ride-detail-modal.tsx`, `ride-stats-cards.tsx`, and `ride-ui-helpers.tsx`
(225+52+41 raw color occurrences per the plan doc) needed #2816 per-line
classification.

## 2. Root cause / findings

Per-line classification (not file-level, per the lesson recorded after
Batch 1):

- **`ride-stats-cards.tsx`: 0 broken lines.** Already fully dark-mode-aware
  — no changes needed.
- **`ride-ui-helpers.tsx`: 7 broken lines, all one categorical map**
  (`STATUS_CONFIG`, 6 ride states: completed/cancelled/in_progress/
  searching/driver_assigned/driver_arrived + a default fallback). Same
  class as `lib/utils.ts`'s `statusColor()` — `bg`/`text` fields already
  have their own `dark:` variant; only the `dot` fields (solid 400/500-shade
  fills) lacked one. Not a migration target: a 3-token semantic system
  can't express 6 states, and the dot fields are solid mid-tone fills that
  clear the 3:1 non-text threshold in either theme without a `dark:`
  variant of their own.
- **`ride-detail-modal.tsx`: 22 broken lines**, individually classified:
  - **1 real fix, existing-pattern reuse**: a status badge (`c.status ===
    "open" ? amber : emerald`) was missing the exact `dark:bg-*-900/30
    dark:text-*-400` pairing already used 6+ other times in this same
    file for the identical amber/emerald badge pattern — applied that
    established sibling pattern rather than inventing a new one.
  - **2 real fixes, new tokens**: a `FileWarning` icon (`text-amber-500`
    → `text-warning`) and an `AlertTriangle` icon on a ban-risk flag
    banner (`text-red-500` → `text-destructive`) — both genuinely
    semantic (warning/destructive), icon-only (not text-on-fill, so no
    new contrast math needed per the Batch 1 precedent).
  - **7-state categorical exclusion**: `STATUS_META` (the ride-state hero
    badge — the same 7 lifecycle states from CLAUDE.md's state machine)
    uses gradient fills, self-contained regardless of theme; no 3-token
    system can express 7 states. Suppressed with a documented
    `eslint-disable`/`eslint-enable` block, same convention as
    `statusColor()`.
  - **12 deliberate non-fixes** (decorative icon accents with no semantic
    meaning — Pickup/Dropoff pin labels, rating stars, GPS-section icons
    subordinate to an already-dark-aware block; a fare-breakdown "color
    legend" — promo=violet, discount=green, tip=amber — that's a
    categorical accent system, not a warning/success state; a
    party-identification color code (rider=blue, other=emerald); one
    solid-fill white-text button, same unverified-contrast risk class
    deferred in Batch 1). Left untouched, matching the "hardcoded but
    fine" / "no matching token" categories from prior #2816 batches.

## 3. Fix / remediation

See §2 for the itemized list. 3 real line-level fixes in
`ride-detail-modal.tsx` (1 existing-pattern reuse, 2 new-token
substitutions) plus 2 documented categorical-map exclusions (one in each
of `ride-detail-modal.tsx` and `ride-ui-helpers.tsx`).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to 2 files**, 25 lines changed total (22
  insertions across both, no deletions beyond the lines being edited in
  place).
- `ride-stats-cards.tsx` untouched — 0 changes, 0 risk.
- The two categorical-exclusion comments are documentation-only (no class
  string changed) — `eslint-disable`/`eslint-enable` blocks affect lint
  output, not runtime output.
- The 3 real fixes are all string-literal className changes; no
  logic/prop/state changes.
- Repo-wide lint warning count: 1,751 → 1,694 (net -57, comfortably under
  the `--max-warnings` ratchet from PR #4329 — confirmed via `npm run
  lint`, not assumed).

## 5. User-experience effect

**Internal admin only.** Visual-only. The one real dark-mode bug fixed
(complaint status badge, line 949) previously showed a light-mode-only
pastel badge with no dark-mode treatment; now matches the rest of the
file's own established pattern for identical badges. The two icon-color
changes (warning/destructive) are visually near-identical in light mode.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/rides/_components/ride-detail-modal.tsx` | 3 real fixes (1 existing-pattern reuse, 2 new-token) + 1 categorical-map exclusion comment | #2816 Batch 2 |
| `admin-dashboard/src/app/dashboard/rides/_components/ride-ui-helpers.tsx` | 1 categorical-map exclusion comment (`STATUS_CONFIG`) | #2816 Batch 2 |

## 7. Before / after (representative)

```tsx
// Before
<span className={`... ${c.status === "open" ? "bg-amber-100 text-amber-700" : "bg-emerald-100 text-emerald-700"}`}>{c.status}</span>
<FileWarning className="h-3.5 w-3.5 shrink-0 text-amber-500" />
<AlertTriangle className="h-4 w-4 text-red-500 shrink-0" />

// After
<span className={`... ${c.status === "open" ? "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400" : "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"}`}>{c.status}</span>
<FileWarning className="h-3.5 w-3.5 shrink-0 text-warning" />
<AlertTriangle className="h-4 w-4 text-destructive shrink-0" />
```

## 8. Rollback plan

`git-revert-safe` — two files, string-literal/comment changes only, no
data/API/schema change.

## 9. Verification performed

- [x] Real production build (`npm run build`) — succeeded.
- [x] `npx tsc --noEmit` — clean.
- [x] `npx vitest run` — 339/339 passed.
- [x] `npm run lint` (the exact CI command, not just `npx eslint` on
  isolated files — corrected practice after Batch 1's `--max-warnings`
  miss) — exits 0, 1,694 total warnings (down from 1,751).
- [x] Ran `npx eslint` on all 3 touched/reviewed files directly — 0
  errors, confirmed the two suppression blocks actually silence their
  targeted lines (spot-checked via line-number output).
- [ ] Not manually click-tested/screenshotted in dark mode — same
  sandbox limitation as every prior UI change-log this session; visual-
  regression baseline still not seeded (blocking prerequisite, tracked
  in the migration-plan doc, needs a human to trigger the workflow).

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated, not assumed.
- [x] No silent behavior change — one real dark-mode bug fixed, two icon colors aligned to their actual semantic meaning, everything else deliberately left with a stated reason.
