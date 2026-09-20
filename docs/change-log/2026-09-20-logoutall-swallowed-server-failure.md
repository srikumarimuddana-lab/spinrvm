# Change Impact & Risk Log — `logoutAll()` Swallowed Server Failure, Making the Failure-Toast UX Unreachable

**Date:** 2026-09-20
**Author:** Claude Code (session), on behalf of ittalenthire.ca@gmail.com
**Surfaces:** driver-app, shared (auth)
**Domain:** auth (sign-out-of-all-devices / lost-stolen-phone recovery flow)

## Issue/gap identified
`shared/store/authStore.ts`'s `logoutAll()` never rejects, even when the `/auth/logout-all` server call fails. `driver-app/app/driver/(tabs)/profile.tsx`'s `handleLogoutAll` wraps `await logoutAll()` in a `try/catch` that shows a "Sign Out Failed" toast and keeps the driver on-screen on failure — but that `catch` branch was unreachable in production, since `logoutAll()` always resolved.

## Root cause
Found during today's `/full-audit` fleet review of this session's merged PRs, independently flagged by `spinr-edge-case-reviewer` and confirmed by reading the code directly. `logoutAll()` (added for B-P1-13) wraps its `/auth/logout-all` POST in a `try/catch` that only `console.log`s in `__DEV__`, then unconditionally falls through via `finally` to a local `logout({ revokeServerSession: false })` call — itself designed to never throw, per its own long-standing "a flaky network must not block sign-out" contract. Net effect: a real network failure calling `/auth/logout-all` (1) silently fails to bump `token_version`/revoke refresh tokens server-side, (2) still clears the local session, (3) still lets the caller believe the operation succeeded — the exact scenario `handleLogoutAll`'s failure-path test (PR #5526, merged earlier today) assumed was reachable, but it wasn't: that test mocks the whole `authStore` module rather than exercising the real swallow-in-`finally` code, so it stayed green regardless.

This directly affects the "sign out of all devices, my phone was stolen" flow — a driver hitting a real network hiccup while trying to kill every other session got false assurance that it worked.

## Fix/remediation
`logoutAll()` now tracks whether the `/auth/logout-all` POST itself failed (`serverCallFailed`), still runs local cleanup unconditionally in `finally` (unchanged — a driver must never be left thinking they're signed in), and re-throws afterward if the server call failed. This makes `handleLogoutAll`'s existing `catch` branch (chosen via this session's earlier `AskUserQuestion`: "Keep current: stay + error toast") actually reachable, without changing which UX behavior was chosen — the bug was that the code couldn't execute the already-decided behavior, not that the wrong behavior was decided.

## Risk & impact on existing functionality
- **Blast radius:** `logoutAll()` has exactly one caller in the entire app — `driver-app/app/driver/(tabs)/profile.tsx`'s `handleLogoutAll` (grepped `rider-app`, `driver-app`, `shared` for all callers). Rider-app has no UI entry point for "sign out of all devices," so this is isolated to driver-app.
- No change to the regular single-device `logout()` path, which intentionally still never throws (unrelated, unchanged contract).
- No change to local-cleanup-always-runs behavior — a driver is still never left in a state that looks signed-in after calling `logoutAll()`, regardless of server outcome.

## User experience effect
Driver-facing. Before: a failed "sign out of all devices" silently succeeded from the driver's point of view (no toast, no warning) even though other sessions may still be live. After: the driver sees the "Sign Out Failed" toast (copy unchanged, already shipped via commit `d1c106e`) when the server call fails, while their own local session still ends either way. This is a bug fix restoring already-decided-and-shipped UX, not a new behavior change — no new `AskUserQuestion` needed.

## Files modified
| File | What changed | Why |
|---|---|---|
| `shared/store/authStore.ts` | `logoutAll()` now tracks `serverCallFailed` and re-throws after local cleanup if the `/auth/logout-all` POST failed | Make the already-shipped failure-toast UX in `handleLogoutAll` actually reachable |
| `driver-app/__tests__/store/authStore.initialize.test.ts` | Added a regression test asserting `logoutAll()` rejects on a failed POST while still clearing local session state | Real coverage of the fix, exercising the actual `authStore.ts` code (not a fully-mocked module, unlike the existing driver-app UI test) |

## Before/after snippet
Before:
```ts
logoutAll: async () => {
  let revoked = 0;
  try {
    const res = await api.post(...);
    revoked = Number(res.data?.revoked_refresh_tokens ?? 0);
  } catch (error) {
    if (__DEV__) console.log('logout-all backend call failed:', ...);
  } finally {
    await get().logout({ revokeServerSession: false });
  }
  return { revoked_refresh_tokens: revoked };
  // never throws -- handleLogoutAll's catch branch is dead code
},
```

After:
```ts
logoutAll: async () => {
  let revoked = 0;
  let serverCallFailed = false;
  try {
    const res = await api.post(...);
    revoked = Number(res.data?.revoked_refresh_tokens ?? 0);
  } catch (error) {
    serverCallFailed = true;
    if (__DEV__) console.log('logout-all backend call failed:', ...);
  } finally {
    await get().logout({ revokeServerSession: false });
  }
  if (serverCallFailed) {
    throw new Error('logout-all server call failed');
  }
  return { revoked_refresh_tokens: revoked };
},
```

## Rollback plan
`git revert` — client-side TypeScript logic only, no data/schema/runtime backend state affected. Reverting restores the prior (silently-succeeding) behavior.

## Verification performed
- Added a new Jest test in `driver-app/__tests__/store/authStore.initialize.test.ts` exercising the real `shared/store/authStore.ts` module (not a mocked stand-in) with a rejected `/auth/logout-all` POST — confirms `logoutAll()` now rejects AND that local session state is still fully cleared.
- `npx jest __tests__/store __tests__/app/driverProfileScreen.test.tsx` in `driver-app/` — 80/80 passing (6 suites), including the new test and all pre-existing logout/logoutAll/driverProfileScreen coverage.
- `npx tsc --noEmit` clean in both `driver-app/` and `rider-app/` (shared file, checked both consumers).
- Grepped `rider-app`, `driver-app`, `shared` for every `logoutAll` reference to confirm blast radius (one caller, driver-app only).
- **Did not run a real production build** (`npm run build`/EAS build) — this is a logic-only change with no native dependency or build-config impact; `tsc --noEmit` + the full Jest suite is the verification performed, per CLAUDE.md's standard for a non-native-dependency change.

## What was NOT verified
- Not run against a real device/simulator — reasoned about via test suite and type-check, not manually tapped through in the app. No visual change (toast copy itself is unchanged, already shipped).
- Did not address the separate, non-blocking finding from today's `/full-audit` that the same generic "Sign Out Failed" toast copy is reused for both single-device and sign-out-all failures — tracked separately, not part of this fix's scope.
