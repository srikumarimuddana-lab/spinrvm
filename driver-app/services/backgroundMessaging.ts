/**
 * Background FCM + Notifee handler registration for ride offers.
 *
 * MUST be imported from index.js (the real bundle entry), NOT from a route
 * module. Ride offers arrive as data-only FCM messages (no native
 * notification block — Notifee renders the rich UI instead). When the app
 * is KILLED, Android delivers them via a headless JS launch: the bundle
 * entry executes but no React component ever mounts, so expo-router route
 * modules (like app/_layout.tsx) are never evaluated. A handler registered
 * in _layout.tsx therefore does not exist in headless mode and the offer is
 * silently dropped — the driver sees nothing. Registering here, at bundle
 * load, is what makes killed-state offers appear.
 *
 * Responsibilities:
 *   1. messaging().setBackgroundMessageHandler — persist the offer payload
 *      to AsyncStorage (so the dashboard hydrates instantly on open) and
 *      surface the Uber-style heads-up / full-screen notification via
 *      Notifee.
 *   2. notifee.onBackgroundEvent — capture Accept/Decline taps from the
 *      lock screen while the app is killed and stash them for the
 *      dashboard to execute on mount.
 *
 * Foreground listeners (onForegroundEvent, notification-tap routing) stay
 * in app/_layout.tsx — those genuinely need the mounted app.
 */

import { Platform } from 'react-native';
import { setBackgroundMessageHandler } from '@shared/services/firebase';

// AsyncStorage keys shared with useDriverDashboard.ts (which consumes both)
// and _layout.tsx (which writes PENDING_ACTION_KEY from foreground events).
export const PENDING_OFFER_KEY = 'spinr_pending_ride_offer';
export const PENDING_ACTION_KEY = 'spinr_pending_notifee_action';

// Lazy-required so the import is a no-op in Expo Go / web where the
// Notifee native module isn't linked (same pattern as _layout.tsx).
let notifee: any = null;
let parseRideOfferEvent: any = null;
let displayRideOfferNotification: any = null;
let dismissRideOfferNotification: any = null;
if (Platform.OS === 'android' || Platform.OS === 'ios') {
  try {
    notifee = require('@notifee/react-native').default;
    const svc = require('./notifeeService');
    parseRideOfferEvent = svc.parseRideOfferEvent;
    displayRideOfferNotification = svc.displayRideOfferNotification;
    dismissRideOfferNotification = svc.dismissRideOfferNotification;
  } catch (e) {
    console.log('[Notifee] native module not available — background ride-offer notifications disabled');
  }
}

// FCM data uses the same keys as the WS dispatch_payload, all stringified.
const safeParse = <T,>(s: any): T | undefined => {
  if (!s || s === 'null' || s === 'None') return undefined;
  if (typeof s !== 'string') return s as T;
  try { return JSON.parse(s) as T; } catch { return undefined; }
};
const toNum = (v: any): number | undefined => {
  if (!v || v === '' || v === 'None') return undefined;
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : undefined;
};

/**
 * Map a stringified `new_ride_assignment` FCM data payload to the shape
 * displayRideOfferNotification expects. Shared by the killed/background
 * handler below and the foreground onMessage listener in _layout.tsx.
 * Returns null when the payload isn't a ride offer.
 */
export function offerDisplayDataFromFcm(data: any): Record<string, any> | null {
  if (data?.type !== 'new_ride_assignment' || !data?.ride_id) return null;
  const incentives = safeParse<any[]>(data.incentives);
  return {
    ride_id: data.ride_id,
    booking_id: data.booking_id || data.ride_id,
    pickup_address: data.pickup_address,
    dropoff_address: data.dropoff_address,
    fare: toNum(data.fare) ?? 0,
    total_bonus: toNum(data.total_bonus) ?? 0,
    distance_km: toNum(data.distance_km),
    duration_minutes: toNum(data.duration_minutes),
    surge_multiplier: toNum(data.surge_multiplier),
    rider_name: data.rider_name,
    incentives_count: Array.isArray(incentives) ? incentives.length : 0,
    countdown_seconds: toNum(data.countdown_seconds),
    offer_expires_at: data.offer_expires_at || undefined,
  };
}

