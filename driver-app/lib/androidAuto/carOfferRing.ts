/**
 * Who rings for a ride offer while Android Auto is connected.
 *
 * ─── The problem ────────────────────────────────────────────────────────────
 * The car shows an offer as a template alert, which is visual only. The phone's
 * own tone (useRideOfferSound) is started by useDriverDashboard, which never
 * mounts on a car-only launch, and the headless FCM path posts a Notifee card
 * that Android Auto does not play through the car. So a driver in the car got a
 * silent popup.
 *
 * ─── The rule: one ring owner ───────────────────────────────────────────────
 * isCarRingOwner() = a head unit is connected AND the android_auto_offer_tone
 * flag is on AND the native RideOfferTone module works on this build. While it
 * is true:
 *   - the car tone rings (native module, navigation-guidance usage, ducks music);
 *   - the phone's in-app loop stays silent (useRideOfferSound checks this);
 *   - the Notifee card is still posted, but muted.
 * Otherwise every existing phone path runs exactly as before. If the car tone
 * cannot start, the ring is handed back to the phone (handBack) and ownership
 * drops for the rest of that offer.
 *
 * Driven only from register.ts (syncCarOfferRing on every store change while a
 * head unit is connected), so nothing here runs on a phone-only session.
 *
 * ─── Car-only offer expiry ──────────────────────────────────────────────────
 * The only offer countdown lives in the phone screen (app/driver/(tabs)/
 * index.tsx), which calls the store's setCountdown(0) → declineRide(
 * 'offer_expired'). On a car-only launch that screen never mounts, so an
 * unanswered offer never expired and setIncomingRide (idle-only) then refused
 * every later offer. This module arms the same setCountdown(0) at the offer
 * deadline + 1.5 s, and only fires it when no phone UI is mounted.
 */
import { Platform } from 'react-native';
import { useDriverStore } from '../../store/driverStore';
import { useAlertPrefsStore } from '../../store/alertPrefsStore';
import { recordNonFatal } from '../../utils/crashlytics';
import { toRideOfferDisplayData } from '../../services/rideOfferDisplayData';
import { pushDebug, setDebugFact } from './carDebug';

type ToneModule = typeof import('../../modules/ride-offer-tone');

// Only the fields read here. The store's IncomingRide (and the WS/FCM
// payloads) carry more, which the hand-back passes through to the card mapper.
export type CarOffer = {
  ride_id: string;
  countdown_seconds?: number;
  offer_expires_at?: string;
};

export type CarOfferRingState = {
  rideState: string;
  incomingRide: CarOffer | null | undefined;
  countdownSeconds?: number;
};

/** Gap between tone plays, matching the phone loop's 2.5 s cadence. */
export const OFFER_TONE_GAP_MS = 2_500;
const FALLBACK_OFFER_MS = 15_000;
const MIN_TONE_MS = 1_000;
const MAX_TONE_MS = 60_000;
/** Lets the server's own expiry (and the phone's countdown, if any) land first. */
export const OFFER_EXPIRY_GRACE_MS = 1_500;
/** Re-check interval while an accept is in flight at the deadline. */
const EXPIRY_HOLD_RETRY_MS = 2_000;

const log = (...args: unknown[]) => {
  if (__DEV__) console.log('[car-offer-ring]', ...args);
  pushDebug('info', ...args);
};
const logError = (...args: unknown[]) => {
  console.error('[car-offer-ring]', ...args);
  pushDebug('error', ...args);
};

let carConnected = false;
let toneEnabled = false;
// The car tone failed for the live offer: the phone owns its ring until the
// offer ends. Reset when the offer ends, so the next one tries the car again.
let toneFailed = false;
let ringingRideId: string | null = null;
let ringDeadlineAt = 0;
// The car was asked to handle the ring for ringingRideId (tone requested, or
// deliberately silent because Sound Effects is off).
let carRingHandled = false;
// Bumped on every change of offer; async work checks it before acting.
let ringGen = 0;
let stopTimer: ReturnType<typeof setTimeout> | null = null;
let expiryTimer: ReturnType<typeof setTimeout> | null = null;
let lastOwner = false;
let reportedThisSession = false;
let phoneRingHandler: ((offer: CarOffer) => void) | null = null;
const listeners = new Set<(owner: boolean) => void>();

let toneModule: ToneModule | null | undefined;
function tone(): ToneModule | null {
  if (toneModule !== undefined) return toneModule;
  try {
    // Lazy: keeps expo-modules-core out of every suite that only imports this
    // module transitively (useRideOfferSound, backgroundMessaging).
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    toneModule = require('../../modules/ride-offer-tone') as ToneModule;
  } catch (e) {
    logError('ride-offer-tone module failed to load:', e);
    toneModule = null;
  }
  return toneModule;
}

function toneSupported(): boolean {
  return tone()?.isRideOfferToneSupported() === true;
}

