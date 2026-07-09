import * as Location from 'expo-location';
import * as TaskManager from 'expo-task-manager';
import * as SecureStore from 'expo-secure-store';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Platform } from 'react-native';
import SpinrConfig from '@shared/config/spinr.config';
import { getAppCheckToken, initFirebaseServices } from '@shared/services/firebase';

// Use the same backend-URL resolver as the shared API client — it carries the
// production fallback (api-spinr.spinr.ca) and the expoConfig.extra value.
// @shared/config's API_URL is env-var-only and resolves to '' on production /
// OTA builds that rely on the hardcoded fallback, which would make every
// headless request (token refresh, location batch) a silent no-op.
const API_URL = SpinrConfig.backendUrl;

const TASK_NAME = 'spinr-background-location';
const BG_LOCATION_QUEUE_KEY = 'spinr_bg_location_queue';
const BG_LOCATION_QUEUE_MAX_POINTS = 1000;

// ── Geofence-based killed-app recovery ─────────────────────────────────
// When the user force-swipes the app, Expo's foreground service is killed
// on Android and the iOS background task is suspended. Without recovery,
// the driver becomes invisible to dispatch until they manually reopen the
// app. Geofencing is OS-level and survives app kill: when the driver
// crosses the boundary, the OS wakes the app process, our task handler
// re-arms the foreground service, and tracking resumes.
const GEOFENCE_TASK = 'spinr-geofence-recovery';
const GEOFENCE_RADIUS_M = 500;          // 500m boundary — re-arms ~1 block away
const GEOFENCE_ID = 'spinr-recovery';
// Persisted across app death so the headless geofence-recovery task knows
// whether to re-arm at trip cadence (a ride is active) or idle cadence.
const TRIP_ACTIVE_KEY = 'spinr_bg_trip_active';

type LocationTaskData = {
  locations: Location.LocationObject[];
};

type BackgroundLocationPoint = {
  lat: number;
  lng: number;
  speed: number | null;
  heading: number | null;
  accuracy: number | null;
  timestamp: string;
  tracking_phase: 'background';
};

