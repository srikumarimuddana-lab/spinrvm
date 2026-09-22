/**
 * fetchRide vs. a locally-cleared ride (rider app)
 *
 * clearRide() records the retired ride in `_clearedRideId` so that a fetchRide
 * response still in flight at that moment cannot re-populate the store and
 * bounce the rider back onto the screen they just left.
 *
 * That check used to be `_clearedRideId === rideId` alone, which is a
 * permanent, one-way latch — `_clearedRideId` is only reset by createRide (or
 * by fetchActiveRide landing a different ride). So every LATER response for
 * that ride was discarded too, including a deliberate re-fetch from a screen
 * that had just mounted and explicitly asked for it. A rider re-entering
 * /ride-completed for a paid ride therefore sat on a receipt whose ride could
 * never load: the fare read $0.00 and the screen's own "already paid, leave"
 * effect never fired, because it keys on a payment_status that stayed
 * undefined.
 *
 * `_clearEpoch` scopes the guard to genuinely in-flight calls. These tests pin
 * both halves — the stale response is still dropped, the deliberate re-fetch
 * now lands — plus the two consumers that still rely on `_clearedRideId`
 * keeping its permanent meaning.
 *
 * Code under test: rider-app/store/rideStore.ts::{fetchRide, clearRide,
 * cancelRide, fetchActiveRide}
 */

import { useRideStore } from '../rideStore';
import api from '@shared/api/client';

jest.mock('@react-native-async-storage/async-storage', () => ({
  setItem: jest.fn(() => Promise.resolve()),
  getItem: jest.fn(() => Promise.resolve(null)),
  removeItem: jest.fn(() => Promise.resolve()),
}));

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    post: jest.fn(),
    get: jest.fn(),
    put: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  },
  hasAuthToken: jest.fn(() => true),
  SpinrApiError: class SpinrApiError extends Error {},
}));

jest.mock('@shared/store/authStore', () => ({
  registerLogoutCallback: jest.fn(),
  useAuthStore: { getState: jest.fn(() => ({ user: { id: 'user-abc' } })) },
}));

jest.mock('expo-router', () => ({
  router: { push: jest.fn(), replace: jest.fn() },
}));

const mockApi = api as jest.Mocked<typeof api>;

const makeRide = (status: string, id = 'ride-789', extra: Record<string, unknown> = {}) => ({
  id,
  rider_id: 'user-abc',
  vehicle_type_id: 'vt-standard',
  status,
  pickup_address: '100 Queen St',
  pickup_lat: 43.65,
  pickup_lng: -79.38,
  dropoff_address: '200 King St',
  dropoff_lat: 43.64,
  dropoff_lng: -79.38,
  estimated_fare: 15.0,
  base_fare: '10.00',
  total_fare: '15.00',
  grand_total: '17.25',
  distance_km: 2.0,
  duration_minutes: 10,
  payment_method: 'card',
  payment_status: 'pending',
  pickup_otp: '1234',
  created_at: '2025-01-01T00:00:00Z',
  ...extra,
});

beforeEach(() => {
  jest.clearAllMocks();
  useRideStore.setState({
    currentRide: null,
    currentDriver: null,
    isLoading: false,
    error: null,
    _clearedRideId: null,
    _clearEpoch: 0,
  });
});

describe('fetchRide vs. a cleared ride', () => {
  it('still discards a response overtaken by clearRide() mid-flight', async () => {
    useRideStore.setState({ currentRide: makeRide('completed') as any });

    // The request is issued first, then clearRide() lands while it is in
    // flight — the original race this guard exists for.
    let resolveGet: (v: unknown) => void = () => {};
    mockApi.get.mockReturnValueOnce(
      new Promise((res) => { resolveGet = res; }) as any,
    );
    const pending = useRideStore.getState().fetchRide('ride-789');

    useRideStore.getState().clearRide();
    resolveGet({ status: 200, data: makeRide('completed') });
    await pending;

    expect(useRideStore.getState().currentRide).toBeNull();
    expect(useRideStore.getState().isLoading).toBe(false);
  });

  it('honours an allowCleared re-fetch after the clear (the receipt-screen re-entry)', async () => {
    useRideStore.setState({ currentRide: makeRide('completed') as any });
    useRideStore.getState().clearRide();
    expect(useRideStore.getState()._clearedRideId).toBe('ride-789');

    // A fresh mount of /ride-completed asks for this exact ride on purpose.
    // Without allowCleared the response is thrown away and currentRide stays
    // null forever — the $0.00 receipt.
    mockApi.get.mockResolvedValueOnce({
      status: 200,
      data: makeRide('completed', 'ride-789', { payment_status: 'paid' }),
    } as any);

    await useRideStore.getState().fetchRide('ride-789', { allowCleared: true });

    expect(useRideStore.getState().currentRide?.id).toBe('ride-789');
    expect(useRideStore.getState().currentRide?.payment_status).toBe('paid');
  });

  it('honours repeated allowCleared re-fetches, not just the first', async () => {
    useRideStore.setState({ currentRide: makeRide('completed') as any });
    useRideStore.getState().clearRide();

    for (const _ of [1, 2]) {
      useRideStore.setState({ currentRide: null });
      mockApi.get.mockResolvedValueOnce({ status: 200, data: makeRide('completed') } as any);
      await useRideStore.getState().fetchRide('ride-789', { allowCleared: true });
      expect(useRideStore.getState().currentRide?.id).toBe('ride-789');
    }
  });

  it('still discards a post-clear fetch that does NOT opt in', async () => {
    // The default must stay closed. Nearly everything that reaches fetchRide
    // does so automatically and would resurrect a retired ride:
    // ride-status.tsx's poll effect re-runs the instant currentRide?.status
    // flips to undefined and re-fetches immediately; useRiderSocket's
    // ride_status_changed handler re-fetches unconditionally and the backend
    // broadcasts exactly that on the rider's own cancel.
    useRideStore.setState({ currentRide: makeRide('searching') as any });
    useRideStore.getState().clearRide();

    mockApi.get.mockResolvedValueOnce({ status: 200, data: makeRide('searching') } as any);
    await useRideStore.getState().fetchRide('ride-789');

    expect(useRideStore.getState().currentRide).toBeNull();
  });

  it('discards an allowCleared response overtaken by a clear mid-flight', async () => {
    // allowCleared opts out of the "already retired before I asked" case only.
    // A clear that lands while the request is in flight still wins — that
    // response describes a ride the rider finished with after asking.
    useRideStore.setState({ currentRide: makeRide('completed') as any });

    let resolveGet: (v: unknown) => void = () => {};
    mockApi.get.mockReturnValueOnce(new Promise((res) => { resolveGet = res; }) as any);
    const pending = useRideStore.getState().fetchRide('ride-789', { allowCleared: true });

    useRideStore.getState().clearRide();
    resolveGet({ status: 200, data: makeRide('completed') });
    await pending;

    expect(useRideStore.getState().currentRide).toBeNull();
  });

  it('is unaffected for a ride that was never cleared', async () => {
    useRideStore.setState({ _clearedRideId: 'ride-OLD', _clearEpoch: 3 });
    mockApi.get.mockResolvedValueOnce({ status: 200, data: makeRide('in_progress', 'ride-NEW') } as any);

    await useRideStore.getState().fetchRide('ride-NEW');

    expect(useRideStore.getState().currentRide?.id).toBe('ride-NEW');
  });
});

