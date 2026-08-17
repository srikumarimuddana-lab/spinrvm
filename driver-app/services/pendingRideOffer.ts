/**
 * Hydrate a ride offer that arrived while the app was killed or backgrounded.
 *
 * `backgroundMessaging.ts` runs at bundle load, so a data-only FCM ride offer
 * reaches the app even when it is fully closed — but all that handler can do
 * from a headless launch is write the payload to AsyncStorage. Something has to
 * read it back and put it in front of the driver.
 *
 * Two surfaces need to, and they are not both mounted:
 *   - the phone dashboard, on mount and on every foreground resume
 *   - the Android Auto car session, which on a car-only launch is the ONLY one
 *     that ever runs (no route module mounts, so useDriverDashboard does not
 *     exist)
 *
 * Extracted from useDriverDashboard so there is exactly one implementation of
 * the rules rather than a car-shaped copy that drifts. Same reasoning as
 * triggerDriverEmergency in hooks/useDriverSafetyTrigger.ts.
 *
 * Racing is safe. Whichever caller wins the removeItem calls setIncomingRide,
 * and the store is what both surfaces render from — so the loser finding nothing
 * is the correct outcome, not a dropped offer.
 */
import { useDriverStore } from '../store/driverStore';
import { PENDING_OFFER_KEY } from './backgroundMessaging';

export interface ConsumePendingOfferOptions {
  /**
   * Fired only when a live offer is about to be surfaced — never for an expired
   * or already-superseded one. The phone uses it to buzz; the car does not,
   * because the head unit raises its own alert.
   */
  onOffer?: () => void;
}

/**
 * Returns true only when an offer was actually surfaced, so a caller can report
 * what happened. Never throws.
 */
export async function consumePendingRideOffer(
  opts: ConsumePendingOfferOptions = {},
): Promise<boolean> {
  try {
    // Deliberately lazy, matching backgroundMessaging.ts: this module sits on
    // the car's connect path, and AsyncStorage should not be pulled in until an
    // offer is actually being looked for.
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const AsyncStorage = require('@react-native-async-storage/async-storage').default;
    const raw = await AsyncStorage.getItem(PENDING_OFFER_KEY);
    if (!raw) return false;
    // Removed before any decision below, so a payload that fails to parse or
    // has expired cannot be retried forever.
    await AsyncStorage.removeItem(PENDING_OFFER_KEY);

    // Never clobber an active ride. The store's own setIncomingRide guards this
    // too, but returning here also keeps `onOffer` from firing.
    if (useDriverStore.getState().rideState !== 'idle') return false;

    const offer = JSON.parse(raw);
    const expiresAt = offer?.offer_expires_at;
    const isExpired = expiresAt && new Date(expiresAt) <= new Date();
    if (isExpired) return false;

    opts.onOffer?.();
    useDriverStore.getState().setIncomingRide(offer);
    return true;
  } catch (e) {
    console.warn('[Push] Failed to hydrate pending ride offer:', e);
    return false;
  }
}
