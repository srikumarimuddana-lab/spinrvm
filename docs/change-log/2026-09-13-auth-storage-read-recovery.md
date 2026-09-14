# Secure storage read recovery

Date: 2026-09-13. Domain: auth. Surfaces: shared, driver-app, rider-app. Finding: F2, September 13 test rides.

| Field | Detail |
|---|---|
| Issue | A failed SecureStore read during cold start deleted a valid persisted refresh token. |
| Root cause | The storage adapter returned null for both a missing key and an exception. initialize treated both as logout and called clearAuthStorage. Reproduced in Jest; not proven to be the trigger on the incident device. |
| Fix | Distinguish unavailable (undefined) from absent (null); preserve credentials, report a fixed non-sensitive error to console/Sentry, and mark initialization recoverable. Do not replay potentially stale in-memory tokens when storage cannot be read. Successful absence clears recovery. |
| Risk / blast radius | Shared store used by both apps. Direct token consumers: both OTP/reactivation screens; shared API client/upload; driver backgroundLocation/sessionTeardown/Android Auto; driver dashboard and rider ride/AI stores. Store readers include both layouts, index/login/profile/account/settings screens and useAuth hooks. No known-forks pair applies to authStore. The 401 rotation retry now preserves the session if storage becomes unreadable during its recheck. |
| User experience | Driver sees the existing Reconnecting/retry UI on storage failure instead of losing credentials. Rider credentials also survive, but rider index currently routes to login on any missing access token and has no recovery UI; a later cold start can recover. No new copy or screen is added. Visible only on the failure path. |
| Rollback | Republish prior mobile JS bundle; there is no storage deletion or server-data change to undo. No existing flag controls the storage adapter. A previously deleted credential cannot be restored by this patch. |
| Verification | Before fix: four regression cases failed, including iOS and Android token deletion. After fix: 18 tests passed across initialize and refreshRace. Tests import the real shared store and mock device storage/API; native device/build validation is pending. |
| Not verified | Actual incident Keychain error, installed OTA SHA, real iOS Keychain failure/relaunch, Sentry delivery. No active visual regression tooling exists in either mobile app: UI effects were reasoned about, not screenshotted. Production exports/build results are recorded in the final audit. |

| File | Change | Why |
|---|---|---|
| shared/store/authStore.ts | Read outcome and recovery guards | Preserve a recoverable session |
| driver-app/__tests__/store/authStore.initialize.test.ts | iOS/Android failure, retry, active refresh, absence cases | Reproduce destructive cleanup and pin recovery |
| This document | Impact record | Live-testing release gate |

Before: `catch { return null; }` followed by `await clearAuthStorage()`.

After: `catch { /* report */ return undefined; }`; `storedRefresh === undefined` returns with `sessionRecoverable: true`, leaving storage untouched.
