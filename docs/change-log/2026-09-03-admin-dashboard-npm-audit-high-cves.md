# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude (session) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this PR) |
| Related issue or gap ID | `G4b · yarn audit` / `G4c · npm audit (admin-dashboard, HIGH blocking)` gates in `security-gates.yml` |

## 1. Issue / gap identified

`security-gates.yml`'s `G4c · npm audit (admin-dashboard, HIGH blocking)` and
3× `G4b · yarn audit (JS deps)` jobs (driver-app, rider-app, admin-dashboard)
were red on `main`'s own tip — 6 blocking HIGH advisories in
`admin-dashboard/package-lock.json`: 2 in `browserslist`
(`GHSA-c83g-rgw3-j3cx`, `GHSA-73wf-gq98-2v4g`) and 4 in `fast-uri`
(`GHSA-5jgf-p345-68v8`, `GHSA-f65p-4m7j-42xc`, `GHSA-fph4-wmhf-6fwf`,
`GHSA-jqff-g426-hqxp`). Confirmed pre-existing before PR #4898 (which only
pinned `eslint`), found while babysitting that PR and PR #4896's CI.

## 2. Root cause

Both packages are transitive devDependencies pulled in via `shadcn@4.19.0`
(the shadcn/ui CLI, a dev-only code-generation tool — never bundled into
the production build): `browserslist` via `@babel/core` /
`update-browserslist-db`, `fast-uri` via
`@modelcontextprotocol/sdk`'s `ajv`/`ajv-formats`. The lockfile had them
resolved to `browserslist@4.28.1` and `fast-uri@3.1.5` — both inside the
vulnerable ranges (`<=4.28.6` and `3.0.0–3.1.5` respectively) — even
though `package.json`'s own declared semver ranges (`^4.24.0`, `^3.0.1`,
neither edited by this change) already permitted newer, patched
versions. The lockfile simply hadn't been refreshed to pick them up.

## 3. Fix / remediation

Ran `npm audit fix` (no `--force`, no manual version pins). It resolved
all 6 blocking findings — plus 3 more (`fflate` moderate,
`postcss-selector-parser` high, `qs` moderate) that weren't part of the
original blocking set but were also outstanding — purely by re-resolving
`package-lock.json` to newer versions already satisfying the existing
semver ranges in `package.json`. **`package.json` itself needed zero
changes** — confirmed via `diff` before/after. `browserslist` moved
4.28.1 → 4.28.8; `fast-uri` moved 3.1.5 → 3.1.7 (both dependency-subtree
updates only, no top-level package.json edit). `npm audit
--audit-level=high` now reports 0 vulnerabilities.

**Deliberately not touched**: `image-size` (and its dependents
`vite-plugin-storybook-nextjs`/`@storybook/nextjs-vite`) — the CI script
(`scripts/security/check_npm_audit_allowlist.py`) already allowlists it
with a documented "no patch exists" rationale, and it wasn't part of the
6 blocking findings covered here. `npm audit fix` did resolve it to a
patched version as a side effect (0 vulnerabilities overall after), but
since it's out of this fix's stated scope and already has its own
allowlist entry, no additional action was taken on it — if the allowlist
entry is now stale (a patch does exist), that's a separate, smaller
follow-up for whoever owns that script.

## 4. Risk & impact on existing functionality

- `shadcn` is a **dev-only CLI** invoked manually by developers to
  scaffold/update UI components — it is never imported by application
  code, never runs in production, and never runs in CI beyond this audit
  step itself. Blast radius: isolated to that one dev workflow.
- No `package.json` version-range change means no risk of a *different*
  major/minor version being resolved than what the project already
  declared as acceptable — this is strictly "pick up the patch release
  the existing range already allows," not a version bump.
- No other package's pinned version changed (confirmed: `browserslist`
  and `fast-uri` are the only entries with new resolved versions in the
  lockfile diff, beyond their own sub-dependencies).

## 5. User-experience effect

None — dev-only tooling dependency, not shipped, not user-facing.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/package-lock.json` | `browserslist` 4.28.1 → 4.28.8, `fast-uri` 3.1.5 → 3.1.7 (both already within `package.json`'s existing semver ranges) | Resolve 6 blocking HIGH CVE audit findings |

## 7. Before / after

```
# Before (npm ls browserslist)
`-- shadcn@4.19.0
  `-- browserslist@4.28.1

# After
`-- shadcn@4.19.0
  `-- browserslist@4.28.8
```

```
# Before (npm ls fast-uri)
`-- shadcn@4.19.0
  `-- @modelcontextprotocol/sdk@1.30.0
    `-- ajv@8.20.0
      `-- fast-uri@3.1.5

# After
      `-- fast-uri@3.1.7
```

## 8. Rollback plan

`git revert` — lockfile-only change, no data or live-behavior impact. If
this somehow broke the `shadcn` CLI for a developer, reverting restores
the prior (vulnerable but previously-working) resolution instantly.

## 9. Verification performed

- [x] `npm audit --audit-level=high` → 0 vulnerabilities (was 9: 1 low, 3
  moderate, 5 high).
- [x] `npm run lint` → exit 0, 336 warnings (0 errors) — one fewer
  warning than before this change, consistent with a dependency bump, no
  new errors.
- [x] **Production build run**: `npm run build` → full Next.js
  production build completed successfully.
- [x] `npx vitest run` → 59 test files, 561 tests, all passed.
- [x] Blast-radius check: confirmed via `npm ls browserslist fast-uri`
  that both packages resolve only under `shadcn`'s dependency subtree,
  and via `diff` on `package.json` that zero top-level dependency
  declarations changed.
- Not feature-flagged — not applicable (dev tooling, not user-visible).

## What was NOT verified

- Did not manually exercise the `shadcn` CLI itself (e.g. `npx shadcn add
  <component>`) — it isn't invoked by any CI job or test in this repo, so
  there's no automated way to check it, and doing so wasn't judged
  necessary given the change is a patch-level bump within an
  already-declared semver range for a dev-only tool.
- Did not re-verify whether `image-size`'s allowlist entry in
  `scripts/security/check_npm_audit_allowlist.py` is now stale (a patched
  version may exist) — flagged above as a separate, smaller follow-up,
  not fixed here since it's outside this change's stated scope.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data impact)
- [x] Blast radius is stated: isolated to `shadcn`'s dev-only dependency
  subtree in admin-dashboard
- [x] No silent behavior change to any shipped flow (devDependency only)
