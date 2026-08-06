# Change Impact & Risk Log — `shared/**/__tests__` was run by no CI job

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (session: driver sign-out investigation) |
| Surface(s) | CI, `shared/`, rider-app |
| Domain (Sentry tag) | `auth` (by subject matter of the rotted tests) |
| PR / commit link | _(pending)_ |
| Related issue or gap ID | Gate-decay finding (CLAUDE.md pre-merge gate #8) |

## 1. Issue / gap identified

Four test suites under `shared/` were executed by **no jest project and no CI job**:

- `shared/api/__tests__/client.refresh.test.ts`
- `shared/api/__tests__/client.sos.test.ts`
- `shared/api/__tests__/client.authHeader.test.ts`
- `shared/utils/__tests__/pii.test.ts`

Two were **failing**. One of them —
`client.refresh.test.ts › deduplicates concurrent refresh calls` — was pointing
directly at the concurrent-401 false-logout that signed drivers out for a whole
release. Its console output contained the smoking gun verbatim
(`[API] 401 Unauthorized — clearing session`) and nobody ever saw it.

This is the single largest contributing factor to that defect surviving.

## 2. Root cause

Nothing was misconfigured — coverage simply never existed:

- `driver-app/jest.config.js` and `rider-app/jest.config.js` both leave `roots`
  unset, so each defaults to its own `<rootDir>`.
- The repo-root `package.json` has **no `scripts` block** at all.
- `.github/workflows/ci.yml` runs `yarn test` per app.
- `.github/workflows/pr-checks.yml` *detects* that `shared/` was touched
  (`paths.filter(p => p.startsWith('shared/'))`) — but only for labelling. It runs
  nothing.

So `shared/` had tests, and a `typecheck` script, but no `test` script and no
runner. Adding a test file there produced no signal of any kind, in either
direction: it never ran, so it never passed and never failed.

The two individual failures had ordinary causes:

- **`client.authHeader.test.ts`** never mocked `../../config/spinr.config`, which
  `api/client` imports and which imports `expo-constants`. Outside an Expo app root
  that throws `Cannot read properties of undefined (reading 'EXDevLauncher')`, so
  the whole suite failed to load. Its two sibling suites already mock it.
- **`client.refresh.test.ts`** awaited a timer *before* attaching
  `Promise.allSettled`, so the losing request's 401 rejection was briefly
  unhandled, Node emitted `unhandledRejection`, and jest failed the test for a
  reason unrelated to its assertions — masking the real defect it was catching.
- **`pii.test.ts`** disagreed with `redactCoords` on half-way rounding for negative
  values: `Math.round` rounds half toward +∞, so `Math.round(-1066.5)` is `-1066`
  and `-106.65` redacted to `-106.6` while `+106.65` redacted to `+106.7`.

## 3. Fix / remediation

1. **`rider-app/jest.config.js` gains `roots: ['<rootDir>', '<rootDir>/../shared']`**,
   so the existing `rider-app-test` CI job runs them. rider-app rather than
   driver-app because driver-app's `moduleNameMapper` redirects `^@shared/(.*)$` to
   its own `__mocks__` directory — it *mocks* shared modules and therefore cannot
   test the real ones. rider-app maps `@shared` to the real files and already hosted
   `__tests__/api-client-401-refresh.test.ts`, which tests `shared/api/client.ts`.
   Deliberately **one** owner: running them from both apps would double-run them and
   split responsibility for keeping them green.
2. **`client.authHeader.test.ts`** gains the missing `spinr.config` mock.
3. **`client.refresh.test.ts`** attaches `.catch()` synchronously, and gains the
   assertion it was missing all along — `expect(mockLogout).not.toHaveBeenCalled()`.
   Without that assertion the test could not have caught the defect even when run.
4. **`redactCoords`** now rounds half **away from zero**, making the rule
   sign-independent and matching what the test always specified.
5. **CI step renamed** "Run rider app tests" → "Run rider app + shared tests", with
   a comment naming this as the gate that covers `shared/`. The failure mode here
   was nobody knowing, so discoverability is part of the fix.
6. **Two hollow tests deleted** from `rider-app/__tests__/auth.integration.ts`
   (see §4).

## 4. Risk & impact on existing functionality

**Blast radius: CI configuration, one unused utility, and test files.** No
production runtime code changes except `redactCoords`.

**`redactCoords` — zero production callers.** Grep across `shared`, `driver-app`,
`rider-app`, `admin-dashboard`: the only importer is its own test. `redactPhone`
likewise has none, and `redactEmail` has exactly one, a dev script
(`admin-dashboard/scripts/test-railway-login.ts`). So changing the rounding is
risk-free — and the fact that a PII-redaction module is essentially unused while
CLAUDE.md mandates redacting coordinates, phones and emails from logs is a
**separate compliance question, filed as its own task.** Privacy is unaffected by
this diff either way: one decimal place is ~11 km whichever direction the half case
breaks.

**Deleting tests deserves scrutiny, so here is the justification.** Two tests were
removed from `rider-app/__tests__/auth.integration.ts`:

| Removed test | Why it could never fail |
|---|---|
| `should handle 401 by triggering auto-refresh` | Body was `expect(true).toBe(true)`, with a comment describing `apiClient.interceptors.response` — the axios client that was dead code and has since been deleted |
| `should update token expiry on refresh` | Called `setState({ tokenExpiresAt: Date.now() + expiresIn * 1000 })` with the arithmetic performed **in the test**, then asserted the value it had just stored. It exercised no production code — which is exactly why it stayed green through the entire release in which the real `setTokens()` was producing `NaN` |

Both flows now have tests that *can* fail, named in a comment left at the deletion
site: `api-client-401-refresh.test.ts` and `client.refresh.test.ts` for the 401 path,
`authStore.tokenExpiry.test.ts` for the expiry arithmetic. A placeholder that always
passes is worse than no test, because it reads as coverage.

**Could enabling the suite destabilise the rider-app CI job?** It is now
responsible for 8 more suites. All 8 pass, and the two that were failing were fixed
*before* the config change so the enabling commit lands green rather than red.
`collectCoverageFrom` is explicitly scoped to `store/**/*.ts`, so shared files are
not instrumented and the existing `coverageThreshold` is unaffected — confirmed by
running the exact CI command with `--coverage`.

Not touched: ride state machine, money/wallet arithmetic, backend background loops,
RLS, migrations, WebSocket events, and any app runtime path.

## 5. User-experience effect

None. No rider, driver, corporate-admin or internal-admin visible change. The
effect is on engineers: `shared/` changes are now gated, and a regression in the
shared API client or auth store fails a required check instead of passing silently.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/jest.config.js` | Added `roots` including `../shared`, with a comment explaining why rider-app is the host and why not driver-app | The suites had no runner |
| `.github/workflows/ci.yml` | Renamed the rider test step to "Run rider app + shared tests" and documented that this job gates `shared/` | Nobody knowing was the failure mode |
| `shared/api/__tests__/client.authHeader.test.ts` | Added the missing `spinr.config` mock | Suite could not load outside an Expo app root |
| `shared/api/__tests__/client.refresh.test.ts` | Synchronous `.catch()` on both requests; added the missing `expect(mockLogout).not.toHaveBeenCalled()` | Harness bug masked the real defect; the missing assertion is why it could not have caught it |
| `shared/utils/pii.ts` | `redactCoords` rounds half away from zero | Sign-dependent rule; zero production callers, so risk-free |
| `rider-app/__tests__/auth.integration.ts` | Deleted the two hollow tests, leaving a comment pointing at the real coverage | A test that cannot fail reads as coverage |

**Natural commit boundaries** (this entry covers work that should land as three
commits, not one):
1. Test-harness fixes — `client.authHeader.test.ts`, `client.refresh.test.ts`
2. `redactCoords` — `shared/utils/pii.ts`
3. Enable + document — `rider-app/jest.config.js`, `ci.yml`,
   `auth.integration.ts`

Sequencing matters: 1 and 2 must precede 3, or the enabling commit turns CI red.

## 7. Before / after

```js
// Before — rider-app/jest.config.js: no `roots`, so default is <rootDir> only.
// shared/**/__tests__ ran nowhere.
module.exports = { preset: 'jest-expo', setupFiles: [...] }
```

```js
// After
module.exports = {
  preset: 'jest-expo',
  roots: ['<rootDir>', '<rootDir>/../shared'],
  ...
}
```

```ts
// Before — shared/utils/pii.ts
const fmt = (n) => Number.isFinite(n) ? (Math.round(n * 10) / 10).toFixed(1) : '?';
// Math.round(-1066.5) === -1066  →  -106.65 becomes "-106.6", +106.65 becomes "106.7"
```

```ts
// After
if (!Number.isFinite(n)) return '?';
return ((Math.sign(n) * Math.round(Math.abs(n) * 10)) / 10).toFixed(1);
// -106.65 → "-106.7", +106.65 → "106.7"; -0.04 → "0.0" (no "-0.0" leak)
```

## 8. Rollback plan

Revert the `roots` line in `rider-app/jest.config.js` — the shared suites stop
running, CI returns to its previous scope, and nothing about the apps changes.
No deploy, no migration, no app build; this is CI configuration and test code.

`redactCoords` is a separate one-line revert with no callers to affect.

**Not feature-flagged** (gate #3): there is no user-visible behaviour and no
runtime code path here to gate. A flag over a jest config is not a meaningful
construct.

## 9. Verification performed

- [x] **The exact CI command** — `yarn test --ci --coverage --forceExit
      --reporters=default` in `rider-app` → **59 suites, 530 tests passed**, coverage
      thresholds met. Before this change that command covered 51 suites / 436 tests;
      the delta is shared's 8 suites and 96 tests, minus the 2 hollow tests removed.
- [x] **The two failures were fixed before the enabling change**, verified by
      running the shared suites in isolation via `--roots` at each step: 3 failing
      suites → 1 failing → **8/8 passing**.
- [x] **`redactCoords` edge cases checked directly in node**, not just via the
      test: `-106.65 → -106.7`, `106.65 → 106.7`, `-0.04 → 0.0` (confirming no
      `"-0.0"` leak), `45 → 45.0`, `NaN → ?`, `Infinity → ?`, `-33.8688 → -33.9`.
- [x] **`npx tsc --noEmit` (rider-app)** → exit 0.
- [x] **CI YAML re-parsed** with `yaml.safe_load` after editing — 18 jobs, and the
      `rider-app-test` step list confirmed intact.
- [x] **Full driver-app suite** — 45/46 suites, **345/346 tests passed**; the single
      failure is `onlineResync`, the pre-existing one already reproduced on HEAD
      with all session changes reverted. Run specifically to confirm the
      `shared/utils/pii.ts` change and the shared-test edits do not reach it — they
      do not, since driver-app maps `@shared` to its own mocks and `redactCoords`
      has no callers.
- [x] **Blast-radius grep performed** — `redactCoords` / `redactPhone` /
      `redactEmail` / `utils/pii` importers across all four surfaces; `roots` and
      `moduleNameMapper` in both app jest configs; `rider-app-test` job definition
      in `ci.yml`.
- [x] **Reviewed against `CLAUDE.md` conventions** — gate #8 (a red or absent gate
      is decay, not "not my problem") is the reason this work exists. PIPEDA: the
      `redactCoords` change does not reduce redaction precision.

### What was NOT verified

- **CI has not actually run.** Everything here was validated locally with the same
  command the workflow invokes, but the GitHub runner has a different environment
  (fresh `yarn install --frozen-lockfile`, Linux paths). `roots` with a `../shared`
  relative path is the piece most likely to behave differently, and the first real
  push is the proof.
- **`shared/` still has no `test` script of its own.** Running `yarn test` inside
  `shared/` does nothing; coverage is borrowed from rider-app's job. A future
  reader in `shared/` will not find a local way to run these — mitigated only by
  the config comment and the renamed CI step. A dedicated `shared/` jest project
  would be the cleaner architecture and is deliberately out of scope here.
- **driver-app still cannot test shared code.** Its `@shared` → `__mocks__`
  mapping is untouched, so a driver-app-only change to `shared/` is gated by the
  *rider* job. That is a real coupling, and it is not obvious from inside
  driver-app.
- **The three now-running `shared/api` suites were not audited for whether they
  assert the right things** — only for whether they run and pass. The missing
  `mockLogout` assertion found in `client.refresh.test.ts` suggests others may have
  similar gaps; I fixed the one I had direct evidence for.
- **`pii.test.ts` coverage of `redactPhone` / `redactEmail` was not reviewed**, only
  the `redactCoords` failure.
- **The PII-module-is-unused finding is filed, not investigated.** Whether raw
  coordinates, phone numbers or emails reach logs anywhere without going through
  these helpers is an open compliance question.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — a one-line revert, no deploy
- [x] Blast radius is stated, not assumed — `redactCoords`'s zero callers
      established by grep, coverage-threshold impact established by running the real
      CI command with `--coverage`
- [x] No silent behavior change to an already-shipped flow — there is no runtime
      behaviour change; §4 justifies the two test deletions explicitly rather than
      letting a shrinking test count pass unremarked
