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
const mockAppCheckReady = jest.fn<Promise<boolean>, []>();
const mockSetAppCheckProvider = jest.fn();
const mockSetAppIdentity = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (u: string) => mockApiGet(u) },
  isAppCheckTokenReady: () => mockAppCheckReady(),
  setAppCheckTokenProvider: (f: unknown) => mockSetAppCheckProvider(f),
  setAppIdentity: (...a: unknown[]) => mockSetAppIdentity(...a),
}));

const mockInitFirebase = jest.fn<Promise<void>, []>();
jest.mock('@shared/services/firebase', () => ({
  getAppCheckToken: jest.fn(() => Promise.resolve('appcheck-token')),
  initFirebaseServices: () => mockInitFirebase(),
}));

jest.mock('expo-constants', () => ({
  __esModule: true,
  default: { nativeApplicationVersion: '1.2.3', expoConfig: { version: '1.2.3' } },
}));

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

const mockStartCarLocation = jest.fn<Promise<string>, []>(() => Promise.resolve('started'));
jest.mock('../carLocationTask', () => ({
  startCarLocationService: () => mockStartCarLocation(),
}));

const mockConsumeFixCount = jest.fn<number, []>(() => 30);
jest.mock('../carFixChannel', () => ({ consumeFixCount: () => mockConsumeFixCount() }));

const mockRecordNonFatal = jest.fn();
jest.mock('../../../utils/crashlytics', () => ({
  recordNonFatal: (...a: unknown[]) => mockRecordNonFatal(...a),
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
  mockConsumeFixCount.mockReturnValue(30);
  mockAppCheckReady.mockResolvedValue(true);
  mockInitFirebase.mockResolvedValue(undefined);
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

describe('App Check — the car must never sign a driver out', () => {
  it('wires the provider that app/_layout.tsx would have', async () => {
    // Without this, X-Firebase-AppCheck is omitted, /auth/refresh 401s under
    // production enforcement, and refreshTokens() deletes the refresh token.
    await startCarSession();
    expect(mockSetAppCheckProvider).toHaveBeenCalledWith(expect.any(Function));
    expect(mockSetAppIdentity).toHaveBeenCalledWith('driver', '1.2.3');
  });

  it('registers the provider BEFORE awaiting Firebase init', async () => {
    // Ordering matters: isAppCheckTokenReady() answers `true` when no provider
    // is registered, so a failed init with the provider unset would wave every
    // request through instead of blocking it.
    let providerSetFirst = false;
    mockInitFirebase.mockImplementation(async () => {
      providerSetFirst = mockSetAppCheckProvider.mock.calls.length > 0;
    });
    await startCarSession();
    expect(providerSetFirst).toBe(true);
  });

  it('issues NO request at all when App Check cannot mint a token', async () => {
    mockAppCheckReady.mockResolvedValue(false);

    await startCarSession();

    expect(mockDriver.fetchActiveRide).not.toHaveBeenCalled();
    expect(mockDriver.fetchEarnings).not.toHaveBeenCalled();
    expect(mockApiGet).not.toHaveBeenCalled();
    expect(mockDriver.hydrateDriverRideState).not.toHaveBeenCalled();
  });

  it('keeps the interval quiet too, not just the bootstrap', async () => {
    // The timer fires long after startCarSession returned, and every one of its
    // requests can reach the same 401 -> logout() path.
    jest.useFakeTimers();
    try {
      await startCarSession();
      mockDriver.fetchActiveRide.mockClear();
      mockAppCheckReady.mockResolvedValue(false);

      jest.advanceTimersByTime(60_000);
      await settle();

      expect(mockDriver.fetchActiveRide).not.toHaveBeenCalled();
    } finally {
      jest.useRealTimers();
    }
  });

  it('a Firebase init failure degrades to map-only, it does not throw', async () => {
    mockInitFirebase.mockRejectedValue(new Error('Play Services updating'));
    mockAppCheckReady.mockResolvedValue(false);
    await expect(startCarSession()).resolves.toBeUndefined();
    expect(mockApiGet).not.toHaveBeenCalled();
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

  it('re-asserts the location service on every tick, even with no API access', async () => {
    // Covers go-offline-while-plugged-in: stopBackgroundLocation tears down the
    // dispatch service the car had deferred to, leaving nothing running. Also a
    // service the OS killed, and permission granted after connect.
    jest.useFakeTimers();
    try {
      await startCarSession();
      mockStartCarLocation.mockClear();
      mockAppCheckReady.mockResolvedValue(false); // drawing a map needs no token

      jest.advanceTimersByTime(60_000);
      await settle();

      expect(mockStartCarLocation).toHaveBeenCalledTimes(1);
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

  it('reports a starved location task once, after a grace period', async () => {
    // The open question the no-notification fallback leaves behind: does Android
    // throttle a plain background task while Android Auto has us bound? Only a
    // real head unit can answer, so the fix rate is measured and reported.
    jest.useFakeTimers();
    try {
      await startCarSession();
      mockConsumeFixCount.mockReturnValue(1); // throttled to ~1/min

      jest.advanceTimersByTime(60_000); // tick 1 — inside the grace period
      await settle();
      jest.advanceTimersByTime(60_000); // tick 2 — still grace (cold GPS is slow)
      await settle();
      expect(mockRecordNonFatal).not.toHaveBeenCalled();

      jest.advanceTimersByTime(60_000); // tick 3 — now it counts
      await settle();
      expect(mockRecordNonFatal).toHaveBeenCalledTimes(1);
      expect(mockRecordNonFatal.mock.calls[0][1]).toMatchObject({
        reason: 'car_location_throttled',
        fixes_per_min: '1',
      });

      // Once per session, not once per minute of driving.
      jest.advanceTimersByTime(180_000);
      await settle();
      expect(mockRecordNonFatal).toHaveBeenCalledTimes(1);
    } finally {
      jest.useRealTimers();
    }
  });

  it('a healthy fix rate is never reported', async () => {
    jest.useFakeTimers();
    try {
      await startCarSession();
      jest.advanceTimersByTime(300_000);
      await settle();
      expect(mockRecordNonFatal).not.toHaveBeenCalled();
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
