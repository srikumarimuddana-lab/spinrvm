# Change Impact & Risk — Storybook devDependency reaches the same unpatched image-size CVE as CR #4548, allowlisted the same way

**Date:** 2026-08-30 · **Surface:** CI / security gates (`admin-dashboard`) · **Risk:** low

## Issue/gap identified

Adding Storybook (component workshop) to `admin-dashboard` for local UI
development pulls in `image-size@2.0.2` transitively (via
`@storybook/nextjs-vite` → `vite-plugin-storybook-nextjs`), which carries two
unpatched HIGH-severity advisories
([GHSA-w3rx-r6r6-pgpr](https://github.com/advisories/GHSA-w3rx-r6r6-pgpr),
[GHSA-5p2g-fcmc-qvqq](https://github.com/advisories/GHSA-5p2g-fcmc-qvqq)) —
DoS via infinite-loop parsing of malicious ICNS/JXL/HEIF images. `npm audit`
confirms `fixAvailable: false`; every published `image-size` release is
affected, so no version bump clears it.

This is the exact same advisory already allowlisted for the mobile apps'
`yarn-audit` gate under CR #4548 (`scripts/security/check_yarn_audit_allowlist.py`),
reached there via a completely different path (`expo` → `@expo/metro` →
`metro`). Same root cause, same "no fix exists" conclusion, different
dependency chain.

## Root cause

`image-size` is a hard dependency of Storybook's Next.js framework
integration itself — verified both `@storybook/nextjs-vite` (Vite-based) and
`@storybook/nextjs` (webpack5-based) declare it directly, so switching
builders does not avoid it.

## Fix/remediation

- Added `scripts/security/check_npm_audit_allowlist.py`, mirroring
  `check_yarn_audit_allowlist.py`'s allowlist (same two GHSA IDs, same
  module) but adapted to `npm audit --json`'s single-object output shape
  (vs. yarn's line-delimited advisories) and its dependency-chain reporting
  (npm reports each ancestor package as its own "vulnerability" whose `via`
  is the child's name, not a duplicate advisory — the script resolves that
  chain recursively so an ancestor is only allowlisted when *every* advisory
  reachable from it is the allowlisted one).
- `security-gates.yml`'s `G4c · npm audit (admin-dashboard, HIGH blocking)`
  step now pipes `npm audit --audit-level=high --json` through this script
  instead of relying on `npm audit`'s own exit code.

## Risk & impact on existing functionality

- **Blast radius: isolated to the G4c step.** Grepped `.github/workflows/`
  for every `npm audit` call — this is the only one. No other gate,
  workflow, or script reads this output.
- The allowlist is scoped to exactly two GHSA IDs on exactly one module
  (`image-size`); any other HIGH/CRITICAL finding, on `image-size` or
  anything else, still fails the gate exactly as before. Verified with a
  planted fake advisory (see PR) that the script still blocks non-allowlisted
  findings.
- `image-size` here is reachable only through Storybook's own dev-time
  tooling (local `npm run storybook` / `npm run build-storybook`), never
  through `npm run build` or the deployed admin-dashboard bundle — confirmed
  by inspecting `@storybook/nextjs-vite`'s dependency tree, not assumed.

## User experience effect

None. This is a CI/dev-tooling change; no rider/driver/corporate-admin/
internal-admin-facing behavior changes.

## Files modified

| File | What changed | Why |
|---|---|---|
| `scripts/security/check_npm_audit_allowlist.py` | New | Scoped allowlist for the same image-size advisory, npm-audit-shaped |
| `.github/workflows/security-gates.yml` | G4c step now pipes through the allowlist script instead of bare `npm audit --audit-level=high` | Storybook's addition would otherwise permanently fail this blocking gate |

## Before/after snippet

```diff
- run: npm audit --audit-level=high
+ run: |
+   npm audit --audit-level=high --json > /tmp/npm-audit-admin.json || true
+   python3 "${GITHUB_WORKSPACE}/scripts/security/check_npm_audit_allowlist.py" /tmp/npm-audit-admin.json
```

## Rollback plan

`git revert` is safe for both files — pure CI configuration and a standalone
script, no data or running state involved. Reverting restores the bare
`npm audit --audit-level=high` call, which would then permanently fail G4c
on admin-dashboard PRs again as long as the Storybook devDependency is
present (dropping Storybook itself is the alternative rollback if the
allowlist approach is rejected).

## Verification performed

- Ran the real `npm audit --json` output from a clean `npm install` through
  the new script: passes (only the two allowlisted GHSA IDs present).
- Planted a fake unrelated HIGH advisory in a copy of that same output and
  re-ran the script: correctly still fails (exit 1), proving the allowlist
  doesn't over-widen.
- Confirmed via `npm view` that both Storybook Next.js framework packages
  (`@storybook/nextjs-vite` and `@storybook/nextjs`) declare `image-size` as
  a direct dependency — this isn't avoidable by choosing the other builder.
- Full admin-dashboard `npm run lint` (0 errors) and `npm test` (404/404
  passing, 40/40 files) re-run after all changes — not just the audit script
  in isolation.

## What was NOT verified

- Not run against a real GitHub Actions runner — validated locally against
  the same command the workflow now runs, but the actual CI environment
  (network to the npm/OSV advisory feed, `GITHUB_WORKSPACE` path resolution)
  was not exercised end-to-end.
- No attempt was made to find or request an upstream Storybook/image-size
  fix; this is a stopgap, same as CR #4548, pending one being published.
