/**
 * Display-only location for the Android Auto map, when the phone app is closed.
 *
 * ─── The problem ────────────────────────────────────────────────────────────
 * Android Auto starts the app's JS context, not its phone UI. In that state the
 * process is, as far as Android is concerned, backgrounded — and a backgrounded
 * app with no foreground service gets its foreground location updates throttled
 * hard. `useCarLocation`'s watchPositionAsync goes quiet for minutes at a time,
 * so the car marker sits where the driver was, not where they are. That is the
 * "it shows where I started" report, and no amount of re-requesting inside the
 * hook fixes it, because the throttle is applied to the process, not the call.
 *
 * Google Maps and Waze solve this the same way: a foreground service. This is
 * ours, and it is deliberately the smallest possible one.
 *
 * ─── What makes this different from utils/backgroundLocation.ts ─────────────
 * That module exists to feed DISPATCH: it records durable trip samples to SQLite
 * and uploads them to /drivers/location-batch. Reusing it here would have been
 * wrong twice over:
 *
 *   1. `startBackgroundLocation()` calls requestBackgroundPermissionsAsync() when
 *      permission is missing. A permission dialog raised from a head unit, at
 *      driving speed, is not acceptable. This module NEVER prompts — if the
 *      permission is not already held, it declines to start and the existing
 *      in-hook watcher stands.
 *   2. Its task handler POSTs the driver's position. An OFFLINE driver — one who
 *      has not gone on shift — must not have their location transmitted just
 *      because they plugged their phone into a car. PIPEDA data minimisation is
 *      explicit that every field is tied to a stated purpose, and "draw a map for
 *      the person holding the phone" does not justify egress.
 *
 * So this handler makes NO network call of any kind. It publishes to
 * carFixChannel, which updates the in-memory marker and the device-local
 * `spinr_driver_last_location` cache. Nothing leaves the device, and nothing
 * here touches `is_online`, so no insurance-period transition is implied: an
 * offline driver watching this map stays in Period 0.
 *
 * ─── One service, never two ─────────────────────────────────────────────────
 * expo-location mints a separate LocationTaskService instance per task name
 * (`LocationTaskService.kt`: `mServiceId = sServiceId++`, channel `appId:taskName`),
 * so running this alongside `spinr-background-location` would put TWO permanent
 * Spinr notifications in the driver's shade. It therefore refuses to start while
 * that one is running — and in that case it isn't needed anyway, because that
 * task publishes to the same channel.
 */
import * as Location from 'expo-location';
import * as TaskManager from 'expo-task-manager';
import { checkLocationIntegrity } from '../../utils/locationIntegrity';
import {
  FOREGROUND_NOTIFICATION_COLOR,
  isBackgroundLocationRunning,
  isSessionEnded,
} from '../../utils/backgroundLocation';
import { publishCarFix } from './carFixChannel';

export const CAR_LOCATION_TASK = 'spinr-car-location';

type CarLocationTaskData = { locations: Location.LocationObject[] };

/**
 * Why a driver's marker is or isn't live, for the debug fact and the logs.
 * `piggyback` is a success, not a failure — the online driver's existing service
 * is already feeding the same channel.
 */
export type CarLocationStart =
  | 'started'
  | 'already-running'
  | 'piggyback'
  | 'no-permission'
  | 'session-ended'
  | 'unavailable';

export async function handleCarLocationTask({
  data,
  error,
}: {
  data?: CarLocationTaskData;
  error?: { message?: string } | null;
}): Promise<void> {
  if (error) {
    // No Sentry: this fires on transient sensor loss (tunnel, garage) and would
    // be pure noise. The marker simply holds its last position.
    console.warn('[CarLocation] task error:', error.message);
    return;
  }
  const locations = data?.locations ?? [];
  // Only the newest sample matters. This drives a map marker, not a route
  // history — replaying a deferred batch would rewind the marker across
  // positions the driver has already left.
  const latest = locations[locations.length - 1];
  if (!latest?.coords) return;

  // Same trust gate as the dispatch watcher. Display-only or not, a mock-location
  // app should not be able to drive where Spinr says the driver is — including in
  // the shared last-location cache that the phone dashboard reads back.
  const integrity = checkLocationIntegrity(latest);
  if (!integrity.trusted) {
    console.warn(`[CarLocation] dropped untrusted sample: ${integrity.reason}`);
    return;
  }

  publishCarFix({
    latitude: latest.coords.latitude,
    longitude: latest.coords.longitude,
    heading: latest.coords.heading ?? null,
  });
}

TaskManager.defineTask<CarLocationTaskData>(CAR_LOCATION_TASK, handleCarLocationTask);

/**
 * Start the display-only service. Safe to call repeatedly; never throws.
 *
 * Deliberately silent about failure at the call site — every negative outcome
 * leaves the driver exactly where they were before (the in-hook foreground
 * watcher plus its staleness watchdog), which is today's behaviour. Losing the
 * upgrade is not worth an alert on a screen someone is driving in front of.
 */
export async function startCarLocationService(): Promise<CarLocationStart> {
  try {
    // A signed-out device must not run a location service at all, for any reason.
    if (await isSessionEnded()) return 'session-ended';

    if (await Location.hasStartedLocationUpdatesAsync(CAR_LOCATION_TASK)) {
      return 'already-running';
    }
    // The online driver's dispatch service is already up and already publishing
    // to carFixChannel — a second service would only add a second notification.
    if (await isBackgroundLocationRunning()) return 'piggyback';

    // NEVER request. See the file header.
    const { status } = await Location.getBackgroundPermissionsAsync();
    if (status !== 'granted') return 'no-permission';

    await Location.startLocationUpdatesAsync(CAR_LOCATION_TASK, {
      // Matches the car surface's own watcher: High because Balanced can fall
      // back to network positioning, which carries no bearing — and without a
      // bearing the car marker has nothing to rotate to.
      accuracy: Location.Accuracy.High,
      timeInterval: 2_000,
      distanceInterval: 5,
      // No deferredUpdatesInterval: batching is for upload efficiency, and there
      // is no upload here. A marker wants each fix as it lands.
      pausesUpdatesAutomatically: false,
      activityType: Location.ActivityType.AutomotiveNavigation,
      showsBackgroundLocationIndicator: true,
      foregroundService: {
        notificationTitle: 'Spinr',
        notificationBody: 'Showing your location on the car screen',
        notificationColor: FOREGROUND_NOTIFICATION_COLOR,
        // The car screen is gone once the app is; so is the reason for this.
        killServiceOnDestroy: true,
      },
    });
    return 'started';
  } catch (e) {
    // Android 12+ can refuse a foreground-service start from the background
    // (ForegroundServiceStartNotAllowedException). That is a legitimate outcome,
    // not a crash: fall through to the in-hook watcher.
    console.warn('[CarLocation] could not start display service:', e);
    return 'unavailable';
  }
}

/**
 * Stop it. Unconditional and silent — `stopLocationUpdatesAsync` throws when the
 * task was never started, which is the normal case on most disconnects.
 */
export async function stopCarLocationService(): Promise<void> {
  try {
    await Location.stopLocationUpdatesAsync(CAR_LOCATION_TASK);
    console.log('[CarLocation] display service stopped');
  } catch {
    // Not running — nothing to do.
  }
}
