# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude Code session |
| Surface(s) | rider-app, driver-app (verified only), shared |
| Domain (Sentry tag) | auth |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | ACTION_ITEMS.md C42-B |

## 1. Issue / gap identified

`shared/**/__tests__` was never wired into any CI job — rider-app's and driver-app's Jest `roots` default to their own `rootDir`, so `shared/` (a sibling package) was only reached through imports, never collected as test files. Wiring it into CI (rider-app's config) surfaced a real, previously-undetected bug in `shared/api/client.ts`'s 401 token-refresh dedup logic: a second concurrent request to the same URL that also 401s does not queue behind the first request's in-flight refresh — it falls straight through to a thrown error instead of retrying with the refreshed token.

## 2. Root cause

`handleApiError`'s 401 branch used `_inflight401Retries`, a `Set<string>` keyed by `"METHOD url"`, to cap a persistently-401ing endpoint to one refresh-retry (preventing an infinite retry loop). The key is per-URL, not per-call — so when two concurrent *original* requests hit the same URL (e.g. two `api.get('/rides/active')` calls racing, as happens routinely: a screen's mount-time fetch overlapping a pull-to-refresh, or two components independently reading the same endpoint), the second request's 401 finds the retryKey already claimed by the first and is excluded from the `if` block entirely — including the `_subscribeTokenRefresh` queueing path that's supposed to let it wait for the first request's shared refresh and retry with the new token. It falls through to the terminal `throw new SpinrApiError(...)` instead.

