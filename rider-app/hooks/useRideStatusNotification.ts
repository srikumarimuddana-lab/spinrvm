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

let Notifications: any = null;
try {
  Notifications = require('expo-notifications');
} catch {}

const NOTIF_ID = 'spinr_ride_status_live';
export const RIDE_STATUS_CHANNEL = 'ride-status-live';

const ACTIVE_STATUSES = new Set([
  'searching', 'driver_assigned', 'driver_accepted', 'driver_arrived', 'in_progress',
]);

function formatEta(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return '';
  const mins = Math.ceil(seconds / 60);
  return mins === 1 ? '1 min away' : `${mins} min away`;
}

function buildContent(
  status: string,
  ride: any,
  driver: any,
  etaSeconds: number | null,
): { title: string; body: string } {
  const driverName = driver?.name?.split(' ')[0] || 'Driver';
  const vehicle = driver
    ? [driver.vehicle_color, driver.vehicle_make, driver.vehicle_model].filter(Boolean).join(' ')
    : '';
  const plate = driver?.license_plate || '';
  const eta = formatEta(etaSeconds);
  const dropoff = ride?.dropoff_address || '';
  const otp = ride?.pickup_otp;

  switch (status) {
    case 'searching':
      return {
        title: 'Finding your driver 🔍',
        body: dropoff ? `To ${dropoff}` : 'Searching for a nearby driver…',
      };
    case 'driver_assigned':
      return {
        title: `${driverName} is being notified`,
        body: [vehicle, plate].filter(Boolean).join(' · '),
      };
    case 'driver_accepted':
      return {
        title: `${driverName} is on the way 🚗`,
        body: [eta, vehicle, plate].filter(Boolean).join(' · '),
      };
    case 'driver_arrived':
      return {
        title: `${driverName} has arrived! 📍`,
        body: [vehicle, plate, otp ? `PIN: ${otp}` : ''].filter(Boolean).join(' · '),
      };
    case 'in_progress':
      return {
        title: `Heading to destination`,
        body: [eta || 'Ride in progress', dropoff].filter(Boolean).join(' · '),
      };
    default:
      return { title: 'Spinr', body: '' };
  }
}

export function useRideStatusNotification() {
  const currentRide = useRideStore(s => s.currentRide);
  const currentDriver = useRideStore(s => s.currentDriver);
  const driverEtaSeconds = useRideStore(s => s.driverEtaSeconds);
  const isPostedRef = useRef(false);

  useEffect(() => {
    if (!Notifications || Platform.OS !== 'android') return;

    const status = currentRide?.status as string | undefined;

    if (status && ACTIVE_STATUSES.has(status)) {
      const { title, body } = buildContent(status, currentRide, currentDriver, driverEtaSeconds);
      Notifications.scheduleNotificationAsync({
        identifier: NOTIF_ID,
        content: {
          title,
          body,
          data: { rideId: currentRide!.id, type: 'ride_status_live' },
          sound: false,
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
      Notifications.dismissNotificationAsync(NOTIF_ID).catch(() => {});
      isPostedRef.current = false;
    }
  }, [currentRide?.status, currentRide?.id, currentDriver?.name, driverEtaSeconds]);
}
