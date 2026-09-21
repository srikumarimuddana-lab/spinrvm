/** Server owns configurable reminders. Clean up alarms made by older app versions. */

import { useCallback } from 'react';
import { Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

const REMINDER_MAP_KEY = '@spinr:scheduled_reminders';
// Lazy-load expo-notifications so the app still runs in Expo Go / web.
let Notifications: any = null;
try {
  // Guarded native-module require — must stay runtime require(), not a
  // static import, so a missing native binary (Expo Go/web) hits the catch
  // below instead of crashing at module load.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  Notifications = require('expo-notifications');
} catch {
  // Not available in Expo Go or web
}

async function getStoredMap(): Promise<Record<string, string>> {
  try {
    const raw = await AsyncStorage.getItem(REMINDER_MAP_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    return Object.fromEntries(Object.entries(parsed).filter((entry): entry is [string, string] => typeof entry[1] === 'string'));
  } catch {
    return {};
  }
}

async function saveStoredMap(map: Record<string, string>): Promise<void> {
  try {
    await AsyncStorage.setItem(REMINDER_MAP_KEY, JSON.stringify(map));
  } catch {}
}

export async function clearLegacyScheduledReminders(): Promise<void> {
  if (!Notifications || Platform.OS === 'web') return;
  const map = await getStoredMap();
  for (const [rideId, notificationId] of Object.entries(map)) {
    try {
      await Notifications.cancelScheduledNotificationAsync(notificationId);
      delete map[rideId];
    } catch {
      // Keep the ID so a later app open can retry native cleanup.
    }
  }
  await saveStoredMap(map);
}

export function useScheduledRideReminder() {
  // Compatibility for booking screens: never create a competing local timer.
  const scheduleReminder = useCallback(async (_rideId: string, _scheduledTime: Date) => {
    await clearLegacyScheduledReminders();
  }, []);

  const cancelReminder = useCallback(async (rideId: string) => {
    if (!Notifications || Platform.OS === 'web') return;

    try {
      const map = await getStoredMap();
      if (map[rideId]) {
        await Notifications.cancelScheduledNotificationAsync(map[rideId]);
        delete map[rideId];
        await saveStoredMap(map);
        console.log(`[Reminder] Cancelled local notification for ride ${rideId}`);
      }
    } catch (e) {
      console.log('[Reminder] Failed to cancel local notification:', e);
    }
  }, []);

  return { scheduleReminder, cancelReminder };
}

/**
 * Handles an incoming `scheduled_ride_reminder` FCM message.
 * Called from the foreground message handler in _layout.tsx.
 * Returns the ride ID extracted from the data payload.
 */
export function handleScheduledRideReminderFCM(remoteMessage: any): string | null {
  const data = remoteMessage?.data ?? {};
  if (data.type !== 'scheduled_ride_reminder') return null;
  const rideId = data.ride_id ?? data.rideId ?? null;
  console.log('[Push] scheduled_ride_reminder received for ride', rideId);
  return rideId;
}
