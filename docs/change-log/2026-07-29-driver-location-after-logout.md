# Change Impact & Risk Log — driver location tracking survived sign-out

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (session `01TBFFJWqVUq5LQyomyWepKM`) |
| Surface(s) | driver-app, backend, shared (rider-app affected transitively) |
| Domain (Sentry tag) | `drivers`, `auth` |
| PR / commit link | `aafafc1`, `9525d9a`, `ecd8057`, `bf99e18`, `b7f5e84`, `9b004eb`, `dcd11a6` on `claude/driver-location-logout-issue-26c9pu` |
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

**Second root cause, found on self-review (`9b004eb`).** The four mechanisms above
describe the *explicit sign-out* path. A session can also end by **expiry**, via
the "No valid stored token" branch of `initialize()` → `clearAuthStorage()`. That
function's docstring claims it wipes "every auth artifact", but it deleted only
`auth_token`, `refresh_token`, and `token_expires_at` — leaving `fg_access_token`,
`bg_access_token`, and `bg_access_token_expires` on disk. It also never ran the
logout callbacks. So on that path the background task still had a usable cached
credential (`getBackgroundAuthToken` step 1) *and* still read as signed-in, and the
original bug was fully intact. The first round of fixes did not cover it.

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
   ahead of both the v1 and v2 writers, and the two WebSocket writers
   (`buffer_ride_breadcrumb` for a single durable ping, `persist_ride_breadcrumbs`
   for `location_batch`) do the same. The WS handshake also rejects a tombstoned
   session outright.
6. **Explicit session-ended marker** (`shared/auth/sessionMarker.ts`) replaces the
   original token-absence inference. Written by `logout()` **and** by
   `clearAuthStorage()`, cleared by `setTokens()`. `clearAuthStorage()` now also
   deletes the three token keys it was missing. This is what closes the expiry
   path, and it makes the client use the same positive-evidence rule as the
   backend tombstone.

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
- **WS sign-out detection lags by up to 30s.** The durable WS writers cache a
  negative revocation result for `_WS_REVOKE_RECHECK_SECONDS` so a trip streaming
  a fix every ~3-4s does not add a Redis GET per fix to the WS fan-out path
  (<100 ms P95 target). A socket already open at sign-out can therefore persist up
  to 30s of extra points — versus the token's full remaining ~15 minutes before
  this change. Positives are cached permanently, since tombstones are never unset.
- **`clearAuthStorage()` now deletes three more keys.** It is called only from the
  no-valid-token branch of `initialize()`, where the session is already gone, so
  there is no path on which those tokens were still wanted. Reaches the rider app
  too; `bg_access_token*` simply never exist there.
- **~~`hasLiveSession()` treats an unreadable SecureStore as no session.~~
  Corrected — this understated the cost and the behaviour has changed
  (`9b004eb`).** The original text claimed a false negative merely "skips that
  re-arm". In the *location-task* path it also dropped the trip samples and
  stopped the task, so a driver whose SecureStore write silently failed
  (`storage.setItem` swallows errors) or whose keystore was momentarily locked
  lost the backgrounded portion of the trip permanently — undercounted billed km
  and a hole in the SGI per-period audit. Before this feature those samples
  stayed in SQLite and the foreground flushed them later, so it was a regression
  in a degraded state, not merely a missed optimisation.

  The gate is now `isSessionEnded()`, which acts on the presence of an explicit
  marker instead. Every ambiguous state — no marker, unreadable store — resolves
  to "session live, keep tracking". `startBackgroundLocation` remains ungated, so
  a SecureStore failure still cannot block a driver from going online.

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
| `shared/auth/sessionMarker.ts` | **New** — `SESSION_ENDED_KEY` + rationale | Leaf module the headless task can read without pulling zustand/API/Firebase |
| `shared/store/authStore.ts` | `clearAuthStorage` deletes `fg_access_token`/`bg_access_token*` and writes the marker; `setTokens` clears it; `logout` writes it | Closes the expiry path (finding 1) |
| `driver-app/utils/backgroundLocation.ts` | `hasLiveSession()` → `isSessionEnded()` | Positive evidence, no false sign-out (finding 2) |
| `backend/routes/websocket.py` | Handshake rejects a tombstoned session; both durable writers re-check with a cached/throttled helper | WS ingest was ungated (finding 3) |
| `backend/routes/drivers/location.py` | Explicit `None` flag → enabled | A NULL column shipped the guard dark (finding 5) |
| `backend/tests/test_websocket_live_location.py` | Rewritten: guard-condition + ordering assertions, behavioural cache test | My WS change broke its whitespace-exact source assertion |
| `backend/tests/test_session_revocation.py` | TTL test uses 7/45 instead of the 15 default; new test pins the TTL is applied | Was tautological (finding 4) |
| `driver-app/__tests__/store/authStore.initialize.test.ts` | +3 marker/token-wipe tests | Regression cover for finding 1 |

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

