# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude (session) |
| Surface(s) | rider-app, driver-app |
| Domain (Sentry tag) | rides (CI/test infra, not runtime) |
| PR / commit link | (this PR) |
| Related issue or gap ID | none — found while checking `main`'s CI status after an unrelated PR merge |

## 1. Issue / gap identified

`main`'s CI (`ci.yml`) had both `Rider app E2E tests (Playwright)` and
`Driver app E2E tests (Playwright)` fail on the same run, each with:
`Error: Timed out waiting 60000ms from config.webServer.` — the E2E
server never came up in time, so no tests even ran.

## 2. Root cause

Both apps' `playwright.config.ts` start the E2E web server via
`npx serve dist -l ${PORT} --single`. `serve` is **not** a declared
dependency in either app's `package.json` — `npx` resolves it on demand
on every CI run, and the logs confirm it: `npm warn exec The following
package was not found and will be installed: serve@14.2.6`. That
on-demand npm-registry fetch competes with the rest of the 60-second
`webServer.timeout` budget; when registry latency is even moderately
slow, the fetch alone can eat the whole window, so the server "times
out" before it's ever actually started listening.

`admin-dashboard`'s own E2E job doesn't hit this — it uses Next.js's
built-in `next start`, not `npx serve` — which is why only rider-app and
driver-app were affected. Confirmed via `grep` on both configs and the
CI logs of the immediately-prior `main` run, where the same two jobs
passed cleanly (no code in either app changed between the two runs —
the intervening commit only touched `admin-dashboard`), consistent with
this being a timing flake rather than a real regression.

## 3. Fix / remediation

Added `serve@^14.2.6` (the exact version `npx` was already resolving)
as a `devDependency` in both `rider-app/package.json` and
`driver-app/package.json`. This makes `yarn install` (a step CI's E2E
jobs already run before `expo export`/`test:e2e`) fetch and cache
`serve` up front, so `npx serve` at webServer-start time resolves it
from the already-populated `node_modules/.bin` instead of hitting the
network — removing the download-latency variable from the 60s startup
budget entirely.

No change to `playwright.config.ts` in either app — the `npx serve dist
-l ${PORT} --single` command and the 60s timeout are both unchanged;
only the dependency resolution path changed.

## 4. Risk & impact on existing functionality

- `serve` is a devDependency-only, CI/local-E2E-only tool — never
  bundled into the Expo web/native production build, never imported by
  application code.
- Blast radius: isolated to the two apps' own E2E test infrastructure.
  No other package.json in the monorepo references `serve`.
- No change to the actual served content, the export step, the port
  numbers, or the webServer timeout — only how fast `serve` becomes
  available.

## 5. User-experience effect

None — CI/test infrastructure only, not shipped, not user-facing.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/package.json` | Added `"serve": "^14.2.6"` to `devDependencies` | Pre-fetch `serve` during `yarn install` instead of on-demand via `npx` at webServer-start time |
| `rider-app/yarn.lock` | Regenerated (adds `serve` and its own dependency subtree) | `yarn install` after the `package.json` edit |
| `driver-app/package.json` | Added `"serve": "^14.2.6"` to `devDependencies` | Same |
| `driver-app/yarn.lock` | Regenerated | Same |

## 7. Before / after

```
# Before (CI log)
$ playwright test
[WebServer] npm warn exec The following package was not found and will be installed: serve@14.2.6
Error: Timed out waiting 60000ms from config.webServer.
```

```
# After (local reproduction of the fixed startup path)
$ npx serve dist -l 3999 --single
 INFO  Accepting connections at http://0.0.0.0:3999
 HTTP  ... GET /
 HTTP  ... Returned 200 in 17 ms
```

## 8. Rollback plan

`git revert` — a devDependency addition and lockfile regeneration only,
no data or live-behavior impact. Reverting restores the prior
on-demand-`npx`-fetch behavior (flaky but not itself broken when the
registry is fast).

## 9. Verification performed

- [x] Confirmed the exact failure locally is fixable: after `yarn
  install` with `serve` declared, `node_modules/.bin/serve` exists
  immediately (no network call needed).
- [x] Ran `npx expo export --platform web` (rider-app) → succeeded,
  produced `dist/`.
- [x] Ran `npx serve dist -l 3999 --single` against that export and
  `curl`'d it → `HTTP 200 in 0.02s`, confirming the server starts and
  serves correctly with the new dependency in place.
- [x] Blast-radius check: `grep -rn "serve" */package.json` confirms no
  other app/package references `serve`; the two lockfile diffs only add
  `serve`'s own dependency subtree, no other package's pinned version
  changed.
- Not a production build change — `npm run build`/production build
  verification doesn't apply here (devDependency, test-infra only).

## What was NOT verified

- Did not run the full Playwright E2E suite end-to-end in this sandbox
  (no browser-automation environment configured here beyond the `curl`
  smoke check) — the fix targets the *webServer startup* step
  specifically, which is directly reproduced and confirmed above; the
  actual test specs themselves are unrelated to this change and were
  already passing before the timeout (they never got to run in the
  failing case, since the server never came up).
- Did not confirm this was the *only* possible cause of the timeout
  (e.g. a genuinely overloaded CI runner could still occasionally hit
  60s) — but removing the network-fetch-on-critical-path is the
  standard, well-understood fix for this exact `npx serve` timing
  pattern, and it directly matches what the failing logs show.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated: isolated to rider-app/driver-app E2E test
  infrastructure (devDependency only)
- [x] No silent behavior change to any shipped flow (test-infra only,
  webServer command/timeout unchanged)
