/**
 * useRideStatusNotification
 *
 * Maintains a persistent Android notification showing the current ride
 * status while the app is backgrounded — the same pattern used by Uber,
 * Lyft, and delivery apps.
 *
 * Behaviour:
 *  - Posts a sticky notification to the `ride-status-live` channel when
 *    a ride becomes active (searching → … → in_progress).
 *  - Replaces the existing notification in-place as status changes so
 *    only one notification is ever shown.
 *  - Dismisses it automatically when the ride completes or is cancelled.
 *  - iOS + Expo Go: no-op (Expo Go removed push-token APIs in SDK 53;
 *    iOS shows status differently via Live Activities, not implemented here).
 *
 * Notification channel: `ride-status-live` — LOW importance so updating
 * the status text doesn't re-ring the phone. The high-importance
 * `ride-updates` channel already handles the individual sound alerts
 * (driver accepted, driver arrived, etc.).
 */

import { useEffect, useRef } from 'react';
import { Platform } from 'react-native';
import { useRideStore } from '../store/rideStore';

// Lazy-require so the app still mounts in Expo Go / web.
let Notifications: any = null;
try {
  Notifications = require('expo-notifications');
} catch {}

const NOTIF_ID = 'spinr_ride_status_live';
export const RIDE_STATUS_CHANNEL = 'ride-status-live';

const STATUS_CONTENT: Record<string, { title: string; body: string }> = {
  searching:       { title: 'Spinr — Finding your driver 🔍', body: 'Searching for a nearby driver…' },
  driver_assigned: { title: 'Spinr — Driver found', body: 'Driver has been notified. Waiting for acceptance…' },
  driver_accepted: { title: 'Spinr — Driver on the way 🚗', body: 'Your driver accepted and is heading to the pickup.' },
  driver_arrived:  { title: 'Spinr — Driver arrived! 📍', body: 'Your driver is waiting at the pickup point.' },
  in_progress:     { title: 'Spinr — Ride in progress', body: 'Sit back and enjoy your ride.' },
};

const ACTIVE_STATUSES = new Set(Object.keys(STATUS_CONTENT));

export function useRideStatusNotification() {
  const currentRide = useRideStore(s => s.currentRide);
  const isPostedRef = useRef(false);

  useEffect(() => {
    // Android APK only — Expo Go / web / iOS are all no-ops.
    if (!Notifications || Platform.OS !== 'android') return;

    const status = currentRide?.status as string | undefined;

    if (status && ACTIVE_STATUSES.has(status)) {
      const { title, body } = STATUS_CONTENT[status];
      Notifications.scheduleNotificationAsync({
        identifier: NOTIF_ID,
        content: {
          title,
          body,
          data: { rideId: currentRide!.id, type: 'ride_status_live' },
          // No sound — individual lifecycle alerts (driver accepted, arrived)
          // are handled by the WS handler with the high-importance channel.
          sound: false,
          // sticky: true prevents the rider from accidentally swiping the
          // status bar notification away mid-trip. The app dismisses it
          // programmatically when the ride ends.
          sticky: true,
          autoDismiss: false,
          channelId: RIDE_STATUS_CHANNEL,
          priority: Notifications.AndroidNotificationPriority.LOW,
        },
        trigger: null,
      })
        .then(() => { isPostedRef.current = true; })
        .catch((e: any) => console.warn('[RideNotif] Failed to post status notification:', e));
    } else if (isPostedRef.current) {
      // Ride completed or cancelled — remove the persistent notification.
      Notifications.dismissNotificationAsync(NOTIF_ID).catch(() => {});
      isPostedRef.current = false;
    }
  }, [currentRide?.status, currentRide?.id]);
}
