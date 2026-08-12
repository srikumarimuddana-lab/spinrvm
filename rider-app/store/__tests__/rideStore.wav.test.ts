/**
 * WAV (wheelchair-accessible vehicle) dispatch tests for rideStore.
 *
 * Pins:
 *  - requiresWav defaults to false
 *  - setRequiresWav toggles the flag
 *  - createRide sends requires_wav: true when flag is set
 *  - createRide sends requires_wav: false (not omitted) when flag is unset
 *  - requiresWav resets to false after a successful createRide
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
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  },
}));
jest.mock('@shared/store/authStore', () => ({
  registerLogoutCallback: jest.fn(),
  useAuthStore: { getState: () => ({ user: { id: 'user-test' } }) },
}));
jest.mock('@shared/services/errorReporting', () => ({ recordNonFatal: jest.fn() }));

const mockApi = api as jest.Mocked<typeof api>;

const BASE_RIDE_STATE = {
  pickup: { address: '123 Main St', lat: 52.1, lng: -106.6 },
  dropoff: { address: '456 Elm St', lat: 52.2, lng: -106.7 },
  selectedVehicle: { id: 'vt-001', name: 'Comfort', capacity: 4 },
  stops: [],
  scheduledTime: null,
  estimates: [],
};

function setBaseState() {
  useRideStore.setState(BASE_RIDE_STATE as any);
}

describe('rideStore WAV flag', () => {
  beforeEach(() => {
    useRideStore.setState({ requiresWav: false, currentRide: null } as any);
    jest.clearAllMocks();
  });

  it('defaults requiresWav to false', () => {
    expect(useRideStore.getState().requiresWav).toBe(false);
  });

  it('setRequiresWav toggles to true', () => {
    useRideStore.getState().setRequiresWav(true);
    expect(useRideStore.getState().requiresWav).toBe(true);
  });

  it('setRequiresWav toggles back to false', () => {
    useRideStore.getState().setRequiresWav(true);
    useRideStore.getState().setRequiresWav(false);
    expect(useRideStore.getState().requiresWav).toBe(false);
  });

  it('createRide includes requires_wav: true in POST body when flag is set', async () => {
    setBaseState();
    useRideStore.setState({ requiresWav: true } as any);
    mockApi.post.mockResolvedValueOnce({ data: { id: 'ride-1', status: 'searching' }, status: 200 });

    await useRideStore.getState().createRide('card');

    const body = mockApi.post.mock.calls[0][1] as any;
    expect(body.requires_wav).toBe(true);
  });

  it('createRide includes requires_wav: false when flag is unset', async () => {
    setBaseState();
    useRideStore.setState({ requiresWav: false } as any);
    mockApi.post.mockResolvedValueOnce({ data: { id: 'ride-2', status: 'searching' }, status: 200 });

    await useRideStore.getState().createRide('card');

    const body = mockApi.post.mock.calls[0][1] as any;
    expect(body.requires_wav).toBe(false);
  });

  it('requiresWav resets to false after successful createRide', async () => {
    setBaseState();
    useRideStore.setState({ requiresWav: true } as any);
    mockApi.post.mockResolvedValueOnce({ data: { id: 'ride-3', status: 'searching' }, status: 200 });

    await useRideStore.getState().createRide('card');

    expect(useRideStore.getState().requiresWav).toBe(false);
  });
});
