import * as Location from 'expo-location';
import * as TaskManager from 'expo-task-manager';
import * as SecureStore from 'expo-secure-store';
import spinrConfig from '@shared/config/spinr.config';
import { getAppCheckToken, initFirebaseServices } from '@shared/services/firebase';
import { tripLocationRecorder, type TripLocationBatchRequest } from './tripLocationRecorder';
import { checkLocationIntegrity } from './locationIntegrity';

// Use the same backend-URL resolver as the shared API client — it carries the
// production fallback (api-spinr.spinr.ca) and the expoConfig.extra value.
// @shared/config's API_URL is env-var-only and resolves to '' on production /
// OTA builds that rely on the hardcoded fallback, which would make every
// headless request (token refresh, location batch) a silent no-op.
const API_URL = spinrConfig.backendUrl;

const TASK_NAME = 'spinr-background-location';

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

type LocationTaskData = {
  locations: Location.LocationObject[];
};

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
    return;
  }
  for (const location of data?.locations ?? []) {
    // Same trust gate as the foreground watcher (useDriverDashboard): a
    // mocked/teleporting/impossibly-fast sample must not enter the durable
    // route history from either path. Reason string only — never coordinates.
    const integrity = checkLocationIntegrity(location);
    if (!integrity.trusted) {
      console.warn(`[BgLocation] Dropped untrusted sample: ${integrity.reason}`);
      continue;
    }
    try {
      // Durable persistence is deliberately before auth/network work. The
      // recorder keeps every accepted native sample across process restarts.
      await tripLocationRecorder.recordNativeFix(location, 'background');
    } catch {
      // No raw coordinates in logs; a later native callback can still queue.
      console.warn('[BgLocation] Failed to persist a native location sample');
    }
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
      if (!response.ok) throw new Error(`location-batch ${response.status}`);
      return response.json();
    }, { force: true });
  } catch {
    // Points stay in SQLite until the server returns a durable acknowledgement.
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

export async function startBackgroundLocation(config?: BgLocationConfig): Promise<boolean> {
  const isRunning = await Location.hasStartedLocationUpdatesAsync(TASK_NAME);
  if (isRunning) {
    console.log('[BgLocation] Already running');
    return true;
  }

  let { status } = await Location.getBackgroundPermissionsAsync();
  if (status !== 'granted') {
    const res = await Location.requestBackgroundPermissionsAsync();
    status = res.status;
  }
  if (status !== 'granted') {
    console.warn('[BgLocation] Background permission not granted');
    return false;
  }

  await _applyTaskOptions(config);
  console.log('[BgLocation] Started');
  return true;
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
  const isRunning = await Location.hasStartedLocationUpdatesAsync(TASK_NAME);
  if (!isRunning) return;
  const { status } = await Location.getBackgroundPermissionsAsync();
  if (status !== 'granted') return;
  await _applyTaskOptions(config);
  console.log(
    `[BgLocation] Cadence updated (t=${config.timeInterval ?? IDLE_CADENCE.timeInterval}ms ` +
      `d=${config.distanceInterval ?? IDLE_CADENCE.distanceInterval}m)`,
  );
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
  const isRunning = await Location.hasStartedLocationUpdatesAsync(TASK_NAME);
  if (!isRunning) return;

  await Location.stopLocationUpdatesAsync(TASK_NAME);
  // Clear cached background token so a new sign-in starts fresh
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
    return;
  }
  const payload = data as
    | { eventType?: Location.GeofencingEventType; region?: Location.LocationRegion }
    | undefined;
  if (!payload || !('eventType' in payload) || payload.eventType !== Location.GeofencingEventType.Exit) return;

  try {
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
    const loc = await Location.getCurrentPositionAsync({
      accuracy: Location.Accuracy.Balanced,
    }).catch(() => null);
    if (loc) {
      await refreshGeofence(loc.coords.latitude, loc.coords.longitude);
    }
  } catch (e) {
    console.warn('[Geofence] Exit handler failed:', e);
  }
});

export async function startGeofenceRecovery(lat: number, lng: number): Promise<boolean> {
  // Geofencing needs "always" location — same prerequisite as the main
  // background task, so by the time we get here startBackgroundLocation has
  // already prompted for it. Re-check defensively in case the user revoked.
  const { status } = await Location.getBackgroundPermissionsAsync();
  if (status !== 'granted') {
    console.warn('[Geofence] Background permission not granted; skipping recovery');
    return false;
  }

  // stopGeofencingAsync throws if the task isn't currently registered, so
  // gate on isTaskRegisteredAsync to avoid a noisy log on first start.
  try {
    const isRunning = await TaskManager.isTaskRegisteredAsync(GEOFENCE_TASK);
    if (isRunning) {
      await Location.stopGeofencingAsync(GEOFENCE_TASK);
    }
  } catch {
    // Swallow — best-effort cleanup before re-arming.
  }

  await Location.startGeofencingAsync(GEOFENCE_TASK, [
    {
      identifier: GEOFENCE_ID,
      latitude: lat,
      longitude: lng,
      radius: GEOFENCE_RADIUS_M,
      // Only wake on exit — enter would fire on every re-arm and burn
      // battery for no benefit.
      notifyOnEnter: false,
      notifyOnExit: true,
    },
  ]);
  // PIPEDA: never log raw lat/lng. Note radius only — the boundary is
  // the only useful diagnostic anyway.
  console.log(`[Geofence] Armed (r=${GEOFENCE_RADIUS_M}m)`);
  return true;
}

export async function stopGeofenceRecovery(): Promise<void> {
  const isRunning = await TaskManager.isTaskRegisteredAsync(GEOFENCE_TASK);
  if (!isRunning) return;
  await Location.stopGeofencingAsync(GEOFENCE_TASK);
  console.log('[Geofence] Disarmed');
}

export async function refreshGeofence(lat: number, lng: number): Promise<void> {
  // Wrapper for clarity at the call site — under the hood it's the same
  // as startGeofenceRecovery (which already stops + re-arms).
  await startGeofenceRecovery(lat, lng);
}
