import { registerLogoutCallback } from '@shared/store/authStore';
import { stopBackgroundLocation, stopGeofenceRecovery } from './backgroundLocation';
import { tripLocationRecorder } from './tripLocationRecorder';
import { recordNonFatal } from './crashlytics';

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
 *     signed in next on that device.
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

  // 3. Drop coordinates already at rest on the device. Unlike every other
  //    caller, this deliberately discards unacknowledged points — see
  //    TripLocationOutbox.purgeAll for why sign-out is the exception.
  try {
    await tripLocationRecorder.purgeAll();
  } catch (e) {
    // PII left at rest is not a "recoverable anomaly", so this is an error-level
    // report rather than a silent catch.
    recordNonFatal(e, { domain: 'drivers', surface: 'driver-app', teardown: 'purge_outbox' });
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
