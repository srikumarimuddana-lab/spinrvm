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
- **`warn`-level alone was NOT sufficient to guarantee this doesn't break
  CI — caught and fixed after initial push.** `package.json`'s `lint`
  script runs `eslint --max-warnings 600`, a repo-wide warning-count
  ceiling `npm run lint` (CI's `admin-test` job) actually enforces
  regardless of individual rule severity. This rule alone adds ~1,419 new
  project-wide warnings (332 baseline → 1,751 total via `npx eslint .`),
  which blew straight through the 600 cap and failed `admin-test` on the
  first push (confirmed via the real CI run, not just local `npx eslint`
  on two files — that check only proves 0 *errors*, not that the
  repo-wide *warning count* stays under the script's actual ceiling).
  Fixed by bumping `--max-warnings` to 1751 (exact current count, same
  ratchet-not-buffer philosophy as `e2e/a11y-baseline.json` — never raise
  it further for an unrelated reason; ratchet down as batches land).
  Re-verified: `npm run lint` now exits 0.
- **Every other admin-dashboard PR in flight or merged today is
  unaffected** — the `--max-warnings` bump only raises the ceiling this
  PR's own new warnings needed; it doesn't loosen anything for unrelated
  future warnings (a PR that pushes the count past 1751 for an unrelated
  reason still fails, same as before at the old 600 ceiling). The
  eventual `error` flip on this specific rule (out of scope for this PR)
  is what actually changes CI *behavior* for new violations, and only
  after the migration batches land.
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
| `admin-dashboard/package.json` | `lint` script's `--max-warnings` bumped 600 → 1751 | The new rule's ~1,419 project-wide warnings blew through the old cap and failed CI's `admin-test` job — caught after first push, see §4 |
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
- [x] **Correction, added after the first push**: initially skipped
  running full-repo `eslint` (reasoned it would "just reproduce the
  grep-based scoping count") — that reasoning was wrong. The two-file
  spot-check proved the rule fires and produces 0 *errors*, but never
  checked the repo-wide *warning count* against `package.json`'s actual
  `lint` script (`eslint --max-warnings 600`), which CI enforces
  regardless of individual rule severity. Ran `npx eslint .` for real
  after CI's `admin-test` job failed on the first push: 1,751 total
  warnings vs. the 600 cap. Fixed (see §4/§6) and re-verified: `npm run
  lint` (the exact command CI runs) now exits 0.
- [x] Ran the full `admin-test` job's actual steps locally in sequence
  after the fix — `npm run lint`, `npm run check:middleware`, `npm test`,
  `npm run build` (with CI's own `BACKEND_URL`/`NEXT_PUBLIC_API_URL` env
  vars) — all four exit 0, not just the subset this PR touches.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated, not assumed — verified `warn`-only (0 errors) directly, not inferred from the config syntax.
- [x] No silent behavior change — this adds visibility only; zero runtime/UI difference, explicitly confirmed in §5.
