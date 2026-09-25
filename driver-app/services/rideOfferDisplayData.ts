import type { RideOfferDisplayData } from './notifeeService';

/**
 * Map a store / WS / FCM offer into the Notifee card's display data.
 *
 * Shared by useDriverDashboard's _surfaceOfferNotification and the Android
 * Auto ring owner (lib/androidAuto/carOfferRing.ts), which re-posts the card
 * when it hands the ring back to the phone on a car-only launch. One mapping,
 * so the two cards can't drift apart. Pure; the type import is erased, so this
 * never loads the Notifee native module.
 */
const num = (v: unknown): number | undefined => {
  if (v === null || v === undefined || v === '' || v === 'None') return undefined;
  const n = typeof v === 'number' ? v : parseFloat(String(v));
  return Number.isFinite(n) ? n : undefined;
};

// `any`: offers arrive as loosely-typed WS / FCM / store payloads.
export function toRideOfferDisplayData(data: any): RideOfferDisplayData {
  return {
    ride_id: data.ride_id,
    booking_id: data.booking_id || data.ride_id,
    pickup_address: data.pickup_address,
    dropoff_address: data.dropoff_address,
    fare: num(data.fare) ?? 0,
    total_bonus: num(data.total_bonus),
    distance_km: num(data.distance_km),
    duration_minutes: num(data.duration_minutes),
    surge_multiplier: num(data.surge_multiplier),
    rider_name: data.rider_name || undefined,
    rider_rating: num(data.rider_rating),
    countdown_seconds: num(data.countdown_seconds),
    offer_expires_at: data.offer_expires_at || undefined,
    offer_card_url: data.offer_card_url || undefined,
    // Absent on the store-built reclaim offer; notifeeService keeps the last one.
    ring_mode: data.ring_mode || undefined,
  };
}
