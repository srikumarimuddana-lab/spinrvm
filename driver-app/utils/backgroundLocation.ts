import * as Location from 'expo-location';
import * as TaskManager from 'expo-task-manager';
import * as SecureStore from 'expo-secure-store';
import spinrConfig from '@shared/config/spinr.config';
import { getAppCheckToken, initFirebaseServices } from '@shared/services/firebase';
import {
  drainTerminalAck,
  TERMINAL_STATUS_CODES,
  tripLocationRecorder,
  type TripLocationBatchRequest,
} from './tripLocationRecorder';
import { createLocationIntegrityChecker, haversineKm } from './locationIntegrity';
import { runExclusive } from './locationTaskArbiter';
import { recordNonFatal } from './crashlytics';
import { publishCarFix } from '../lib/androidAuto/carFixChannel';
import { SESSION_ENDED_KEY } from '@shared/auth/sessionMarker';

// Use the same backend-URL resolver as the shared API client — it carries the
// production fallback (api-spinr.spinr.ca) and the expoConfig.extra value.
// @shared/config's API_URL is env-var-only and resolves to '' on production /
// OTA builds that rely on the hardcoded fallback, which would make every
// headless request (token refresh, location batch) a silent no-op.
const API_URL = spinrConfig.backendUrl;

const TASK_NAME = 'spinr-background-location';

// This producer's own anti-spoof state — the teleport comparison is only
// meaningful between consecutive fixes from the SAME watcher (see
// locationIntegrity.ts). Gates DISPLAY (the car marker) only, never capture.
const bgIntegrity = createLocationIntegrityChecker();

// In-handler self-heal cadence (see the block in handleBackgroundLocationTask).
const ENSURE_PROMOTION_INTERVAL_MS = 60_000;
let lastEnsureAt = 0;

/** @internal Test-only — reset the self-heal throttle between cases. */
export function _resetBackgroundSelfHeal(): void {
  lastEnsureAt = 0;
}

/**
 * Accent for BOTH Spinr foreground-service notifications — this one and the
 * Android Auto display service (lib/androidAuto/carLocationTask.ts). Shared so
 * the two can never drift: a driver seeing two Spinr notifications in different
 * colours would read as two apps. Predates the shared/theme palette and is not
 * one of its tokens; moving it onto `darkColors.primary` is a deliberate
 * separate change, since it alters an existing live notification.
 */
export const FOREGROUND_NOTIFICATION_COLOR = '#6C63FF';

// ── Geofence-based future-capture re-arm ────────────────────────────────
// A force-quit can suspend location delivery. Geofence re-entry may wake the
// app and re-arm future tracking, but it cannot reconstruct samples missed
// while the process was not running. Durable samples already queued in SQLite
// remain available for later upload.
const GEOFENCE_TASK = 'spinr-geofence-recovery';
const GEOFENCE_RADIUS_M = 500;          // 500m boundary — re-arms ~1 block away
const GEOFENCE_ID = 'spinr-recovery';
// Persisted across app death so the headless geofence-recovery task knows
// whether to re-arm at trip cadence (a ride is active) or idle cadence.
const TRIP_ACTIVE_KEY = 'spinr_bg_trip_active';

// ── Geofence re-arm gating ──────────────────────────────────────────────
// The exit handler re-arms the geofence, and every startGeofencingAsync makes
// CoreLocation re-evaluate region state. Ungated, a moving driver re-arms
// continuously and floods EXTaskService's pending-event array — the
// NSRangeException crash.
//
// The gate is *displacement*, not elapsed time. A re-arm exists to recentre the
// region, so "have we moved?" is the semantically correct question, and because
// the centre is persisted it still works on a headless wake — where module
// state starts fresh and a time-based debounce would be dead code in exactly
// the path this module exists for. Displacement also can't be jammed by a
// backward wall-clock correction the way a Date.now() debounce can.
const GEOFENCE_MIN_DISPLACEMENT_M = 150;
// Device-local, encrypted (same store as TRIP_ACTIVE_KEY). Operational state,
// never logged or sent anywhere.
const GEOFENCE_CENTRE_KEY = 'spinr_bg_geofence_centre';
// Best-effort coalescing of concurrent stop/start pairs into CLLocationManager
// (go-online racing a headless exit wake). A check-then-act window remains;
// worst case is one redundant re-arm, which the displacement gate then absorbs.
let _geofenceRearmPromise: Promise<boolean> | null = null;

