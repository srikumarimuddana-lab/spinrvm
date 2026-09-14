# Change Impact & Risk — logout() must report a failed marker write, not reject on it

**Date:** 2026-09-13
**Surfaces:** rider-app, driver-app (live-tested) — via `shared/store/authStore.ts`
**Related:** F2 in `docs/audit/2026-09-13-driver-app-mid-ride-process-death.md`; revises the `logout()`
half of `eaa90b29c` / `docs/change-log/2026-09-13-auth-storage-write-failure.md`

## Issue/gap identified

`eaa90b29c` wrapped `logout()`'s session-ended marker write so a failure would surface, leaving
`logout()` able to **reject**. Roughly seven callers do `await logout(); router.replace('/login')` with no
`catch`, so on a SecureStore write failure the navigation never ran and the user was left sitting on a
screen whose store had just been nulled — signed out in state, still on the signed-in screen.

Callers affected: `rider-app/app/(tabs)/account.tsx:333`, `rider-app/app/profile-setup.tsx:161`,
`rider-app/app/privacy-settings.tsx:132`, `driver-app/app/index.tsx:82` and `:111`,
`driver-app/app/driver/(tabs)/profile.tsx:291`, `driver-app/app/become-driver.tsx:636`.

## Root cause

`storage.setItem()` throws a wrapped error on a SecureStore write failure, and `logout()`'s
`try { … } finally { … }` had no `catch` — so the rejection escaped after the teardown ran.

## Fix/remediation

Catch it. Report loudly, do not rethrow. **Chosen deliberately over the alternative** of guarding all
seven call sites with `try/finally`: rejecting protected nothing — the local teardown *and*
`_runLogoutCallbacks()` (which tears down driver location) already run via the `finally` either way — so
the rejection's only observable effect was breaking navigation. One file fixes every caller; seven files
would have preserved a signal with no protective value.

Visibility is preserved and arguably improved. `storage.setItem()` already logs and `captureMessage`s the
raw write failure (`authStore.ts:116-117`), so the failure reaches Sentry regardless. `logout()` now adds
a *distinct* message for the logout-specific consequence, which a generic "write failed" does not convey:
headless contexts that cannot read this store use the marker to know the session ended, so a missing
marker can leave background location tracking armed after sign-out (`shared/auth/sessionMarker.ts`).

## Before/after

```diff
  try {
    await storage.setItem(SESSION_ENDED_KEY, '1');
+ } catch (e) {
+   console.error('[Auth] session-ended marker write failed; headless tracking may remain armed:', e);
+   captureMessage('session-ended marker write failed', 'error', { tags: { domain: 'auth' } });
  } finally {
    set({ user: null, driver: null, token: null, … });
    …
  }
```

## Risk & impact on existing functionality

- **`logout()` no longer rejects on this path.** Every caller that previously had to survive a rejection
  now simply proceeds. No caller depended on the rejection: none of the seven had a `catch`, and the two
  in-library invocations (`shared/api/client.ts:977`, `:1183`) were already un-awaited or inside a `catch`.
- **Knock-on, intended:** `initialize()` no longer rejects via this path either. The rejection originated
  in `logout()` and propagated out through `refreshTokens()` → `initialize()`. `dcfb7d624`'s test asserted
  that rejection; it is updated to assert a clean resolve, with its state assertions (settled flags, full
  wipe, failure reported) unchanged — those were always the test's real subject.
- **`initialize()` can still reject** via `setTokens()` (a refresh-token write failure). That is a
  different path, is unchanged by this commit, and remains covered by the `it.each(['ios','android'])`
  test. The `Promise.all` guard added in `d3a5356fb` therefore remains necessary.
- **`setTokens()` is untouched** — the other half of `eaa90b29c` (publish only after durable persistence)
  stands exactly as written.
- Teardown ordering is unchanged; only the escape of the exception changed.

## User experience effect

Visible only in the failure case, and strictly an improvement: signing out with a failing keychain now
actually navigates to `/login` instead of stranding the user on a nulled screen. No change to any success
path. Note this *is* a behaviour change to a live-tested flow (sign-out), which is why it carries its own
entry rather than riding along in another commit.

## Files modified

| File | What changed | Why |
|---|---|---|
| `shared/store/authStore.ts` | `logout()` catches the marker-write failure, reports it, does not rethrow | A rejection stranded ~7 callers mid-sign-out and protected nothing |
| `driver-app/__tests__/store/authStore.initialize.test.ts` | Two tests updated from `.rejects.toThrow()` to `.resolves`, each asserting the failure is still reported | The contract changed; silence would be the real defect, so both tests still assert `console.error` fired |

## Rollback plan

Remove the `catch` block (restoring the bare `try/finally`) — one hunk, code-only, no persisted state, no
schema, no server behaviour. A revert restores the prior rejecting behaviour exactly, with nothing to
reconcile. No feature flag: this path only executes when a SecureStore write is already failing.

## Verification performed

- driver-app `__tests__/store` — **5 suites / 38 tests pass** (includes both updated tests).
- rider-app + shared auth-adjacent set (`accountScreen`, `api-client-401-refresh`, `api-client-503-retry`,
  all of `shared/api/__tests__`) — **8 suites / 81 tests pass**.
- `npx tsc --noEmit` — both apps.

## What was NOT verified

- **No production build was run** for either app — only `tsc --noEmit` and jest.
- **The seven call sites were not exercised end-to-end.** Correctness is argued from the fact that
  `logout()` now always resolves, not from driving each screen's sign-out with a failing keychain.
- **Not reproduced on a physical device** with a genuinely failing SecureStore write; the rejection is a
  jest mock.
- **The headless-tracking consequence is reported, not fixed.** If the marker write fails, a headless
  context can still miss the session-ended signal. `_runLogoutCallbacks()` tears down driver location in
  the same `finally`, so the primary protection holds, but the marker remains best-effort — unchanged from
  before this commit, and worth a separate look.
- rider-app/driver-app have no visual-regression tooling; visual impact (expected nil) was reasoned about,
  not screenshotted.
