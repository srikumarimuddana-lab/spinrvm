/**
 * useRideStatusNotification
 *
 * Keeps the rider's ongoing Android notification in sync with the current ride
 * while the app's JS is alive (foreground / backgrounded-but-not-killed) — the
 * same pattern used by Uber, Lyft, and delivery apps.
 *
 * Rendering moved from expo-notifications to Notifee (services/rideLiveNotification).
 * The backend's data-only `live_activity` FCM drives the SAME notification id
 * when the app is backgrounded/killed (app/_layout FCM handler), so there is
 * never a duplicate. This hook is the foreground driver and builds the richer
 * content (ETA, plate, pickup PIN) from local store state.
 *
 * Android-only; Notifee no-ops on iOS / Expo Go / web. iOS uses native Live
 * Activities (Phase 3), not this.
 */

import { useEffect, useRef } from 'react';
import { Platform } from 'react-native';
import * as rideLive from '../services/rideLiveNotification';
import { useRideStore } from '../store/rideStore';

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
    if (Platform.OS !== 'android') return;

    const status = currentRide?.status as string | undefined;

    if (status && ACTIVE_STATUSES.has(status)) {
      const { title, body } = buildContent(status, currentRide, currentDriver, driverEtaSeconds);
      rideLive.showOrUpdate({ title, body, rideId: currentRide!.id })
        .then(() => { isPostedRef.current = true; })
        .catch((e: any) => console.warn('[RideNotif] Failed to post status notification:', e));
    } else if (isPostedRef.current) {
      rideLive.cancel().catch(() => {});
      isPostedRef.current = false;
    }
  }, [currentRide?.status, currentRide?.id, currentDriver?.name, driverEtaSeconds]);
}