describe('_clearedRideId keeps its permanent meaning for the other consumers', () => {
  it('cancelRide records the id AND moves the epoch', async () => {
    useRideStore.setState({ currentRide: makeRide('searching') as any });
    mockApi.post.mockResolvedValueOnce({ status: 200, data: {} } as any);

    await useRideStore.getState().cancelRide();

    // useRiderSocket's ride_cancelled handler reads _clearedRideId directly to
    // suppress the server's echo of a cancel the rider already saw. That guard
    // is untouched by the epoch work and must keep seeing the id.
    expect(useRideStore.getState()._clearedRideId).toBe('ride-789');
    expect(useRideStore.getState()._clearEpoch).toBeGreaterThan(0);
  });

  it('fetchActiveRide still treats a just-cancelled ride as inactive, permanently', async () => {
    // Deliberately NOT epoch-scoped: this guards against server
    // read-after-write lag (/rides/active still reporting a cancelled ride as
    // active), which is a property of the server's state, not of request
    // ordering. Re-asserted here so the epoch change can't quietly loosen it.
    useRideStore.setState({ _clearedRideId: 'ride-789', _clearEpoch: 9 });
    mockApi.get.mockResolvedValueOnce({
      status: 200,
      data: { active: true, ride: makeRide('searching', 'ride-789') },
    } as any);

    const result = await useRideStore.getState().fetchActiveRide();

    expect(result).toBeNull();
    expect(useRideStore.getState().currentRide).toBeNull();
  });
});

describe('cancel-flicker: a post-cancel automatic re-fetch', () => {
  it('cannot resurrect the cancelled ride', async () => {
    // These are the paths that made an epoch-only guard wrong, so they are
    // pinned explicitly:
    //   - ride-status.tsx's poll effect re-runs the moment currentRide?.status
    //     flips to undefined and calls fetchRide immediately (not a rare race);
    //   - useRiderSocket's ride_status_changed handler re-fetches
    //     unconditionally, and backend/routes/rides/cancellation.py broadcasts
    //     that on the rider's own cancel.
    // A resurrected ride is not cosmetic: fetchRide calls _persistRide, so it
    // would be written to ACTIVE_RIDE_KEY and survive a restart, and
    // fetchActiveRide cannot undo it (its own _clearedRideId check returns
    // before that function's clearRide() cleanup).
    useRideStore.setState({ currentRide: makeRide('searching') as any });
    mockApi.post.mockResolvedValueOnce({ status: 200, data: {} } as any);
    await useRideStore.getState().cancelRide();

    mockApi.get.mockResolvedValueOnce({ status: 200, data: makeRide('cancelled') } as any);
    await useRideStore.getState().fetchRide('ride-789');

    expect(useRideStore.getState().currentRide).toBeNull();
    expect(useRideStore.getState()._clearedRideId).toBe('ride-789');
  });

  it('is not resurrected even under read-after-write lag reporting it active', async () => {
    useRideStore.setState({ currentRide: makeRide('searching') as any });
    mockApi.post.mockResolvedValueOnce({ status: 200, data: {} } as any);
    await useRideStore.getState().cancelRide();

    // The server has not committed the cancel yet and still says 'searching'.
    // Letting this through would put the rider back in a live-ride UI.
    mockApi.get.mockResolvedValueOnce({ status: 200, data: makeRide('searching') } as any);
    await useRideStore.getState().fetchRide('ride-789');

    expect(useRideStore.getState().currentRide).toBeNull();
  });
});
