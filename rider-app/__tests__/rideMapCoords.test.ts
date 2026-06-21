/**
 * getRideMapCoords — validates pickup/dropoff coordinates before they reach
 * react-native-maps. A non-finite lat/lng native-crashes the MapView (white
 * screen, uncatchable by the JS ErrorBoundary), so the ride screens must only
 * mount the map when all four coords are finite. This is the regression guard
 * for the "rider goes white while searching for a driver" crash.
 */

import { getRideMapCoords, finiteNumber } from '../utils/rideMapCoords';

describe('finiteNumber', () => {
  it('passes through finite numbers', () => {
    expect(finiteNumber(50.4)).toBe(50.4);
    expect(finiteNumber(0)).toBe(0);
    expect(finiteNumber(-104.6)).toBe(-104.6);
  });

  it('coerces numeric strings (Postgres/JSON sometimes sends coords as strings)', () => {
    expect(finiteNumber('50.4')).toBe(50.4);
    expect(finiteNumber('-104.65')).toBe(-104.65);
  });

  it('rejects non-finite / non-numeric values', () => {
    expect(finiteNumber(null)).toBeNull();
    expect(finiteNumber(undefined)).toBeNull();
    expect(finiteNumber('')).toBeNull();
    expect(finiteNumber('abc')).toBeNull();
    expect(finiteNumber(NaN)).toBeNull();
    expect(finiteNumber(Infinity)).toBeNull();
    expect(finiteNumber({})).toBeNull();
  });
});

describe('getRideMapCoords', () => {
  const valid = {
    pickup_lat: 50.40976,
    pickup_lng: -104.6555,
    dropoff_lat: 50.45,
    dropoff_lng: -104.61,
  };

  it('returns coords when all four are valid numbers', () => {
    expect(getRideMapCoords(valid)).toEqual({
      pickupLat: 50.40976,
      pickupLng: -104.6555,
      dropoffLat: 50.45,
      dropoffLng: -104.61,
    });
  });

  it('coerces string coords to numbers', () => {
    const got = getRideMapCoords({
      pickup_lat: '50.4',
      pickup_lng: '-104.6',
      dropoff_lat: '50.45',
      dropoff_lng: '-104.61',
    });
    expect(got).toEqual({ pickupLat: 50.4, pickupLng: -104.6, dropoffLat: 50.45, dropoffLng: -104.61 });
  });

  it('returns null when the ride is null/undefined (map must not mount)', () => {
    expect(getRideMapCoords(null)).toBeNull();
    expect(getRideMapCoords(undefined)).toBeNull();
  });

  it.each([
    ['pickup_lat', { ...valid, pickup_lat: null }],
    ['pickup_lng', { ...valid, pickup_lng: undefined }],
    ['dropoff_lat', { ...valid, dropoff_lat: NaN }],
    ['dropoff_lng', { ...valid, dropoff_lng: 'not-a-number' }],
  ])('returns null when %s is invalid (prevents the native map crash)', (_field, ride) => {
    expect(getRideMapCoords(ride)).toBeNull();
  });

  it('falls back to nested pickup/dropoff objects', () => {
    const got = getRideMapCoords({
      pickup: { lat: 50.4, lng: -104.6 },
      dropoff: { lat: 50.45, lng: -104.61 },
    });
    expect(got).toEqual({ pickupLat: 50.4, pickupLng: -104.6, dropoffLat: 50.45, dropoffLng: -104.61 });
  });
});