async function loadQueuedBackgroundPoints(): Promise<BackgroundLocationPoint[]> {
  try {
    const raw = await AsyncStorage.getItem(BG_LOCATION_QUEUE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

async function saveQueuedBackgroundPoints(points: BackgroundLocationPoint[]): Promise<void> {
  const capped = points.slice(-BG_LOCATION_QUEUE_MAX_POINTS);
  try {
    if (capped.length === 0) {
      await AsyncStorage.removeItem(BG_LOCATION_QUEUE_KEY);
    } else {
      await AsyncStorage.setItem(BG_LOCATION_QUEUE_KEY, JSON.stringify(capped));
    }
  } catch (e) {
    console.warn('[BgLocation] Failed to persist offline queue:', e);
  }
}

/**
 * Get a valid access token for the background task.
 *
 * The main app keeps the access token in memory only — it's wiped when the
 * app process dies. The background task runs in a fresh JS context with no
 * shared memory, so we MUST refresh via the refresh_token (which is persisted
 * to SecureStore) to get a fresh access token.
 */
export async function getBackgroundAuthToken(): Promise<string | null> {
  if (!API_URL) return null;

  // First try the in-memory persisted access token (set by setTokens flow below)
  try {
    const cached = await SecureStore.getItemAsync('bg_access_token');
    const cachedExpiry = await SecureStore.getItemAsync('bg_access_token_expires');
    if (cached && cachedExpiry) {
      const expiresAt = parseInt(cachedExpiry, 10);
      // Use cached token if it has >60s left
      if (Date.now() < expiresAt - 60_000) {
        return cached;
      }
    }
  } catch {
    // SecureStore read failed — fall through to refresh
  }

  // Refresh via refresh_token
  const refreshToken = await SecureStore.getItemAsync('refresh_token');
  if (!refreshToken) {
    console.warn('[BgLocation] No refresh token in SecureStore');
    return null;
  }

  try {
    // App Check is enforced on /api/* in production. Initialize it here first —
    // this headless task doesn't mount _layout, so initFirebaseServices() (which
    // configures the App Check provider) hasn't run yet; without it
    // getAppCheckToken() returns null. initFirebaseServices is idempotent.
    await initFirebaseServices();
    const appCheckToken = await getAppCheckToken();
    const resp = await fetch(`${API_URL}/api/v1/auth/refresh`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(appCheckToken ? { 'X-Firebase-AppCheck': appCheckToken } : {}),
      },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!resp.ok) {
      console.warn('[BgLocation] Refresh failed:', resp.status);
      return null;
    }
    const data = await resp.json() as {
      token: string;
      refresh_token: string;
      access_expires_at: string;
    };

    // Persist the new refresh token (rotated) and cache the access token
    // for subsequent background fires. The main app picks up the rotated
    // refresh token from SecureStore on next foreground.
    const expiresAtMs = new Date(data.access_expires_at).getTime();
    await SecureStore.setItemAsync('refresh_token', data.refresh_token);
    await SecureStore.setItemAsync('bg_access_token', data.token);
    await SecureStore.setItemAsync('bg_access_token_expires', String(expiresAtMs));

    return data.token;
  } catch (e) {
    console.warn('[BgLocation] Token refresh failed:', e);
    return null;
  }
}

export async function handleBackgroundLocationTask({ data, error }: { data?: LocationTaskData; error?: { message?: string } | null }): Promise<void> {
  if (error) {
    console.error('[BgLocation] Task error:', error.message);
    return;
  }
  if (!data?.locations?.length) return;

  // Filter out spoofed/mock locations
  const trusted = data.locations.filter((loc) => {
    if (Platform.OS === 'android' && loc.mocked === true) return false;
    if ((loc.coords.accuracy ?? 0) === 0) return false;
    if ((loc.coords.speed ?? 0) > 90) return false;
    return true;
  });
  if (!trusted.length) return;

  const points: BackgroundLocationPoint[] = trusted.map((loc) => ({
    lat: loc.coords.latitude,
    lng: loc.coords.longitude,
    speed: loc.coords.speed ?? null,
    heading: loc.coords.heading ?? null,
    accuracy: loc.coords.accuracy ?? null,
    timestamp: new Date(loc.timestamp).toISOString(),
    tracking_phase: 'background',
  }));
  const queued = await loadQueuedBackgroundPoints();
  const pointsToUpload = [...queued, ...points].slice(-BG_LOCATION_QUEUE_MAX_POINTS);

  const token = await getBackgroundAuthToken();
  if (!token || !API_URL) {
    await saveQueuedBackgroundPoints(pointsToUpload);
    return;
  }

  try {
    // App Check is enforced on /api/* in production. Initialize it (idempotent;
    // this headless task doesn't mount _layout) and attach the token so the
    // breadcrumb upload isn't 401'd — otherwise rider ETA / the period audit
    // go stale while the task logs the points as sent.
    await initFirebaseServices();
    const appCheckToken = await getAppCheckToken();
    const resp = await fetch(`${API_URL}/api/v1/drivers/location-batch`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
        ...(appCheckToken ? { 'X-Firebase-AppCheck': appCheckToken } : {}),
      },
      body: JSON.stringify({ points: pointsToUpload }),
    });
    if (!resp.ok) {
      throw new Error(`location-batch ${resp.status}`);
    }
    await saveQueuedBackgroundPoints([]);
    console.log(`[BgLocation] Sent ${pointsToUpload.length} points`);
  } catch (e) {
    console.warn('[BgLocation] Upload failed:', e);
    await saveQueuedBackgroundPoints(pointsToUpload);
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
  const isRunning = await TaskManager.isTaskRegisteredAsync(TASK_NAME);
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
  const isRunning = await TaskManager.isTaskRegisteredAsync(TASK_NAME);
  if (!isRunning) return;
  const { status } = await Location.getBackgroundPermissionsAsync();
  if (status !== 'granted') return;
  await _applyTaskOptions(config);
  console.log(
    `[BgLocation] Cadence updated (t=${config.timeInterval ?? IDLE_CADENCE.timeInterval}ms ` +
      `d=${config.distanceInterval ?? IDLE_CADENCE.distanceInterval}m)`,
  );
}

export async function stopBackgroundLocation(): Promise<void> {
  const isRunning = await TaskManager.isTaskRegisteredAsync(TASK_NAME);
  if (!isRunning) return;

  await Location.stopLocationUpdatesAsync(TASK_NAME);
  // Clear cached background token so a new sign-in starts fresh
  await SecureStore.deleteItemAsync('bg_access_token').catch(() => {});
  await SecureStore.deleteItemAsync('bg_access_token_expires').catch(() => {});
  await SecureStore.deleteItemAsync(TRIP_ACTIVE_KEY).catch(() => {});
  console.log('[BgLocation] Stopped');
}

/**
 * Persist whether a ride is active so the headless geofence-recovery task can
 * re-arm tracking at trip cadence after a force-kill (it has no access to the
 * React/zustand ride state). Best-effort.
 */
export async function setBackgroundTripActive(active: boolean): Promise<void> {
  try {
    if (active) {
      await SecureStore.setItemAsync(TRIP_ACTIVE_KEY, 'true');
    } else {
      await SecureStore.deleteItemAsync(TRIP_ACTIVE_KEY);
    }
  } catch {
    // best-effort — recovery falls back to idle cadence if unreadable
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
  if (!payload || payload.eventType !== Location.GeofencingEventType.Exit) return;

  try {
    // 1. Re-arm background tracking if it was killed. startBackgroundLocation
    //    short-circuits if the task is already running, so this is cheap.
    const isRunning = await TaskManager.isTaskRegisteredAsync(TASK_NAME);
    if (!isRunning) {
      // Re-arm at trip cadence if a ride was active when the app was killed,
      // otherwise the rest of the trip is sampled at the coarse idle cadence
      // and undercounts. The flag is persisted by setBackgroundTripActive
      // since this headless task can't read the React/zustand ride state.
      const tripActive = (await SecureStore.getItemAsync(TRIP_ACTIVE_KEY).catch(() => null)) === 'true';
      await startBackgroundLocation(tripActive ? TRIP_CADENCE : undefined);
      console.log(`[Geofence] Recovered background tracking after kill (cadence=${tripActive ? 'trip' : 'idle'})`);
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
