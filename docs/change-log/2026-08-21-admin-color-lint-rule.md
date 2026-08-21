# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | #2816 (113-file hardcoded-color backlog) — Stage 1 of a design-system enforcement plan, see `docs/change-log/2026-08-21-admin-color-token-migration-plan.md` |

## 1. Issue / gap identified

#2816's hardcoded-Tailwind-color backlog has grown, not shrunk, since it
was first flagged — every incremental migration batch (staff, forecast,
service-areas) fixed existing files, but nothing stopped *new* files from
being added to the backlog. There was no enforcement mechanism, only a
tracked-but-unenforced convention.

## 2. Root cause

No lint rule or CI gate ever existed for raw color-utility usage —
consistency depended entirely on individual PR authors knowing about
#2816 and choosing to use the semantic tokens in `globals.css` instead of
Tailwind's default palette.

## 3. Fix / remediation

Added a `no-restricted-syntax` ESLint rule (flat config, `eslint.config.mjs`)
matching the same Tailwind-color-utility pattern as the #2816 backlog grep
(`bg-red-500`, `text-gray-900`, etc.) against JSX/string literals. **`warn`,
not `error`** — same gradual-migration convention already used for
`@typescript-eslint/no-explicit-any` and the React-hooks rules in this same
file (downgrade legacy violations to visible-but-non-blocking, flip to
`error` once the backlog is actually migrated).

Also added the one confirmed intentional exception found while scoping
this: `lib/utils.ts`'s `statusColor()` is a categorical status-color map
(10 ride/ticket states), already contrast-verified per-shade in both
themes, with no semantic-token equivalent (the token system is a 3-state
warning/success/destructive scheme, not a 10-state one). Suppressed with
an `eslint-disable`/`eslint-enable` block and a one-line reason, matching
the suppression convention this rule's own error message documents.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to lint configuration + one suppression
  comment.** No component logic, styling, or runtime behavior changed —
  `statusColor()`'s actual returned class strings are byte-identical to
  before.
- **`warn`-level means this cannot break CI on any existing PR.** Verified
  directly: ran `npx eslint` against the two largest offenders
  (`lib/utils.ts` pre-suppression, `drivers/page.tsx`) — 184 warnings, 0
  errors.
- **Every other admin-dashboard PR in flight or merged today is
  unaffected** — this only adds visibility (a warning in `eslint`/CI
  output), not a new blocking gate. The eventual `error` flip (out of
  scope for this PR) is what actually changes CI behavior, and only after
  the migration batches land.
- This rule does not touch `.claude/hooks/pre-commit`'s grep-based checks
  — `.husky/pre-commit` already runs `eslint` unconditionally on every
  staged TS file (not IDE-dependent), so no second enforcement layer is
  needed; adding a duplicate grep check there would be redundant.

## 5. User-experience effect

**None.** This is a lint-time-only change — no runtime code path is
different, no page renders differently, no admin/rider/driver-facing
behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/eslint.config.mjs` | Added `no-restricted-syntax` (warn) flagging raw Tailwind color utilities | Stop the #2816 backlog from growing while it's migrated |
| `admin-dashboard/src/lib/utils.ts` | Added `eslint-disable`/`eslint-enable` block + comment around `statusColor()`'s map | Documented, intentional exception — categorical status coloring, not a token-migration target |
| `docs/change-log/2026-08-21-admin-color-token-migration-plan.md` | New — Stage 1 batching plan | Tracks the actual migration work this rule is a prerequisite for |

## 7. Before / after

```js
// eslint.config.mjs — after (new block, nothing removed)
{
  rules: {
    "no-restricted-syntax": [
      "warn",
      {
        selector: "Literal[value=/\\b(bg|text|border|...)-(red|orange|...)-[0-9]{2,3}\\b/]",
        message: "Raw Tailwind color utility — use a semantic theme token... Tracked: #2816.",
      },
    ],
  },
},
```

```ts
// lib/utils.ts — before
export function statusColor(status: string) {
  // ...contrast comment...
  const map: Record<string, string> = { searching: "bg-yellow-500/15 ...", ... };
  return map[status] || "bg-zinc-500/15 text-zinc-600";
}

// after
export function statusColor(status: string) {
  // ...contrast comment... + new exclusion-rationale comment...
  /* eslint-disable no-restricted-syntax -- categorical status map, contrast-verified per-shade, see comment above (#2816) */
  const map: Record<string, string> = { searching: "bg-yellow-500/15 ...", ... };
  return map[status] || "bg-zinc-500/15 text-zinc-600";
  /* eslint-enable no-restricted-syntax */
}
```

## 8. Rollback plan

`git-revert-safe` — lint config + one comment block, no data/API/schema/
runtime change.

## 9. Verification performed

- [x] Real production build (`npm run build`) — succeeded.
- [x] `npx tsc --noEmit` — clean.
- [x] `npx vitest run` — 339/339 passed.
- [x] Ran `npx eslint` directly against `lib/utils.ts` and
  `drivers/page.tsx` to confirm the rule actually fires (184 warnings) and
  is genuinely non-blocking (0 errors) — not just assumed from reading the
  config.
- [x] Re-ran `npx eslint src/lib/utils.ts` after adding the suppression —
  confirmed 0 warnings, proving the exclusion works as intended.
- [ ] Did not run the full-repo `eslint` (all 115 backlog files) — that
  would just reproduce the same warning count as the earlier grep-based
  scoping pass; not needed to verify this PR's own change (config +
  one file), and the migration-plan doc already captures the current
  per-file counts from the grep pass, not from a full eslint run.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated, not assumed — verified `warn`-only (0 errors) directly, not inferred from the config syntax.
- [x] No silent behavior change — this adds visibility only; zero runtime/UI difference, explicitly confirmed in §5.
