# Change Impact & Risk — cold-start init must not abort when auth init throws

**Date:** 2026-09-13
**Surfaces:** rider-app, driver-app (live-tested)
**Related:** F2/F4 in `docs/audit/2026-09-13-driver-app-mid-ride-process-death.md`; commits `b4f279e13`, `eaa90b29c`

## Issue/gap identified

`eaa90b29c` made `authStore.initialize()` re-throw a SecureStore failure. Both apps call it inside
`await Promise.all([initializeAuth(), ...])`, and `Promise.all` short-circuits on the first rejection —
so a Keychain failure silently skips every remaining cold-start step.

## Root cause

`Promise.all` rejects as soon as any input rejects; the remaining `await`s in the enclosing `try` never
run. The outer `catch` logs and moves on, so the skipped work fails **silently**. `initialize()` itself is
fine — it settles `isInitialized`/`isLoading` before throwing (`shared/store/authStore.ts:433`), so the
splash gate is not wedged and the `hideNativeSplash('watchdog')` fallback is not implicated. The defect is
purely in the two call sites.

What was being skipped:

| App | Skipped on a SecureStore failure |
|---|---|
| rider-app | **`hydrateActiveRide()`** — a rider mid-ride reopening the app sees no active ride; plus `initFirebaseServices`, `requestNotificationPermission`, cold-start marker, Android channels |
| driver-app | `initFirebaseServices`, `requestNotificationPermission`, the **cold-start relaunch marker** (this app's only relaunch counter — `_layout.tsx:470-474`), Android channels, **`ensureNotifeeReady()`** (ride-offer heads-up). A driver could go online and never be rung for an offer. |

## Fix/remediation

Catch the rejection on the `initializeAuth()` promise only, so one failure cannot abort the rest of
cold-start. Deliberately `console.error` rather than swallow — CLAUDE.md forbids silently swallowing auth
errors, and the visibility `eaa90b29c` intended is preserved.

## Before/after

```diff
- await Promise.all([initializeAuth(), initializeLocation()]);
+ await Promise.all([
+   initializeAuth().catch((e) => {
+     console.error('[Auth] initialize failed; continuing app init:', e);
+   }),
+   initializeLocation(),
+ ]);
```

(rider-app is the same change against its three-promise `Promise.all`, which also contains
`hydrateWorkProfile()`.)

## Risk & impact on existing functionality

Blast radius is deliberately **narrow**: the change is in the two app root layouts, **not** in
`shared/store/authStore.ts`, so no other consumer of the shared store is affected. I chose the call-site
fix precisely to avoid touching shared auth semantics used by ~98 files across both apps.

- `initializeAuth` is `useAuthStore.initialize` — unchanged here.
- `initializeLocation`, `hydrateWorkProfile` — unchanged; still reject as before.
- Behaviour when `initialize()` **resolves** (the overwhelmingly common path) is **byte-identical**.
- Only the rejection path differs: previously abort-rest-of-init, now log-and-continue.

Not addressed here (still open from the audit): `shared/api/client.ts:185-193` keeps its own
read-collapse (`catch { return false }`), and `logout()` can still reject at ~6 unawaited call sites.
Both are separate changes with their own blast radius.

## User experience effect

Only visible in the failure case, and strictly an improvement: a rider whose Keychain read fails mid-ride
now still gets their active ride restored; a driver still gets Firebase, notification permission, and the
Notifee ride-offer channel. No change to any success path, so nothing changes for a user mid-session under
normal conditions.

## Files modified

| File | What changed | Why |
|---|---|---|
| `rider-app/app/_layout.tsx` | `initializeAuth()` wrapped in `.catch` inside `Promise.all` | Stop a SecureStore throw from skipping `hydrateActiveRide()` et al. |
| `driver-app/app/_layout.tsx` | same | Stop it skipping Notifee/Firebase/relaunch-marker setup |

## Rollback plan

Revert the two `.catch` wrappers — the change is additive and self-contained, touches no persisted state,
no schema, and no server behaviour, so reverting restores prior behaviour exactly with no data to
reconcile. No feature flag was added: the change only executes on a path that is currently broken, so
flagging it would mean flagging between "broken" and "fixed".

## Verification performed

- `npx tsc --noEmit` — **clean, exit 0, both apps**.
- `npx jest __tests__/store` (driver-app) — **5 suites / 38 tests pass**.
- Like-for-like regression check on the 7 screen suites that failed in a first, noisy run:
  **1 failed / 122 passed with the edit reverted, and 1 failed / 122 passed with it applied — identical.**
  The original 10 failures were load-induced timeouts from running jest concurrently with two `tsc`
  processes; `driverSettingsScreen` passes in isolation.

## What was NOT verified

- **No production build was run** for either app (`eas build` / production JS export). Only `tsc --noEmit`
  and jest. CLAUDE.md treats those as *not* equivalent to a real build — stated explicitly.
- **The failure path itself was not exercised on a device.** No test forces `storage.getItem` to throw at
  `_layout` level; correctness is argued from `Promise.all` semantics plus the existing store-level tests,
  not from an observed repro. A regression test that mocks a throwing SecureStore at the layout level is
  the obvious follow-up and is not included here.
- **No visual-regression tooling exists for rider-app or driver-app**, so the (expected-nil) visual impact
  was reasoned about, not screenshotted.
- Not tested against live Supabase; no live session was exercised.
