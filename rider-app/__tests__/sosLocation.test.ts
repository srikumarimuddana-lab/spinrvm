/**
 * Tests for the bounded SOS location helper (C1 regression).
 *
 * Lives under rider-app/__tests__ (not shared/utils/__tests__) so it is
 * collected by an actual `yarn test` run — shared/ has no Jest config or test
 * script, so tests placed there never execute in CI. Imports the helper
 * through the @shared alias that rider-app's jest.config.js already maps.
 *
 * The SOS critical path must never hang on GPS: a fresh high-accuracy fix is
 * raced against a hard deadline, then the cached last-known position (bounded
 * by maxAge so a stale previous-trip fix is never reported as the emergency
 * location), then no coordinates at all. Every branch resolves — the helper
 * never throws and never waits past its deadline.
 */
import * as Location from 'expo-location';

import {
  getSOSLocation,
  SOS_FRESH_FIX_TIMEOUT_MS,
  SOS_LAST_KNOWN_TIMEOUT_MS,
  SOS_LAST_KNOWN_MAX_AGE_MS,
} from '@shared/utils/sosLocation';

jest.mock('expo-location', () => ({
  Accuracy: { High: 4 },
  getCurrentPositionAsync: jest.fn(),
  getLastKnownPositionAsync: jest.fn(),
}));

const mockGetCurrent = Location.getCurrentPositionAsync as jest.Mock;
const mockGetLastKnown = Location.getLastKnownPositionAsync as jest.Mock;

const fix = (latitude: number, longitude: number) => ({
  coords: { latitude, longitude },
});

/** A promise that never settles — models a GPS fix that never arrives. */
const hangs = () => new Promise(() => {});

beforeEach(() => {
  jest.useFakeTimers();
  mockGetCurrent.mockReset();
  mockGetLastKnown.mockReset();
});

afterEach(() => {
  jest.useRealTimers();
});

describe('getSOSLocation', () => {
  it('returns the fresh fix when it resolves within the deadline', async () => {
    mockGetCurrent.mockResolvedValue(fix(52.13, -106.67));

    await expect(getSOSLocation()).resolves.toEqual({ lat: 52.13, lng: -106.67 });
    expect(mockGetLastKnown).not.toHaveBeenCalled();
  });

  it('falls back to last-known when the fresh fix hangs past the deadline', async () => {
    mockGetCurrent.mockImplementation(hangs);
    mockGetLastKnown.mockResolvedValue(fix(50.45, -104.61));

    const promise = getSOSLocation();
    await jest.advanceTimersByTimeAsync(SOS_FRESH_FIX_TIMEOUT_MS);

    await expect(promise).resolves.toEqual({ lat: 50.45, lng: -104.61 });
  });

  it('bounds the cached fix by maxAge so stale positions are rejected', async () => {
    mockGetCurrent.mockRejectedValue(new Error('location unavailable'));
    mockGetLastKnown.mockResolvedValue(fix(50.45, -104.61));

    await getSOSLocation();

    // A stale fix must never be accepted as the emergency location: the
    // fallback must ask expo to reject anything older than the bound.
    expect(mockGetLastKnown).toHaveBeenCalledWith({ maxAge: SOS_LAST_KNOWN_MAX_AGE_MS });
  });

  it('returns no coords when the cached fix is too stale (expo returns null)', async () => {
    mockGetCurrent.mockRejectedValue(new Error('location unavailable'));
    // expo returns null when no cached position satisfies the maxAge bound.
    mockGetLastKnown.mockResolvedValue(null);

    await expect(getSOSLocation()).resolves.toEqual({});
  });

  it('falls back to last-known when the fresh fix rejects', async () => {
    mockGetCurrent.mockRejectedValue(new Error('location unavailable'));
    mockGetLastKnown.mockResolvedValue(fix(50.45, -104.61));

    await expect(getSOSLocation()).resolves.toEqual({ lat: 50.45, lng: -104.61 });
  });

  it('returns no coords when the fix hangs and last-known is empty', async () => {
    mockGetCurrent.mockImplementation(hangs);
    mockGetLastKnown.mockResolvedValue(null);

    const promise = getSOSLocation();
    await jest.advanceTimersByTimeAsync(SOS_FRESH_FIX_TIMEOUT_MS);

    await expect(promise).resolves.toEqual({});
  });

  it('resolves with no coords even when BOTH lookups hang (hard bound)', async () => {
    mockGetCurrent.mockImplementation(hangs);
    mockGetLastKnown.mockImplementation(hangs);

    const promise = getSOSLocation();
    await jest.advanceTimersByTimeAsync(SOS_FRESH_FIX_TIMEOUT_MS);
    await jest.advanceTimersByTimeAsync(SOS_LAST_KNOWN_TIMEOUT_MS);

    await expect(promise).resolves.toEqual({});
  });

  it('never rejects, even when both lookups reject', async () => {
    mockGetCurrent.mockRejectedValue(new Error('gps error'));
    mockGetLastKnown.mockRejectedValue(new Error('cache error'));

    await expect(getSOSLocation()).resolves.toEqual({});
  });

  it('respects a caller-supplied fresh-fix deadline', async () => {
    mockGetCurrent.mockImplementation(hangs);
    mockGetLastKnown.mockResolvedValue(fix(50.45, -104.61));

    const promise = getSOSLocation(500);
    await jest.advanceTimersByTimeAsync(500);

    await expect(promise).resolves.toEqual({ lat: 50.45, lng: -104.61 });
  });
});
