# Change Impact & Risk Log — driver-app Profile Screen: Stale Logout-All Test

**Date:** 2026-09-20
**Author:** Claude Code (session), on behalf of ittalenthire.ca@gmail.com
**Surfaces:** driver-app (test-only — no production code changed)
**Domain:** auth (session lifecycle UX, driver-facing)

## Issue/gap identified
`driver-app/__tests__/app/driverProfileScreen.test.tsx`'s `handleLogoutAll confirms then signs out of every device and routes to /login even on failure` test failed: `expect(mockReplace).toHaveBeenCalledWith('/login')` got 0 calls after a rejected `logoutAll()`.

## Root cause
Not related to the `shared/store/authStore.ts` fix shipped separately today (different files, confirmed via `git log`/`git show`) — this is a stale test. Commit `d1c106e` ("fix(driver): report failed session cleanup before navigating", 2026-09-15) deliberately changed `driver-app/app/driver/(tabs)/profile.tsx`'s `handleLogout`/`handleLogoutAll` from a `try { await logoutAll(); } finally { router.replace('/login'); }` pattern (always navigate, regardless of outcome) to `try { await logoutAll(); router.replace('/login'); } catch { showToast('error', ...); }` (navigate only on success; show an error toast and stay on-screen on failure). The test file was never updated for this deliberate behavior change and still asserted the old always-navigate contract — its own inline comment explicitly described pre-`d1c106e` code that no longer exists.

This was escalated to the user via `AskUserQuestion` rather than resolved unilaterally, since it's a driver-facing UX/security decision on the "sign out of all devices" (lost/stolen phone) flow: should a failed sign-out-all leave the driver on-screen to retry (current, post-`d1c106e` behavior), or force navigation to `/login` regardless of server-side outcome (old, pre-`d1c106e` behavior the stale test expected)? **User decision: keep current behavior** (stay on screen + error toast) — this fix updates the test to match it.

## Fix/remediation
Split the single stale test into two, matching the current, intended behavior:
1. `handleLogoutAll confirms then signs out of every device and routes to /login` — success path, unchanged assertions (navigates on success).
2. `handleLogoutAll shows an error toast and stays on screen if sign-out-all fails` (new) — asserts `mockReplace` is **not** called with `/login` and `mockShowToast` **is** called with the exact error toast the real `catch` branch shows.

No production code changed — `profile.tsx`'s `d1c106e` behavior is confirmed intentional and is being kept as-is per explicit user direction.

## Risk & impact on existing functionality
- **Blast radius:** isolated to this one test file. No other test in the repo exercises `handleLogoutAll`'s failure path (grepped `mockLogoutAll` usage — only this one test file references it, and no other test asserts on `driverProfileScreen.tsx`'s logout-all navigation/toast behavior).
- `handleLogout`'s (single-device) analogous failure path was already left untested before this change and remains untested — out of scope for this fix, which only touches the logout-**all** test that was actually failing in CI.
- Does not touch `profile.tsx` or any other production code — the driver-facing behavior (stay on-screen + toast on failure) is unchanged from what `d1c106e` already shipped weeks ago; this fix only brings test coverage in line with reality.

## User experience effect
None — no production code changed. Confirms via test that the already-shipped behavior (driver stays on Profile screen with an error toast if "sign out of all devices" fails, rather than being forced to `/login` regardless of whether the server-side session revoke succeeded) is the intended, retained behavior.

## Files modified
| File | What changed | Why |
|---|---|---|
| `driver-app/__tests__/app/driverProfileScreen.test.tsx` | Split the stale combined test into a success-path test (unchanged assertions) and a new failure-path test asserting the toast/no-navigate behavior `d1c106e` actually shipped | Match current, user-confirmed-intentional screen behavior |

## Before/after snippet
Before:
```tsx
it('handleLogoutAll confirms then signs out of every device and routes to /login even on failure', async () => {
  mockLogoutAll.mockRejectedValue(new Error('network'));
  ...
  await act(async () => { await confirm.onPress().catch(() => {}); await flush(); });
  expect(mockLogoutAll).toHaveBeenCalled();
  expect(mockReplace).toHaveBeenCalledWith('/login'); // FAILS — current code doesn't navigate on failure
});
```

After:
```tsx
it('handleLogoutAll confirms then signs out of every device and routes to /login', async () => {
  // success path — navigates as before
});

it('handleLogoutAll shows an error toast and stays on screen if sign-out-all fails', async () => {
  mockLogoutAll.mockRejectedValue(new Error('network'));
  ...
  await act(async () => { await confirm.onPress(); await flush(); });
  expect(mockLogoutAll).toHaveBeenCalled();
  expect(mockReplace).not.toHaveBeenCalledWith('/login');
  expect(mockShowToast).toHaveBeenCalledWith('error', 'Sign Out Failed', 'Your session could not be closed. Please try again.');
});
```

## Rollback plan
`git revert` — test-only change, no runtime behavior affected.

## Verification performed
- `npx jest __tests__/app/driverProfileScreen.test.tsx` — **24/24 pass** (was 23 passed / 1 failed).
- Confirmed via `git log`/`git show` that this failure is unrelated to the `shared/store/authStore.ts` fix shipped separately today — different files, no overlap.
- Confirmed no other test in the repo exercises this code path (grepped `mockLogoutAll` and `handleLogoutAll` — only this test file).
- Product/UX decision (keep current stay-on-screen-and-toast behavior vs. revert to always-navigate) was explicitly escalated to and confirmed by the user before implementing, per CLAUDE.md's "escalate, don't silently ship" gate for a driver-facing auth/security flow.

## What was NOT verified
- Did not add a test for `handleLogout`'s (single-device) analogous failure path, which received the identical `d1c106e` treatment but was never tested for it either — out of scope for this fix (only the logout-**all** test was actually failing in CI).
- Not run against a real backend — pure unit/component test with mocked `logoutAll`/`showToast`/`router`.
