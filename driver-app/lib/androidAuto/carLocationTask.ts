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
 * ─── One task, never two (single-writer invariant) ──────────────────────────
 * expo-location runs ALL location tasks through ONE shared Android
 * LocationTaskService (single manifest <service>; LocationTaskConsumer.kt
 * builds an explicit component intent — one instance, ONE notification id;
 * an earlier version of this comment claimed one service per task, which is
 * wrong). Two consequences make overlap with `spinr-background-location`
 * actively destructive, not just cosmetic:
 *   1. Stopping EITHER task runs `stopForeground(true); stopSelf()` on the
 *      shared instance — stripping the other task's foreground promotion,
 *      which is what lets Android throttle background GPS. Every stop here
 *      therefore repairs the dispatch task (reassertDispatchTaskUnlocked).
 *   2. A STATIC `sLastTimestamp` in LocationTaskConsumer dedups fixes across
 *      ALL consumers: with both tasks registered, each fix is delivered to
 *      both and the losing copy is silently discarded before JS. This 2s
 *      watcher out-races the 4s dispatch task and starves the durable route
 *      stream (how ride SPR-PE7TTB lost 83% of a trip).
 * So this task runs ONLY while dispatch tracking is not registered, all
 * start/stops are serialized through the locationTaskArbiter, and while the
 * driver is online the dispatch task feeds the same carFixChannel instead.
 */
import * as Location from 'expo-location';
import * as TaskManager from 'expo-task-manager';
import { recordNonFatal } from '../../utils/crashlytics';
import { createLocationIntegrityChecker } from '../../utils/locationIntegrity';
import { runExclusive } from '../../utils/locationTaskArbiter';
import {
  FOREGROUND_NOTIFICATION_COLOR,
  isBackgroundLocationRunning,
  isSessionEnded,
  reassertDispatchTaskUnlocked,
} from '../../utils/backgroundLocation';
import { publishCarFix } from './carFixChannel';

// This producer's own anti-spoof state. The 2s car watcher used to share one
// module-global last-location with the trip recorders — its dense cadence
// stomped their teleport baseline and could falsely reject legitimate trip
// fixes (see locationIntegrity.ts). Display-only gate: this task never
// persists or uploads, so filtering here is correct (unlike capture paths).
const carIntegrity = createLocationIntegrityChecker();

export const CAR_LOCATION_TASK = 'spinr-car-location';

type CarLocationTaskData = { locations: Location.LocationObject[] };

/**
 * Why a driver's marker is or isn't live, for the debug fact and the logs.
 * `piggyback` is a success, not a failure — the online driver's existing service
 * is already feeding the same channel.
 */
export type CarLocationStart =
  /** Foreground service running — the good case. Notification visible. */
  | 'started'
  /** Registered WITHOUT a foreground service, because Android refused one. */
  | 'started-no-notification'
  | 'already-running'
  | 'piggyback'
  | 'no-permission'
  | 'session-ended'
  /** Android refused the foreground service AND the fallback also failed. */
  | 'fgs-refused'
  | 'unavailable';

/**
 * Whether a thrown error is Android refusing a background foreground-service start.
 *
 * expo-location raises this from `LocationModule.kt:327` whenever
 * `AppForegroundedSingleton.isForegrounded` is false — and that singleton is set
 * ONLY by Activity lifecycle hooks (`LocationModule.kt:374-380`). An Android Auto
 * car-only launch creates no Activity, so on the exact path this module exists
 * for, the refusal is not an edge case: it is the guaranteed outcome.
 *
 * Matched on the derived code AND the message text, because the code string is
 * generated by expo-modules from the Kotlin class name and is not part of any
 * public contract we can pin.
 */
function isForegroundServiceRefusal(e: unknown): boolean {
  const err = e as { code?: unknown; message?: unknown };
  const code = String(err?.code ?? '');
  const message = String(err?.message ?? '');
  if (/FOREGROUND_SERVICE_START_NOT_ALLOWED/i.test(code)) return true;
  return /foreground service/i.test(message) && /background/i.test(message);
}

/**
 * One report per process, not per attempt. `startCarLocationService` is called on
 * every connect AND on carSession's 60s tick, so an unguarded report would emit
 * ~60 Crashlytics events per driver per hour of driving.
 */
let refusalReported = false;

/**
 * SecureStore is not free and this handler runs every ~2s, so the sign-out check
 * is throttled rather than run per fix. 30s of residual tracking for a driver who
 * just signed out is an acceptable trade against ~30 encrypted reads a minute;
 * running it per fix would make the privacy gate itself the battery cost.
 */
