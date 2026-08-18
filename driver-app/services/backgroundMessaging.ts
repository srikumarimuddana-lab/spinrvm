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
import { setBackgroundMessageHandler, getAppCheckToken, initFirebaseServices } from '@shared/services/firebase';
// Keep the default import: many test files jest.mock(
// '@shared/config/spinr.config', () => ({ default: {...} })) without a
// matching named 'SpinrConfig' export, so switching to a named import
// breaks those mocks (confirmed in rider-app's utils/aiChat.ts).
// eslint-disable-next-line import/no-named-as-default
import SpinrConfig from '@shared/config/spinr.config';
import { getBackgroundAuthToken } from '../utils/backgroundLocation';

// Same backend-URL source as the shared API client (production fallback +
// expoConfig.extra), not @shared/config's env-var-only API_URL which resolves
// to '' on production / OTA builds that rely on the hardcoded fallback.
const API_URL = SpinrConfig.backendUrl;

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
    // Guarded native-module requires — same reasoning as _layout.tsx:
    // notifeeService.ts statically imports notifee at its own module scope,
    // so requiring it eagerly here would defeat the android/ios + try/catch
    // guard around it.
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    notifee = require('@notifee/react-native').default;
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const svc = require('./notifeeService');
    parseRideOfferEvent = svc.parseRideOfferEvent;
    displayRideOfferNotification = svc.displayRideOfferNotification;
    dismissRideOfferNotification = svc.dismissRideOfferNotification;
  } catch {
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
    rider_rating: toNum(data.rider_rating),
    incentives_count: Array.isArray(incentives) ? incentives.length : 0,
    countdown_seconds: toNum(data.countdown_seconds),
    offer_expires_at: data.offer_expires_at || undefined,
    offer_card_url: data.offer_card_url || undefined,
  };
}

/**
 * A dispatch event that reached this headless handler, republished in-process.
 *
 * Exists for the Android Auto car session. On a car-only launch NOTHING reads
 * PENDING_OFFER_KEY back — useDriverDashboard, which does that on the phone,
 * never mounts — so a ride offer would arrive, be written to AsyncStorage, and
 * sit there while the head unit showed an idle map. The car subscribes to this
 * instead and puts the offer straight into the shared driver store.
 *
 * Same idiom as carFixChannel's fix listeners, and the same reason: a headless
 * handler and a rendered surface need a channel between them that is not React.
 */
export type BackgroundDispatchEvent =
  | { type: 'new_ride_assignment'; ride_id: string; offer: Record<string, unknown> }
  | { type: 'ride_cancelled'; ride_id: string };

const dispatchListeners = new Set<(e: BackgroundDispatchEvent) => void>();

/**
 * Subscribe to dispatch events seen by the background handler.
 *
 * With NO subscriber — which is every phone-only launch — this channel does
 * nothing at all, so the existing phone behaviour is byte-for-byte unchanged.
 */
export function subscribeBackgroundDispatch(
  listener: (e: BackgroundDispatchEvent) => void,
): () => void {
  dispatchListeners.add(listener);
  return () => {
    dispatchListeners.delete(listener);
  };
}

function notifyBackgroundDispatch(event: BackgroundDispatchEvent): void {
  for (const l of dispatchListeners) {
    try {
      l(event);
    } catch (e) {
      // A bad subscriber must not stop the others, and must never be the reason
      // an offer notification fails to render.
      console.warn('[Push] background dispatch listener threw:', e);
    }
  }
}

let registered = false;

