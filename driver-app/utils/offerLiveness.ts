/**
 * Is this ride offer still worth ringing for?
 *
 * Guards the in-app offer tone. The AppState re-election starts the tone after
 * an async notification handover, and the only thing that stops it is the
 * `incomingRide` -> null effect. An offer that expired while the app was
 * backgrounded is still in the store on return (its expiry event went to a
 * closed socket; the store's auto-decline only clears it after the decline
 * request settles, which can take seconds offline), so without this check the
 * tone rings for an offer the driver can no longer act on. The same check after
 * the handover covers an offer cleared while it was in flight: the stop effect
 * already ran for that clear, so a late play() would have nothing to stop it.
 *
 * Pure. An offer with no (or an unparseable) `offer_expires_at` counts as live:
 * legacy and store-built offers carry no absolute deadline, and treating those
 * as dead would silence a real offer.
 */
export function isOfferStillLive(
  state: {
    rideState: string;
    incomingRide?: { ride_id?: string; offer_expires_at?: string } | null;
  },
  rideId: string,
  now: number = Date.now(),
): boolean {
  if (state.rideState !== 'ride_offered') return false;
  const offer = state.incomingRide;
  if (!offer || offer.ride_id !== rideId) return false;
  const expiresAt = offer.offer_expires_at ? Date.parse(offer.offer_expires_at) : NaN;
  return !(Number.isFinite(expiresAt) && expiresAt <= now);
}
