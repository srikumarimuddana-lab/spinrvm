# Change Impact & Risk Log — driver location tracking survived sign-out

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (session `01TBFFJWqVUq5LQyomyWepKM`) |
| Surface(s) | driver-app, backend, shared (rider-app affected transitively) |
| Domain (Sentry tag) | `drivers`, `auth` |
| PR / commit link | `aafafc1`, `9525d9a`, `ecd8057`, `bf99e18`, `b7f5e84` on `claude/driver-location-logout-issue-26c9pu` |
| Related issue or gap ID | Reported in live app testing: "app is taking the location even after logout" |

## 1. Issue / gap identified

The driver app kept collecting, storing, and uploading GPS after the driver signed
out. Android continued showing the **"You're online and receiving ride requests"**
foreground-service notification to a signed-out user, and the server accepted the
uploads. Reported from live app testing, then confirmed by tracing the code.

## 2. Root cause

Location teardown was scoped to the **online toggle**, not to the **session**.
`stopBackgroundLocation()` / `stopGeofenceRecovery()` were called only from
`toggleOnline(false)` (`driver-app/hooks/useDriverDashboard.ts:1462-1463`).
`logout()` (`shared/store/authStore.ts`) never touched location.

`registerLogoutCallback` (`shared/store/authStore.ts:24`) exists for exactly this
and the rider app uses it (`rider-app/store/rideStore.ts:1227`). The driver app
never registered one — the only matches in `driver-app/` were Jest mocks.

Four independent mechanisms then kept it alive:

1. **Cached credential.** `stopBackgroundLocation` cleared `bg_access_token`
   behind an `if (!isRunning) return`, so a stop on an already-dead task (OS
   kill, or the logout path) left a live driver JWT on disk. The headless task
   kept uploading with it for the token's remaining ~15 minutes.
2. **Geofence resurrection.** The `spinr-geofence-recovery` exit handler runs
   headless with no access to the auth store. On each 500 m boundary crossing it
   re-started location updates and re-centred the fence — indefinitely, and even
   after the OS killed the app.
3. **No server-side session end.** `logout()` never called `POST /auth/logout`
   (which already existed, `backend/routes/auth.py:1634`). The refresh token
   stayed valid for its full 30 days and the access token for its remaining exp.
4. **No ingest check.** `/drivers/location-batch` re-read the driver row but
   never checked whether the session had ended, so the client was the only
   control.

Additionally, `spinr_trip_location_active_ride` was never cleared, so a sign-out
mid-ride left the SQLite outbox accumulating fixes tagged with the old `ride_id`,
to be uploaded under whichever account signed in next.

## 3. Fix / remediation

1. **Driver-app session teardown** (`utils/sessionTeardown.ts`) registers a logout
   callback that stops the task, disarms the geofence, and purges the outbox.
   Armed at module scope in `_layout.tsx`, not from a hook, because the API
   client's 401 interceptor can trigger a logout before any screen mounts.
2. **Headless session gating** (`utils/backgroundLocation.ts`) — `hasLiveSession()`
   reads the persisted auth tokens, so the headless contexts can see session
   state. Both the location task and the geofence exit handler now tear down
   instead of recording/re-arming. The location task gates *before*
   `recordNativeFix`, so nothing reaches SQLite. Credential cleanup in
   `stopBackgroundLocation` is now unconditional.
3. **Outbox purge** (`utils/tripLocationOutbox.ts`, `tripLocationRecorder.ts`) —
   `purgeAll()` drops points, quarantine, and sessions in one exclusive
   transaction, plus the active-ride pointer.
4. **Server-side session end** — `logout()` calls `POST /auth/logout`, which now
   also writes a session-revocation tombstone.
5. **Ingest guard** — `/drivers/location-batch` rejects a tombstoned session
   ahead of both the v1 and v2 writers.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface.** `shared/store/authStore.ts` and
