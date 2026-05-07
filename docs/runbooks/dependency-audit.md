# Runbook — Dependency Vulnerability Audit

**Owner:** `backend` + `frontend` · **Cadence:** Weekly (manual until automated)
**Closes:** B-P1-4 (initial sweep + cadence definition)

---

## Why This Matters

A leaked CVE in a dependency we ship to production can quietly invalidate
work we just merged. After B-P1-3 (refresh-token reuse detection) and
B-P1-13 (sign-out-everywhere UX), a `PyJWT` or `cryptography` CVE in the
backend, or a `protobufjs` RCE in the rider Firebase chain, would
re-open exactly what we closed.

This runbook defines the cadence and triage rules so a P1 finding is
caught and patched before a release ships, not after.

---

## Cadence

| Surface | Tool | When |
|---|---|---|
| `backend/` | `pip-audit -r requirements.txt` | Weekly + before every prod deploy |
| `admin-dashboard/` | `npm audit --omit=dev` | Weekly + before every Vercel deploy |
| `rider-app/` | `yarn audit --groups dependencies` | Weekly + before every EAS `[build]` commit |
| `driver-app/` | `yarn audit --groups dependencies` | Weekly + before every EAS `[build]` commit |

`--omit=dev` / `--groups dependencies` is intentional: dev-only deps
(eslint, jest, vitest, expo CLI build tooling) ship to nobody and
their findings are noise relative to runtime risk.

The current sweep was run on **2026-04-27**. Next due: **2026-05-04**.

---

## Triage Rules

For each finding, assess in this order:

1. **Severity:** `critical` / `high` → must-fix before next deploy.
   `moderate` / `low` → fix if cheap; document if not.
2. **Reachability:** does our code path actually invoke the vulnerable
   API? A dompurify XSS bypass in a server-rendered PDF where we never
   accept untrusted HTML is unreachable; the same CVE in client-side
   `dangerouslySetInnerHTML` is reachable. Reachability can downgrade a
   `high` to "deferred with note" or upgrade a `moderate` to "ship-stop".
3. **Fix availability:** is there a patched version that satisfies our
   semver range? If yes, upgrade. If no but the parent dep has an
   override hatch (`overrides` for npm/yarn-berry, `resolutions` for
   yarn-classic), use it. If no fix exists at all, document under
   *Deferred CVEs* and watch upstream.

Never just `npm audit fix --force` blindly — it has bricked our
admin-dashboard build before by trying to "fix" a `next` postcss
finding by downgrading `next` from 16 to 9.

---

## Operating

### Run a full sweep

```bash
# Backend
cd backend && pip-audit -r requirements.txt --skip-editable

# Admin
cd admin-dashboard && npm audit --omit=dev

# Rider / Driver
cd rider-app && yarn audit --groups dependencies --level moderate
cd driver-app && yarn audit --groups dependencies --level moderate
```

### Apply a transitive-dep fix

When a vulnerable package comes in via a parent we don't directly
control, force the version via `resolutions` (yarn-classic) +
`overrides` (npm). Mirror in both for cross-package-manager safety.
Example from `rider-app/package.json`:

```jsonc
"overrides": {
  "protobufjs": "^7.5.5",      // CVE-2024-XXXX RCE — parent: @grpc/proto-loader
  "@xmldom/xmldom": "^0.8.13"  // GHSA-...      uncontrolled recursion — parent: @expo/plist
},
"resolutions": {
  "protobufjs": "^7.5.5",
  "@xmldom/xmldom": "^0.8.13"
}
```

After editing, run `yarn install` (or `npm install`) and re-audit to
confirm the override actually landed (`npm ls <pkg>` should show
`overridden`).

### Verify the upgrade didn't regress runtime

For mobile: run `yarn test` and compare against the baseline failing
suites (currently 6 pre-existing failures in rider-app — see
`store/__tests__/rideStore*.test.ts`). Any *new* failures = the
upgrade broke something. Roll back and pin a narrower range.

For admin: run `npx tsc --noEmit && npm test`. Both must stay green.

For backend: run `pytest -m "not slow" --no-cov` and watch the
collection count, not just exit code — a pinning regression often
shows up as a collection-time `ImportError`, not a test failure.

---

## Deferred CVEs

Ship-blocking findings only — see audit log for the full list.

| Surface | Pkg | Severity | Deferred because | Watch |
|---|---|---|---|---|
| admin | `postcss <8.5.10` (inside `next@16.2.4`) | moderate | Next pins postcss to *exactly* 8.4.31 — overriding risks breaking the CSS Modules pipeline. The CVE is XSS via stringify of *untrusted CSS*; we only ever stringify CSS we authored, so the vector is unreachable. | `npm view next dependencies.postcss` weekly |
| rider | `postcss <8.5.10` (inside expo build tools) | moderate | Build-time only. Same reachability argument — expo only processes our own CSS source. Not in the runtime app bundle. | Tracks with admin-postcss |
| rider | `uuid <14` (inside `expo` CLI / `@expo/ngrok` / `xcode` plist) | moderate | All paths are dev/CI tooling, never in the app runtime bundle. The CVE requires a `buf` arg that none of these callers pass. | Tracks with expo upgrades |
| driver | `postcss <8.5.10`, `uuid <14` | moderate | Same as rider — dev-time only. | Same |

If any of these escape into a runtime path (e.g. expo starts processing
user-uploaded CSS), promote to ship-blocking immediately.

---

## What NOT to Do

- **Do not run `npm audit fix --force` without reading the proposed
  upgrades.** It will happily downgrade a major version to hit a
  matching constraint — we have already had it offer to take Next from
  16 to 9.3.3 to "fix" a postcss finding. The auto-fix is a hint, not
  a recommendation.
- **Do not `# noqa: B-P1-4` a critical finding to unblock a deploy.**
  Critical CVEs are deploy-stoppers by definition; the right response
  is a hot-patch PR, not a suppression.
- **Do not skip the dev-deps audit forever.** This runbook says to
  filter dev-deps for the weekly sweep, but a yearly full sweep
  (including `--include-dev`) is a P3 follow-up — toolchain CVEs can
  poison a CI build environment even if they never reach prod.
- **Do not mirror `overrides` to `resolutions` without testing both
  package managers.** Yarn-classic and npm parse the JSON differently
  for nested overrides; the simple `"pkg": "version"` form works in
  both, but `{"parent": {"child": "version"}}` works in npm overrides
  only — yarn-classic ignores the wrapper.

---

## 2026-04-27 Sweep Results

| Surface | Before | Critical/High Fixed | After | Deferred |
|---|---|---|---|---|
| backend | 0 | — | 0 | — |
| admin | 4 moderate | 0 (none crit/high) | 2 moderate | 2 (postcss-in-next, postcss-in-tailwind) |
| rider | 4 crit + 28 high + 7 mod | protobufjs 7.5.4→7.5.5 (RCE), @xmldom/xmldom 0.8.12→0.8.13 | 7 moderate | 2 (postcss + uuid in expo build tools) |
| driver | 7 moderate | — | 7 moderate | 2 (postcss + uuid in expo build tools) |

Net: every critical/high finding patched. Remaining seven moderates are
all in dev/build tooling and documented above.

Patches applied:
- `admin-dashboard/package.json`: `overrides.jspdf.dompurify=^3.4.1`
  (was 3.3.3 — XSS bypass family GHSA-39q2-94rc-95cp et al.). Removed
  unused `uuid` + `@types/uuid` direct deps.
- `rider-app/package.json`: added `protobufjs ^7.5.5` and
  `@xmldom/xmldom ^0.8.13` to both `overrides` and `resolutions`.