export function registerBackgroundMessageHandlers(): void {
  if (registered) return;
  registered = true;

  setBackgroundMessageHandler(async (remoteMessage: any) => {
    const data = remoteMessage?.data || {};

    // Cancellations carry no payload to persist and render no notification here
    // (the backend sends those with their own visible alert), so they are handled
    // ahead of the offer path and only republished in-process. Nothing else about
    // this handler's behaviour for non-offer messages changes: without a
    // subscriber, notifyBackgroundDispatch is a no-op.
    if (data?.type === 'ride_cancelled' && data?.ride_id) {
      notifyBackgroundDispatch({ type: 'ride_cancelled', ride_id: String(data.ride_id) });
      return;
    }

    if (data?.type !== 'new_ride_assignment' || !data?.ride_id) return;

    const fare = toNum(data.fare) ?? 0;
    const totalBonus = toNum(data.total_bonus) ?? 0;
    const surgeMultiplier = toNum(data.surge_multiplier);
    const incentives = safeParse<any[]>(data.incentives);
    const questHint = safeParse<any>(data.quest_hint);

    // 1. Build the full offer payload. Named rather than inlined into the
    //    setItem below so the car session can be handed the SAME object the
    //    dashboard hydrates from storage — one payload shape, not two that drift.
    const offerPayload = {
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
      quiet_mode: data.quiet_mode === 'true' || data.quiet_mode === 'True',
      countdown_seconds: toNum(data.countdown_seconds),
      offer_expires_at: data.offer_expires_at || undefined,
      surge_multiplier: surgeMultiplier,
      incentives,
      total_bonus: totalBonus || undefined,
      quest_hint: questHint,
      payment_method: data.payment_method || undefined,
    };

    try {
      // Deliberately lazy — see the alertPrefsStore require below and the
      // file header: handlers register at bundle load (headless JS launch),
      // so AsyncStorage is deferred to only when a message actually arrives
      // rather than loaded unconditionally on that critical path.
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const AsyncStorage = require('@react-native-async-storage/async-storage').default;
      await AsyncStorage.setItem(PENDING_OFFER_KEY, JSON.stringify(offerPayload));
    } catch (e) {
      console.warn('[Push] Failed to persist background ride offer:', e);
    }

    // 2. Surface the Uber-style heads-up + full-screen-intent notification
    //    via Notifee — ANDROID ONLY. Android dispatch pushes are data-only
    //    (backend sends notification=None), so nothing renders unless we do.
    //    iOS dispatch instead rides on a visible APNs alert (backend sets
    //    aps.alert + sound=ride_offer.caf + category=ride-offer, with
    //    content_available=True which also wakes this handler) — so the OS
    //    already shows the offer card with Accept/Decline. Rendering Notifee
    //    here on iOS would produce a duplicate notification for the same ride.
    if (Platform.OS === 'android' && displayRideOfferNotification) {
      try {
        const offer = offerDisplayDataFromFcm(data);
        if (offer) {
          // Headless launch: the alert-prefs store hasn't hydrated, so read
          // it explicitly before ringing. muted kills audio only — the
          // heads-up card and full-screen wake still fire.
          // Deliberately lazy: this file's handlers register at bundle load
          // (headless JS launch, see file header) — a static import would
          // pull alertPrefsStore's module scope into that critical path even
          // when no offer ever arrives. Deferred to only when this branch
          // (Android + an actual ride-offer FCM message) actually runs.
          // eslint-disable-next-line @typescript-eslint/no-require-imports
          const { useAlertPrefsStore } = require('../store/alertPrefsStore');
          await useAlertPrefsStore.getState().loadAlertPrefs();
          const muted = !useAlertPrefsStore.getState().soundEffects;
          await displayRideOfferNotification(offer, muted ? { muted } : undefined);
        }
      } catch (e) {
        console.warn('[Notifee] displayRideOfferNotification failed:', e);
      }
    }

    // 3. Republish in-process, LAST — after the durable write and the phone's
    //    notification, so a throwing subscriber cannot cost a driver either.
    notifyBackgroundDispatch({
      type: 'new_ride_assignment',
      ride_id: String(data.ride_id),
      offer: offerPayload,
    });
  });

  // Notifee background event listener — fires when the user taps
  // Accept/Decline from the lock screen or notification shade while the app
  // is killed or backgrounded.
  //
  // Accept (and a body tap) launch the app (launchActivity: 'default'), so we
  // stash the action and let the mounted dashboard execute it.
  //
  // Decline does NOT launch the app (no launchActivity on Android, iOS
  // foreground: false), so the dashboard never mounts to consume a stashed
  // action — and its 60s stale guard would drop it anyway. The backend would
  // then see no response and count the offer as a miss (3 misses auto-offline
  // the driver). So execute the decline headlessly here; only fall back to
  // stashing if we couldn't reach the backend, so it can retry on next open.
  if (notifee && parseRideOfferEvent) {
    notifee.onBackgroundEvent(async (event: any) => {
      const parsed = parseRideOfferEvent(event);
      if (!parsed || !parsed.ride_id) return;
      try {
        if (parsed.action === 'decline') {
          // The driver declined — drop the stashed offer payload so the
          // dashboard can't re-hydrate this (now declined) offer panel on the
          // next open before the offer's original expiry.
          await _clearPendingOffer();
          const delivered = await _declineHeadless(parsed.ride_id);
          if (!delivered) await _stashAction(parsed.action, parsed.ride_id);
        } else if (parsed.action === 'accept') {
          // Clear the stashed offer before launching: on cold start the
          // dashboard consumes the accept AND hydrates PENDING_OFFER_KEY
          // concurrently — if hydration sets rideState='ride_offered' after
          // the accept lands, fetchActiveRide() short-circuits and strands the
          // driver on the offer panel instead of pickup navigation.
          await _clearPendingOffer();
          await _stashAction(parsed.action, parsed.ride_id);
        } else {
          // Body tap — keep PENDING_OFFER_KEY so the dashboard hydrates the
          // offer panel for the driver to act on.
          await _stashAction(parsed.action, parsed.ride_id);
        }
        // Dismiss the notification so the driver doesn't see stale action
        // buttons while we open the app.
        if (dismissRideOfferNotification) await dismissRideOfferNotification();
      } catch (e) {
        console.warn('[Notifee] background action handling failed:', e);
      }
    });
  }
}

