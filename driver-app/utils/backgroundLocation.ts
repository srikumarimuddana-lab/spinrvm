import * as Location from 'expo-location';
import * as TaskManager from 'expo-task-manager';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';
import { API_URL } from '@shared/config';

const TASK_NAME = 'spinr-background-location';

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

export async function startBackgroundLocation(): Promise<boolean> {
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

  await Location.startLocationUpdatesAsync(TASK_NAME, {
    accuracy: Location.Accuracy.Balanced,
    timeInterval: 30_000,
    distanceInterval: 50,
    deferredUpdatesInterval: 30_000,
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
