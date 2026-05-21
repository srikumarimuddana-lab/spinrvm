import * as Location from 'expo-location';
import * as TaskManager from 'expo-task-manager';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';
import { API_URL } from '@shared/config';

const TASK_NAME = 'spinr-background-location';

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

type LocationTaskData = {
  locations: Location.LocationObject[];
};

/**
 * Get a valid access token for the background task.
 *
 * The main app keeps the access token in memory only — it's wiped when the
 * app process dies. The background task runs in a fresh JS context with no
 * shared memory, so we MUST refresh via the refresh_token (which is persisted
 * to SecureStore) to get a fresh access token.
 */
async function getBackgroundAuthToken(): Promise<string | null> {
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
    const resp = await fetch(`${API_URL}/api/v1/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
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

TaskManager.defineTask<LocationTaskData>(TASK_NAME, async ({ data, error }) => {
  if (error) {
    console.error('[BgLocation] Task error:', error.message);
    return;
  }
  if (!data?.locations?.length) return;

  const token = await getBackgroundAuthToken();
  if (!token || !API_URL) return;

  // Filter out spoofed/mock locations
  const trusted = data.locations.filter((loc) => {
    if (Platform.OS === 'android' && loc.mocked === true) return false;
    if ((loc.coords.accuracy ?? 0) === 0) return false;
    if ((loc.coords.speed ?? 0) > 90) return false;
    return true;
  });
  if (!trusted.length) return;

  const points = trusted.map((loc) => ({
    lat: loc.coords.latitude,
    lng: loc.coords.longitude,
    speed: loc.coords.speed,
    heading: loc.coords.heading,
    accuracy: loc.coords.accuracy,
    timestamp: new Date(loc.timestamp).toISOString(),
    tracking_phase: 'background',
  }));

  try {
    await fetch(`${API_URL}/api/v1/drivers/location-batch`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ points }),
    });
    console.log(`[BgLocation] Sent ${points.length} points`);
  } catch (e) {
    console.warn('[BgLocation] Upload failed:', e);
  }
});

interface BgLocationConfig {
  timeInterval?: number;
  distanceInterval?: number;
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

  const interval = config?.timeInterval ?? 30_000;
  const distance = config?.distanceInterval ?? 50;

  await Location.startLocationUpdatesAsync(TASK_NAME, {
    accuracy: Location.Accuracy.Balanced,
    timeInterval: interval,
    distanceInterval: distance,
    deferredUpdatesInterval: interval,
    showsBackgroundLocationIndicator: true,
    foregroundService: {
      notificationTitle: 'Spinr Driver',
      notificationBody: "You're online and receiving ride requests",
      notificationColor: '#6C63FF',
    },
  });

  console.log('[BgLocation] Started');
  return true;
}

export async function stopBackgroundLocation(): Promise<void> {
  const isRunning = await TaskManager.isTaskRegisteredAsync(TASK_NAME);
  if (!isRunning) return;

  await Location.stopLocationUpdatesAsync(TASK_NAME);
  // Clear cached background token so a new sign-in starts fresh
  await SecureStore.deleteItemAsync('bg_access_token').catch(() => {});
  await SecureStore.deleteItemAsync('bg_access_token_expires').catch(() => {});
  console.log('[BgLocation] Stopped');
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
      await startBackgroundLocation();
      console.log('[Geofence] Recovered background tracking after kill');
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
