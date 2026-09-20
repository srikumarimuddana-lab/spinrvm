# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Author | Claude Code (agent), for ittalenthire.ca@gmail.com |
| Surface(s) | rider-app, driver-app, shared |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `mvapps/focused-tesla-0o88ku` (not yet pushed/opened as a PR by this session) |
| Related issue or gap ID | CR-2026-035 (issue #5516) |

## 1. Issue / gap identified

`driver-app-test` failed deterministically on `main` and on every PR touching `driver-app/`: 3 tests across `__tests__/app/driverProfileScreen.test.tsx` (`handleLogoutAll`) and `__tests__/store/authStore.refreshRace.test.ts` (two "rotation-race recovery" cases), one of which hung for the full 15s Jest timeout. Filed as CR-2026-035 while babysitting PR #5513, since the failure was unrelated to that PR's own diff but pre-existing and repo-wide.

## 2. Root cause

Three separate, real bugs in `shared/store/authStore.ts`'s `logout`/`setTokens` actions (used by both rider-app and driver-app) — not test-mock staleness:

1. **Deadlock.** `logout()` started the go-offline `PUT /drivers/{id}/status` outside the session lock ("so it overlaps /auth/logout rather than blocking it" per its own comment), but then re-awaited it via `Promise.all([goOffline, serverLogout])` *inside* the locked critical section anyway. A slow/hung go-offline call therefore held the session lock open, deadlocking any other locked operation queued behind it — e.g. a fresh `setTokens()` call from a new login racing a slow prior logout. This is what hung the "does not let delayed go-offline cleanup wipe a new login" test for 15s.
2. **Missed server-side revoke.** `logout()` computed `liveCredential = ... && !!token` synchronously from the in-memory Zustand `token`, *before* the locked callback ever reads the persisted `fg_access_token` from SecureStore. The inner gate for calling `/auth/logout` then checked `liveCredential && (persistedAccess || token)` — but since `liveCredential` had already latched `false` whenever in-memory `token` was null (e.g. a background task rotated a fresh token pair into SecureStore while foreground JS held none), the server-side revoke never fired even though a real, revokable credential existed. This is what made "revokes persisted background credentials even if foreground memory has no access token" assert 0 calls to `/auth/logout`.
3. **Generation-check ordering.** `setTokens()` incremented the module-level `loginGeneration` counter *inside* its own `withSessionLock` callback. `logout()` captures `generation = loginGeneration` synchronously before entering the lock, to detect and bail out of a stale logout superseded by a newer login. Because both actions share one FIFO session-lock queue, a `setTokens()` call issued after a `logout()` call couldn't bump the counter in time — the increment only took effect once `setTokens()` reached its own turn in the queue, by which point the earlier-queued `logout()` had already run to completion under the stale (not-yet-bumped) generation.

## 3. Fix / remediation

- `logout()`'s go-offline PUT is now genuinely fire-and-forget with respect to the session lock (never awaited inside `withSessionLock`), removing the deadlock.
- Introduced `revokeAllowed = options?.revokeServerSession !== false` (the caller's *intent* only, no token check) as the gate for the server-side `/auth/logout` call, combined with whichever credential (persisted or in-memory) is actually known by the time the locked callback runs. `liveCredential` (unchanged definition) is still used only to decide whether to fire the go-offline PUT, which still legitimately needs an in-memory token.
- `setTokens()` now increments `loginGeneration` synchronously, before calling `withSessionLock(...)`, mirroring how `logout()` already captures its own generation synchronously.
- Post-fix, `spinr-security-auditor` flagged that fully decoupling the go-offline PUT from the lock (fix #1) introduced a new *operational* risk: `logout()` could now resolve — and its caller navigate away / the app background — before that PUT completes, silently leaving `drivers.is_online = true` stuck server-side. Addressed by racing the PUT against `sessionWork` and a 3-second timeout (outside the lock, so it still can't reintroduce the deadlock), with the timer cleared as soon as anything settles so it never lingers.
- `driver-app/app/driver/(tabs)/profile.tsx`'s `handleLogoutAll` now always navigates to `/login` in a `finally`, even if `logoutAll()` throws — matching the test's documented intent for a "sign out of all devices" (lost/stolen-phone) flow: never leave the user sitting on an authenticated screen because a network error occurred, given `logoutAll()` itself already falls through to a local `logout()` on failure regardless.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface.** `shared/store/authStore.ts` is imported by both rider-app and driver-app. Grepped every call site of `logout()`/`setTokens()`/`logoutAll()` across both apps and `shared/`:
- rider-app: `utils/apiClient.ts`, `app/otp.tsx`, `app/reactivate-account.tsx`.
- driver-app: `app/become-driver.tsx`, `app/index.tsx`, `app/driver/(tabs)/profile.tsx`, `app/driver/settings.tsx`, `app/profile-setup.tsx`.
- shared: `shared/api/client.ts` (two call sites, including the 401-interceptor backstop), `shared/store/authStore.ts`'s own `logoutAll()`.
- Confirmed (via `spinr-security-auditor`) none of these pass `options` or depend on internal timing in a way any of the three fixes changes their observable contract. The two call sites that intentionally pass `revokeServerSession: false` (the 401 backstop and `logoutAll()`'s own fallback) are unaffected by the `revokeAllowed` change — it only widens the revoke call for callers that were *not* opting out, where a real background-rotated credential exists.
- `admin-dashboard/src/store/authStore.ts` is a separate, unrelated store — not in scope. `frontend/` is deprecated per `CLAUDE.md` — not touched.
- No interaction with `backend/core/lifespan.py`'s background loops, the ride state machine, or money/wallet deltas — this is a pure client-side auth/session-lifecycle fix.
- Confirmed no local-session-wipe race: the go-offline PUT is constructed and dispatched synchronously, before any `await`, so it always captures a valid Authorization header before `clearLocalSessionUnlocked()` (which nulls the in-memory token) can run.

## 5. User-experience effect

- Rider and driver: sign-out (single-device and "sign out of all devices") now completes without occasionally hanging when a slow network call for driver go-offline status overlaps a fresh login elsewhere (rare, session-management edge case — no normal-path UX change for a user who isn't actively racing a login against a logout).
- Driver-specific: "Sign out of all devices" now always lands on the login screen, even if the network revoke call fails — previously it would show an error toast and leave the driver on the profile screen while still nominally logged out locally in some failure paths. This is a safety-relevant fix for the lost/stolen-phone use case this button exists for.
- Not visible mid-session to someone already using the app in a normal flow — this only changes behavior during the sign-out/sign-in actions themselves.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/store/authStore.ts` | `logout()`: go-offline PUT no longer awaited inside the session lock (now raced with a bounded timeout outside it); `revokeAllowed` replaces the stale `liveCredential` gate for the `/auth/logout` call. `setTokens()`: `loginGeneration++` moved to before `withSessionLock(...)` is called. | Fix the deadlock, the missed server-side revoke, and the generation-ordering race — root causes of CR-2026-035's 3 failing tests. |
| `driver-app/app/driver/(tabs)/profile.tsx` | `handleLogoutAll`'s `onPress` now navigates to `/login` in a `finally`, not only on the success path of a `try`. | Match the lost/stolen-phone safety intent the failing test documents: never strand the user on an authenticated screen because the revoke-all network call failed. |
| `docs/change-log/2026-09-20-auth-logout-race-fixes-cr-2026-035.md` | This file. | Mandatory Change Impact Log for a live-tested auth surface. |

## 7. Before / after

```ts
// Before — logout() (shared/store/authStore.ts)
const liveCredential = options?.revokeServerSession !== false && !!token;
const goOffline =
  liveCredential && driver?.id
    ? api.put(`/drivers/${driver.id}/status`, { is_online: false }).catch(...)
    : Promise.resolve();

return withSessionLock(async () => {
  if (generation !== loginGeneration) { await goOffline; return; }
  const persistedAccess = await storage.getItem('fg_access_token');
  ...
  const serverLogout = (async () => {
    if (!(liveCredential && (persistedAccess || token))) return;
    ...
  })();
  await Promise.all([goOffline, serverLogout]);
  await clearLocalSessionUnlocked();
});
```

```ts
// After — logout() (shared/store/authStore.ts)
const revokeAllowed = options?.revokeServerSession !== false;
const liveCredential = revokeAllowed && !!token;
const goOffline =
  liveCredential && driver?.id
    ? api.put(`/drivers/${driver.id}/status`, { is_online: false }).catch(...)
    : null;

const sessionWork = withSessionLock(async () => {
  if (generation !== loginGeneration) { return; }
  const persistedAccess = await storage.getItem('fg_access_token');
  ...
  const serverLogout = (async () => {
    if (!(revokeAllowed && (persistedAccess || token))) return;
    ...
  })();
  await serverLogout;
  await clearLocalSessionUnlocked();
});

if (goOffline) {
  // bounded race against sessionWork + a 3s timeout, timer cleared on settle
  await new Promise<void>((resolve) => { /* ... */ });
}
return sessionWork;
```

```tsx
// Before — driver-app profile.tsx handleLogoutAll
onPress: async () => {
  try {
    await logoutAll();
    router.replace('/login' as any);
  } catch {
    showToast('error', 'Sign Out Failed', 'Your session could not be closed. Please try again.');
  }
},
```

```tsx
// After
onPress: async () => {
  try {
    await logoutAll();
  } catch {
    showToast('error', 'Sign Out Failed', 'Your other sessions may not have been fully revoked, but you have been signed out on this device.');
  } finally {
    router.replace('/login' as any);
  }
},
```

## 8. Rollback plan

`git-revert-safe` — no data migration, no destructive write, no server-side change. A plain revert of this commit restores the exact prior client-side behavior (including its bugs). No `app_settings`/feature flag involved; this is auth/session-lifecycle logic, not a gated feature.

## 9. Verification performed

- [x] Automated tests run: targeted (`driverProfileScreen.test.tsx`, `authStore.refreshRace.test.ts`, `authStore.initialize.test.ts` — 63/63 pass) and full suite for both apps after a clean `yarn install --frozen-lockfile` reinstall — driver-app 156/156 suites (1833 tests), rider-app 156/156 suites (2148 tests), zero regressions.
- [x] `tsc --noEmit` clean on both apps.
- [ ] Manual repro steps followed in staging — **not done**; no real device/staging environment available in this sandbox. All verification is via the Jest suites above (mocked `apiClient`/SecureStore), not a real backend or real SecureStore.
- [x] Blast-radius grep performed: every `logout()`/`setTokens()`/`logoutAll()` call site across rider-app, driver-app, and `shared/` (listed in §4).
- [x] Reviewed against relevant CLAUDE.md convention: ran `spinr-security-auditor` against the full diff (auth/session surface) — verdict **SAFE TO MERGE**; its one WARNING (the go-offline PUT could now silently never complete) was fixed with the bounded-race addition described above, not just documented.
- [x] Feature-flagged if user-visible and non-trivial: not flagged — this is a bug fix to existing sign-out behavior (deadlock/missed-revoke/premature-navigation), not a new feature, and CLAUDE.md's flag gate is for new/changed UX, not restoring already-intended behavior.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-layer dependency).
- [x] Blast radius is stated, not assumed (§4 — cross-surface, every call site enumerated).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 covers the one user-visible change: "sign out of all devices" now always reaches the login screen).