`shared/api/client.ts` are consumed by both mobile apps.

### `/drivers/location-batch` — every caller enumerated

| Caller | Effect |
|---|---|
| `driver-app/hooks/useDriverDashboard.ts:530` (foreground transport) | Guarded; live session unaffected |
| `driver-app/utils/tripLocationTransport.ts:27` | Guarded; unaffected |
| `driver-app/utils/backgroundLocation.ts:209` (headless) | Guarded; the intended target |
| `loadtest/locustfile.py:297` | Uses live tokens — unaffected |
| `tests/test_cross_app_ride_lifecycle.py:335` | Passes |
| `driver-app/e2e/fixtures.ts:327` | Mock, no session id — fails open |

Backend suites re-run: `test_location_batch`, `test_p3_background_location`,
`test_period1_accumulation_endpoint`, `test_driver_location_redis_resilience`.

### `logout()` — every caller enumerated

| Caller | Change |
|---|---|
| `driver-app/app/driver/(tabs)/profile.tsx:233`, `settings.tsx:193` | User sign-out — now revokes server-side + tears down location |
| `shared/store/authStore.ts:281` (no refresh token) | `revokeServerSession:false` — credential already dead |
| `shared/store/authStore.ts:347` (refresh rejected 401) | `revokeServerSession:false` |
| `shared/api/client.ts:782` (G2 backstop) | `revokeServerSession:false` |
| `driver-app/utils/apiClient.ts:44` (refresh failed) | `revokeServerSession:false` |
| `logoutAll()` | `revokeServerSession:false` — `/auth/logout-all` already revoked everything |
| **rider-app** (all sign-out paths) | Now revokes server-side; previously never did |

The `revokeServerSession:false` opt-outs are not cosmetic. `logout()` calls
`api.*`, and a 401 from inside it queues behind the interceptor's in-flight
`_refreshPromise` — the deadlock the existing go-offline PUT comment
(`authStore.ts:268-271`) already warns about. Every path where the credential is
known-dead opts out.

### Regression risks accepted

- **Multi-device tombstone coupling.** `users.current_session_id` is a single
  column, so a second device logging in overwrites it and a later refresh can
  land both devices on the same `session_id`. `should_tombstone()` only
  tombstones when the logging-out token still matches that column, which covers
  the common case but not a driver signed in on two phones that have both
  refreshed onto the same id. Worst case: the second device's location uploads
  401 for up to `ACCESS_TOKEN_EXPIRE_MINUTES` (15 min), losing breadcrumbs.
  Bounded, and only reachable via an explicit user sign-out.
- **Sign-out mid-trip discards the pending GPS tail.** `purgeAll()` is the only
  caller that drops unacknowledged points. Everywhere else they are retained
  because billed distance and the SGI per-insurance-period audit are settled from
  them. Signing out mid-ride is already an abnormal action; leaving a signed-out
  driver's coordinates on a shared device is the worse outcome.
- **One extra network call on every sign-out**, both apps. Swallowed and
  non-blocking on failure.
- **`hasLiveSession()` treats an unreadable SecureStore as no session.** A
  keystore hiccup during a headless wake skips that re-arm. Self-heals via the
  foreground online-reconcile effect (`useDriverDashboard.ts:1542-1555`).
  `startBackgroundLocation` is deliberately **not** gated, so a SecureStore
  failure can never block a driver from going online.

### Not touched

Ride state machine, dispatch, fares, wallets, Stripe, corporate billing, the 16
background loops, and `get_current_user` (deliberately — a Redis round-trip on
every authenticated request would risk the auth-refresh <200 ms and dispatch
<2 s P95 SLAs). No migration; no schema change.

## 5. User-experience effect

**Driver (visible, and the point of the change):** after signing out, the Android
foreground-service notification disappears and the iOS location indicator turns
off. Previously both persisted, with the notification falsely reading "You're
online and receiving ride requests".

