# Change Impact & Risk Log — authStore: Logout/Login Race Fix

**Date:** 2026-09-20
**Author:** Claude Code (session), on behalf of ittalenthire.ca@gmail.com
**Surfaces:** shared (`shared/store/authStore.ts` — consumed by both driver-app and rider-app)
**Domain:** auth (token rotation / session lifecycle)

## Issue/gap identified
`driver-app/__tests__/store/authStore.refreshRace.test.ts` had 2 failing tests:
1. `does not let delayed go-offline cleanup wipe a new login` — timed out (15s) waiting on an operation that never resolved.
2. `revokes persisted background credentials even if foreground memory has no access token` — the `/auth/logout` revoke POST never fired (0 calls).

## Root cause
Both are a real regression introduced by commit `09a1ab8` ("fix(auth): stop sign-out-all hanging before the login screen", 2026-09-16), confirmed via git archaeology — not stale tests. That commit's own Change Impact Log only ran `authStore.initialize.test.ts`, never this file, so it silently broke two invariants:

1. **Deadlock**: `09a1ab8` restructured `logout()` to enter the session lock immediately and hold it for the full go-offline network round trip. The prior design fully awaited go-offline *before* entering the lock, leaving a window where a concurrent `setTokens()` (new login) could win the lock queue first and let logout's `generation !== loginGeneration` staleness check correctly see the newer login. After `09a1ab8`, logout grabs the lock queue slot synchronously and holds it, so a concurrent `setTokens()` gets stuck queued behind a logout that can't finish — deadlock.
2. **Revocation gap**: `09a1ab8` introduced a single `liveCredential` flag (gated on foreground token presence only) reused to gate both the go-offline PUT and the `/auth/logout` revoke POST. When foreground memory has no token but a background-rotated refresh token is persisted in SecureStore, the revoke POST was skipped entirely — leaving a valid, unrevoked refresh token after "logout."

## Fix/remediation
In `shared/store/authStore.ts`:
1. `setTokens()`: `loginGeneration++` now runs synchronously **before** `withSessionLock(...)` is called, so a logout already queued ahead of it is guaranteed to observe the new generation the instant its own turn runs, regardless of network timing.
2. `logout()`: split `liveCredential` into `skipServerRevoke` (gates only the `/auth/logout` revoke POST, independent of foreground token presence) vs. keeping the foreground-token check specific to the go-offline PUT gate (which authenticates off the in-memory token only, so there's no point attempting it without one).
3. Removed `await goOffline` from the generation-mismatch early-return branch in `logout()` — it's already fire-and-forget with errors swallowed; awaiting it there was what re-introduced the deadlock (holding the lock/queue slot for the network call's duration defeats the point of the generation fence).

## Risk & impact on existing functionality
- **Blast radius:** `shared/store/authStore.ts` is imported by both `driver-app` and `rider-app` (grepped: driver-app's `authStore.refreshRace.test.ts`/`authStore.initialize.test.ts`, rider-app's `auth.integration.ts`/`api-client-401-refresh.test.ts` and every screen that reads `useAuthStore`). Both apps' login/logout flows are affected by this shared module.
- Does **not** reintroduce the sequential-PUT-then-POST latency `09a1ab8` targeted: `authStore.initialize.test.ts`'s "fires go-offline and /auth/logout together" test still passes — only the generation-mismatch early-return path stopped awaiting go-offline; the normal path's `Promise.all([goOffline, serverLogout])` is untouched.
- Adversarial security review (`spinr-security-auditor`) confirmed: the revoke POST always reads the refresh token fresh from storage at execution time inside the lock (never a value captured before a concurrent rotation), and a logout correctly identified as stale (the normal logout-then-login ordering this fix targets) never constructs or runs the revoke call at all — so it cannot revoke a newer login's token.
- The reviewer flagged one **pre-existing, not-introduced-by-this-diff** edge case as a warning, not a blocker: if `setTokens()` and a bare `logout()` (no `revokeServerSession:false`) are ever called in the *reverse* order (new-login-then-logout, not the logout-then-login order this fix targets), the generation fence correctly treats the logout as current and tears down the just-established session — which is arguably correct behavior for that literal call order, but the diff's own code comments could be read as claiming broader protection than that. No current caller triggers this ordering (`shared/api/client.ts`'s two `logout()` call sites are either `revokeServerSession:false` or the definitive-401 case, not login-adjacent). Recommend a follow-up regression test pinning this ordering's intended behavior for documentation purposes — not required for this fix to be safe.
- The reviewer also flagged that `withSessionLock` is a real mutex only on driver-app (native SQLite lock) — on rider-app it's a no-op passthrough (`work => work()`). The fix is correct today because the generation compare/bump is synchronous at call time, before either body reaches the lock, but this is fragile: a future change moving either check behind an `await` would silently reintroduce a real race on rider-app specifically. Flagging for future editors; not addressed in this fix (out of scope — no code change needed today).