This was never caught because the test that pins exactly this behavior (`client.refresh.test.ts`'s "deduplicates concurrent refresh calls — only one refresh in-flight") has existed but was never run by any CI workflow, for the same reason C42-B exists.

## 3. Fix / remediation

Replaced the per-URL `Set` with a per-call-chain boolean. `client.get/post/put/patch/delete` each gained an internal `_isRetry = false` parameter (not part of the public call surface — no external caller passes it), threaded through to a new `isRetryAttempt` parameter on `handleApiError`. The 401 branch's guard is now `!isRetryAttempt` instead of `!_inflight401Retries.has(...)`. Each method's own `retryFn` closure (the function `handleApiError` calls to retry) now passes `true` for this parameter, so:

- A call spawned *by* a retry (`isRetryAttempt === true`) that 401s again is excluded from re-entering the refresh block — the original loop-prevention guarantee is preserved, one refresh-retry per original call.
- Two concurrent *original* calls (`isRetryAttempt === false` on both) both correctly enter the block. The second one finds `_refreshPromise` already set by the first and correctly queues via `_subscribeTokenRefresh`, waiting for the shared refresh and retrying with the new token — the behavior the test (and the code's own comments) describe as intended, now actually true.

Separately (found while reproducing C42-B's documented "2 pii.test.ts failures", which was actually 1 real bug + 2 undocumented failures elsewhere):

- `shared/utils/pii.ts`'s `redactCoords` had a real rounding bug: `Math.round(n * 10) / 10` rounds `.5` ties toward `+Infinity` (JS's documented `Math.round` behavior), not away from zero, so `redactCoords(-106.65)` returned `"-106.6"` instead of the expected `"-106.7"`. Fixed by rounding the magnitude and reapplying the sign.
- `shared/api/__tests__/client.authHeader.test.ts` was missing a `jest.mock('../../config/spinr.config', ...)` call that all three sibling test files in the same directory already have (to avoid the real module's `expo-constants` import throwing once `react-native` is also mocked). Added it, matching the existing pattern exactly — a test-file fix, not a product-code fix.

CI wiring itself: added `roots: ['<rootDir>', '<rootDir>/../shared']` to `rider-app/jest.config.js`. No `.github/workflows/ci.yml` change was needed — the existing `rider-app-test` job's `yarn test --ci --coverage --forceExit --reporters=default` step now picks up `shared/`'s suites automatically.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (frontend auth path), but wide within it** — `shared/api/client.ts`'s `client` object (`get`/`post`/`put`/`patch`/`delete`) is the sole HTTP client used by both rider-app and driver-app for every backend call, so this touches the retry path for rides, payments, wallet, corporate, safety/SOS (SOS is explicitly exempted from this path already, unchanged), and every other authenticated endpoint.

- **Who else calls `client.get/post/put/patch/delete`:** every screen and store in both apps, via the shared `api` default export. Grepped for any caller already passing a 3rd (or 4th, for `post`/`put`/`patch`) positional argument — `grep -rn "api\.\(get\|post\|put\|patch\|delete\)(" rider-app/ driver-app/ shared/ --include="*.ts" --include="*.tsx"` filtered to 3+-arg call sites returned zero matches. The new `_isRetry` parameter is additive and defaults to `false`, so every existing call site is unaffected — this is not a breaking API change to `client`'s public shape.
- **Could this regress a flow that currently works?** The single-request-401 path (no concurrency) is unchanged: `isRetryAttempt` is `false` on the first call exactly as `_inflight401Retries.has(...)` was `false` before, so the "retries the original request after a successful token refresh", "does not retry when refresh callback returns false", "DOES force a logout on 401 when no refresh path exists", and "does not trigger a refresh loop on /auth/refresh itself" tests all pass unchanged (verified — see below). The only *behavior change* is the previously-broken case: a second concurrent same-URL request during a refresh now correctly waits and retries instead of throwing immediately. That is a **fix**, not a new regression — before this change, any two near-simultaneous 401s to the same endpoint (a real occurrence: rapid re-fetches, a screen's own retry logic, two independent components reading the same resource) meant the second one always failed with a raw 401 error even when the refresh succeeded milliseconds later.
- **Interaction with background loops / ride state machine / money deltas:** none directly — this is a frontend HTTP client concern, not backend state. Indirectly, any authenticated mutation (fare confirm, wallet top-up, ride actions) that happened to race a token refresh benefits from the fix rather than being put at new risk.
- **`_inflight503Retries`** (the sibling Set for 503 retries) is untouched — different code path, different guard, not implicated in this bug or fix.

## 5. User-experience effect

Rider and driver apps: **fixes** a pre-existing (never-shipped-correctly) bug — a rider or driver whose access token happened to expire while two requests to the same endpoint were in flight would previously see one succeed and one fail with a raw session/auth error, potentially requiring a manual retry or reload. After this fix, both requests correctly wait for the shared token refresh and succeed. Not visible mid-session as a *change* in a way a user would consciously notice (no new UI, no new copy) — it removes an intermittent failure mode rather than adding new user-facing behavior. `redactCoords`'s fix only affects log-line precision, never rendered to a user (per CLAUDE.md, PII redaction is log/analytics-only).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/api/client.ts` | Replaced per-URL `_inflight401Retries: Set<string>` 401-retry-loop guard with a per-call-chain `isRetryAttempt` boolean threaded through `client.get/post/put/patch/delete`'s new internal `_isRetry` parameter and `handleApiError`'s new `isRetryAttempt` parameter | Fixes a real bug: two concurrent original requests to the same URL shared one Set key, so the second request's 401 skipped the refresh-queue path and threw instead of retrying |
| `shared/utils/pii.ts` | `redactCoords`'s rounding now rounds the magnitude and reapplies the sign, instead of `Math.round(n * 10) / 10` directly | `Math.round` rounds `.5` ties toward `+Infinity`, not away from zero — `redactCoords(-106.65)` returned `"-106.6"` instead of `"-106.7"` |
| `shared/api/__tests__/client.authHeader.test.ts` | Added the `jest.mock('../../config/spinr.config', ...)` call already present in its 3 sibling test files | Missing mock let the real module's `expo-constants` import throw once this file's own `react-native` mock was in place; test-file fix, not a product bug |
| `rider-app/jest.config.js` | Added `roots: ['<rootDir>', '<rootDir>/../shared']` | Wires `shared/**/__tests__` into rider-app's existing CI test step — the actual C42-B fix; everything else in this log is what wiring it in surfaced |

## 7. Before / after

```ts
// Before — shared/api/client.ts (401 branch, abbreviated)
const _inflight401Retries = new Set<string>();

const handleApiError = async (response, method, url, retryFn?) => {
  // ...
  if (response.status === 401 && _refreshCallback && retryFn && !isSosUrl(url)
      && !_inflight401Retries.has(`${method} ${url}`)) {
    const retryKey = `${method} ${url}`;
    _inflight401Retries.add(retryKey);
    try {
      // ... refresh + queue logic ...
    } finally {
      _inflight401Retries.delete(retryKey);
    }
  }
};

// client.get, unchanged shape:
async get(url, config) {
  // ...
  if (!response.ok) return handleApiError(response, 'GET', url, () => client.get(url, config));
};
```

```ts
// After — shared/api/client.ts (401 branch, abbreviated)
const handleApiError = async (response, method, url, retryFn?, isRetryAttempt = false) => {
  // ...
  if (response.status === 401 && _refreshCallback && retryFn && !isSosUrl(url) && !isRetryAttempt) {
    try {
      // ... same refresh + queue logic, no Set to add/delete ...
    } catch { /* ... */ }
  }
};

// client.get gains the internal retry flag, passed true only from its own retryFn:
async get(url, config, _isRetry = false) {
  // ...
  if (!response.ok) return handleApiError(response, 'GET', url, () => client.get(url, config, true), _isRetry);
};
```

```ts
// Before — shared/utils/pii.ts
const fmt = (n: number): string =>
  Number.isFinite(n) ? (Math.round(n * 10) / 10).toFixed(1) : '?';
// redactCoords(-106.65) -> "-106.6" (wrong: -1066.5 rounds toward +Infinity, i.e. -1066)

// After
const fmt = (n: number): string =>
  Number.isFinite(n)
    ? (Math.sign(n) * (Math.round(Math.abs(n) * 10) / 10)).toFixed(1)
    : '?';
// redactCoords(-106.65) -> "-106.7" (correct: rounds away from zero)
```

## 8. Rollback plan

`git revert` is sufficient and safe here — none of this touches persisted data, Stripe charges, wallet deltas, or ride state; it's a frontend HTTP-client retry-path fix plus a CI test-discovery config change. No feature flag needed: the fixed behavior (queueing a concurrent request behind an in-flight refresh) is strictly a correctness fix over the previous behavior (throwing immediately), with no new failure mode introduced — reverting returns to the pre-existing bug, not to a worse state. If reverted, `rider-app/jest.config.js`'s `roots` addition should be reverted together (or `shared/`'s tests would start running again against the pre-fix `client.ts` and immediately fail CI on the reverted code, which is the correct signal, not a bug).

## 9. Verification performed

- [x] Automated tests run — unit only (this is a pure frontend change, no backend/integration/e2e surface touched):
  - rider-app: `yarn test --ci --coverage --forceExit --reporters=default` (the exact CI invocation) — 133 suites / 1875 tests green, exit code 0.
  - driver-app (config untouched, re-verified because `client.ts` is a shared dependency): `yarn test --ci --coverage --forceExit --reporters=default` — 116 suites / 1315 tests green.
  - Both apps: `yarn tsc --noEmit` clean.
  - Target test (`client.refresh.test.ts`'s "deduplicates concurrent refresh calls") re-run 3× standalone post-fix — deterministic pass every time.
  - One unrelated flake (`rideOptionsScreen.test.tsx`, the documented C37/C41-class leaked-timer flake) hit once in a full-suite run; confirmed pre-existing and unrelated via standalone re-run (119/119 pass) and a clean second full-suite run, per CLAUDE.md's re-run-once-to-confirm-a-flake guidance.
- [ ] Manual repro steps followed in staging — not done; no staging environment access from this session. The fix is verified against the existing unit test suite, which already pins the exact concurrent-401 scenario this fix addresses.
- [x] Blast-radius grep performed — searched for any external caller of `client.get/post/put/patch/delete` already passing a 3rd/4th positional argument (`grep -rn "api\.\(get\|post\|put\|patch\|delete\)(" rider-app/ driver-app/ shared/ --include="*.ts" --include="*.tsx"` filtered to 3+-arg calls): zero matches, confirming the new optional parameter is fully backward-compatible.
- [x] Reviewed against relevant CLAUDE.md convention(s) — auth: rider/driver role and token handling; the fix does not touch the JWT trust model or token-lifetime conventions, only the client-side retry/dedup mechanics around an already-issued 401.
- [x] Feature-flagged if user-visible and non-trivial — not applicable; this is a bug fix restoring already-intended behavior (the code's own pre-existing comments describe the dedup-queueing behavior as the design), not new user-visible functionality, and CLAUDE.md's flagging guidance is for new/changed UX, not for a retry-path correctness fix with no flag precedent in this file.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-level remediation needed)
- [x] Blast radius is stated, not assumed (single-surface — frontend auth/HTTP-client path — grepped for external callers, zero found passing extra args)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (Section 5 above states explicitly: fixes an intermittent failure mode, no new UI/copy, not perceptible as a "change" to a user beyond one fewer failure case)

## What was NOT verified

- Not tested against a real backend or real Supabase-issued tokens — the existing unit test suite mocks `fetch` and the refresh callback entirely; this fix was verified against that same mocked harness (which already pins the intended behavior), not against a live token-expiry scenario in staging.
- Driver-app's own test suite mocks `@shared/api/client` and other `@shared/*` paths entirely (its `moduleNameMapper`), so driver-app's 116/116 green result confirms `client.ts`'s new signature doesn't break driver-app's *type-checking or its own mocked test assumptions* — it does not exercise the real fixed 401-dedup logic through driver-app's test harness. The real logic is exercised only via rider-app's `shared/api/__tests__/client.refresh.test.ts`, which is the file that actually pins this behavior.
- No visual regression tooling exists for either mobile surface (per CLAUDE.md) — not applicable here since this change has no UI surface at all.