function stopTone(): void {
  tone()?.stopRideOfferTone();
}

function computeOwner(): boolean {
  return carConnected && toneEnabled && !toneFailed && toneSupported();
}

/** True while the car, not the phone, owns the audible ride-offer alert. */
export function isCarRingOwner(): boolean {
  return computeOwner();
}

function liveOffer(): CarOffer | null {
  const { rideState, incomingRide } = useDriverStore.getState() as unknown as CarOfferRingState;
  return rideState === 'ride_offered' && incomingRide?.ride_id ? incomingRide : null;
}

/**
 * How long this offer has left, in ms: the server's absolute expiry first, then
 * the store's countdown, then the payload's, then 15 s. Clamped to 1..60 s.
 */
export function offerDeadlineMs(
  offer: CarOffer,
  countdownSeconds?: number,
  now: number = Date.now(),
): number {
  let ms = FALLBACK_OFFER_MS;
  const expiresAt = Date.parse(String(offer.offer_expires_at ?? ''));
  if (Number.isFinite(expiresAt) && expiresAt - now > 0) {
    ms = expiresAt - now;
  } else if (typeof countdownSeconds === 'number' && countdownSeconds > 0) {
    ms = countdownSeconds * 1000;
  } else if (typeof offer.countdown_seconds === 'number' && offer.countdown_seconds > 0) {
    ms = offer.countdown_seconds * 1000;
  }
  return Math.min(MAX_TONE_MS, Math.max(MIN_TONE_MS, ms));
}

/** Give the ring back to the phone. Never throws. */
function handBack(offer: CarOffer): void {
  try {
    if (phoneRingHandler) {
      log('hand back → phone app', offer.ride_id);
      phoneRingHandler(offer);
      return;
    }
    if (Platform.OS !== 'android') return;
    log('hand back → notification', offer.ride_id);
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const notifee = require('../../services/notifeeService');
    const muted = !useAlertPrefsStore.getState().soundEffects;
    Promise.resolve(
      notifee.displayRideOfferNotification(toRideOfferDisplayData(offer), { reclaim: true, muted }),
    ).catch((e: unknown) => logError('hand back notification failed:', e));
  } catch (e) {
    logError('hand back failed:', e);
  }
}

function notify(owner: boolean): void {
  listeners.forEach((cb) => {
    try {
      cb(owner);
    } catch (e) {
      logError('ring owner listener threw:', e);
    }
  });
}

/**
 * Re-evaluate ownership and act on a change: true→false gives a live offer's
 * ring back to the phone; false→true starts the car tone for a live offer the
 * car already knows about (e.g. the flag came on mid-offer).
 */
function refreshOwner(): void {
  const owner = computeOwner();
  if (owner === lastOwner) return;
  lastOwner = owner;
  setDebugFact('offerToneOwner', owner ? 'car' : 'phone');
  const offer = liveOffer();
  if (!owner) {
    if (carRingHandled) {
      stopTone();
      carRingHandled = false;
    }
    clearStopTimer();
    notify(false);
    if (offer) handBack(offer);
    return;
  }
  notify(true);
  if (offer && offer.ride_id === ringingRideId && !carRingHandled) {
    startCarTone(offer, ringGen);
  }
}

function clearStopTimer(): void {
  if (stopTimer !== null) {
    clearTimeout(stopTimer);
    stopTimer = null;
  }
}

function clearExpiryTimer(): void {
  if (expiryTimer !== null) {
    clearTimeout(expiryTimer);
    expiryTimer = null;
  }
}

/**
 * Expire an unanswered offer when no phone UI is mounted (see the header).
 * Armed only while a car is connected; left armed across a disconnect, since
 * it re-validates the store before acting and the stuck state it prevents is
 * the same with or without the car.
 */
function armExpiry(rideId: string): void {
  clearExpiryTimer();
  if (!carConnected) return;
  const fire = () => {
    expiryTimer = null;
    // The phone screen runs its own countdown; never race it.
    if (phoneRingHandler) return;
    const st = useDriverStore.getState();
    if (st.rideState !== 'ride_offered' || st.incomingRide?.ride_id !== rideId) return;
    if (st.acceptNetworkHold) {
      // An accept is in flight; the store settles the offer when it returns.
      expiryTimer = setTimeout(fire, EXPIRY_HOLD_RETRY_MS);
      return;
    }
    log('car-only offer expired →', rideId);
    st.setCountdown(0);
  };
  expiryTimer = setTimeout(fire, Math.max(0, ringDeadlineAt - Date.now()) + OFFER_EXPIRY_GRACE_MS);
}

function reportOnce(result: string): void {
  if (reportedThisSession) return;
  reportedThisSession = true;
  // One event per car session: tells us, from real head units, whether focus
  // is granted (playing) or refused (playing_unfocused), and how often the
  // native side fails. Not per offer — that would be an event per ride.
  recordNonFatal(new Error(`Car offer tone: ${result}`), {
    domain: 'drivers',
    module: 'androidAuto',
    reason: 'car_offer_tone_result',
    result,
  });
}