// Monotonic fence so a stop is always authoritative regardless of timing.
// Bumped synchronously (no await between read and write, so no interleaving
// is possible) at the start of every stopGeofenceRecovery call. Any arm that
// captured an older value self-detects it has been superseded — even one that
// slips in during stopGeofenceRecovery's own await window — and undoes itself
// instead of leaving a stop-requested driver geofenced.
let _geofenceGeneration = 0;

// Upper bound on how long stopGeofenceRecovery waits for an in-flight arm
// before proceeding anyway. Waiting first (rather than not waiting at all)
// avoids the common case of two concurrent native geofencing calls — the
// class of bug this module exists to prevent — but an unbounded wait would
// let a hung native call (getBackgroundPermissionsAsync/startGeofencingAsync)
// stall stopGeofenceRecovery forever, and with it toggleOnline's `finally`
// (see useDriverDashboard.ts toggleOnline), permanently disabling the online
// reconcile effect. The generation fence makes it safe to give up: a stale
// arm that resolves after the timeout will still self-correct.
const GEOFENCE_STOP_WAIT_TIMEOUT_MS = 8_000;

/** @internal Test-only — reset in-flight gating state between test cases. */
export function _resetGeofenceDebounce(): void {
  _geofenceRearmPromise = null;
  _geofenceGeneration = 0;
}

