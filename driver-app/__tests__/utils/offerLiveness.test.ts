/**
 * isOfferStillLive — the guard in front of the in-app ride-offer tone.
 *
 * Code under test: driver-app/utils/offerLiveness.ts
 */
import { isOfferStillLive } from '../../utils/offerLiveness';

const NOW = Date.parse('2026-09-25T20:00:00Z');
const iso = (offsetMs: number) => new Date(NOW + offsetMs).toISOString();

describe('isOfferStillLive', () => {
  it('is live while the same ride is offered and has time left', () => {
    const state = { rideState: 'ride_offered', incomingRide: { ride_id: 'r1', offer_expires_at: iso(5000) } };
    expect(isOfferStillLive(state, 'r1', NOW)).toBe(true);
  });

  // The stale-offer case: expired while backgrounded, still in the store
  // because the decline request has not settled yet.
  it('is dead once the server deadline has passed, even though the store still holds it', () => {
    const state = { rideState: 'ride_offered', incomingRide: { ride_id: 'r1', offer_expires_at: iso(-1) } };
    expect(isOfferStillLive(state, 'r1', NOW)).toBe(false);
  });

  it('is dead exactly at the deadline', () => {
    const state = { rideState: 'ride_offered', incomingRide: { ride_id: 'r1', offer_expires_at: iso(0) } };
    expect(isOfferStillLive(state, 'r1', NOW)).toBe(false);
  });

  it('is dead when the offer was cleared while the handover was in flight', () => {
    expect(isOfferStillLive({ rideState: 'idle', incomingRide: null }, 'r1', NOW)).toBe(false);
  });

  it('is dead when a different ride replaced the one we were ringing for', () => {
    const state = { rideState: 'ride_offered', incomingRide: { ride_id: 'r2', offer_expires_at: iso(5000) } };
    expect(isOfferStillLive(state, 'r1', NOW)).toBe(false);
  });

  it('is dead once the ride moved on (accepted)', () => {
    const state = { rideState: 'navigating_to_pickup', incomingRide: { ride_id: 'r1' } };
    expect(isOfferStillLive(state, 'r1', NOW)).toBe(false);
  });

  // Legacy / store-built offers carry no absolute deadline. Treating those as
  // dead would silence a real offer, so they keep the previous behaviour: live.
  it('is live when the offer has no deadline at all', () => {
    const state = { rideState: 'ride_offered', incomingRide: { ride_id: 'r1' } };
    expect(isOfferStillLive(state, 'r1', NOW)).toBe(true);
  });

  it('is live when the deadline is unparseable rather than silencing a real offer', () => {
    const state = { rideState: 'ride_offered', incomingRide: { ride_id: 'r1', offer_expires_at: 'not-a-date' } };
    expect(isOfferStillLive(state, 'r1', NOW)).toBe(true);
  });
});
