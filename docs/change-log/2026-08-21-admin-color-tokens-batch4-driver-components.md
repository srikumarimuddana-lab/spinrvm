# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | #2816 Stage 1, Batch 4 — see `docs/change-log/2026-08-21-admin-color-token-migration-plan.md` |

## 1. Issue / gap identified

`driver-action-bar.tsx`, `driver-timeline.tsx`, `driver-stats-cards.tsx`,
`document-reviewer.tsx` (122+94+40+37 raw occurrences per the plan doc)
needed #2816 per-line classification.

## 2. Root cause / findings

- **`driver-stats-cards.tsx`: 0 broken lines.** No changes needed.
- **`driver-action-bar.tsx`: 19 broken lines** — 17 are solid-fill
  white-text confirm buttons (approve=emerald, suspend=orange, ban=red)
  across the driver status-change dialog; same unverified white-on-
  solid-fill contrast-risk class deferred in Batch 1, left untouched. The
  remaining 2 (`text-red-500` on a required-field marker and a validation
  message) are genuinely semantic and safe (text-only, not fill) →
  migrated to `text-destructive`.
- **`driver-timeline.tsx`: 5 broken lines, all one ternary chain** — a
  status-change-log badge (`old_status → new_status`) with **no** `dark:`
  treatment anywhere, unlike the categorical maps excluded in Batches 1-2
  (which already had partial dark-mode support). This one maps cleanly:
  active→success, banned/rejected→destructive, suspended→(and the
  default fallback)→warning — a real 4-state-to-3-token fit, unlike the
  6-7 state hero/status maps excluded elsewhere. Migrated to
  `bg-success/15 text-success`, `bg-destructive/15 text-destructive`,
  `bg-warning/15 text-warning` — same tint+text pairing pattern already
  used in Batch 3 (`bg-success/15`) for an icon container.
- **`document-reviewer.tsx`: 7 broken lines** — 4 are solid-fill white-
  text buttons (same exclusion), 1 is a decorative `Bell`/`BellOff`
  notify-toggle icon (no semantic meaning), and 2 are required-field
  asterisks (`text-red-500` → `text-destructive`, text-only).

## 3. Fix / remediation

5 real fixes total: 4 required-field-marker/validation-message text
colors → `text-destructive` (driver-action-bar.tsx ×2,
document-reviewer.tsx ×2), plus 1 categorical status-badge ternary in
driver-timeline.tsx migrated to the tint+text semantic-token pattern.
21 solid-fill buttons and 1 decorative icon deliberately left untouched.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to 3 files** (driver-stats-cards.tsx
  untouched), 9 lines changed total.
- All 5 fixes are string-literal className changes; no logic/prop/state
  changes.
- The `bg-X/15 text-X` tint pattern reused here was already applied in
  Batch 3 (icon-container backgrounds), not a novel combination.
- Repo-wide lint warning count stayed under the `--max-warnings` ratchet
  (confirmed via `npm run lint`, exit 0).

## 5. User-experience effect

**Internal admin only.** The status-change-log badge in the driver
timeline (`driver-timeline.tsx`) previously showed a 100%-light-mode-only
pastel badge with no dark-mode treatment at all — now correctly reflects
the theme. The two required-field markers/validation messages are
visually near-identical in light mode.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-action-bar.tsx` | 2 real fixes (required marker, validation text) | #2816 Batch 4 |
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-timeline.tsx` | 1 categorical status-badge ternary migrated to semantic tokens | #2816 Batch 4 |
| `admin-dashboard/src/app/dashboard/drivers/_components/document-reviewer.tsx` | 2 real fixes (required markers) | #2816 Batch 4 |

## 7. Before / after

```tsx
// driver-timeline.tsx — before
meta.new_status === "active" ? "bg-emerald-100 text-emerald-700" :
meta.new_status === "banned" ? "bg-red-100 text-red-700" :
meta.new_status === "suspended" ? "bg-orange-100 text-orange-700" :
meta.new_status === "rejected" ? "bg-red-100 text-red-700" :
"bg-amber-100 text-amber-700"

// after
meta.new_status === "active" ? "bg-success/15 text-success" :
meta.new_status === "banned" ? "bg-destructive/15 text-destructive" :
meta.new_status === "suspended" ? "bg-warning/15 text-warning" :
meta.new_status === "rejected" ? "bg-destructive/15 text-destructive" :
"bg-warning/15 text-warning"
```

## 8. Rollback plan

`git-revert-safe` — three files, string-literal className changes only,
no data/API/schema change.

## 9. Verification performed

- [x] Real production build (`npm run build`) — succeeded.
- [x] `npx tsc --noEmit` — clean.
- [x] `npx vitest run` — 339/339 passed.
- [x] `npm run lint` (the exact CI command) — exit 0.
- [x] `npx eslint` on all 4 touched/reviewed files — 0 errors.
- [ ] Not manually click-tested/screenshotted in dark mode — same
  sandbox limitation as every prior UI change-log this session; visual-
  regression baseline still not seeded.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated, not assumed.
- [x] No silent behavior change — one real dark-mode-badge bug fixed with a clean 4-state-to-3-token mapping, everything else left with a stated reason.