async function _readGeofenceCentre(): Promise<{ lat: number; lng: number } | null> {
  try {
    const raw = await SecureStore.getItemAsync(GEOFENCE_CENTRE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (typeof parsed?.lat !== 'number' || typeof parsed?.lng !== 'number') return null;
    return { lat: parsed.lat, lng: parsed.lng };
  } catch {
    // Unreadable/corrupt — treat as "no centre" so we re-arm rather than skip.
    return null;
  }
}

/**
 * Skip a re-arm only when a geofence is *genuinely* armed and the driver has
 * not moved far enough from its centre to need recentring. Anything uncertain
 * (probe throws, no persisted centre) resolves to "do not skip" — leaving a
 * driver with no recovery geofence is far worse than one redundant re-arm.
 */
async function _shouldSkipRearm(lat: number, lng: number): Promise<boolean> {
  let armed: boolean;
  try {
    armed = await Location.hasStartedGeofencingAsync(GEOFENCE_TASK);
  } catch (e) {
    recordNonFatal(e, {
      domain: 'drivers', surface: 'driver-app', geofence: 'liveness_probe_failed',
    });
    return false;
  }
  if (!armed) return false;

  const centre = await _readGeofenceCentre();
  if (!centre) return false;

  return haversineKm(lat, lng, centre.lat, centre.lng) * 1000 < GEOFENCE_MIN_DISPLACEMENT_M;
}

type LocationTaskData = {
  locations: Location.LocationObject[];
};

/**
 * Whether the session on this device has ended.
 *
 * Reads the explicit marker the auth store writes on logout and on the
 * no-valid-token path, rather than inferring from absent tokens. Same
 * positive-evidence rule as the backend tombstone, and for the same reason: an
 * unreadable SecureStore or a partially-cleared token set must not read as
 * "signed out". A false "signed out" here would drop a working driver's trip
 * samples and stop tracking mid-trip, costing billed distance and leaving a gap
 * in the SGI per-insurance-period audit trail. See shared/auth/sessionMarker.ts
 * for the full rationale.
 *
 * Every ambiguous state resolves to false ("session still live, keep tracking").
 *
 * Consulted by EVERY capture start/recover path (startBackgroundLocation,
 * recoverTripLocation, the geofence handler, the car-task start) as well as
 * the running handlers: recoverTripLocation is reachable headless via the FCM
 * location_health nudge, so "the foreground auth store knows best" stopped
 * being true for the start paths. The fail-open-on-store-error design above
 * means a transient SecureStore hiccup still never blocks a legitimate
 * go-online — only a positively recorded sign-out refuses a start.
 */
export async function isSessionEnded(): Promise<boolean> {
  try {
    return (await SecureStore.getItemAsync(SESSION_ENDED_KEY)) !== null;
  } catch {
    // Unreadable store is not evidence of a sign-out.
    return false;
  }
}

/**
 * Get a valid access token for the background task.
 *
 * Strategy: read the foreground-persisted access token from SecureStore.
 * The foreground proactive refresh (2-min buffer) keeps it fresh far more
 * often than the background cadence needs. If the foreground token is
 * expired or absent, return null — the caller defers the upload to the
 * durable SQLite outbox, which the foreground flushes on resume.
 *
 * The background task NEVER calls /auth/refresh itself. Two independent
 * refresh actors sharing one single-use rotating credential caused the
 * foreground/background rotation race that triggered driver sign-outs —
 * especially right after ride completion when both contexts fire
 * concurrently during the completion burst.
 */
export async function getBackgroundAuthToken(): Promise<string | null> {
  if (!API_URL) return null;

  // 1. Try the background-cached access token (set by setTokens flow
  //    or a prior successful read below).
  try {
    const cached = await SecureStore.getItemAsync('bg_access_token');
    const cachedExpiry = await SecureStore.getItemAsync('bg_access_token_expires');
    if (cached && cachedExpiry) {
      const expiresAt = parseInt(cachedExpiry, 10);
      if (Date.now() < expiresAt - 60_000) {
        return cached;
      }
    }
  } catch {
    // SecureStore read failed — fall through
  }

  // 2. Read the foreground's persisted access token. The foreground writes
  //    it on every setTokens() call (authStore.ts:245).
  try {
    const fgToken = await SecureStore.getItemAsync('fg_access_token');
    const fgExpiry = await SecureStore.getItemAsync('token_expires_at');
    if (fgToken && fgExpiry) {
      const expiresAt = parseInt(fgExpiry, 10);
      if (Date.now() < expiresAt - 30_000) {
        // Cache it for subsequent background fires within this process
        await SecureStore.setItemAsync('bg_access_token', fgToken);
        await SecureStore.setItemAsync('bg_access_token_expires', fgExpiry);
        return fgToken;
      }
    }
  } catch {
    // SecureStore read failed — fall through
  }

  // 3. No valid token available — defer the upload. Points stay in SQLite
  //    and the foreground flushes them on resume or next interval.
  return null;
}

export async function handleBackgroundLocationTask({ data, error }: { data?: LocationTaskData; error?: { message?: string } | null }): Promise<void> {
  if (error) {
    console.error('[BgLocation] Task error:', error.message);
    recordNonFatal(new Error(`BgLocation task error: ${error.message}`), { domain: 'drivers', surface: 'driver-app' });
    return;
  }
  // Session gate, ahead of any persistence. A signed-out driver's coordinates
  // must not reach the durable outbox at all — recordNativeFix below writes raw
  // lat/lng to SQLite before any auth work happens, so gating only the upload
  // would still leave PII at rest on the device. Self-heal by stopping the task:
  // reaching here after a sign-out means no teardown ran (session ended by
  // expiry via clearAuthStorage, stale build, or killed mid-logout), so nothing
  // else is going to stop it.
  if (await isSessionEnded()) {
    console.log('[BgLocation] Session ended — dropping samples and stopping');
    await stopBackgroundLocation().catch(() => {});
    await stopGeofenceRecovery().catch(() => {});
    return;
  }

  for (const location of data?.locations ?? []) {
    // CAPTURE BEFORE FILTER. Durable persistence comes first, unconditionally:
    // a fix that never reaches the outbox is route history lost forever
    // (SPR-PE7TTB lost 83% of a trip to silent client-side drops). The v2
    // point carries the `mocked` flag to the server, and settlement filtering
    // (backend utils/trip_distance.py spike/speed/gap caps) owns billing
    // quality — the client integrity check below gates DISPLAY only.
    try {
      // Durable persistence is deliberately before auth/network work. The
      // recorder keeps every native sample across process restarts.
      await tripLocationRecorder.recordNativeFix(location, 'background');
    } catch (e) {
      // No raw coordinates in logs; a later native callback can still queue.
      console.warn('[BgLocation] Failed to persist a native location sample');
      recordNonFatal(e, { domain: 'drivers', surface: 'driver-app' });
    }

    // Display trust gate — a mocked/teleporting/impossibly-fast sample must
    // not move the car marker. Reason string only, never coordinates.
    const integrity = bgIntegrity.check(location);
    if (!integrity.trusted) {
      console.warn(`[BgLocation] Untrusted sample kept for audit, hidden from display: ${integrity.reason}`);
      continue;
    }
    // Feed the Android Auto map. This service already holds a foreground-service
    // notification, so while it runs the car needs no second one of its own —
    // carLocationTask.startCarLocationService() defers to it for exactly this
    // reason. Device-local only: publishCarFix updates the in-memory marker and
    // the existing `spinr_driver_last_location` cache, and sends nothing.
    publishCarFix({
      latitude: location.coords.latitude,
      longitude: location.coords.longitude,
      heading: location.coords.heading ?? null,
    });
  }

  // Periodic self-heal: re-assert this task's options (~1/min) so a demoted
  // shared service re-promotes even with no Android Auto events and no live
  // WebSocket — the cross-context repair the per-JS-context arbiter cannot
  // provide. Runs only on the live-session path (the session-ended gate above
  // returned already). Cost when healthy: one native probe + one SecureStore
  // read + one setOptions per minute — the same budget as the AA 60s tick.
  const ensureNow = Date.now();
  if (ensureNow - lastEnsureAt >= ENSURE_PROMOTION_INTERVAL_MS) {
    lastEnsureAt = ensureNow;
    await reassertDispatchTask().catch(() => {});
  }

  const token = await getBackgroundAuthToken();
  if (!token || !API_URL) return;

  try {
    await tripLocationRecorder.flushPending(async (request: TripLocationBatchRequest) => {
      // App Check is enforced on /api/* in production. Initialize it
      // idempotently because this headless task does not mount the app shell.
      await initFirebaseServices();
      const appCheckToken = await getAppCheckToken();
      const response = await fetch(`${API_URL}/api/v1/drivers/location-batch`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
          ...(appCheckToken ? { 'X-Firebase-AppCheck': appCheckToken } : {}),
        },
        body: JSON.stringify(request),
      });
      // Terminal statuses drain the batch, mirroring apiLocationBatchTransport:
      // this fetch previously threw on them, and because flushPending aborts
      // its whole loop on a transport throw, ONE permanently-rejected batch
      // (e.g. 422 outside the completed-ride retention window) blocked every
      // other pending session's upload forever from the background path.
      if (TERMINAL_STATUS_CODES.has(response.status)) {
        console.warn(`[BgLocation] Draining terminally-rejected batch (${response.status})`);
        return drainTerminalAck(request);
      }
      if (!response.ok) throw new Error(`location-batch ${response.status}`);
      return response.json();
    }, { force: true });
  } catch {
    // Degraded-but-recovered, so no Sentry (CLAUDE.md observability rules):
    // points stay durable in SQLite and retry. At TRIP_CADENCE this catch runs
    // every ~4s, and flushPending is called with { force: true }, so reporting
    // each deferred upload would emit ~15 events/min/driver for the whole
    // duration of a backend or network outage.
    console.warn('[BgLocation] Durable upload deferred');
  }
}