const SESSION_CHECK_INTERVAL_MS = 30_000;
let lastSessionCheckAt = 0;

/** @internal Test-only — resets every module-scope flag this file keeps. */
export function _resetCarLocationTelemetry(): void {
  refusalReported = false;
  lastSessionCheckAt = 0;
  currentMode = 'none';
}

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
  // Sign-out gate, mirroring handleBackgroundLocationTask.
  //
  // This is not belt-and-braces. The task registration is persisted by
  // expo-task-manager and restored on process start, so a device that signs out
  // while plugged in can be relaunched headlessly by a location broadcast with
  // no car connected and nobody to call stopCarLocationService() — and on the
  // fallback path there is no notification to make that visible. Without this
  // gate a signed-out driver's coordinates keep reaching the shared
  // last-location cache indefinitely. Self-heal by stopping: reaching here after
  // a sign-out means no teardown ran, so nothing else is going to stop it.
  const now = Date.now();
  if (now - lastSessionCheckAt >= SESSION_CHECK_INTERVAL_MS) {
    lastSessionCheckAt = now;
    if (await isSessionEnded()) {
      console.log('[CarLocation] session ended — stopping');
      await stopCarLocationService().catch(() => {});
      return;
    }
  }

  const locations = data?.locations ?? [];
  // Only the newest sample matters. This drives a map marker, not a route
  // history — replaying a deferred batch would rewind the marker across
  // positions the driver has already left.
  const latest = locations[locations.length - 1];
  if (!latest?.coords) return;

  // Display trust gate: a mock-location app must not be able to drive where
  // Spinr says the driver is — including in the shared last-location cache
  // that the phone dashboard reads back. Filtering is CORRECT here (display
  // only, nothing durable) — the capture paths persist first instead.
  const integrity = carIntegrity.check(latest);
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
 * The location request itself. Shared by both start modes so the ONLY difference
 * between them is the `foregroundService` block — nothing about accuracy or
 * cadence silently changes when we fall back.
 */
const BASE_OPTIONS: Location.LocationTaskOptions = {
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
};

const FOREGROUND_SERVICE_OPTIONS: Location.LocationTaskOptions = {
  ...BASE_OPTIONS,
  foregroundService: {
    notificationTitle: 'Spinr',
    notificationBody: 'Showing your location on the car screen',
    notificationColor: FOREGROUND_NOTIFICATION_COLOR,
    // NO killServiceOnDestroy. The flag is stored on the SHARED
    // LocationTaskService instance (LocationTaskService.kt onStartCommand
    // overwrites mKillService), so setting it here made swipe-from-recents
    // kill the dispatch task's on-trip tracking too — a flag that task never
    // opted into. Accepted residual: on an AA-only session, a swipe-away
    // leaves this display-only service (and its notification) until
    // didDisconnect or OS reclaim — it makes no network calls either way.
  },
};

/**
 * How the task is currently registered, so the 60s re-assert can tell
 * "nothing to do" from "running, but degraded and worth upgrading".
 *
 * Deliberately NOT persisted. After a headless process relaunch this reads
 * 'none' while the task may still be registered in either mode, so a degraded
 * task started before the restart is reported 'already-running' and never
 * upgraded. That is bounded — the next disconnect stops the task and the next
 * connect starts clean — and persisting it would mean a disk read on the
 * bundle-load path to buy back one upgrade attempt.
 */
let currentMode: 'none' | 'foreground-service' | 'no-notification' = 'none';

/** @internal Test-only. */
export function _carLocationMode(): typeof currentMode {
  return currentMode;
}

/**
 * Start the display-only service. Safe to call repeatedly; never throws.
 *
 * Deliberately silent about failure at the call site — every negative outcome
 * leaves the driver exactly where they were before (the in-hook foreground
 * watcher plus its staleness watchdog), which is today's behaviour. Losing the
 * upgrade is not worth an alert on a screen someone is driving in front of.
 */
export async function startCarLocationService(): Promise<CarLocationStart> {
  return runExclusive('car-start', startCarLocationServiceUnlocked);
}

