// Contract A: the backend emits `route_finalized` a few seconds after a ride
// completes, once the durable route geometry/quality/snapshot are persisted.
// handleRouteFinalizedFromWS must refetch the ride so the ride-completed /
// ride-details screens replace the "still processing" state with the real
// route — but ONLY when the pushed revision is strictly newer than what's
// displayed, and only for the ride currently on screen (loop-safe, no-op
// otherwise).

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    post: jest.fn(),
    get: jest.fn(() => Promise.resolve({ data: { id: 'ride_1', status: 'completed', route_revision: 3 } })),
    put: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  },
  SpinrApiError: class SpinrApiError extends Error {},
  hasAuthToken: jest.fn(() => false),
}));
jest.mock('@shared/store/authStore', () => ({
  registerLogoutCallback: jest.fn(),
  useAuthStore: { getState: jest.fn(() => ({ user: { id: 'user-abc' } })) },
}));
jest.mock('@react-native-async-storage/async-storage', () => ({
  setItem: jest.fn(() => Promise.resolve()),
  getItem: jest.fn(() => Promise.resolve(null)),
  removeItem: jest.fn(() => Promise.resolve()),
}));

import api from '@shared/api/client';
import { useRideStore } from '../rideStore';

const apiGet = api.get as jest.Mock;

const finalize = (rideId: string, routeRevision: number) =>
  useRideStore.getState().handleRouteFinalizedFromWS(rideId, routeRevision);

const seed = (id: string, route_revision = 0) =>
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  useRideStore.setState({ currentRide: { id, status: 'completed', route_revision } as any });

describe('rideStore — route_finalized handling (Contract A)', () => {
  beforeEach(() => {
    apiGet.mockClear();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    useRideStore.setState({ currentRide: null, currentDriver: null, _clearedRideId: null } as any);
  });

  it('refetches the displayed ride when the pushed revision is newer', () => {
    seed('ride_1', 0);
    finalize('ride_1', 3);
    expect(apiGet).toHaveBeenCalledWith('/rides/ride_1');
  });

  it('no-ops for a push targeting a different ride', () => {
    seed('ride_1', 0);
    finalize('ride_2', 3);
    expect(apiGet).not.toHaveBeenCalled();
  });

  it('no-ops when no ride is on screen', () => {
    finalize('ride_1', 3);
    expect(apiGet).not.toHaveBeenCalled();
  });

  it('does not refetch on an equal or older revision (loop guard)', () => {
    seed('ride_1', 3);
    finalize('ride_1', 3); // duplicate fan-out
    finalize('ride_1', 2); // reconnect replay
    expect(apiGet).not.toHaveBeenCalled();
  });
});