TaskManager.defineTask<LocationTaskData>(TASK_NAME, handleBackgroundLocationTask);

export interface BgLocationConfig {
  timeInterval?: number;
  distanceInterval?: number;
  accuracy?: Location.Accuracy;
}

// Idle cadence: coarse + battery-friendly — the driver is online but not on a
// trip, so a rough live marker is enough. Trip cadence: dense + high accuracy
// so a trip stays well-sampled even while the app is backgrounded (driver in
// Google Maps / screen locked) — exactly when the foreground watchPositionAsync
// stops firing. Billed distance is settled from these breadcrumbs, so
// under-sampling here directly undercounts km and the SGI per-period audit.
export const IDLE_CADENCE: BgLocationConfig = {
  timeInterval: 30_000,
  distanceInterval: 50,
  accuracy: Location.Accuracy.Balanced,
};
export const TRIP_CADENCE: BgLocationConfig = {
  timeInterval: 4_000,
  distanceInterval: 10,
  accuracy: Location.Accuracy.High,
};

async function _applyTaskOptions(config?: BgLocationConfig): Promise<void> {
  const interval = config?.timeInterval ?? IDLE_CADENCE.timeInterval!;
  const distance = config?.distanceInterval ?? IDLE_CADENCE.distanceInterval!;

  await Location.startLocationUpdatesAsync(TASK_NAME, {
    accuracy: config?.accuracy ?? Location.Accuracy.Balanced,
    timeInterval: interval,
    distanceInterval: distance,
    deferredUpdatesInterval: interval,
    showsBackgroundLocationIndicator: true,
    // iOS: prevent CoreLocation from silently pausing updates when the
    // driver appears stationary (red light, loading zone, traffic jam).
    pausesUpdatesAutomatically: false,
    activityType: Location.ActivityType.AutomotiveNavigation,
    foregroundService: {
      notificationTitle: 'Spinr Driver',
      notificationBody: "You're online and receiving ride requests",
      notificationColor: '#6C63FF',
    },
  });
}

/**
 * Whether the dispatch location service is up.
 *
 * Exported for lib/androidAuto/carLocationTask.ts, which must not start its own
 * foreground service alongside this one — expo-location mints a service (and a
 * notification) per task name, so two running tasks means two permanent Spinr
 * notifications. Never throws; an unreadable probe answers "yes, assume it is
 * running", because a missing car marker is a far smaller failure than a
 * duplicate notification on every driver's phone.
 */