**Driver (mid-session):** nothing changes for a signed-in driver, online or
on-trip. No copy changes. No new UI.

**Rider:** no visible change. Sign-out now revokes the session server-side.

**Internal admin:** no visible change. A signed-out driver already flipped
`is_online=false`; they now also stop sending location, so the live-monitoring
map no longer shows a stale moving marker for a signed-out driver.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/utils/sessionTeardown.ts` | **New** — logout callback stopping tracking and purging the outbox | The missing session-scoped teardown |
| `driver-app/app/_layout.tsx` | Register teardown at module scope | Must be armed before any screen mounts |
| `driver-app/utils/backgroundLocation.ts` | `hasLiveSession()`; gate location task + geofence handler; unconditional credential cleanup | Headless contexts had no session visibility; cleanup was skippable |
| `driver-app/utils/tripLocationOutbox.ts` | `purgeAll()` | Raw coordinates survived sign-out |
| `driver-app/utils/tripLocationRecorder.ts` | `purgeAll()` wrapper; `purgeAll` added to the outbox type | Also clear the active-ride pointer |
| `driver-app/utils/apiClient.ts` | `revokeServerSession:false` on the refresh-failure logout | Credential already dead |
| `shared/store/authStore.ts` | `logout()` calls `POST /auth/logout`; optional `revokeServerSession` | Session never ended server-side |
| `shared/api/client.ts` | `revokeServerSession:false` on the G2 backstop | Avoid re-entering the interceptor |
| `backend/utils/session_revocation.py` | **New** — tombstone write/read + `should_tombstone` | Positive-evidence revocation |
| `backend/utils/redis_client.py` | Register `revoked_session:` prefix | Admin Redis accounting |
| `backend/dependencies/__init__.py` | `get_token_session_id` dependency | Expose the claim without the payload |
| `backend/routes/auth.py` | Tombstone the session on logout | Make the access token rejectable |
| `backend/routes/drivers/location.py` | `_guard_revoked_session` ahead of the v1/v2 split | Server-side enforcement |
| `backend/routes/drivers/_deps.py` | Re-export the new dependency | Dual-import pattern |
| `backend/tests/test_session_revocation.py` | **New** — 12 tests | Fail-open contract |
| `backend/tests/test_location_batch_revoked_session.py` | **New** — 8 tests | Guard + kill switch + fail-open |
| `driver-app/utils/__tests__/sessionTeardown.test.ts` | **New** — 9 tests | Teardown ordering + resilience |
| `driver-app/utils/__tests__/backgroundLocation.test.ts` | +11 session-gating tests; session-aware SecureStore mock | Regression cover |
| `driver-app/utils/__tests__/tripLocationOutbox.test.ts` | +3 purge tests; unqualified-DELETE handling in the memory DB | Regression cover |
| `driver-app/utils/__tests__/tripLocationRecorder.test.ts` | +2 purge tests | Regression cover |

## 7. Before / after

### Credential cleanup was skippable

```ts
// Before — a stop on an already-dead task left a live driver JWT on disk
export async function stopBackgroundLocation(): Promise<void> {
  const isRunning = await Location.hasStartedLocationUpdatesAsync(TASK_NAME);
  if (!isRunning) return;                       // <-- cleanup below never ran
  await Location.stopLocationUpdatesAsync(TASK_NAME);
  await SecureStore.deleteItemAsync('bg_access_token').catch(() => {});
  await SecureStore.deleteItemAsync('bg_access_token_expires').catch(() => {});
  await SecureStore.deleteItemAsync(TRIP_ACTIVE_KEY).catch(() => {});
}
```

```ts
// After — the native stop stays conditional, the cleanup does not
export async function stopBackgroundLocation(): Promise<void> {
  let isRunning = false;
  try { isRunning = await Location.hasStartedLocationUpdatesAsync(TASK_NAME); }
  catch (e) { recordNonFatal(e, {...}); isRunning = true; }
  if (isRunning) {
    try { await Location.stopLocationUpdatesAsync(TASK_NAME); }
    catch (e) { recordNonFatal(e, {...}); }     // never strands the cleanup
  }
  await SecureStore.deleteItemAsync('bg_access_token').catch(() => {});
  await SecureStore.deleteItemAsync('bg_access_token_expires').catch(() => {});
  await SecureStore.deleteItemAsync(TRIP_ACTIVE_KEY).catch(() => {});
}
```

### Geofence resurrected tracking for a signed-out driver

```ts
// Before — headless handler re-armed unconditionally
const isRunning = await Location.hasStartedLocationUpdatesAsync(TASK_NAME);
if (!isRunning) {
  const tripActive = (await SecureStore.getItemAsync(TRIP_ACTIVE_KEY)...) === 'true';
  await startBackgroundLocation(tripActive ? TRIP_CADENCE : undefined);
}
```

```ts
// After — no session means tear down, not re-arm
if (!(await hasLiveSession())) {
  await stopBackgroundLocation().catch(() => {});
  await stopGeofenceRecovery().catch(() => {});
  return;
}
```

### Sign-out did not end the session server-side

```ts
// Before — only the local session ended
const { driver, token } = get();
if (driver?.id && token) { await api.put(`/drivers/${driver.id}/status`, { is_online: false }); }
setInMemoryToken(null);
```

```ts
// After — revoke while the credential is still usable
if (options?.revokeServerSession !== false && token) {
  try { await api.post('/auth/logout'); }
  catch (error) { if (__DEV__) console.log('[Auth] server logout failed (non-fatal):', error); }
}
setInMemoryToken(null);
```

## 8. Rollback plan

**Server-side ingest guard (highest-risk piece) — flag, no deploy.** Set
`app_settings.location_reject_revoked_sessions_enabled = false` via the admin
dashboard. `_guard_revoked_session` returns immediately and the endpoint behaves
exactly as before. This is the lever to pull if legitimate batches start 401ing.

**Tombstone writes:** self-expiring. They carry a TTL of
`ACCESS_TOKEN_EXPIRE_MINUTES + 60s` (~16 min), so stopping the writes drains the
keyspace on its own. `redis-cli --scan --pattern 'revoked_session:*' | xargs redis-cli del`
clears them immediately if needed. Nothing durable is written — no table, no
migration, no data-level remediation.

**Client-side pieces:** require an app release (OTA for the JS-only changes) to
revert. No feature flag, and deliberately so — the flagged alternative would be
"keep tracking signed-out drivers," which is not a state worth being able to
return to. The pieces are independent, so a partial revert is possible.

**Not reversible:** outbox rows purged at sign-out are gone. This is data
deletion by design, not a side effect. It only affects points a signed-out driver
had not yet uploaded.

## 9. Verification performed

- [x] **Automated tests.** Backend: 20 new (12 tombstone + 8 ingest guard); unit
      tier **523 passed, 1 skipped**; every affected existing suite re-run
      (`test_location_batch`, `test_p3_background_location`,
      `test_period1_accumulation_endpoint`, `test_auth`, `test_logout_all`,
      `test_driver_location_redis_resilience`) — **75 passed, 1 pre-existing
      xfail**. Driver-app: **45 suites / 339 tests pass** (25 new).
      Rider-app: **51 suites / 434 tests pass** — the shared-`logout()`
      regression check.
- [x] **Real production build.** `npx expo export --platform web` for
      **driver-app** (exit 0) and **rider-app** — not just `tsc --noEmit`, per the
      CLAUDE.md gate. Both apps also pass `tsc --noEmit` cleanly.
- [x] **Blast-radius grep.** `location-batch` (all callers, listed in §4);
      `logout|signOut` across both apps and shared; `registerLogoutCallback`
      (confirmed the driver app had none); `stopBackgroundLocation` /
      `stopGeofenceRecovery`; `create_jwt_token` call sites (confirmed
      `session_id` is set on every real login path and preserved across
      `/auth/refresh`, which is what makes a session-scoped tombstone viable);
      `f"session:"` writers (confirmed `session:{user_id}` is single-device and
      therefore unusable as the revocation key); `get_current_user` (confirmed it
      does **not** read the Redis session key, so `/auth/logout` alone never
      invalidated the access token).
- [x] **CLAUDE.md conventions.** PIPEDA (no coordinates logged; gate placed
      before persistence). Dual-import pattern preserved in both blocks of
      `auth.py` and `_deps.py`. Observability (`recordNonFatal` with
      `domain`/`surface`; deferred-upload path left at `warning` per the
      degraded-but-recovered rule). Errors surfaced, not swallowed: the ingest
      guard returns a clean 401 and settings/Redis failures log `warning` with
      `exc_info`. No float arithmetic; pre-commit money check clean on all five
      commits.
- [x] **Feature-flagged.** The server-side rejection — the only piece that can
      reject previously-valid input — sits behind
      `location_reject_revoked_sessions_enabled`. Defaults **ON**: the check
      fires only on positive evidence of a logout, so shipping it dark would ship
      no protection. Every ambiguous state (no `session_id`, unreadable settings
      row, Redis outage) fails open.

## 10. What was NOT verified

- **No device testing.** The core symptom is OS-level — an Android
  foreground-service notification and the iOS location indicator persisting after
  sign-out. Both are asserted only through mocked `expo-location` calls. Neither
  the notification disappearing nor the geofence-wake teardown has been observed
  on real hardware. **This is the main gap and should be the first manual check.**
- **Not run against live Supabase or a real Redis.** Backend tests use the
  `mock_supabase_client` fixture and the in-process `redis_client` fallback. The
  tombstone has not been exercised against a real Redis TTL.
- **Multi-device tombstone coupling not tested end-to-end.** `should_tombstone`
  is unit-tested, but the two-real-devices scenario (both refreshed onto the same
  `session_id`, one signs out) was reasoned about, not reproduced.
- **Sign-out mid-trip not exercised against a live ride.** The purge is tested at
  the outbox/recorder level with a fake SQLite; the interaction with an
  `in_progress` ride and its settled distance was not run end to end.
- **`expo-sqlite` `withExclusiveTransactionAsync` semantics assumed.** The
  purge's atomicity is verified against `MemorySqliteDatabase`, which serialises
  transactions by construction. Real WAL-mode behaviour under a concurrent
  headless enqueue was not tested.
- **No visual regression tooling for the driver app.** Consistent with the
  standing gap already recorded for the admin dashboard. Nothing here changes
  driver-app layout, so this is a stated limit, not a suspected risk.
- **Rider-app sign-out not manually exercised.** It inherits the shared
  `logout()` change and its full suite passes, but no manual rider sign-out was
  performed.
- **ESLint could not be run — pre-existing environment breakage.**
  `npx eslint` fails with `TypeError: expand is not a function` on **untouched**
  files too (`minimatch@3.1.5` resolved against `brace-expansion@5.0.8`, whose
  export shape changed). Verified pre-existing, not introduced here. Per
  CLAUDE.md gate 8 this is flagged rather than papered over with a dependency
  bump, since forcing that version change has its own unverified blast radius
  across every lint target. **Worth a `[CR]` issue.**

## 11. Sign-off

- [x] Rollback plan is concrete and testable — `app_settings` flag for the
      server guard; self-expiring tombstones; nothing durable written.
- [x] Blast radius is stated, not assumed — every caller of
      `/drivers/location-batch` and of `logout()` is enumerated in §4, including
      the rider-app surface the shared change reaches.
- [x] No silent behavior change to an already-shipped flow — the one visible
      change (notification/indicator stopping at sign-out) is in §5.