async function _stashAction(action: string, rideId: string): Promise<void> {
  // Deliberately lazy — see the require in the message handler above.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const AsyncStorage = require('@react-native-async-storage/async-storage').default;
  await AsyncStorage.setItem(
    PENDING_ACTION_KEY,
    JSON.stringify({ action, ride_id: rideId, ts: Date.now() }),
  );
}

async function _clearPendingOffer(): Promise<void> {
  try {
    // Deliberately lazy — see the require in the message handler above.
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const AsyncStorage = require('@react-native-async-storage/async-storage').default;
    await AsyncStorage.removeItem(PENDING_OFFER_KEY);
  } catch (e) {
    console.warn('[Notifee] clear pending offer failed:', e);
  }
}

// Execute a ride-offer decline directly against the backend from the headless
// background context. Returns true only when the decline is settled — a 2xx,
// or a terminal "offer already gone" status (404/409/410) where retrying is
// pointless. Returns false for token-rejection (401/403), transient/5xx, or
// network errors so the caller stashes the decline to retry with fresh auth on
// the next app open. Mirrors the headless auth + fetch pattern in
// utils/backgroundLocation.
async function _declineHeadless(rideId: string): Promise<boolean> {
  if (!API_URL) return false;
  let token: string | null = null;
  try {
    token = await getBackgroundAuthToken();
  } catch {
    return false;
  }
  if (!token) return false;
  try {
    // App Check is enforced on /api/* in production. This headless task doesn't
    // mount _layout, so initialize App Check here first (idempotent) — otherwise
    // its provider is unconfigured and getAppCheckToken() returns null.
    await initFirebaseServices();
    const appCheckToken = await getAppCheckToken();
    const resp = await fetch(`${API_URL}/api/v1/drivers/rides/${rideId}/decline`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
        ...(appCheckToken ? { 'X-Firebase-AppCheck': appCheckToken } : {}),
      },
    });
    if (resp.ok) return true;
    // Offer already reassigned/expired/gone — terminal, don't bother retrying.
    if (resp.status === 404 || resp.status === 409 || resp.status === 410) return true;
    // 401/403 (token rejected) or 5xx/429 (transient) — undelivered; stash so
    // the dashboard retries with a fresh token on next open.
    console.warn(`[Notifee] headless decline returned ${resp.status}; will retry on open`);
    return false;
  } catch (e) {
    console.warn('[Notifee] headless decline network error:', e);
    return false;
  }
}