export async function isBackgroundLocationRunning(): Promise<boolean> {
  try {
    return await Location.hasStartedLocationUpdatesAsync(TASK_NAME);
  } catch {
    return true;
  }
}

export async function startBackgroundLocation(config?: BgLocationConfig): Promise<boolean> {
  // A signed-out device must never (re)start location capture, from ANY
  // caller — go-online, geofence recovery, or the FCM location_health nudge
  // (which runs headless, where "the foreground auth store knows best" does
  // not apply). Safe for the legitimate go-online path by construction:
  // isSessionEnded() fails OPEN on store errors, and setTokens() deletes the
  // marker on sign-in before any go-online can run. Checked before the
  // liveness probe and the permission flow so a headless resurrection can
  // never pop a permission dialog on a signed-out device. If an orphaned
  // task is somehow still registered, reap it here rather than adopting it.
  if (await isSessionEnded()) {
    recordNonFatal(new Error('start refused: session ended'), {
      domain: 'drivers', surface: 'driver-app', location: 'bg_start_refused_signed_out',
    });
    await stopBackgroundLocation().catch(() => {});
    return false;
  }

  const isRunning = await Location.hasStartedLocationUpdatesAsync(TASK_NAME);
  if (isRunning) {
    console.log('[BgLocation] Already running');
    return true;
  }

  // Permission flow stays OUTSIDE the arbiter lock: the request can hold a
  // system dialog open for minutes, and holding the lock through it would
  // starve go-offline/AA transitions into the bounded-wait timeout.
  let { status } = await Location.getBackgroundPermissionsAsync();
  if (status !== 'granted') {
    const res = await Location.requestBackgroundPermissionsAsync();
    status = res.status;
  }
  if (status !== 'granted') {
    console.warn('[BgLocation] Background permission not granted');
    return false;
  }

  return runExclusive('bg-start', async () => {
    // Re-probe under the lock — a queued car-task start may have raced us.
    if (await Location.hasStartedLocationUpdatesAsync(TASK_NAME)) return true;

    // SINGLE-WRITER, order matters: stop the car task BEFORE promoting this
    // one. expo-location runs ALL location tasks through ONE shared Android
    // service, and stopping any task demotes it (stopForeground on the shared
    // instance) — the previous stop-after-promote order un-promoted the
    // service we had just promoted on the go-online-while-plugged-in path.
    // Overlap is also actively lossy: a static native dedup discards one
    // consumer's copy of every fix while two tasks are registered. Lazily
    // required to keep the module cycle (carLocationTask imports this file)
    // from resolving at load time; runs on iOS too (harmless — nothing starts
    // that task off Android).
    try {
      // Unlocked variant — we hold the arbiter lock; repairDispatch false
      // because the promotion on the next line supersedes any repair.
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      await require('../lib/androidAuto/carLocationTask').stopCarLocationServiceUnlocked({
        repairDispatch: false,
      });
    } catch {
      // Android Auto layer absent — nothing to stand down.
    }

    await _applyTaskOptions(config);
    console.log('[BgLocation] Started');
    return true;
  });
}

/**
 * Re-assert the dispatch task's options in place — re-promoting the shared
 * Android location service if something (an Android Auto task stop, an OS
 * hiccup) demoted it. Never STARTS the task (that is go-online's job) and
 * never throws. Composition primitive: callers already holding the arbiter
 * lock use this; everyone else uses reassertDispatchTask().
 */
export async function reassertDispatchTaskUnlocked(): Promise<void> {
  try {
    if (!(await Location.hasStartedLocationUpdatesAsync(TASK_NAME))) return;
    const { status } = await Location.getBackgroundPermissionsAsync();
    if (status !== 'granted') return;
    let tripActive = false;
    try {
      tripActive = (await SecureStore.getItemAsync(TRIP_ACTIVE_KEY)) === 'true';
    } catch {
      // Unreadable flag → idle cadence; the ride-state effect re-tightens it.
    }
    // startLocationUpdatesAsync on a live task replaces options in place and
    // re-runs the native foreground promotion — the repair we're here for.
    await _applyTaskOptions(tripActive ? TRIP_CADENCE : IDLE_CADENCE);
    console.log('[BgLocation] Dispatch task re-asserted');
  } catch (e) {
    recordNonFatal(e, { domain: 'drivers', surface: 'driver-app', location: 'reassert_failed' });
  }
}

export function reassertDispatchTask(): Promise<void> {
  return runExclusive('bg-reassert', reassertDispatchTaskUnlocked);
}

