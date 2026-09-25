# Change Impact & Risk Log — Android drivers must allow location all the time

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-24 |
| Author | driver-app location gate |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `fix/c136-destination-mode-and-push-token-ownership` |
| Related issue or gap ID | Closed driver app drops out of dispatch because location was only "while using" |

## 1. Issue / gap identified

A driver can go online, or stay online, with location set to While using the app. Closing Spinr then stops the heartbeat, so a searching rider never offers that driver.

## 2. Root cause

Go-online asked for background location only after the backend had already been told the driver was online, and a resume with the permission missing only set an error string. It left the driver online. A still-registered location task also counted as success without checking that Allow all the time was still granted.

## 3. Fix / remediation

On Android, Go online requests foreground location and then Allow all the time, and does not flip online if either is refused. Opening the app while marked online, with no active trip, takes the driver offline and sends them to system location settings. Starting background tracking also refuses, and stops a leftover task, when that permission is not granted.

## 4. Risk & impact on existing functionality

- Blast radius: driver go-online and the resume tracking effect in `useDriverDashboard.ts`, plus `startBackgroundLocation` in `backgroundLocation.ts`. iOS still goes through the same background-permission check inside `startBackgroundLocation`. An in-progress trip is not forced offline.
- A driver who previously stayed online on "while using" will be taken offline the next time they open the app, until they choose Allow all the time.
- No ride-state, money, or insurance-period change.

## 5. User-experience effect

- Android drivers see an alert, Allow all the time, with Open settings, instead of going online on a weaker grant.
- Visible the next time they tap Go online, and on the next open if they were already online without that grant.
- Copy is the settings instruction. iOS is unchanged aside from the shared start path refusing a missing Always grant.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/utils/backgroundLocation.ts` | Require the always grant before tracking starts; `requireAlwaysLocationPermission` | While-using is not a dispatch heartbeat |
| `driver-app/hooks/useDriverDashboard.ts` | Block Android go-online, and idle resume, without Allow all the time | The online flag was sticking without the permission |
| `driver-app/utils/__tests__/backgroundLocation.test.ts` | Denied always-permission does not start tracking | Regression |

## 7. Before / after

```ts
// Before — online first, permission after, resume only sets an error string
await updateDriverStatus(true);
const bgStarted = await startBackgroundLocation();
if (!bgStarted) { /* roll back */ }
```

```ts
// After — Android go-online stops before the status write
if (!(await requireAlwaysLocationPermission())) {
  showAlert('Allow all the time', '...');
  return;
}
```

## 8. Rollback plan

Revert this commit and ship the previous JS. No migration and no feature flag. Drivers already taken offline can tap Go online again on the previous build.

## 9. Verification performed

- Jest `driver-app/utils/__tests__/backgroundLocation.test.ts`: 87 passed.
- Not run on a device. No visual regression tooling for driver-app. The system permission sheet itself was not screenshotted.

## 10. Sign-off

- Rollback is a JS revert.
- Blast radius is driver go-online and resume tracking.
- Drivers without Allow all the time cannot stay online. That is the intended change.
