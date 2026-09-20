# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-16 |
| Author | Cursor Grok, on behalf of live-testing report |
| Surface(s) | driver-app |
| Domain (Sentry tag) | dispatch |
| PR / commit link | uncommitted local work |
| Related issue or gap ID | Live report: driver minimized ~2h, returned to red Connection lost + pale STOP; tapping the chip did nothing |

## 1. Issue / gap identified

After a long minimize, an already-online driver sees a red **Connection lost** chip and a pale STOP control. The chip is not a button, resume waits on token refresh before painting **Reconnecting…**, and a hung refresh holds `wsConnectingRef` so later reconnects never start. Force-quit was the only recovery.

## 2. Root cause

Three stacked client bugs, confirmed against a 4:08 Regina screenshot (`You're Online` + red Connection lost + STOP, 5G):

1. Background close (3s) sets `connectionState` to `disconnected`. Resume calls `connectWebSocket()` → `openWebSocket` **awaits `ensureFreshToken()` before** `setConnectionState('reconnecting')`. After 2h the 15-min JWT is expired, so the chip stays red for the whole refresh (or forever if it hangs).
2. `connectWebSocket` no-ops while `wsConnectingRef` is set. AppState `'active'` did not clear that mutex (only `'background'` did). A hung foreground refresh made every later retry a no-op. The chip had no `onPress`.
3. STOP greys when `driver.status !== 'active'`, defaulting missing status to `'pending'`. `refreshProfile()` on resume can briefly lose `status` while local `isOnline` stays true, so STOP looks disabled next to **You're Online**.

Not the cause: 4h `stale_intent` (2h is under the cutoff); missed-offer `auto_offline` (that hides the chip by setting `isOnline=false`).

## 3. Fix / remediation

Auto-recover on resume without force-quit: paint **Reconnecting…** before token refresh, cap that wait at 15s (and swallow a late `ensureFreshToken` reject so the race cannot become an unhandled RN rejection), release the hung-connect mutex on foreground, make the chip a Retry control (disconnected and reconnecting), and never grey STOP while the driver is still locally online.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface (driver-app dashboard).** `DriverTopBar` is used from `app/driver/(tabs)/index.tsx` only. `retryConnection` is new on `useDriverDashboard`. `canGoOnline` is local to `DriverIdlePanel`.
- Rider-app `useRiderSocket.ts` is a known sibling WS client; not changed (no background-close + Connection lost chip on that screen).
- Shared `ensureFreshToken` is unchanged; only the driver WS caller races it with a 15s timeout.
- Duplicate-socket mutex is preserved: resume/retry clear the lock **then** go through `connectWebSocket()`, which still refuses a second in-flight attempt. Stale `onclose` still ignores `ws !== wsRef.current`.
- Could regress: short-background reconnect (still covered by existing socketLifecycle tests). Retry while CONNECTING/OPEN is a no-op (does not `close(4000)`); hung refresh with no socket still clears the mutex and opens a new one.
- No ride state machine, money, or insurance-period writes.

## 5. User-experience effect

- **Who:** drivers on the idle Home map, already online.
- **Visible mid-session:** yes. After a long background they should see amber **Reconnecting…** instead of a stuck red chip; tapping the chip retries. STOP stays the green/enabled treatment while `isOnline`.
- **Copy:** existing `dashboard.connectionLost` / `dashboard.reconnecting`; chip gains `accessibilityHint` `common.retry`. No new customer-facing sentence.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/hooks/useDriverDashboard.ts` | Reconnecting before token refresh; 15s refresh cap; mutex release on `'active'`; `retryConnection` (no-op if socket already CONNECTING/OPEN) | Unstick long-background resume without aborting a live handshake |
| `driver-app/components/dashboard/DriverTopBar.tsx` | Chip is a button when disconnected/reconnecting | Taps were a no-op |
| `driver-app/components/dashboard/DriverIdlePanel.tsx` | `canGoOnline = isOnline \|\| status === 'active'` | Don't grey STOP while already online |
| `driver-app/app/driver/(tabs)/index.tsx` | Pass `onRetryConnection={retryConnection}` | Wire the chip |
| `driver-app/hooks/__tests__/useDriverDashboard.socketLifecycle.test.ts` | Resume, hung-refresh, 15s cap, manual retry, Retry-does-not-abort-handshake | Lock the 2h path |
| `driver-app/__tests__/components/DriverTopBar.test.tsx` | Chip tap | Affordance |
| `driver-app/__tests__/components/DriverIdlePanel.onlineStop.test.tsx` | Online + missing status | STOP not grey |
| `driver-app/__tests__/app/driverDashboardScreen.test.tsx` | Mock `retryConnection` | Screen mock completeness |

## 7. Before / after

```
# Before (openWebSocket)
await ensureFreshToken();
// early returns leave connectionState at 'disconnected'
setConnectionState('reconnecting');
const ws = new WebSocket(wsUrl);
```

```
# After
setConnectionState('reconnecting');
await Promise.race([ensureFreshToken(), timeout(15s)]);
const ws = new WebSocket(wsUrl);
```

```
# Before (IdlePanel)
const canGoOnline = driverStatus === 'active';
```

```
# After
const canGoOnline = isOnline || driverStatus === 'active';
```

## 8. Rollback plan

No live-data writes. Rollback is an OTA/EAS revert of the driver-app bundle (or revert this commit and ship). There is no client feature flag for this chip. Retry while a handshake is already in flight is a no-op; auto-resume still uses the existing mutex. A `git revert` is sufficient because no DB/Stripe/ride-state rows are touched.

## 9. Verification performed

- [x] Automated tests: `useDriverDashboard.socketLifecycle.test.ts` (22 cases including 15s token cap + Retry-does-not-abort-handshake), plus chat/wsSenders/TopBar/IdlePanel/screen suites — 96 passed on the earlier related run; lifecycle suite re-run after handshake no-op
- [ ] Manual repro: 20-min background (JWT expiry) then 2h Recents restore — not run here
- [x] Blast-radius grep: `DriverTopBar`, `retryConnection`, `canGoOnline` — dashboard Home only
- [x] Conventions: WS reconnect still goes through `connectWebSocket` mutex; no PII in new logs
- [x] Not feature-flagged: restores already-intended reconnect UX (amber during handshake, keep retrying). Tap-to-retry is the 2026-09-13 missing affordance. Driver-app has no `app_settings` flag for this chip; OTA revert is the off switch
- [ ] Production `eas build` / store binary — **not run**. `tsc`/Jest only. A real production build was not run.

## 10. What was NOT verified

- No driver-app visual-regression tooling exists; the 4:08 screenshot is the before-state, reasoned after-state (amber chip / tappable / green STOP), not screenshotted.
- Fly live logs only retain minutes; the 4:08 window was already gone. Sentry CLI in this environment is `org:ci` (cannot list issues). Backend `SENTRY_API_TOKEN` exists on Fly (`spinr-backend` / `crimson-smoke-7445`) but was not queried after the screenshot arrived.
- Hung `ensureFreshToken` via real SecureStore/keychain was not reproduced on a device; the unit test fakes a never-resolving promise.
- iOS vs Android OEM freeze (JS never ran the 3s close) is not this screenshot (`connectionState` was `disconnected`, so the close did run).

## 11. Sign-off

- [x] Rollback plan is concrete (OTA revert)
- [x] Blast radius is stated (driver Home only)
- [x] UX field filled in (mid-session, already-online drivers)