### The gate inferred sign-out from missing tokens (review finding 2)

```ts
// Before — absence of a token read as "signed out"; a locked keystore or an
// unpersisted write therefore dropped a working driver's trip samples
const SESSION_TOKEN_KEYS = ['refresh_token', 'fg_access_token'] as const;
export async function hasLiveSession(): Promise<boolean> {
  for (const key of SESSION_TOKEN_KEYS) {
    try { if (await SecureStore.getItemAsync(key)) return true; } catch {}
  }
  return false;   // <-- also the answer when the store simply could not be read
}
```

```ts
// After — only an explicit marker means "signed out"; everything else keeps
// tracking, matching the backend tombstone's positive-evidence rule
export async function isSessionEnded(): Promise<boolean> {
  try {
    return (await SecureStore.getItemAsync(SESSION_ENDED_KEY)) !== null;
  } catch {
    return false;   // unreadable store is not evidence of a sign-out
  }
}
```

### `clearAuthStorage` left the background credentials behind (review finding 1)

```ts
// Before — "every auth artifact" minus the three keys that actually mattered
await SecureStore.deleteItemAsync('auth_token');
await SecureStore.deleteItemAsync('refresh_token');
await SecureStore.deleteItemAsync('token_expires_at');
```

```ts
// After — plus the headless task's credentials, plus the marker
await SecureStore.deleteItemAsync('auth_token');
await SecureStore.deleteItemAsync('refresh_token');
await SecureStore.deleteItemAsync('token_expires_at');
await SecureStore.deleteItemAsync('fg_access_token');
await SecureStore.deleteItemAsync('bg_access_token');
await SecureStore.deleteItemAsync('bg_access_token_expires');
await SecureStore.setItemAsync(SESSION_ENDED_KEY, '1');
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

**WebSocket guard:** *not* covered by the `app_settings` flag — it calls
`is_session_revoked` directly. Turning off the REST flag does not disable it. To
neutralise it without a deploy, clear the tombstone keyspace (below); with no
tombstones the check always returns False. Flagging it alongside the REST guard is
a reasonable follow-up if the WS path ever needs an independent kill switch.

**Client-side pieces:** require an app release (OTA for the JS-only changes) to
revert. No feature flag, and deliberately so — the flagged alternative would be
"keep tracking signed-out drivers," which is not a state worth being able to
return to. The pieces are independent, so a partial revert is possible.

**Not reversible:** outbox rows purged at sign-out are gone. This is data
deletion by design, not a side effect. It only affects points a signed-out driver
had not yet uploaded.

## 9. Verification performed

- [x] **Automated tests.** Backend unit tier **534 passed, 1 skipped** (up from
      523 — 11 added across the review fixes). Every affected existing suite
      re-run (`test_location_batch`, `test_p3_background_location`,
      `test_period1_accumulation_endpoint`, `test_auth`, `test_logout_all`,
      `test_driver_location_redis_resilience`, plus all four `test_websocket_*`).
      Driver-app: **45 suites / 343 tests pass**. Rider-app: **51 suites / 434
      tests pass** — the shared-`logout()` / `clearAuthStorage()` regression check.
- [x] **Real production build, re-run after the review fixes.** `npx expo export
      --platform web` exit 0 for **both** driver-app and rider-app — not just
      `tsc --noEmit`, per the CLAUDE.md gate. Both also pass `tsc --noEmit`.
- [x] **Adversarial self-review of the first five commits**, which found six
      issues: the expiry-path hole (finding 1, high — the reported bug was still
      reachable), the inverted client gate (2), ungated WS ingest (3), a
      tautological TTL test (4), a NULL-flag default that shipped the guard dark
      (5), and a redundant keychain read (6). All six are fixed in `9b004eb` and
      `dcd11a6`. Also checked and cleared: `driver-app/node_modules/@spinr/shared`
      is a stale vendored copy of `shared/` that does **not** contain these
      changes, but nothing resolves to it (`@shared` → `../shared` in tsconfig,
      babel, and metro; no bare `@spinr/shared` imports anywhere). Worth knowing
      it exists — it is a live landmine for any future `shared/` edit.
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
- **The 30s WS re-check window is not exercised end to end.** The caching
  contract is tested behaviourally against a reimplementation of the helper, not
  against a live socket — the endpoint is ~1500 lines with no seam to drive, so
  the in-endpoint version is pinned structurally instead. A refactor that moved
  the helper's logic could pass both tests and still regress.
- **`clearAuthStorage()` still does not run the logout callbacks.** The marker
  makes that unnecessary for *collection* (a headless task that fires afterwards
  sees it and tears itself down), but the native task keeps running until its next
  fire — so on the expiry path the Android notification can linger for up to one
  idle cadence (~30s) rather than clearing immediately. Deliberate: calling
  `_runLogoutCallbacks()` from `initialize()` would reach into the rider app's
  callback set for a marginal gain.
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

## 11. Self-review round (`9b004eb`, `dcd11a6`)

An adversarial pass over the first five commits found six issues. Recorded here
because two of them meant the originally-claimed fix was incomplete.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `clearAuthStorage()` left `fg_access_token` / `bg_access_token*` and never ran the logout callbacks, so a session ending by **expiry** still recorded and uploaded. The reported bug was still reachable. | `9b004eb` — delete all three keys, write the marker |
| 2 | Medium | The client gate inferred sign-out from *missing* tokens, the opposite of the backend's positive-evidence rule. An unpersisted SecureStore write or locked keystore dropped a signed-in driver's trip samples and stopped tracking mid-trip. | `9b004eb` — `isSessionEnded()` on an explicit marker |
| 3 | Medium | "Server-side enforcement" was REST-only; the two WebSocket writers were ungated. | `dcd11a6` — handshake rejection + re-check on both writers |
| 4 | Low | TTL test patched `ACCESS_TOKEN_EXPIRE_MINUTES` to 15, already the default → passed against a hardcoded constant. | `dcd11a6` — 7 and 45, plus a test that the TTL is applied |
| 5 | Low | `.get(flag, True)` returns `None` for a NULL column and `bool(None)` is False, so a null flag shipped the guard dark. | `dcd11a6` — `None` means default-on |
| 6 | Nit | Gate cost two keychain reads per background callback. | `9b004eb` — one, as a side effect of the marker |

Checked and cleared: `driver-app/node_modules/@spinr/shared` is a stale vendored
copy of `shared/` without these changes, but nothing resolves to it — `@shared` →
`../shared` in tsconfig, babel, and metro, and there are no bare `@spinr/shared`
imports. A latent trap for future `shared/` work.

Also corrected in this document: the §4 risk bullet that described the client
gate's failure mode as "skips that re-arm" understated it. See the struck-through
entry.

## 12. Sign-off

- [x] Rollback plan is concrete and testable — `app_settings` flag for the REST
      guard; tombstone-keyspace clear for the WS guard (which the flag does not
      cover, stated explicitly in §8); self-expiring tombstones; nothing durable
      written.
- [x] Blast radius is stated, not assumed — every caller of
      `/drivers/location-batch` and of `logout()` is enumerated in §4, including
      the rider-app surface the shared change reaches, and both WebSocket writers.
- [x] No silent behavior change to an already-shipped flow — the one visible
      change (notification/indicator stopping at sign-out) is in §5.
- [x] Claims in this document match the code as merged. The one that did not
      (§4's understated client-gate risk) is corrected in place rather than
      quietly reworded.
