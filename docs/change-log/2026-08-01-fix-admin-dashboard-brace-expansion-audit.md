# Change Impact & Risk Log

This is a dev-tooling-only dependency bump (lockfile only, no `package.json`
edit, no runtime/shipped code touched). Kept brief per CLAUDE.md's own
guidance to scale the log to blast radius — full 10-section detail (rollback
via feature flag, mid-session UX effect, etc.) does not apply to a devDependency
lockfile change with zero runtime surface.

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude Code (background job) |
| Surface(s) | admin-dashboard (CI/dev-tooling only — no app code) |
| Domain (Sentry tag) | admin (CI gate, not a runtime code path) |
| PR / commit link | see PR description |
| Related issue or gap ID | none — found via `G4c · npm audit (admin-dashboard, HIGH blocking)` CI gate today, no filed issue |

## 1. Issue / gap identified

`G4c · npm audit (admin-dashboard, HIGH blocking)` was reporting one real
HIGH-severity finding: `brace-expansion <1.1.17`
([GHSA-mh99-v99m-4gvg](https://github.com/advisories/GHSA-mh99-v99m-4gvg), a
ReDoS/OOM DoS advisory), pulled in transitively at v1.1.16 via `minimatch@3.1.5`,
itself a transitive dependency of the ESLint toolchain (`eslint`,
`eslint-plugin-import`, `eslint-plugin-react`, `eslint-plugin-jsx-a11y`,
`@eslint/config-array`, `@eslint/eslintrc`).

## 2. Root cause

`brace-expansion@1.1.16` was resolved in `package-lock.json` under several
ESLint-toolchain devDependency subtrees. `minimatch@3.1.5` (the direct
consumer in each of those subtrees) declares `"brace-expansion": "^1.1.7"`,
which is wide enough to already include the patched `1.1.17`/`1.1.18` —
the lockfile simply hadn't been refreshed to pick it up.

## 3. Fix / remediation

Ran `npm audit fix` (no `--force`) in `admin-dashboard/`. It re-resolved
`brace-expansion` to `1.1.18` in every affected subtree, all within
`minimatch@3.1.5`'s existing `^1.1.7` range — a pure lockfile update, zero
`package.json` changes, zero major/breaking bumps. As a side effect (also
non-breaking, also just a lockfile resolution), it bumped
`@modelcontextprotocol/sdk` 1.29.0 → 1.30.0, which widened its
`@hono/node-server` peer range to include `^2.0.5` and incidentally cleared
the pre-existing moderate `@hono/node-server` finding too (not required by
this task's blocking threshold, but harmless and welcome).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `admin-dashboard/package-lock.json`.** No
  `package.json` dependency version constraints changed; no application code
  changed.
- `brace-expansion`/`minimatch` here are exclusively ESLint-toolchain
  devDependencies — never imported by any file under `admin-dashboard/src/`,
  never bundled into the Next.js production build. Grepped: no direct
  `require`/`import` of `brace-expansion` or `minimatch` anywhere in
  `admin-dashboard/src/`.
- `@modelcontextprotocol/sdk`/`@hono/node-server` are similarly dev-only
  (pulled in by an MCP-related devDependency chain, not runtime code).
- No shared component/hook/utility, no DB table, no background loop, no
  ride/payment/auth/corporate/safety code path is touched.

## 5. User-experience effect

None. No rider/driver/corporate-admin/internal-admin-visible change of any
kind — this only affects what runs in CI and local `eslint`/build tooling.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/package-lock.json` | `brace-expansion` resolved to `1.1.18` (was `1.1.16`) across ESLint-toolchain subtrees; `@modelcontextprotocol/sdk` 1.29.0→1.30.0 and `@hono/node-server` 1.19.15→2.0.12 (transitive, incidental) | Clears the HIGH `brace-expansion` CVE (GHSA-mh99-v99m-4gvg) blocking `G4c`; the MCP/hono bump incidentally cleared the pre-existing moderate finding too |

## 7. Before / after

```
# Before (npm ls brace-expansion, admin-dashboard)
eslint@9.39.5 (+ eslint-plugin-import, eslint-plugin-react,
eslint-plugin-jsx-a11y, @eslint/config-array, @eslint/eslintrc)
  minimatch@3.1.5
    brace-expansion@1.1.16   <-- vulnerable, GHSA-mh99-v99m-4gvg

# After
  minimatch@3.1.5
    brace-expansion@1.1.18   <-- patched
```

No `package.json` diff (before/after identical — this is a lockfile-only change).

## 8. Rollback plan

`git revert` of this commit is a complete, sufficient rollback: it only
touches a devDependency lockfile with no runtime effect and no data ever
written anywhere. No feature flag, config, or migration is applicable —
stating that explicitly per the template's own allowance for genuinely
isolated, low-risk changes.

## 9. Verification performed

- [x] `npm ci && npm audit --audit-level=high` in `admin-dashboard/` (CI's
      exact `G4c` command) — confirmed HIGH finding present before the fix,
      confirmed `found 0 vulnerabilities` / exit 0 after.
- [x] `npm run lint` (real run, not `tsc --noEmit` alone) — exit 0, 0 errors /
      319 warnings, matching the documented pre-existing baseline exactly.
- [x] `npm run build` (real production Next.js build, not dev server) — exit
      0, compiled successfully, TypeScript check passed, all 70 routes/pages
      generated.
- [x] `npx vitest run` (full admin-dashboard test suite) — exit 0, 18 test
      files / 153 tests, all passed.
- [x] Blast-radius grep: confirmed `brace-expansion`/`minimatch` are only
      transitive devDependencies of the ESLint toolchain and MCP tooling, not
      imported anywhere in application source.
- [x] `npm audit` (no level filter) after the fix — `found 0 vulnerabilities`,
      confirming no new findings were introduced.

## 10. What was NOT verified

- Not tested against Playwright e2e (`test:e2e`) — out of scope for a
  devDependency lockfile change with no runtime/UI surface touched, and
  `security-gates.yml`'s `G4c` gate itself only runs `npm audit`, not e2e.
- Not verified against a live Vercel deploy/preview — only local
  `npm run build`.

## Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no live
      data involved).
- [x] Blast radius is stated, not assumed: isolated to
      `admin-dashboard/package-lock.json`, ESLint/MCP devDependency subtrees
      only.
- [x] No behavior change to any shipped flow — nothing to fill in for the UX
      field beyond "none."