## User experience effect
Fixes a real bug: previously, a rider or driver signing out while a background-rotated refresh token existed (foreground token cleared but a background refresh had rotated credentials) would not have their server-side refresh token revoked — the token remained valid after the user believed they'd logged out. Also fixes a deadlock that could hang a fast logout-then-login sequence. No new UI/copy change.

## Files modified
| File | What changed | Why |
|---|---|---|
| `shared/store/authStore.ts` | `setTokens()` generation bump moved outside the lock; `logout()`'s revoke-gate split from the go-offline gate; `await goOffline` removed from the stale-generation early return | Fix deadlock + close revocation gap, both from `09a1ab8` |

## Before/after snippet
Before:
```ts
setTokens: (token, refreshToken, expiresIn, csrfToken) => withSessionLock(async () => {
  loginGeneration++;
  await publishTokensUnlocked(token, refreshToken, expiresIn, csrfToken, true);
}),
...
const liveCredential = options?.revokeServerSession !== false && !!token;
const goOffline = liveCredential && driver?.id ? api.put(...).catch(...) : Promise.resolve();
return withSessionLock(async () => {
  if (generation !== loginGeneration) {
    await goOffline;
    return;
  }
  ...
  const serverLogout = (async () => {
    if (!(liveCredential && (persistedAccess || token))) return;
    ...
```

After:
```ts
setTokens: (token, refreshToken, expiresIn, csrfToken) => {
  loginGeneration++;
  return withSessionLock(async () => {
    await publishTokensUnlocked(token, refreshToken, expiresIn, csrfToken, true);
  });
},
...
const skipServerRevoke = options?.revokeServerSession === false;
const goOffline = !skipServerRevoke && driver?.id && token ? api.put(...).catch(...) : Promise.resolve();
return withSessionLock(async () => {
  if (generation !== loginGeneration) {
    return; // no await goOffline — fire-and-forget, already swallows errors
  }
  ...
  const serverLogout = (async () => {
    if (skipServerRevoke || !(persistedAccess || token)) return;
    ...
```

## Rollback plan
`git revert` — pure code change to a shared client-side store, no server-side state or migration involved. If reverted, the deadlock and revocation gap both return (this is a regression fix, not new behavior), so a revert should be paired with reverting to a pre-`09a1ab8` state or an equivalent fix, not left as a bare revert long-term.

## Verification performed
- `npx jest __tests__/store/authStore.refreshRace.test.ts` (driver-app) — **14/14 pass** (was 2 failing).
- `npx jest __tests__/store/authStore.initialize.test.ts` (driver-app) — **26/26 pass**, confirming no regression of `09a1ab8`'s own "logout/logoutAll" scenarios including the hang it fixed.
- Full driver-app suite (`npx jest`, no path filter) — 1485 passed / 1 failed / 34 suites failed to load, both confirmed pre-existing and unrelated (identical with the fix stashed out): 34 suites fail to *load* due to an unrelated `react-test-renderer` version mismatch (19.3.0 expected, 19.2.3 found), and one `driverProfileScreen.test.tsx` failure is a separate pre-existing bug in that screen/test.
- rider-app: `auth.integration.ts` (12 tests) + `api-client-401-refresh.test.ts` (5 tests) — **17/17 pass**, no regression on the sibling app's use of the same shared store.
- `spinr-security-auditor` adversarial review — **SAFE TO MERGE**, no blockers. Confirmed the revoke POST always reads the refresh token fresh from storage (never a pre-rotation snapshot), confirmed the no-credential case is a safe no-op, and confirmed the fix does not reintroduce a token-revocation-of-a-newer-login risk in the ordering this fix targets. Two non-blocking warnings noted above (pre-existing reverse-ordering edge case; `withSessionLock`'s rider-app no-op fragility) — neither is a regression from this diff.

## What was NOT verified
- Not run against a real backend/Supabase — these are pure unit/store tests with mocked `api`/`storage`.
- Did not add the regression test the security reviewer recommended (pinning the reverse login-then-logout ordering's intended behavior) — flagging as a follow-up, not blocking this fix, since no current caller triggers that ordering.
- Did not investigate the unrelated pre-existing `driverProfileScreen.test.tsx` failure or the `react-test-renderer` version mismatch — both confirmed unrelated to this change and out of scope.
