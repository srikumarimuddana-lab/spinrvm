/**
 * Unit tests for lib/androidAuto/carSession.ts — the headless bootstrap that
 * stands in for useDriverDashboard's mount effects on a car-only launch.
 *
 * What these pin down: the bootstrap runs in an order where the token exists
 * before the requests that need it; a signed-out driver still gets a working
 * map; every individual failure is contained; and the refresh timer + AppState
 * listener are torn down on disconnect. That last one is not theoretical — this
 * branch already shipped one timer that outlived its session.
 */
const mockAuth: Record<string, unknown> = {};
const mockAuthListeners = new Set<(s: unknown) => void>();
jest.mock('@shared/store/authStore', () => ({
  useAuthStore: {
    getState: () => mockAuth,
    subscribe: (l: (s: unknown) => void) => {
      mockAuthListeners.add(l);
      return () => mockAuthListeners.delete(l);
    },
  },
}));

const mockApiGet = jest.fn();
jest.mock('@shared/api/client', () => ({ __esModule: true, default: { get: (u: string) => mockApiGet(u) } }));

const mockConsumePending = jest.fn<Promise<boolean>, []>();
jest.mock('../../../services/pendingRideOffer', () => ({
  consumePendingRideOffer: () => mockConsumePending(),
}));

let mockDispatchCb: ((e: unknown) => void) | null = null;
const mockUnsubDispatch = jest.fn();
jest.mock('../../../services/backgroundMessaging', () => ({
  subscribeBackgroundDispatch: (cb: (e: unknown) => void) => {
    mockDispatchCb = cb;
    return mockUnsubDispatch;
  },
}));

const calls: string[] = [];
const mockDriver = {
  rideState: 'idle' as string,
  activeRide: null as unknown,
  incomingRide: null as unknown,
  setIncomingRide: jest.fn(),
  resetRideState: jest.fn(),
  fetchActiveRide: jest.fn(() => { calls.push('fetchActiveRide'); return Promise.resolve(); }),
  fetchEarnings: jest.fn(() => { calls.push('fetchEarnings'); return Promise.resolve(); }),
  hydrateDriverRideState: jest.fn(() => { calls.push('hydrate'); return Promise.resolve(); }),
  applyDriverConfig: jest.fn(() => { calls.push('applyDriverConfig'); }),
};
jest.mock('../../../store/driverStore', () => ({
  useDriverStore: { getState: () => mockDriver },
}));

let mockAppStateCb: ((s: string) => void) | null = null;
const mockRemoveAppState = jest.fn();
jest.mock('react-native', () => ({
  AppState: {
    addEventListener: (_e: string, cb: (s: string) => void) => {
      mockAppStateCb = cb;
      return { remove: mockRemoveAppState };
    },
  },
}));

import { startCarSession, stopCarSession } from '../carSession';

const setAuth = (next: Record<string, unknown>) => {
  Object.keys(mockAuth).forEach((k) => delete mockAuth[k]);
  Object.assign(mockAuth, next);
};

/** Let the bootstrap's chained awaits settle. */
const settle = async () => {
  for (let i = 0; i < 8; i++) await Promise.resolve();
};

beforeEach(() => {
  jest.clearAllMocks();
  jest.spyOn(console, 'error').mockImplementation(() => {});
  jest.spyOn(console, 'log').mockImplementation(() => {});
  calls.length = 0;
  mockAuthListeners.clear();
  mockAppStateCb = null;
  mockDispatchCb = null;
  mockDriver.rideState = 'idle';
  mockDriver.activeRide = null;
  mockDriver.incomingRide = null;
  mockConsumePending.mockResolvedValue(false);
  mockApiGet.mockResolvedValue({ data: { ride_offer_timeout_seconds: 20 } });
  setAuth({ isInitialized: true, isLoading: false, token: 'tok' });
});

afterEach(() => {
  stopCarSession();
  jest.restoreAllMocks();
});

