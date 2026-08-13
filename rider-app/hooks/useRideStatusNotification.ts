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

// Takes flat scalar fields rather than the whole ride/driver store objects
// so its one caller (the effect below) can depend on just those fields
// instead of the whole objects, which change on every ride poll.
function buildContent(
  status: string,
  dropoffAddress: string | null | undefined,
  pickupOtp: string | null | undefined,
  driverFullName: string | null | undefined,
  vehicleColor: string | null | undefined,
  vehicleMake: string | null | undefined,
  vehicleModel: string | null | undefined,
  licensePlate: string | null | undefined,
  etaSeconds: number | null,
): { title: string; body: string } {
  const driverName = driverFullName?.split(' ')[0] || 'Driver';
  const vehicle = [vehicleColor, vehicleMake, vehicleModel].filter(Boolean).join(' ');
  const plate = licensePlate || '';
  const eta = formatEta(etaSeconds);
  const dropoff = dropoffAddress || '';
  const otp = pickupOtp;

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
    const rideId = currentRide?.id;

    if (status && rideId && ACTIVE_STATUSES.has(status)) {
      const { title, body } = buildContent(
        status,
        currentRide?.dropoff_address,
        currentRide?.pickup_otp,
        currentDriver?.name,
        currentDriver?.vehicle_color,
        currentDriver?.vehicle_make,
        currentDriver?.vehicle_model,
        currentDriver?.license_plate,
        driverEtaSeconds,
      );
      rideLive.showOrUpdate({ title, body, rideId })
        .then(() => { isPostedRef.current = true; })
        .catch((e: any) => console.warn('[RideNotif] Failed to post status notification:', e));
    } else if (isPostedRef.current) {
      rideLive.cancel().catch(() => {});
      isPostedRef.current = false;
    }
    // Narrowed to the specific fields buildContent() actually reads, rather
    // than the whole currentRide/currentDriver objects: those update on
    // every ride poll (fare, timestamps, …), and re-posting a native
    // Android notification that often would be visible churn (icon
    // flicker/re-buzz), not just a wasted render. Previously only
    // status/id/driver.name/driverEtaSeconds were tracked, so vehicle info,
    // plate, dropoff address, or the pickup OTP changing without one of
    // those four also changing would leave the posted notification showing
    // stale text — a real, if narrow, gap this closes.
  }, [
    currentRide?.status,
    currentRide?.id,
    currentRide?.dropoff_address,
    currentRide?.pickup_otp,
    currentDriver?.name,
    currentDriver?.vehicle_color,
    currentDriver?.vehicle_make,
    currentDriver?.vehicle_model,
    currentDriver?.license_plate,
    driverEtaSeconds,
  ]);
}
