/**
 * Tests for the mis-resolved-dropoff guard: block a booking whose dropoff
 * coordinate sits on top of the pickup while the address differs, without
 * false-positiving on genuine trips or same-place bookings.
 */
import { dropoffLikelyMisresolved, haversineMeters } from '../bookingDistanceGuard';

const PICKUP = { lat: 50.4452, lng: -104.6189, address: '4321 Wakeling St, Regina' };

describe('haversineMeters', () => {
  it('is ~0 for identical points', () => {
    expect(haversineMeters(PICKUP, PICKUP)).toBeCloseTo(0, 5);
  });

  it('measures a realistic short distance', () => {
    // ~0.001 deg latitude ≈ 111 m.
    const d = haversineMeters(PICKUP, { ...PICKUP, lat: PICKUP.lat + 0.001 });
    expect(d).toBeGreaterThan(100);
    expect(d).toBeLessThan(125);
  });
});

describe('dropoffLikelyMisresolved', () => {
  it('flags a dropoff on top of pickup with a different address', () => {
    const dropoff = { lat: 50.4452, lng: -104.6189, address: 'Walmart Supercentre, Gordon Rd' };
    expect(dropoffLikelyMisresolved(PICKUP, dropoff)).toBe(true);
  });

  it('does not flag a genuine trip far enough away', () => {
    const dropoff = { lat: 50.4460, lng: -104.6100, address: 'Walmart Supercentre, Gordon Rd' };
    expect(dropoffLikelyMisresolved(PICKUP, dropoff)).toBe(false);
  });

  it('does not flag a same-address booking (round trip / same place)', () => {
    const dropoff = { lat: 50.4452, lng: -104.6189, address: '4321 Wakeling St, Regina' };
    expect(dropoffLikelyMisresolved(PICKUP, dropoff)).toBe(false);
  });

  it('address comparison ignores case and whitespace', () => {
    const dropoff = { lat: 50.4452, lng: -104.6189, address: '  4321  WAKELING st, regina ' };
    expect(dropoffLikelyMisresolved(PICKUP, dropoff)).toBe(false);
  });

  it('does not flag when an address is missing (cannot judge)', () => {
    const dropoff = { lat: 50.4452, lng: -104.6189, address: null };
    expect(dropoffLikelyMisresolved(PICKUP, dropoff)).toBe(false);
  });

  it('returns false when a coordinate is missing or non-finite', () => {
    expect(dropoffLikelyMisresolved(null, PICKUP)).toBe(false);
    expect(
      dropoffLikelyMisresolved(PICKUP, { lat: NaN, lng: -104.6189, address: 'x' }),
    ).toBe(false);
  });

  it('respects a custom minMeters threshold', () => {
    // 111 m away, different address: not flagged at 100 m, flagged at 300 m.
    const dropoff = { lat: PICKUP.lat + 0.001, lng: PICKUP.lng, address: 'Somewhere Else' };
    expect(dropoffLikelyMisresolved(PICKUP, dropoff)).toBe(false);
    expect(dropoffLikelyMisresolved(PICKUP, dropoff, { minMeters: 300 })).toBe(true);
  });
});