/**
 * Re-tune the cadence/accuracy of the *already-running* background task —
 * tighten to TRIP_CADENCE while a ride is active so a backgrounded trip is
 * still sampled densely, relax to IDLE_CADENCE when idle. No-op if the task
 * isn't registered (go-online hasn't started it yet) or permission was
 * revoked. Calling startLocationUpdatesAsync on a live task replaces its
 * options in place — the task identity and handler are unchanged.
 */
export async function updateBackgroundLocationCadence(config: BgLocationConfig): Promise<void> {
  return runExclusive('bg-cadence', async () => {
    const isRunning = await Location.hasStartedLocationUpdatesAsync(TASK_NAME);
    if (!isRunning) return;
    const { status } = await Location.getBackgroundPermissionsAsync();
    if (status !== 'granted') return;
    await _applyTaskOptions(config);
    console.log(
      `[BgLocation] Cadence updated (t=${config.timeInterval ?? IDLE_CADENCE.timeInterval}ms ` +
        `d=${config.distanceInterval ?? IDLE_CADENCE.distanceInterval}m)`,
    );
  });
}

/**
 * Recover trip location tracking after a server `location_health` nudge (the
 * backend saw the trip stop reporting). If the background task died — force
 * quit, OS-killed — restart it at TRIP_CADENCE; if it's alive, re-assert the
 * dense trip cadence in place. Returns whether tracking is running afterward so
 * the caller can surface a "check location permission" banner. Never throws.
 */
export async function recoverTripLocation(): Promise<boolean> {
  try {
    // Reachable HEADLESS (FCM location_health data push) as well as from the
    // dashboard WS handler. The server's gap monitor does not know the driver
    // signed out — an abandoned in_progress ride keeps nudging — so without
    // this gate a push could restart GPS capture on a signed-out device,
    // repeatedly. startBackgroundLocation now refuses too; this earlier check
    // also keeps the cadence path from touching an orphaned task.
    if (await isSessionEnded()) {
      recordNonFatal(new Error('recover refused: session ended'), {
        domain: 'drivers', surface: 'driver-app', location: 'recover_refused_signed_out',
      });
      await stopBackgroundLocation().catch(() => {});
      return false;
    }
    const isRunning = await Location.hasStartedLocationUpdatesAsync(TASK_NAME);
    if (!isRunning) {
      return await startBackgroundLocation(TRIP_CADENCE);
    }
    await updateBackgroundLocationCadence(TRIP_CADENCE);
    return true;
  } catch (e) {
    console.warn('[BgLocation] recoverTripLocation failed', e);
    return false;
  }
}

export async function stopBackgroundLocation(): Promise<void> {
  return runExclusive('bg-stop', stopBackgroundLocationUnlocked);
}

async function stopBackgroundLocationUnlocked(): Promise<void> {
  // The native stop is conditional (stopLocationUpdatesAsync throws when the
  // task was never started) but the key cleanup is NOT. It used to sit behind
  // the same early return, so a stop that ran while the task happened to be
  // down — the exact state after an OS kill, or on the logout path — left
  // bg_access_token behind. That cached token is a live driver JWT: the
  // headless task kept using it to upload a signed-out driver's GPS for the
  // remainder of its 15-minute lifetime.
  let isRunning = false;
  try {
    isRunning = await Location.hasStartedLocationUpdatesAsync(TASK_NAME);
  } catch (e) {
    // Probe failed — attempt the stop anyway rather than skipping cleanup.
    recordNonFatal(e, {
      domain: 'drivers', surface: 'driver-app', location: 'stop_liveness_probe_failed',
    });
    isRunning = true;
  }
  if (isRunning) {
    try {
      await Location.stopLocationUpdatesAsync(TASK_NAME);
    } catch (e) {
      // Never let a native stop failure strand the credential cleanup below.
      recordNonFatal(e, {
        domain: 'drivers', surface: 'driver-app', location: 'stop_updates_failed',
      });
    }
  }

  // Clear cached background credentials + operational state unconditionally so
  // a new sign-in starts fresh and a signed-out one retains nothing usable.
  await SecureStore.deleteItemAsync('bg_access_token').catch(() => {});
  await SecureStore.deleteItemAsync('bg_access_token_expires').catch(() => {});
  await SecureStore.deleteItemAsync(TRIP_ACTIVE_KEY).catch(() => {});
  console.log('[BgLocation] Stopped');
}

/**
 * Persist whether a ride is active so the headless geofence task can re-arm
 * future tracking at trip cadence. It has no access to React/zustand state.
 */
