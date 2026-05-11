import * as Location from 'expo-location';
import * as TaskManager from 'expo-task-manager';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';
import { API_URL } from '@shared/config';

const TASK_NAME = 'spinr-background-location';

type LocationTaskData = {
  locations: Location.LocationObject[];
};

TaskManager.defineTask<LocationTaskData>(TASK_NAME, async ({ data, error }) => {
  if (error) {
    console.error('[BgLocation] Task error:', error.message);
    return;
  }
  if (!data?.locations?.length) return;

  const token = await SecureStore.getItemAsync('auth_token');
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
  } catch (e) {
    console.warn('[BgLocation] Upload failed:', e);
  }
});

export async function startBackgroundLocation(): Promise<boolean> {
  const isRunning = await TaskManager.isTaskRegisteredAsync(TASK_NAME);
  if (isRunning) return true;

  const { status } = await Location.requestBackgroundPermissionsAsync();
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
  console.log('[BgLocation] Stopped');
}