let registered = false;

export function registerBackgroundMessageHandlers(): void {
  if (registered) return;
  registered = true;

  setBackgroundMessageHandler(async (remoteMessage: any) => {
    const data = remoteMessage?.data || {};
    if (data?.type !== 'new_ride_assignment' || !data?.ride_id) return;

    const fare = toNum(data.fare) ?? 0;
    const totalBonus = toNum(data.total_bonus) ?? 0;
    const surgeMultiplier = toNum(data.surge_multiplier);
    const incentives = safeParse<any[]>(data.incentives);
    const questHint = safeParse<any>(data.quest_hint);

    // 1. Persist the full offer payload so the in-app panel can hydrate
    //    instantly on cold start (driver dashboard reads on mount).
    try {
      const AsyncStorage = require('@react-native-async-storage/async-storage').default;
      await AsyncStorage.setItem(
        PENDING_OFFER_KEY,
        JSON.stringify({
          ride_id: data.ride_id,
          booking_id: data.booking_id || data.ride_id,
          pickup_address: data.pickup_address || '',
          dropoff_address: data.dropoff_address || '',
          pickup_lat: toNum(data.pickup_lat) ?? 0,
          pickup_lng: toNum(data.pickup_lng) ?? 0,
          dropoff_lat: toNum(data.dropoff_lat) ?? 0,
          dropoff_lng: toNum(data.dropoff_lng) ?? 0,
          fare,
          distance_km: toNum(data.distance_km),
          duration_minutes: toNum(data.duration_minutes),
          rider_name: data.rider_name || undefined,
          rider_rating: toNum(data.rider_rating),
          requires_wav: data.requires_wav === 'true' || data.requires_wav === 'True',
          countdown_seconds: toNum(data.countdown_seconds),
          offer_expires_at: data.offer_expires_at || undefined,
          surge_multiplier: surgeMultiplier,
          incentives,
          total_bonus: totalBonus || undefined,
          quest_hint: questHint,
          payment_method: data.payment_method || undefined,
        }),
      );
    } catch (e) {
      console.warn('[Push] Failed to persist background ride offer:', e);
    }

    // 2. Surface the Uber-style heads-up + full-screen-intent notification
    //    via Notifee. This is what the driver actually sees on the lock
    //    screen, with Accept/Decline buttons.
    if (displayRideOfferNotification) {
      try {
        const offer = offerDisplayDataFromFcm(data);
        if (offer) await displayRideOfferNotification(offer);
      } catch (e) {
        console.warn('[Notifee] displayRideOfferNotification failed:', e);
      }
    }
  });

  // Notifee background event listener — fires when the user taps
  // Accept/Decline from the lock screen or notification shade while the app
  // is killed. Stash the action so useDriverDashboard.ts can consume it on
  // mount and call accept/decline against the backend.
  if (notifee && parseRideOfferEvent) {
    notifee.onBackgroundEvent(async (event: any) => {
      const parsed = parseRideOfferEvent(event);
      if (!parsed || !parsed.ride_id) return;
      try {
        const AsyncStorage = require('@react-native-async-storage/async-storage').default;
        await AsyncStorage.setItem(
          PENDING_ACTION_KEY,
          JSON.stringify({ action: parsed.action, ride_id: parsed.ride_id, ts: Date.now() }),
        );
        // Dismiss the notification so the driver doesn't see stale "Accept"
        // buttons while we open the app.
        if (dismissRideOfferNotification) await dismissRideOfferNotification();
      } catch (e) {
        console.warn('[Notifee] background action persist failed:', e);
      }
    });
  }
}
