/**
 * fetchDirectionsRoute (R7, docs/audit/ride-experience/ROADMAP.md) — the
 * shared client for the backend's GET /maps/directions proxy. Pins:
 *  - request URL/query shape (origin/destination always sent; waypoints
 *    only when given, in the given order, never optimized)
 *  - response shape matches MapViewDirections' onReady contract
 *    ({coordinates: {latitude,longitude}[], distance, duration})
 *  - a backend/network failure propagates (rejects) rather than resolving
 *    to an empty/partial result, so callers can fall back to on-device
 *    MapViewDirections
 */
const mockApiGet = jest.fn();
jest.mock('../client', () => ({
  __esModule: true,
  default: { get: (...args: unknown[]) => mockApiGet(...args) },
}));

import { fetchDirectionsRoute } from '../directions';

beforeEach(() => {
  jest.clearAllMocks();
});

describe('fetchDirectionsRoute', () => {
  const origin = { latitude: 38.5, longitude: -120.2 };
  const destination = { latitude: 43.252, longitude: -126.453 };

  it('requests origin/destination as lat,lng query params', async () => {
    mockApiGet.mockResolvedValue({
      data: { coordinates: [], distance_km: null, duration_minutes: null },
    });

    await fetchDirectionsRoute(origin, destination);

    expect(mockApiGet).toHaveBeenCalledWith('/maps/directions?origin=38.5%2C-120.2&destination=43.252%2C-126.453');
  });

  it('appends waypoints in the given order, pipe-separated, when provided', async () => {
    mockApiGet.mockResolvedValue({
      data: { coordinates: [], distance_km: null, duration_minutes: null },
    });

    await fetchDirectionsRoute(origin, destination, [
      { latitude: 40.0, longitude: -121.0 },
      { latitude: 41.0, longitude: -122.0 },
    ]);

    const url = mockApiGet.mock.calls[0][0] as string;
    expect(url).toContain('waypoints=40%2C-121%7C41%2C-122');
  });

  it('omits the waypoints param entirely when none are given', async () => {
    mockApiGet.mockResolvedValue({
      data: { coordinates: [], distance_km: null, duration_minutes: null },
    });

    await fetchDirectionsRoute(origin, destination, []);

    const url = mockApiGet.mock.calls[0][0] as string;
    expect(url).not.toContain('waypoints');
  });

  it('converts [lat,lng] pairs to {latitude,longitude} objects and passes distance/duration through', async () => {
    mockApiGet.mockResolvedValue({
      data: {
        coordinates: [
          [38.5, -120.2],
          [40.7, -120.95],
        ],
        distance_km: 5.2,
        duration_minutes: 12.5,
      },
    });

    const result = await fetchDirectionsRoute(origin, destination);

    expect(result).toEqual({
      coordinates: [
        { latitude: 38.5, longitude: -120.2 },
        { latitude: 40.7, longitude: -120.95 },
      ],
      distance: 5.2,
      duration: 12.5,
    });
  });

  it('defaults distance/duration to null when the backend omits them', async () => {
    mockApiGet.mockResolvedValue({ data: { coordinates: [] } });

    const result = await fetchDirectionsRoute(origin, destination);

    expect(result.distance).toBeNull();
    expect(result.duration).toBeNull();
  });

  it('propagates a backend/network failure instead of swallowing it', async () => {
    mockApiGet.mockRejectedValue(new Error('proxy unavailable'));

    await expect(fetchDirectionsRoute(origin, destination)).rejects.toThrow('proxy unavailable');
  });
});