async function startCarLocationServiceUnlocked(): Promise<CarLocationStart> {
  try {
    const alreadyRunning = await Location.hasStartedLocationUpdatesAsync(CAR_LOCATION_TASK);

    // A signed-out device must not run a location service at all, for any reason.
    // Checked AFTER the liveness probe on purpose: an orphaned task left by a
    // sign-out that happened while plugged in can then be reaped here, on the
    // 60s tick. Checking first would return 'session-ended' and stop nothing.
    if (await isSessionEnded()) {
      // No dispatch repair: a signed-out device has no dispatch tracking to
      // re-promote (teardown stopped it, or the bg handler is about to).
      if (alreadyRunning) await stopCarLocationServiceUnlocked({ repairDispatch: false });
      return 'session-ended';
    }
    // Running WITH a notification is finished business. Running without one is
    // not: Android refuses a background foreground-service start, but it stops
    // refusing the moment the phone app is foregrounded, so every re-assert is
    // a chance to upgrade. startLocationUpdatesAsync on a live task replaces its
    // options in place (same mechanism updateBackgroundLocationCadence relies
    // on), so the upgrade needs no stop/start and never drops a fix.
    if (alreadyRunning && currentMode !== 'no-notification') return 'already-running';

    // SINGLE-WRITER: the online driver's dispatch task is already up and
    // already publishing to carFixChannel — this task must not compete with
    // it (shared-service demotion + native fix dedup, see file header). Under
    // the arbiter lock this check is atomic with go-online's own sequence,
    // closing the race where the 60s car-session tick started this task in
    // the gap before startBackgroundLocation finished.
    if (await isBackgroundLocationRunning()) {
      // …and if OUR task is somehow still registered, stop it AND repair the
      // dispatch task the stop just demoted. Reachable since a degraded
      // ('no-notification') task deliberately falls past the already-running
      // check above to look for an upgrade: if the driver went online in the
      // meantime, this is where it lands.
      if (alreadyRunning) await stopCarLocationServiceUnlocked({ repairDispatch: true });
      return 'piggyback';
    }

    // NEVER request. See the file header.
    const { status } = await Location.getBackgroundPermissionsAsync();
    if (status !== 'granted') return 'no-permission';

    try {
      await Location.startLocationUpdatesAsync(CAR_LOCATION_TASK, FOREGROUND_SERVICE_OPTIONS);
      currentMode = 'foreground-service';
      return 'started';
    } catch (e) {
      if (!isForegroundServiceRefusal(e)) throw e;

      // The expected outcome on a car-only launch — see isForegroundServiceRefusal.
      //
      // Register the SAME task without the foregroundService block instead of
      // giving up. expo-location only throws when that block is present
      // (LocationModule.kt:327), and the consumer still calls
      // requestLocationUpdates either way — only maybeStartForegroundService is
      // gated. So this yields a live task with no notification.
      //
      // Whether Android then throttles it depends on our process importance
      // while Android Auto has our CarAppService bound, which is not knowable
      // from source. That is exactly what the fix-rate telemetry in carSession
      // measures. Worst case it is throttled and we are no worse off than
      // before this module existed; best case the marker is live and the driver
      // never sees a notification at all.
      if (!refusalReported) {
        refusalReported = true;
        recordNonFatal(e, {
          domain: 'drivers',
          module: 'androidAuto',
          reason: 'foreground_service_start_refused',
        });
      }
      try {
        await Location.startLocationUpdatesAsync(CAR_LOCATION_TASK, BASE_OPTIONS);
        currentMode = 'no-notification';
        console.warn('[CarLocation] foreground service refused — running without a notification');
        return 'started-no-notification';
      } catch (fallbackError) {
        console.warn('[CarLocation] fallback registration also failed:', fallbackError);
        return 'fgs-refused';
      }
    }
  } catch (e) {
    // Anything else — permission revoked mid-call, native module absent.
    console.warn('[CarLocation] could not start display service:', e);
    return 'unavailable';
  }
}

/**
 * Stop it. Unconditional and silent — `stopLocationUpdatesAsync` throws when the
 * task was never started, which is the normal case on most disconnects. The
 * public form repairs the dispatch task afterwards: stopping ANY location task
 * demotes the SHARED Android service (see file header), and the AA-disconnect
 * path was exactly how an on-trip driver's tracking lost its foreground
 * promotion (SPR-PE7TTB).
 */
export async function stopCarLocationService(): Promise<void> {
  return runExclusive('car-stop', () => stopCarLocationServiceUnlocked({ repairDispatch: true }));
}

/** @internal Composition primitive for callers already holding the arbiter lock. */
export async function stopCarLocationServiceUnlocked(
  options: { repairDispatch: boolean },
): Promise<void> {
  currentMode = 'none';
  try {
    await Location.stopLocationUpdatesAsync(CAR_LOCATION_TASK);
    console.log('[CarLocation] display service stopped');
  } catch {
    // Not running — nothing to do.
  }
  if (options.repairDispatch) {
    try {
      if (await isBackgroundLocationRunning()) {
        await reassertDispatchTaskUnlocked();
      }
    } catch {
      // Repair is best-effort; the 60s in-handler self-heal is the backstop.
    }
  }
}