function startCarTone(offer: CarOffer, gen: number): void {
  carRingHandled = true;
  void (async () => {
    try {
      const prefs = useAlertPrefsStore.getState();
      if (!prefs.isLoaded) await prefs.loadAlertPrefs();
      if (gen !== ringGen || !computeOwner()) return;
      if (!useAlertPrefsStore.getState().soundEffects) {
        // Driver muted Sound Effects: the car shows the offer, nothing rings.
        log('sound effects off — car offer is visual only');
        return;
      }
      const ms = Math.min(MAX_TONE_MS, Math.max(MIN_TONE_MS, ringDeadlineAt - Date.now()));
      const result = await tone()!.startRideOfferTone(ms, OFFER_TONE_GAP_MS);
      if (gen !== ringGen) return;
      log('car offer tone:', result, offer.ride_id);
      setDebugFact('offerTone', result);
      reportOnce(result);
      // 'cancelled' with the same gen means no JS stop or new offer superseded
      // it: the native hard stop fired while still waiting out a nav prompt,
      // so nothing ever played. Hand back, or every source stays silent.
      if (result === 'unsupported' || result === 'error' || result === 'cancelled') {
        carRingHandled = false;
        toneFailed = true;
        refreshOwner(); // owner → false → handBack
        return;
      }
      if (result === 'playing' || result === 'playing_unfocused') {
        clearStopTimer();
        stopTimer = setTimeout(() => {
          stopTimer = null;
          if (gen === ringGen) stopTone();
        }, ms);
      }
      // 'blocked_call': never ring over a call, and no hand back either — the
      // phone would ring over the same call.
    } catch (e) {
      logError('car offer tone start threw:', e);
      if (gen !== ringGen) return;
      carRingHandled = false;
      toneFailed = true;
      refreshOwner();
    }
  })();
}

/** End whatever the car was doing for the current offer. */
function resetRing(): void {
  ringGen += 1;
  if (carRingHandled) {
    stopTone();
    carRingHandled = false;
  }
  clearStopTimer();
}

/**
 * Called by register.ts on every driver-store change while a head unit is
 * connected. Starts the car tone for a new (or replacing) offer, and stops it
 * and dismisses the offer card once the offer is no longer live.
 */
export function syncCarOfferRing(s: CarOfferRingState): void {
  const id = s.rideState === 'ride_offered' && s.incomingRide?.ride_id ? s.incomingRide.ride_id : null;
  if (id) {
    if (id === ringingRideId) return;
    resetRing();
    ringingRideId = id;
    ringDeadlineAt = Date.now() + offerDeadlineMs(s.incomingRide as CarOffer, s.countdownSeconds);
    toneFailed = false;
    armExpiry(id);
    if (computeOwner()) startCarTone(s.incomingRide as CarOffer, ringGen);
    refreshOwner();
    return;
  }
  if (ringingRideId === null) return;
  resetRing();
  clearExpiryTimer();
  ringingRideId = null;
  toneFailed = false;
  refreshOwner();
  // On a car-only launch nothing else dismisses the card when the offer ends
  // (useDriverDashboard's dismiss effect never mounts). Idempotent with it.
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const notifee = require('../../services/notifeeService');
    Promise.resolve(notifee.dismissRideOfferNotification()).catch((e: unknown) =>
      logError('offer card dismiss failed:', e),
    );
  } catch (e) {
    logError('offer card dismiss failed:', e);
  }
}

/** register.ts: true on connect (after the template exists), false on disconnect. */
export function setCarConnected(connected: boolean): void {
  if (connected === carConnected) return;
  carConnected = connected;
  if (connected) {
    reportedThisSession = false;
    refreshOwner();
    return;
  }
  refreshOwner(); // hands a live offer back to the phone
  // No sync runs after a disconnect, so drop the offer the car was tracking;
  // a reconnect mid-offer then starts it afresh.
  resetRing();
  ringingRideId = null;
  toneFailed = false;
}

/** From /drivers/config: android_auto_offer_tone_enabled (the kill switch). */
export function setCarOfferToneEnabled(enabled: boolean): void {
  if (enabled === toneEnabled) return;
  toneEnabled = enabled;
  refreshOwner();
}

/** Called with the new owner value whenever isCarRingOwner() changes. */
export function subscribeCarRingOwner(cb: (owner: boolean) => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

/**
 * The phone dashboard registers how it re-rings an offer (in-app loop when
 * foreground, reclaimed notification when background). With none registered
 * the hand back posts the notification directly.
 */
export function registerPhoneRingHandler(fn: ((offer: CarOffer) => void) | null): () => void {
  phoneRingHandler = fn;
  return () => {
    if (phoneRingHandler === fn) phoneRingHandler = null;
  };
}