export async function setBackgroundTripActive(active: boolean): Promise<void> {
  try {
    if (active) {
      await SecureStore.setItemAsync(TRIP_ACTIVE_KEY, 'true');
    } else {
      await SecureStore.deleteItemAsync(TRIP_ACTIVE_KEY);
    }
  } catch {
    // Best-effort — future tracking falls back to idle cadence if unreadable.
  }
}

// ── Geofence recovery task ─────────────────────────────────────────────
// Fires when the driver crosses the boundary. On exit, we re-arm
// background location (a no-op if it's already running) and re-register
// a fresh geofence around the new position. The task runs in a headless
// JS context — no React, no zustand, no shared in-memory state.
TaskManager.defineTask(GEOFENCE_TASK, async ({ data, error }) => {
  if (error) {
    console.error('[Geofence] Task error:', error.message);
    recordNonFatal(new Error(`Geofence task error: ${error.message}`), { domain: 'drivers', surface: 'driver-app' });
    return;
  }
  const payload = data as
    | { eventType?: Location.GeofencingEventType; region?: Location.LocationRegion }
    | undefined;
  if (!payload || !('eventType' in payload) || payload.eventType !== Location.GeofencingEventType.Exit) return;

  try {
    // 0. Session gate. This handler runs headless, with no React and no zustand,
    //    so it cannot see the auth store — which is precisely how a signed-out
    //    driver kept getting tracked: the exit handler re-armed location updates
    //    and re-centred the fence indefinitely, resurrecting tracking even after
    //    the OS killed the app. Read the persisted end-of-session marker and
    //    tear down instead of re-arming.
    if (await isSessionEnded()) {
      console.log('[Geofence] Session ended — tearing down instead of re-arming');
      await stopBackgroundLocation().catch(() => {});
      await stopGeofenceRecovery().catch(() => {});
      return;
    }

    // 1. Re-arm future background tracking if it is no longer running.
    //    startBackgroundLocation short-circuits if it is already running.
    const isRunning = await Location.hasStartedLocationUpdatesAsync(TASK_NAME);
    if (!isRunning) {
      // Use trip cadence when the persisted flag says a ride is active. This
      // affects only subsequent fixes; it does not claim to recover missed ones.
      const tripActive = (await SecureStore.getItemAsync(TRIP_ACTIVE_KEY).catch(() => null)) === 'true';
      await startBackgroundLocation(tripActive ? TRIP_CADENCE : undefined);
      console.log(`[Geofence] Re-armed future background tracking (cadence=${tripActive ? 'trip' : 'idle'})`);
    }

    // 2. Refresh the geofence around the current location so subsequent
    //    movement keeps triggering re-arms. Without this, the driver
    //    exits the original geofence once and we never get another wake.
    //    refreshGeofence inherits the debounce + single-flight guards from
    //    startGeofenceRecovery, preventing the feedback loop that caused
    //    the NSRangeException crash.
    const loc = await Location.getCurrentPositionAsync({
      accuracy: Location.Accuracy.Balanced,
    }).catch(() => null);
    if (loc) {
      await refreshGeofence(loc.coords.latitude, loc.coords.longitude);
    }
  } catch (e) {
    console.warn('[Geofence] Exit handler failed:', e);
    recordNonFatal(e, { domain: 'drivers', surface: 'driver-app' });
  }
});

