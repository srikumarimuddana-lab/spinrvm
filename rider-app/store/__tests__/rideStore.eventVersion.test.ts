// V3 (issue #11): the rider must drop stale / out-of-order ride events by the
// server-authoritative monotonic `version` (rides.version, bumped by the
// migration-225 trigger and stamped into the ride_status_changed payload by
// broadcast_ride_status). applyRideStatusFromWS receives the version in `extra`
// and drops any event whose version is not strictly greater than the highest
// already applied for the current ride. Events without a version (un-migrated
// backend, or specific events that don't carry one) always apply — behaviour
// identical to before V3.

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { post: jest.fn(), get: jest.fn(), put: jest.fn(), patch: jest.fn(), delete: jest.fn() },
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

import { useRideStore } from '../rideStore';

const apply = (rideId: string, status: string, version?: number) =>
  useRideStore.getState().applyRideStatusFromWS(
    rideId,
    status,
    version === undefined ? undefined : { version },
  );

describe('rideStore — monotonic ride event ordering (V3)', () => {
  beforeEach(() => {
    useRideStore.setState({
      currentRide: null,
      currentDriver: null,
      // Reset the version tracker between tests.
      _lastEventRideId: null,
      _lastEventVersion: -1,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
  });

  const seedRide = (status = 'driver_assigned') =>
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    useRideStore.setState({ currentRide: { id: 'ride_1', status } as any });

  it('applies the first versioned event for a ride', () => {
    seedRide('driver_assigned');
    apply('ride_1', 'driver_accepted', 5);
    expect(useRideStore.getState().currentRide?.status).toBe('driver_accepted');
  });

  it('drops an event whose version is lower than the highest applied (stale)', () => {
    seedRide('driver_assigned');
    apply('ride_1', 'driver_accepted', 5);
    // A delayed driver_arrived arriving after the ride already advanced.
    apply('ride_1', 'driver_arrived', 3);
    expect(useRideStore.getState().currentRide?.status).toBe('driver_accepted');
  });

  it('drops a duplicate (same version is not strictly greater)', () => {
    seedRide('driver_assigned');
    apply('ride_1', 'driver_accepted', 5);
    apply('ride_1', 'in_progress', 5);
    expect(useRideStore.getState().currentRide?.status).toBe('driver_accepted');
  });

  it('applies a strictly newer version and advances the baseline', () => {
    seedRide('driver_assigned');
    apply('ride_1', 'driver_accepted', 5);
    apply('ride_1', 'driver_arrived', 7);
    expect(useRideStore.getState().currentRide?.status).toBe('driver_arrived');
    // 6 now stale relative to 7.
    apply('ride_1', 'in_progress', 6);
    expect(useRideStore.getState().currentRide?.status).toBe('driver_arrived');
  });

  it('resets the baseline for a different ride (versions are per-ride)', () => {
    seedRide('driver_assigned');
    apply('ride_1', 'in_progress', 9);
    // A new ride starts its own version sequence — a low version here must NOT
    // be dropped as stale against the previous ride's high version.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    useRideStore.setState({ currentRide: { id: 'ride_2', status: 'driver_assigned' } as any });
    apply('ride_2', 'driver_accepted', 1);
    expect(useRideStore.getState().currentRide?.status).toBe('driver_accepted');
  });

  it('always applies an event with no version (un-migrated backend / compat)', () => {
    seedRide('driver_assigned');
    apply('ride_1', 'driver_arrived'); // no version → applies
    expect(useRideStore.getState().currentRide?.status).toBe('driver_arrived');
    // Establish a baseline, then confirm a stale versioned event is dropped...
    apply('ride_1', 'in_progress', 4);
    apply('ride_1', 'driver_arrived', 2);
    expect(useRideStore.getState().currentRide?.status).toBe('in_progress');
    // ...but an absent-version event still applies (compat wins over ordering).
    apply('ride_1', 'completed');
    expect(useRideStore.getState().currentRide?.status).toBe('completed');
  });
});
