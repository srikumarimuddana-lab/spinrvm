/**
 * useScheduledRideReminder
 *
 * Schedules a local push notification 10 minutes before a confirmed
 * scheduled ride. Called from the ride-options / payment-confirm flow
 * immediately after the ride is created.
 *
 * The backend also fires an FCM `scheduled_ride_reminder` at T-10 min
 * (backend/utils/scheduled_rides.py::_send_reminder) as the authoritative
 * reminder. This local notification is a best-effort client-side fallback
 * for cases where FCM delivery is delayed. Previously this fired at T-15
 * while the backend fired at T-10 — riders could see two reminders five
 * minutes apart, or (worse) treat the earlier local one as the real signal.
 * Kept in sync with the backend value; if that changes, update both.
 */

import { useCallback } from 'react';
import { Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

const REMINDER_MAP_KEY = '@spinr:scheduled_reminders';
const REMINDER_LEAD_MINUTES = 10; // keep in sync with backend/utils/scheduled_rides.py's 10-minute reminder window
const REMINDER_LEAD_MS = REMINDER_LEAD_MINUTES * 60 * 1000;

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
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

async function saveStoredMap(map: Record<string, string>): Promise<void> {
  try {
    await AsyncStorage.setItem(REMINDER_MAP_KEY, JSON.stringify(map));
  } catch {}
}

export function useScheduledRideReminder() {
  const scheduleReminder = useCallback(async (rideId: string, scheduledTime: Date) => {
    if (!Notifications || Platform.OS === 'web') return;

    const triggerMs = scheduledTime.getTime() - REMINDER_LEAD_MS;
    const now = Date.now();

    if (triggerMs <= now) {
      // Less than REMINDER_LEAD_MINUTES away — skip local scheduling, FCM will handle it
      return;
    }

    try {
      // Cancel any existing reminder for this ride (idempotent re-schedule)
      const map = await getStoredMap();
      if (map[rideId]) {
        try {
          await Notifications.cancelScheduledNotificationAsync(map[rideId]);
        } catch {}
      }

      const notifId = await Notifications.scheduleNotificationAsync({
        content: {
          title: `Ride in ${REMINDER_LEAD_MINUTES} minutes`,
          body: `Your scheduled Spinr ride departs at ${scheduledTime.toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit' })}. Your driver is on the way!`,
          data: { type: 'scheduled_ride_reminder', rideId },
          sound: 'default',
          ...(Platform.OS === 'android' ? { channelId: 'ride-updates' } : {}),
        },
        trigger: { date: new Date(triggerMs) },
      });

      map[rideId] = notifId;
      await saveStoredMap(map);
      console.log(`[Reminder] Scheduled local notification for ride ${rideId} at`, new Date(triggerMs).toISOString());
    } catch (e) {
      console.log('[Reminder] Failed to schedule local notification:', e);
    }
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
