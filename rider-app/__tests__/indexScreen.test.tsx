/**
 * app/index.tsx — rider-app's cold-start routing gate. Pins:
 *  - waits for isInitialized before routing at all
 *  - no token -> /login
 *  - authenticated but profile incomplete -> /profile-setup
 *  - an active ride redirects by status: completed+unpaid -> /ride-completed,
 *    in_progress -> /ride-in-progress, driver_arrived -> /driver-arrived,
 *    driver_assigned/driver_accepted/searching -> /driver-arriving
 *  - a completed ride that's already paid (or admin-waived) does NOT
 *    redirect to /ride-completed -- falls through to the consent check
 *  - hydrateActiveRide/fetchActiveRide failing fails open (falls through
 *    to the consent check, never traps the rider)
 *  - the consent check: needs_notice -> /legacy-consent-notice, else
 *    /(tabs); a failed consent check fails open to /(tabs)
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';

// Stable module-level mock objects: a fresh literal per call would
// destabilize useEffect([..., router]) or any [useRideStore.getState()]-
// style identity comparison on re-render (see driver-app's index.tsx and
// subscription/success.tsx test files for the concrete failure mode this
// avoids -- an effect silently re-running/rescheduling instead of firing
// once).
const mockReplace = jest.fn();
const mockRouter = { replace: mockReplace };
jest.mock('expo-router', () => ({
  useRouter: () => mockRouter,
}));

const mockApiGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
}));

jest.mock('@shared/store/authStore', () => {
  const { create: createStore } = require('zustand');
  const useAuthStore = createStore(() => ({ isInitialized: false, token: null, user: null }));
  return { useAuthStore };
});

const mockHydrateActiveRide = jest.fn().mockResolvedValue(undefined);
const mockFetchActiveRide = jest.fn().mockResolvedValue({ active: false });
jest.mock('../store/rideStore', () => ({
  useRideStore: {
    getState: () => ({
      hydrateActiveRide: mockHydrateActiveRide,
      fetchActiveRide: mockFetchActiveRide,
    }),
  },
}));

import Index from '../app/index';
import { useAuthStore } from '@shared/store/authStore';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<Index />);
    await flush();
  });
  return renderer!;
}

beforeEach(() => {
  jest.clearAllMocks();
  useAuthStore.setState({ isInitialized: false, token: null, user: null });
  mockHydrateActiveRide.mockResolvedValue(undefined);
  mockFetchActiveRide.mockResolvedValue({ active: false });
  mockApiGet.mockResolvedValue({ data: { needs_notice: false } });
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('Index (rider-app cold start routing)', () => {
  it('does not navigate until initialized', async () => {
    await renderScreen();
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it('routes to /login when there is no token', async () => {
    useAuthStore.setState({ isInitialized: true, token: null, user: null });
    await renderScreen();
    expect(mockReplace).toHaveBeenCalledWith('/login');
  });

  it('routes to /profile-setup when the profile is incomplete', async () => {
    useAuthStore.setState({
      isInitialized: true, token: 't',
      user: { profile_complete: false, first_name: '', last_name: '', email: '' } as any,
    });
    await renderScreen();
    expect(mockReplace).toHaveBeenCalledWith('/profile-setup');
  });

  it('routes to /ride-completed for a completed-but-unpaid active ride', async () => {
    mockFetchActiveRide.mockResolvedValue({
      active: true, ride: { id: 'ride-1', status: 'completed', payment_status: 'pending' },
    });
    useAuthStore.setState({ isInitialized: true, token: 't', user: { profile_complete: true } as any });
    await renderScreen();
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/ride-completed', params: { rideId: 'ride-1' } });
  });

  it('does not redirect for a completed AND already-paid ride -- falls through to consent check', async () => {
    mockFetchActiveRide.mockResolvedValue({
      active: true, ride: { id: 'ride-1', status: 'completed', payment_status: 'paid' },
    });
    useAuthStore.setState({ isInitialized: true, token: 't', user: { profile_complete: true } as any });
    await renderScreen();
    expect(mockReplace).not.toHaveBeenCalledWith(expect.objectContaining({ pathname: '/ride-completed' }));
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('treats waived_admin the same as paid -- no redirect to ride-completed', async () => {
    mockFetchActiveRide.mockResolvedValue({
      active: true, ride: { id: 'ride-1', status: 'completed', payment_status: 'waived_admin' },
    });
    useAuthStore.setState({ isInitialized: true, token: 't', user: { profile_complete: true } as any });
    await renderScreen();
    expect(mockReplace).not.toHaveBeenCalledWith(expect.objectContaining({ pathname: '/ride-completed' }));
  });

  it('routes to /ride-in-progress for an in_progress ride', async () => {
    mockFetchActiveRide.mockResolvedValue({ active: true, ride: { id: 'ride-1', status: 'in_progress' } });
    useAuthStore.setState({ isInitialized: true, token: 't', user: { profile_complete: true } as any });
    await renderScreen();
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/ride-in-progress', params: { rideId: 'ride-1' } });
  });

  it('routes to /driver-arrived for a driver_arrived ride', async () => {
    mockFetchActiveRide.mockResolvedValue({ active: true, ride: { id: 'ride-1', status: 'driver_arrived' } });
    useAuthStore.setState({ isInitialized: true, token: 't', user: { profile_complete: true } as any });
    await renderScreen();
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/driver-arrived', params: { rideId: 'ride-1' } });
  });

  it.each(['driver_assigned', 'driver_accepted', 'searching'])(
    'routes to /driver-arriving for a %s ride',
    async (status) => {
      mockFetchActiveRide.mockResolvedValue({ active: true, ride: { id: 'ride-1', status } });
      useAuthStore.setState({ isInitialized: true, token: 't', user: { profile_complete: true } as any });
      await renderScreen();
      expect(mockReplace).toHaveBeenCalledWith({ pathname: '/driver-arriving', params: { rideId: 'ride-1' } });
    },
  );

  it('fails open to the consent check when fetchActiveRide throws', async () => {
    mockFetchActiveRide.mockRejectedValue(new Error('network down'));
    useAuthStore.setState({ isInitialized: true, token: 't', user: { profile_complete: true } as any });
    await renderScreen();
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('routes to /legacy-consent-notice when the consent check reports needs_notice', async () => {
    mockApiGet.mockResolvedValue({ data: { needs_notice: true } });
    useAuthStore.setState({ isInitialized: true, token: 't', user: { profile_complete: true } as any });
    await renderScreen();
    expect(mockReplace).toHaveBeenCalledWith('/legacy-consent-notice');
  });

  it('fails open to /(tabs) when the consent check itself fails', async () => {
    mockApiGet.mockRejectedValue(new Error('network down'));
    useAuthStore.setState({ isInitialized: true, token: 't', user: { profile_complete: true } as any });
    await renderScreen();
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });
});
