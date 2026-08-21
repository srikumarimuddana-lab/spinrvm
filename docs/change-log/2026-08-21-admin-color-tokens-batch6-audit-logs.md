# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | #2816 Stage 1, Batch 6 — see `docs/change-log/2026-08-21-admin-color-token-migration-plan.md`, which flagged this file's `ACTION_CONFIG` as "likely another partial exclusion, to be confirmed" |

## 1. Issue / gap identified

`audit-logs/page.tsx` (98 raw occurrences per the plan doc) needed
#2816 per-line classification. The plan doc's suspicion is confirmed:
`ACTION_CONFIG` is a categorical audit-action-type map, not a migration
target.

## 2. Root cause / findings

- **`ACTION_CONFIG`: 47 broken lines, one categorical map.** 35 distinct
  audit-action types (login, break-glass access, staff CRUD, ride
  lifecycle, wallet credit/debit, exports, 10+ corporate-account events,
  settings changes, DSAR/safety events, payouts, generic CRUD fallbacks)
  across 8 hues (purple/red/orange/emerald/blue/amber/cyan). Same class
  as `lib/utils.ts`'s `statusColor()`, just far larger — a 3-token
  semantic system genuinely cannot express "this was a KYB submission vs.
  a settings update vs. a data export." Each entry's `/15`-tint +
  matching-hue text pairing is theme-invariant by construction (no
  `dark:` variant needed — both halves derive from the same fixed hue at
  a fixed relationship). **Not a migration target** — documented and
  suppressed with `eslint-disable`/`eslint-enable`, same convention as
  `statusColor()`.
- **1 real fix**: the fallback color when an unrecognized action type
  hits the badge (`actionCfg?.color || "bg-zinc-500/15 text-zinc-600"`)
  → this fallback is a genuine "unknown/neutral" case (not itself part
  of the categorical differentiation), migrated to
  `bg-muted text-muted-foreground`.
- **2 deliberate non-fixes**: two decorative header icons (`Shield`,
  `TrendingUp`, both `text-violet-500`) with no semantic meaning —
  matches the decorative-icon-accent exclusion used throughout this
  batch series.

## 3. Fix / remediation

1 real token fix (the fallback), 1 documented categorical exclusion
(`ACTION_CONFIG`, 47 lines), 2 deliberate non-fixes.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one file**, 12 diff lines (1 real
  className change + 2 comment/suppression lines added around
  `ACTION_CONFIG`).
- The suppression comment/`eslint-disable` block is lint-output-only —
  no class string in `ACTION_CONFIG` itself changed.
- Repo-wide lint warning count stayed under the `--max-warnings` ratchet
  (confirmed via `npm run lint`, exit 0).

## 5. User-experience effect

**Internal admin only, no visible change.** The one real fix (unknown-
action fallback badge) is a rarely-hit code path (only fires for an
action type not in `ACTION_CONFIG`'s 35 entries) and is visually a close
neutral-gray match to the token it replaced.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/audit-logs/page.tsx` | 1 real fix (unknown-action fallback → `bg-muted text-muted-foreground`) + documented `ACTION_CONFIG` exclusion | #2816 Batch 6 |

## 7. Before / after

```tsx
// Before
<Badge className={actionCfg?.color || "bg-zinc-500/15 text-zinc-600"}>

// After
<Badge className={actionCfg?.color || "bg-muted text-muted-foreground"}>
```

## 8. Rollback plan

`git-revert-safe` — single file, one className change + documentation
comments, no data/API/schema change.

## 9. Verification performed

- [x] Real production build (`npm run build`) — succeeded.
- [x] `npx tsc --noEmit` — clean.
- [x] `npx vitest run` — 339/339 passed.
- [x] `npm run lint` (the exact CI command) — exit 0, 1,608 total warnings.
- [x] `npx eslint` on the touched file — 0 errors; confirmed the
  remaining 2 warnings are exactly the 2 deliberately-excluded decorative
  icons (line-number-verified), and the `ACTION_CONFIG` suppression
  block actually silences all 47 lines within it.
- [ ] Not manually click-tested/screenshotted — same sandbox limitation
  as every prior UI change-log this session; visual-regression baseline
  still not seeded.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated, not assumed.
- [x] No silent behavior change — the one real fix only affects an already-rare unknown-action fallback path; everything else is documentation or unchanged.