async function _startGeofenceRecoveryInner(lat: number, lng: number, myGeneration: number): Promise<boolean> {
  const { status } = await Location.getBackgroundPermissionsAsync();
  if (status !== 'granted') {
    console.warn('[Geofence] Background permission not granted; skipping recovery');
    return false;
  }

  // stopGeofencingAsync is called unconditionally — it throws harmlessly when
  // nothing is monitored, and gating it on a probe risks skipping the stop and
  // letting regions accumulate (the original isTaskRegisteredAsync bug).
  try {
    await Location.stopGeofencingAsync(GEOFENCE_TASK);
  } catch {
    // Throws with "not monitoring" on the very first arm of every session and
    // any time monitoring has already been torn down — the expected path, not
    // an error. Reporting it would emit a fleet-wide event per driver per
    // go-online. Region accumulation is caught by the hasStartedGeofencingAsync
    // probe in _shouldSkipRearm instead.
  }

  // A stopGeofenceRecovery call bumps the generation synchronously, so if one
  // arrived while we were awaiting permissions/stop above, ours is stale — the
  // driver asked to go offline mid-arm. Don't arm on behalf of a request that
  // no longer holds.
  if (myGeneration !== _geofenceGeneration) return false;

  await Location.startGeofencingAsync(GEOFENCE_TASK, [
    {
      identifier: GEOFENCE_ID,
      latitude: lat,
      longitude: lng,
      radius: GEOFENCE_RADIUS_M,
      notifyOnEnter: false,
      notifyOnExit: true,
    },
  ]);

  // Re-check after the native call too: stopGeofenceRecovery may have given up
  // waiting for us (GEOFENCE_STOP_WAIT_TIMEOUT_MS) and already run its own
  // stop while we were still arming. Leaving ourselves armed at that point is
  // exactly the "offline driver still geofenced" bug — undo immediately.
  if (myGeneration !== _geofenceGeneration) {
    await Location.stopGeofencingAsync(GEOFENCE_TASK).catch(() => {});
    await SecureStore.deleteItemAsync(GEOFENCE_CENTRE_KEY).catch(() => {});
    console.log('[Geofence] Arm superseded by a stop; undone');
    return false;
  }

  // Persist the centre so the displacement gate survives process death.
  await SecureStore.setItemAsync(GEOFENCE_CENTRE_KEY, JSON.stringify({ lat, lng }))
    .catch(() => { /* Gate degrades to "always re-arm" — safe direction. */ });
  // PIPEDA: never log raw lat/lng.
  console.log(`[Geofence] Armed (r=${GEOFENCE_RADIUS_M}m)`);
  return true;
}

/**
 * Arm (or recentre) the recovery geofence around a position.
 *
 * Returns `true` only when a geofence is actually armed — either because this
 * call armed one, or because one is already armed close enough to `lat`/`lng`
 * that recentring would be pointless. It never returns `true` speculatively:
 * `useDriverDashboard.toggleOnline` and the P0/P1 remediation plan both treat
 * the return value as "the driver has a geofence".
 */
export async function startGeofenceRecovery(lat: number, lng: number): Promise<boolean> {
  if (await _shouldSkipRearm(lat, lng)) {
    console.log('[Geofence] Still armed and driver has not moved enough to recentre');
    return true;
  }

  if (_geofenceRearmPromise) {
    // Wait for the in-flight arm, but don't adopt its result: it may have
    // centred somewhere materially different, and its rejection isn't ours.
    await _geofenceRearmPromise.catch(() => {});
    if (await _shouldSkipRearm(lat, lng)) return true;
  }

  const myGeneration = _geofenceGeneration;
  _geofenceRearmPromise = _startGeofenceRecoveryInner(lat, lng, myGeneration).finally(() => {
    _geofenceRearmPromise = null;
  });
  return _geofenceRearmPromise;
}

export async function stopGeofenceRecovery(): Promise<void> {
  // Bump the fence synchronously, before any await — this is what makes stop
  // authoritative no matter how the timing works out. Any arm already in
  // flight (or one that slips in during this function's own await window)
  // captured or will capture an older generation, and self-corrects in
  // _startGeofenceRecoveryInner instead of leaving a stop-requested driver
  // geofenced.
  _geofenceGeneration++;

  // Best-effort: still wait for an in-flight arm so the common case doesn't
  // run two native geofencing calls concurrently (the class of bug this
  // module exists to prevent), but cap the wait — the generation fence above
  // makes it safe to give up rather than risk stalling the caller
  // (toggleOnline) forever on a hung native call.
  const inFlight = _geofenceRearmPromise;
  _geofenceRearmPromise = null;
  if (inFlight) {
    await Promise.race([
      inFlight.catch(() => {}),
      new Promise<void>((resolve) => setTimeout(resolve, GEOFENCE_STOP_WAIT_TIMEOUT_MS)),
    ]);
  }

  // Clear the persisted centre so the displacement gate can never suppress a
  // later arm on stale state — a driver toggling offline and back on must
  // always end up with a geofence.
  await SecureStore.deleteItemAsync(GEOFENCE_CENTRE_KEY).catch(() => {});

  // Stop unconditionally rather than gating on a liveness probe: a probe-gated
  // stop can skip the teardown and let regions accumulate.
  try {
    await Location.stopGeofencingAsync(GEOFENCE_TASK);
    console.log('[Geofence] Disarmed');
  } catch {
    // Throws when nothing is monitored — the normal case here, not an error.
  }
}

export async function refreshGeofence(lat: number, lng: number): Promise<void> {
  // Wrapper for clarity at the call site — under the hood it's the same
  // as startGeofenceRecovery (which already stops + re-arms), and inherits
  // the debounce + single-flight guards.
  await startGeofenceRecovery(lat, lng);
}
