# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude (session) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this PR) |
| Related issue or gap ID | none — found while babysitting PR #4896's CI |

## 1. Issue / gap identified

`admin-test`'s `npm run lint` step crashes outright on `main`'s own tip
(exit code 2, "Oops! Something went wrong!") instead of reporting lint
findings — `TypeError: contextOrFilename.getFilename is not a function`
while linting `.storybook/main.ts`.

## 2. Root cause

`eslint-plugin-react@7.37.5` (the latest version ever published) calls
`context.getFilename()`, a method ESLint's rule-context API removed in
ESLint 9+ (replaced by a `context.filename` property; ESLint 9 kept a
deprecated shim, ESLint 10 dropped it). `eslint` was bumped to `^10.9.1`
via dependabot PR #4774 without checking whether `eslint-plugin-react`
(a transitive dependency of `eslint-config-next`) actually supports it —
its own `peerDependencies` field caps at `^9.7`, confirmed via
`npm view eslint-plugin-react@7.37.5 peerDependencies`. No newer
`eslint-plugin-react` release exists yet (confirmed via
`npm view eslint-plugin-react versions`) that adds ESLint 10 support, so
bumping the plugin forward is not currently an option.

## 3. Fix / remediation

Pinned `eslint` back to `^9.39.5` (latest 9.x, still within
`eslint-plugin-react`'s declared `^9.7` peer range and
`eslint-config-next@16.3.3`'s declared `>=9.0.0` peer range) instead of
`^10.9.1`. Regenerated `package-lock.json` via `npm install
eslint@^9.39.5` (touches eslint's own transitive dependency subtree only
— `espree`, `@eslint/*`, etc. — plus a cosmetic alphabetical re-sort of
`devDependencies` in `package.json` that `npm install` performs as a
side effect of any version-spec edit; no other package's pinned version
changed).

## 4. Risk & impact on existing functionality

- Isolated to `admin-dashboard`'s dev/CI tooling (`eslint` is a
  `devDependency`; it never ships in the production bundle). No runtime
  code path reads or depends on the ESLint version.
- Blast radius: single surface (admin-dashboard), lint/CI tooling only.
- No interaction with any backend table, background loop, or ride/money
  state.

## 5. User-experience effect

None — dev/CI tooling only, not shipped to any user.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/package.json` | `eslint`: `^10.9.1` → `^9.39.5` | ESLint 10 is incompatible with the currently-latest `eslint-plugin-react` (7.37.5), which crashes the lint step instead of running |
| `admin-dashboard/package-lock.json` | Regenerated (eslint's own transitive dep subtree only) | `npm install eslint@^9.39.5` |

## 7. Before / after

```
# Before
"eslint": "^10.9.1",
```

```
# After
"eslint": "^9.39.5",
```

## 8. Rollback plan

`git revert` — a pure devDependency version pin, no data or live-behavior
impact. If a future `eslint-plugin-react` release adds real ESLint 10+
support, re-bump `eslint` forward again then.

## 9. Verification performed

- [x] `npm run lint` → exit code 0, 337 warnings (0 errors), well under
  the configured `--max-warnings 1751` cap. No crash.
- [x] **Production build run**: `npm run build` → completed successfully,
  full Next.js production build (not just `tsc --noEmit`).
- [x] `npx vitest run` → 59 test files, 561 tests, all passed.
- [x] Blast-radius check: `eslint` is a `devDependency` only; `grep -rn
  '"eslint"' admin-dashboard/package.json` confirms the only reference.
  No other package.json in the monorepo shares this lockfile.
- [ ] Not feature-flagged — not applicable (dev tooling, not
  user-visible).

## What was NOT verified

- Did not attempt to make `eslint-plugin-react` itself compatible with
  ESLint 10 (e.g. patching, forking, or waiting on an upstream release) —
  pinning `eslint` back to the last mutually-compatible major version is
  the standard, lower-risk fix for this exact class of "tool bumped ahead
  of a required peer" problem, per this repo's own `CLAUDE.md` guidance
  on dependency version bumps ("verify a newer/patched dependency version
  actually works... before pinning it").
- Did not audit whether `eslint@^10.9.1` was pulled in for a specific new
  rule/feature this repo actually wants — grepped `eslint.config.mjs` and
  found no ESLint-10-specific syntax in use, so downgrading is safe on
  the config side too, but this wasn't exhaustively diffed rule-by-rule.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated: isolated to admin-dashboard dev tooling
- [x] No silent behavior change to any shipped flow (devDependency only)