describe('bootstrap', () => {
  it('loads what the phone dashboard would have, in its order', async () => {
    await startCarSession();

    // Cached state paints first, then the server correction overrides it —
    // same ordering as useDriverDashboard's mount effect.
    expect(calls.indexOf('hydrate')).toBeLessThan(calls.indexOf('fetchActiveRide'));
    expect(mockDriver.fetchEarnings).toHaveBeenCalledWith('today');
    expect(mockApiGet).toHaveBeenCalledWith('/drivers/config');
    expect(mockDriver.applyDriverConfig).toHaveBeenCalledWith({ ride_offer_timeout_seconds: 20 });
  });

  it('looks for a stashed offer before anything that needs a token', async () => {
    mockConsumePending.mockResolvedValue(true);
    await startCarSession();
    expect(mockConsumePending).toHaveBeenCalledTimes(1);
  });

  it('initialises the session when no phone screen has', async () => {
    const initialize = jest.fn(() => {
      setAuth({ isInitialized: true, isLoading: false, token: 'fresh' });
      return Promise.resolve();
    });
    setAuth({ isInitialized: false, isLoading: false, token: null, initialize });

    await startCarSession();

    expect(initialize).toHaveBeenCalledTimes(1);
    expect(mockDriver.fetchActiveRide).toHaveBeenCalled();
  });

  it('joins an initialize already in flight instead of racing a second one', async () => {
    // The refresh token is single-use and rotates; two concurrent initializes
    // is the race that signs drivers out.
    const initialize = jest.fn(() => Promise.resolve());
    setAuth({ isInitialized: false, isLoading: true, token: null, initialize });

    const done = startCarSession();
    await settle();
    expect(initialize).not.toHaveBeenCalled();

    setAuth({ isInitialized: true, isLoading: false, token: 'tok' });
    mockAuthListeners.forEach((l) => l(mockAuth));
    await done;

    expect(initialize).not.toHaveBeenCalled();
    expect(mockDriver.fetchActiveRide).toHaveBeenCalled();
  });
});

describe('degraded paths', () => {
  it('a signed-out driver still gets a car session — just no data', async () => {
    setAuth({ isInitialized: true, isLoading: false, token: null });

    await expect(startCarSession()).resolves.toBeUndefined();

    // The map, marker and buttons need none of this; issuing the calls anyway
    // would just 401 and log noise at a driver who is not signed in.
    expect(mockDriver.fetchActiveRide).not.toHaveBeenCalled();
    expect(mockApiGet).not.toHaveBeenCalled();
  });

  it('every fetch failing still completes the bootstrap', async () => {
    mockDriver.fetchActiveRide.mockRejectedValueOnce(new Error('503'));
    mockDriver.fetchEarnings.mockRejectedValueOnce(new Error('503'));
    mockDriver.hydrateDriverRideState.mockRejectedValueOnce(new Error('storage'));
    mockApiGet.mockRejectedValueOnce(new Error('timeout'));

    await expect(startCarSession()).resolves.toBeUndefined();
  });

  it('a config response with no body does not apply an empty config', async () => {
    mockApiGet.mockResolvedValue({});
    await startCarSession();
    expect(mockDriver.applyDriverConfig).not.toHaveBeenCalled();
  });

  it('an initialize that never settles cannot hold the bootstrap open forever', async () => {
    jest.useFakeTimers();
    try {
      setAuth({ isInitialized: false, isLoading: true, token: null });
      const done = startCarSession();
      await settle();
      jest.advanceTimersByTime(8_000); // AUTH_WAIT_MS
      await expect(done).resolves.toBeUndefined();
    } finally {
      jest.useRealTimers();
    }
  });
});

