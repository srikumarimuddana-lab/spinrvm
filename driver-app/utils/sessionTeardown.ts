import { registerLogoutCallback } from '@shared/store/authStore';
import { stopBackgroundLocation, stopGeofenceRecovery } from './backgroundLocation';
import { resetLocationIntegrity } from './locationIntegrity';
import { tripLocationRecorder } from './tripLocationRecorder';
import { recordNonFatal } from './crashlytics';
import { LAST_LOCATION_KEY } from '../lib/androidAuto/carFixChannel';

/**
 * Tear down driver location tracking on sign-out.
 *
 * Until this existed, teardown lived *only* in `toggleOnline(false)`
 * (useDriverDashboard) — tracking was scoped to the online toggle, not to the
 * session. Signing out while online therefore left:
 *
 *   - the native `spinr-background-location` task running, so Android kept
 *     showing "You're online and receiving ride requests" and iOS kept the
 *     location indicator lit for a driver who had signed out;
 *   - the recovery geofence armed, whose headless exit handler re-armed
 *     tracking after an OS kill, resurrecting it indefinitely;
 *   - `bg_access_token` (a live driver JWT) on disk, which the headless task
 *     used to keep uploading GPS for the rest of its 15-minute lifetime;
 *   - the SQLite outbox full of raw lat/lng, flushed under whichever account
 *     signed in next on that device;
 *   - and, once the Android Auto work landed, the `spinr-car-location` task,
 *     which this function did not know about at all. That one is worse than its
 *     sibling: on the no-foreground-service fallback path there is no
 *     notification, so a signed-out driver had no way to see it running.
 *
 * The rider app has used `registerLogoutCallback` for per-session state since
 * rideStore; the driver app simply never registered one.
 *
 * Ordering: stop the producers before purging the store, so the purge cannot
 * race a native callback that re-enqueues a point immediately after the delete.
 * Every step is independently guarded — one failure must not skip the rest,
 * because each on its own leaves a signed-out driver being tracked.
 */
export async function teardownDriverLocationSession(): Promise<void> {
  // 1. Stop producing fixes. Also clears the cached background credential, so
  //    even a task that somehow survives has nothing to authenticate with.
  try {
    await stopBackgroundLocation();
  } catch (e) {
    recordNonFatal(e, { domain: 'drivers', surface: 'driver-app', teardown: 'stop_background_location' });
  }

  // 2. Disarm the geofence so nothing wakes the app to re-arm tracking.
  try {
    await stopGeofenceRecovery();
  } catch (e) {
    recordNonFatal(e, { domain: 'drivers', surface: 'driver-app', teardown: 'stop_geofence' });
  }

  // 3. Stop the Android Auto display-only task. Its registration is persisted by
  //    expo-task-manager and restored on process start, so leaving it running
  //    means a location broadcast can relaunch the app headlessly and resume
  //    tracking a signed-out driver with nothing on screen to show for it.
  //    Lazily required: this is the driver app's logout path on BOTH platforms,
  //    and the Android Auto tree is Android-only (index.js loads it under a
  //    Platform check). The require is what keeps it off iOS.
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    await require('../lib/androidAuto/carLocationTask').stopCarLocationService();
  } catch (e) {
    recordNonFatal(e, { domain: 'drivers', surface: 'driver-app', teardown: 'stop_car_location' });
  }

  // 4. Clear every producer's anti-spoof last-location state. Producers are
  //    stopped above, but the checkers' baselines survive in module memory —
  //    the next account on this device must not have its first fixes compared
  //    against the previous driver's final position (a legitimate first fix
  //    from a different part of town would read as a teleport).
  try {
    resetLocationIntegrity();
  } catch (e) {
    recordNonFatal(e, { domain: 'drivers', surface: 'driver-app', teardown: 'reset_integrity' });
  }

  // 5. Drop coordinates already at rest on the device. Unlike every other
  //    caller, this deliberately discards unacknowledged points — see
  //    TripLocationOutbox.purgeAll for why sign-out is the exception.
  try {
    await tripLocationRecorder.purgeAll();
  } catch (e) {
    // PII left at rest is not a "recoverable anomaly", so this is an error-level
    // report rather than a silent catch.
    recordNonFatal(e, { domain: 'drivers', surface: 'driver-app', teardown: 'purge_outbox' });
  }

  // 6. And the plain-AsyncStorage last-known position. The outbox purge above
  //    does not cover it — different store, and it is the one coordinate pair
  //    that survives a sign-out precisely because it is a display cache rather
  //    than trip data. `useDriverDashboard` already deletes it on go-offline for
  //    the same reason; sign-out is the stronger case.
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const AsyncStorage = require('@react-native-async-storage/async-storage').default;
    await AsyncStorage.removeItem(LAST_LOCATION_KEY);
  } catch (e) {
    recordNonFatal(e, { domain: 'drivers', surface: 'driver-app', teardown: 'clear_last_location' });
  }
}

let _registered = false;

/**
 * Register the sign-out teardown exactly once per JS runtime.
 *
 * Called from the app shell at module scope rather than from a hook, so it is
 * armed before any screen mounts — a logout triggered by the API client's 401
 * interceptor can fire before the dashboard ever renders.
 */
export function registerDriverSessionTeardown(): void {
  if (_registered) return;
  _registered = true;
  registerLogoutCallback(teardownDriverLocationSession);
}

/** @internal Test-only — allow re-registration between cases. */
export function _resetSessionTeardownRegistration(): void {
  _registered = false;
}