describe('lifecycle', () => {
  it('a reconnect refreshes rather than re-running the bootstrap', async () => {
    // The reversing camera re-fires didConnect without a disconnect.
    await startCarSession();
    mockDriver.hydrateDriverRideState.mockClear();
    mockApiGet.mockClear();

    await startCarSession();

    expect(mockDriver.hydrateDriverRideState).not.toHaveBeenCalled();
    expect(mockApiGet).not.toHaveBeenCalled();
    expect(mockDriver.fetchActiveRide).toHaveBeenCalled();
  });

  it('does not stack a second refresh timer on reconnect', async () => {
    jest.useFakeTimers();
    try {
      await startCarSession();
      const afterFirst = jest.getTimerCount();
      await startCarSession();
      expect(jest.getTimerCount()).toBe(afterFirst);
    } finally {
      jest.useRealTimers();
    }
  });

  it('refreshes on the interval while connected', async () => {
    jest.useFakeTimers();
    try {
      await startCarSession();
      mockDriver.fetchActiveRide.mockClear();
      jest.advanceTimersByTime(60_000);
      await settle();
      expect(mockDriver.fetchActiveRide).toHaveBeenCalledTimes(1);
    } finally {
      jest.useRealTimers();
    }
  });

  it('refreshes when the phone app comes to the foreground', async () => {
    await startCarSession();
    mockDriver.fetchActiveRide.mockClear();

    mockAppStateCb?.('background');
    expect(mockDriver.fetchActiveRide).not.toHaveBeenCalled();

    mockAppStateCb?.('active');
    await settle();
    expect(mockDriver.fetchActiveRide).toHaveBeenCalledTimes(1);
  });

  it('stopCarSession clears the timer and the AppState listener', async () => {
    jest.useFakeTimers();
    try {
      const before = jest.getTimerCount();
      await startCarSession();
      expect(jest.getTimerCount()).toBeGreaterThan(before);

      stopCarSession();

      expect(jest.getTimerCount()).toBe(before);
      expect(mockRemoveAppState).toHaveBeenCalledTimes(1);
    } finally {
      jest.useRealTimers();
    }
  });

  it('stopCarSession is safe when nothing was started', () => {
    expect(() => stopCarSession()).not.toThrow();
  });

  it('unsubscribes from the FCM channel on disconnect', async () => {
    // With no car connected the channel must have NO subscriber, so the phone's
    // own offer path is exactly what it was before any of this existed.
    await startCarSession();
    stopCarSession();
    expect(mockUnsubDispatch).toHaveBeenCalledTimes(1);
  });
});

describe('offers on a car-only launch', () => {
  const offer = { ride_id: 'r1', pickup_address: '1 Main St' };

  it('puts an offer straight into the store — nothing else reads it back', async () => {
    await startCarSession();
    mockDispatchCb?.({ type: 'new_ride_assignment', ride_id: 'r1', offer });
    expect(mockDriver.setIncomingRide).toHaveBeenCalledWith(offer);
  });

  it('subscribes before the bootstrap awaits, so an offer mid-bootstrap lands', async () => {
    mockConsumePending.mockImplementation(async () => {
      // An offer arriving while the session is still coming up.
      mockDispatchCb?.({ type: 'new_ride_assignment', ride_id: 'r1', offer });
      return false;
    });
    await startCarSession();
    expect(mockDriver.setIncomingRide).toHaveBeenCalledWith(offer);
  });

  it('cancels the ride the car is showing', async () => {
    mockDriver.rideState = 'navigating_to_pickup';
    mockDriver.activeRide = { ride: { id: 'r1' } };
    await startCarSession();

    mockDispatchCb?.({ type: 'ride_cancelled', ride_id: 'r1' });

    expect(mockDriver.resetRideState).toHaveBeenCalledTimes(1);
  });

  it('cancels an offer the driver has not accepted yet', async () => {
    mockDriver.rideState = 'ride_offered';
    mockDriver.incomingRide = { ride_id: 'r2' };
    await startCarSession();

    mockDispatchCb?.({ type: 'ride_cancelled', ride_id: 'r2' });

    expect(mockDriver.resetRideState).toHaveBeenCalled();
  });

  it('ignores a cancellation for some other ride', async () => {
    mockDriver.rideState = 'navigating_to_pickup';
    mockDriver.activeRide = { ride: { id: 'r1' } };
    await startCarSession();

    mockDispatchCb?.({ type: 'ride_cancelled', ride_id: 'someone-elses-ride' });

    expect(mockDriver.resetRideState).not.toHaveBeenCalled();
  });

  it('REFUSES to cancel a trip already under way', async () => {
    // CLAUDE.md: the only transition out of in_progress is completed. Acting on
    // this would strand a driver mid-trip with an idle car screen.
    mockDriver.rideState = 'trip_in_progress';
    mockDriver.activeRide = { ride: { id: 'r1' } };
    await startCarSession();

    mockDispatchCb?.({ type: 'ride_cancelled', ride_id: 'r1' });

    expect(mockDriver.resetRideState).not.toHaveBeenCalled();
  });

  it('a throwing store action cannot break the channel', async () => {
    mockDriver.setIncomingRide.mockImplementationOnce(() => {
      throw new Error('store blew up');
    });
    await startCarSession();
    expect(() =>
      mockDispatchCb?.({ type: 'new_ride_assignment', ride_id: 'r1', offer }),
    ).not.toThrow();
  });
});
