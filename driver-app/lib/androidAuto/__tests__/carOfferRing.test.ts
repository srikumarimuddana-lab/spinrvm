/**
 * Unit tests for lib/androidAuto/carOfferRing.ts — the single ring owner for a
 * ride offer while Android Auto is connected.
 *
 * The native tone module, the stores, Notifee and Crashlytics are mocked, so
 * these pin the JS contract: exactly one source rings, the car tone stops the
 * moment an offer is no longer live, and every way the car can lose the ring
 * mid-offer hands it back to the phone.
 *
 * Vars referenced inside jest.mock factories are `mock`-prefixed (jest hoisting).
 */
const mockSupported = jest.fn<boolean, []>(() => true);
const mockStart = jest.fn<Promise<string>, [number, number]>(() => Promise.resolve('playing'));
const mockStop = jest.fn();
jest.mock('../../../modules/ride-offer-tone', () => ({
  isRideOfferToneSupported: () => mockSupported(),
  startRideOfferTone: (maxMs: number, gapMs: number) => mockStart(maxMs, gapMs),
  stopRideOfferTone: () => mockStop(),
}));

const mockSetCountdown = jest.fn();
const mockDriver: Record<string, unknown> = {
  rideState: 'idle',
  incomingRide: null,
  countdownSeconds: 0,
  acceptNetworkHold: false,
  setCountdown: (n: number) => mockSetCountdown(n),
};
jest.mock('../../../store/driverStore', () => ({
  useDriverStore: { getState: () => mockDriver },
}));

const mockLoadPrefs = jest.fn(async () => {
  mockPrefs.isLoaded = true;
});
const mockPrefs: Record<string, unknown> = {
  soundEffects: true,
  isLoaded: true,
  loadAlertPrefs: () => mockLoadPrefs(),
};
jest.mock('../../../store/alertPrefsStore', () => ({
  useAlertPrefsStore: { getState: () => mockPrefs },
}));

const mockDisplay = jest.fn((..._a: unknown[]) => Promise.resolve());
const mockDismiss = jest.fn(() => Promise.resolve());
jest.mock('../../../services/notifeeService', () => ({
  displayRideOfferNotification: (...a: unknown[]) => mockDisplay(...a),
  dismissRideOfferNotification: () => mockDismiss(),
}));

const mockRecordNonFatal = jest.fn();
jest.mock('../../../utils/crashlytics', () => ({
  recordNonFatal: (...a: unknown[]) => mockRecordNonFatal(...a),
}));

jest.mock('../carDebug', () => ({ pushDebug: jest.fn(), setDebugFact: jest.fn() }));

jest.mock('react-native', () => ({ Platform: { OS: 'android' } }));

type Ring = typeof import('../carOfferRing');
let ring: Ring;

const offer = (id: string, extra: Record<string, unknown> = {}) => ({
  ride_id: id,
  fare: '12.50',
  pickup_address: '1 Main St',
  countdown_seconds: 20,
  ...extra,
});

/** Put an offer in the (mock) store and tell the ring owner about it. */
const offerLive = (o: ReturnType<typeof offer>, countdownSeconds = 20) => {
  mockDriver.rideState = 'ride_offered';
  mockDriver.incomingRide = o;
  mockDriver.countdownSeconds = countdownSeconds;
  ring.syncCarOfferRing({ rideState: 'ride_offered', incomingRide: o, countdownSeconds });
};

const offerEnds = (rideState = 'idle') => {
  mockDriver.rideState = rideState;
  mockDriver.incomingRide = null;
  mockDriver.countdownSeconds = 0;
  ring.syncCarOfferRing({ rideState, incomingRide: null, countdownSeconds: 0 });
};

const makeOwner = () => {
  ring.setCarConnected(true);
  ring.setCarOfferToneEnabled(true);
};

/** Let the async start (prefs load → native start) settle. */
const flush = async () => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
};

beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  jest.spyOn(console, 'log').mockImplementation(() => {});
  jest.spyOn(console, 'error').mockImplementation(() => {});
  mockSupported.mockReturnValue(true);
  mockStart.mockImplementation(() => Promise.resolve('playing'));
  mockDriver.rideState = 'idle';
  mockDriver.incomingRide = null;
  mockDriver.countdownSeconds = 0;
  mockDriver.acceptNetworkHold = false;
  mockPrefs.soundEffects = true;
  mockPrefs.isLoaded = true;
  // Module state (connected, flag, ringing offer) must not leak between tests.
  jest.isolateModules(() => {
    ring = require('../carOfferRing');
  });
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

describe('ownership', () => {
  it('is the car only when connected, flagged on, and the native tone works', () => {
    expect(ring.isCarRingOwner()).toBe(false);
    ring.setCarConnected(true);
    expect(ring.isCarRingOwner()).toBe(false); // flag still off
    ring.setCarOfferToneEnabled(true);
    expect(ring.isCarRingOwner()).toBe(true);
    ring.setCarConnected(false);
    expect(ring.isCarRingOwner()).toBe(false);
  });

  it('is never the car on a build without the native module', () => {
    mockSupported.mockReturnValue(false);
    makeOwner();
    expect(ring.isCarRingOwner()).toBe(false);
  });

  it('notifies subscribers only when ownership actually changes', () => {
    const cb = jest.fn();
    ring.subscribeCarRingOwner(cb);
    ring.setCarConnected(true);
    expect(cb).not.toHaveBeenCalled();
    ring.setCarOfferToneEnabled(true);
    expect(cb).toHaveBeenLastCalledWith(true);
    ring.setCarOfferToneEnabled(true); // no change
    expect(cb).toHaveBeenCalledTimes(1);
    ring.setCarConnected(false);
    expect(cb).toHaveBeenLastCalledWith(false);
  });

  it('stops notifying after unsubscribe', () => {
    const cb = jest.fn();
    const unsub = ring.subscribeCarRingOwner(cb);
    unsub();
    makeOwner();
    expect(cb).not.toHaveBeenCalled();
  });
});

describe('ringing', () => {
  it('starts the car tone for a new offer when the car owns the ring', async () => {
    makeOwner();
    offerLive(offer('r1'));
    await flush();
    expect(mockStart).toHaveBeenCalledTimes(1);
    expect(mockStart).toHaveBeenCalledWith(20_000, ring.OFFER_TONE_GAP_MS);
  });

  it('does not start when the flag is off — the phone rings as today', async () => {
    ring.setCarConnected(true);
    offerLive(offer('r1'));
    await flush();
    expect(mockStart).not.toHaveBeenCalled();
  });

  it('does not start on a build without the native module', async () => {
    mockSupported.mockReturnValue(false);
    makeOwner();
    offerLive(offer('r1'));
    await flush();
    expect(mockStart).not.toHaveBeenCalled();
  });

  it('starts once per offer, not on every store tick', async () => {
    makeOwner();
    offerLive(offer('r1'));
    offerLive(offer('r1'), 19);
    offerLive(offer('r1'), 18);
    await flush();
    expect(mockStart).toHaveBeenCalledTimes(1);
  });

  it('loads the alert prefs first on a car-only launch', async () => {
    mockPrefs.isLoaded = false;
    makeOwner();
    offerLive(offer('r1'));
    await flush();
    expect(mockLoadPrefs).toHaveBeenCalledTimes(1);
    expect(mockStart).toHaveBeenCalledTimes(1);
  });

  it('with Sound Effects off, the car offer is visual only', async () => {
    mockPrefs.soundEffects = false;
    makeOwner();
    offerLive(offer('r1'));
    await flush();
    expect(mockStart).not.toHaveBeenCalled();
    // Still the owner: the phone must stay silent too.
    expect(ring.isCarRingOwner()).toBe(true);
  });

  it('restarts for a replacing offer', async () => {
    makeOwner();
    offerLive(offer('r1'));
    await flush();
    mockStop.mockClear();
    offerLive(offer('r2'));
    await flush();
    expect(mockStop).toHaveBeenCalled();
    expect(mockStart).toHaveBeenCalledTimes(2);
  });

  it('stops the tone at the offer deadline', async () => {
    makeOwner();
    offerLive(offer('r1'), 10);
    await flush();
    // offer.countdown_seconds is 20 but the store countdown (10) wins.
    expect(mockStart).toHaveBeenCalledWith(10_000, ring.OFFER_TONE_GAP_MS);
    mockStop.mockClear();
    jest.advanceTimersByTime(10_000);
    expect(mockStop).toHaveBeenCalled();
  });

  it('reports the first result of a car session once, not per offer', async () => {
    makeOwner();
    offerLive(offer('r1'));
    await flush();
    offerLive(offer('r2'));
    await flush();
    expect(mockRecordNonFatal).toHaveBeenCalledTimes(1);
    expect(mockRecordNonFatal.mock.calls[0][1]).toMatchObject({
      reason: 'car_offer_tone_result',
      result: 'playing',
    });
  });

  it.each([
    ['accepted', 'navigating_to_pickup'],
    ['declined / expired / cancelled', 'idle'],
  ])('stops and dismisses the card once the offer is %s', async (_label, next) => {
    makeOwner();
    offerLive(offer('r1'));
    await flush();
    mockStop.mockClear();
    offerEnds(next);
    expect(mockStop).toHaveBeenCalled();
    expect(mockDismiss).toHaveBeenCalledTimes(1);
  });

  it('dismisses the card when the offer ends even if the car never rang (flag off)', () => {
    ring.setCarConnected(true);
    offerLive(offer('r1'));
    offerEnds();
    expect(mockDismiss).toHaveBeenCalledTimes(1);
    expect(mockStop).not.toHaveBeenCalled();
  });

  it('does nothing when no offer was ever live', () => {
    makeOwner();
    offerEnds();
    expect(mockDismiss).not.toHaveBeenCalled();
  });

  it('a tone start still in flight when the offer ends never hands back', async () => {
    let resolveStart: (r: string) => void = () => {};
    mockStart.mockImplementation(() => new Promise((r) => { resolveStart = r; }));
    const handler = jest.fn();
    ring.registerPhoneRingHandler(handler);
    makeOwner();
    offerLive(offer('r1'));
    await flush();
    offerEnds();
    resolveStart('error');
    await flush();
    expect(handler).not.toHaveBeenCalled();
    expect(mockDisplay).not.toHaveBeenCalled();
  });
});

describe('hand back to the phone', () => {
  it('flag switched off mid-offer: stops the car tone and re-rings via the phone app', async () => {
    const handler = jest.fn();
    ring.registerPhoneRingHandler(handler);
    makeOwner();
    const o = offer('r1');
    offerLive(o);
    await flush();
    mockStop.mockClear();

    ring.setCarOfferToneEnabled(false);

    expect(mockStop).toHaveBeenCalled();
    expect(handler).toHaveBeenCalledWith(o);
    expect(mockDisplay).not.toHaveBeenCalled();
  });

  it('car disconnected mid-offer with no phone UI: reclaims the notification', async () => {
    makeOwner();
    offerLive(offer('r1'));
    await flush();

    ring.setCarConnected(false);

    expect(mockDisplay).toHaveBeenCalledTimes(1);
    const [data, opts] = mockDisplay.mock.calls[0] as [Record<string, unknown>, Record<string, unknown>];
    expect(data).toMatchObject({ ride_id: 'r1', fare: 12.5 });
    expect(opts).toEqual({ reclaim: true, muted: false });
  });

  it('reclaims muted when the driver turned Sound Effects off', async () => {
    makeOwner();
    offerLive(offer('r1'));
    await flush();
    mockPrefs.soundEffects = false;
    ring.setCarConnected(false);
    expect(mockDisplay.mock.calls[0][1]).toEqual({ reclaim: true, muted: true });
  });

  it('no hand back when ownership changes with no live offer', () => {
    const handler = jest.fn();
    ring.registerPhoneRingHandler(handler);
    makeOwner();
    ring.setCarConnected(false);
    expect(handler).not.toHaveBeenCalled();
    expect(mockDisplay).not.toHaveBeenCalled();
  });

  it.each(['error', 'unsupported'])(
    'a %s start hands back and drops ownership for the rest of that offer',
    async (result) => {
      mockStart.mockImplementation(() => Promise.resolve(result));
      const handler = jest.fn();
      const owners: boolean[] = [];
      ring.registerPhoneRingHandler(handler);
      ring.subscribeCarRingOwner((o) => owners.push(o));
      makeOwner();
      offerLive(offer('r1'));
      await flush();

      expect(handler).toHaveBeenCalledTimes(1);
      expect(ring.isCarRingOwner()).toBe(false);
      expect(owners.at(-1)).toBe(false);

      // The next offer tries the car again.
      offerEnds();
      expect(ring.isCarRingOwner()).toBe(true);
    },
  );

  it('a start that throws is treated like an error', async () => {
    mockStart.mockImplementation(() => Promise.reject(new Error('boom')));
    const handler = jest.fn();
    ring.registerPhoneRingHandler(handler);
    makeOwner();
    offerLive(offer('r1'));
    await flush();
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('blocked_call: no car tone and NO hand back — never ring over a call', async () => {
    mockStart.mockImplementation(() => Promise.resolve('blocked_call'));
    const handler = jest.fn();
    ring.registerPhoneRingHandler(handler);
    makeOwner();
    offerLive(offer('r1'));
    await flush();
    expect(handler).not.toHaveBeenCalled();
    expect(mockDisplay).not.toHaveBeenCalled();
    expect(ring.isCarRingOwner()).toBe(true);
  });

  it('a hand back that throws is contained', async () => {
    ring.registerPhoneRingHandler(() => {
      throw new Error('handler blew up');
    });
    makeOwner();
    offerLive(offer('r1'));
    await flush();
    expect(() => ring.setCarOfferToneEnabled(false)).not.toThrow();
  });

  it('unregistering the phone handler falls back to the notification', async () => {
    const handler = jest.fn();
    const unregister = ring.registerPhoneRingHandler(handler);
    unregister();
    makeOwner();
    offerLive(offer('r1'));
    await flush();
    ring.setCarOfferToneEnabled(false);
    expect(handler).not.toHaveBeenCalled();
    expect(mockDisplay).toHaveBeenCalledTimes(1);
  });

  it('flag switched ON mid-offer: the car takes the ring for the live offer', async () => {
    ring.setCarConnected(true);
    offerLive(offer('r1'));
    await flush();
    expect(mockStart).not.toHaveBeenCalled();
    ring.setCarOfferToneEnabled(true);
    await flush();
    expect(mockStart).toHaveBeenCalledTimes(1);
  });

  it('a reconnect mid-offer rings the car again for the same offer', async () => {
    makeOwner();
    offerLive(offer('r1'));
    await flush();
    ring.setCarConnected(false);
    ring.setCarConnected(true);
    offerLive(offer('r1'));
    await flush();
    expect(mockStart).toHaveBeenCalledTimes(2);
  });
});

describe('car-only offer expiry', () => {
  // countdownSeconds 10 → deadline 10 s, fires at 10 s + grace.
  const FIRE_AT = 10_000 + 1_500;

  it('expires an unanswered offer when no phone UI is mounted', () => {
    ring.setCarConnected(true); // flag off: expiry is not tied to the tone
    offerLive(offer('r1'), 10);
    jest.advanceTimersByTime(FIRE_AT - 1);
    expect(mockSetCountdown).not.toHaveBeenCalled();
    jest.advanceTimersByTime(1);
    expect(mockSetCountdown).toHaveBeenCalledWith(0);
  });

  it('never races the phone screen countdown', () => {
    ring.registerPhoneRingHandler(jest.fn());
    ring.setCarConnected(true);
    offerLive(offer('r1'), 10);
    jest.advanceTimersByTime(FIRE_AT);
    expect(mockSetCountdown).not.toHaveBeenCalled();
  });

  it('does nothing once the offer was answered', () => {
    ring.setCarConnected(true);
    offerLive(offer('r1'), 10);
    offerEnds('navigating_to_pickup');
    jest.advanceTimersByTime(FIRE_AT);
    expect(mockSetCountdown).not.toHaveBeenCalled();
  });

  it('re-validates the store at fire time (state changed with no sync)', () => {
    ring.setCarConnected(true);
    offerLive(offer('r1'), 10);
    mockDriver.rideState = 'idle';
    mockDriver.incomingRide = null;
    jest.advanceTimersByTime(FIRE_AT);
    expect(mockSetCountdown).not.toHaveBeenCalled();
  });

  it('is not armed without a car', () => {
    offerLive(offer('r1'), 10);
    jest.advanceTimersByTime(FIRE_AT);
    expect(mockSetCountdown).not.toHaveBeenCalled();
  });

  it('a replacing offer gets its own deadline; the old one never fires', () => {
    ring.setCarConnected(true);
    offerLive(offer('r1'), 10);
    jest.advanceTimersByTime(5_000);
    offerLive(offer('r2'), 30);
    jest.advanceTimersByTime(FIRE_AT);
    expect(mockSetCountdown).not.toHaveBeenCalled();
    jest.advanceTimersByTime(30_000 + 1_500 - FIRE_AT);
    expect(mockSetCountdown).toHaveBeenCalledTimes(1);
  });

  it('waits out an accept in flight, then expires only if still unanswered', () => {
    ring.setCarConnected(true);
    offerLive(offer('r1'), 10);
    mockDriver.acceptNetworkHold = true;
    jest.advanceTimersByTime(FIRE_AT);
    expect(mockSetCountdown).not.toHaveBeenCalled();
    mockDriver.acceptNetworkHold = false; // accept failed offline, offer still up
    jest.advanceTimersByTime(2_000);
    expect(mockSetCountdown).toHaveBeenCalledWith(0);
  });
});

describe('offerDeadlineMs', () => {
  const NOW = 1_000_000;

  it('prefers the server expiry', () => {
    const o = { ride_id: 'r', offer_expires_at: new Date(NOW + 12_000).toISOString(), countdown_seconds: 30 };
    expect(ring.offerDeadlineMs(o, 25, NOW)).toBe(12_000);
  });

  it('falls back to the store countdown when the expiry is missing, invalid or past', () => {
    expect(ring.offerDeadlineMs({ ride_id: 'r', countdown_seconds: 30 }, 25, NOW)).toBe(25_000);
    expect(ring.offerDeadlineMs({ ride_id: 'r', offer_expires_at: 'garbage' }, 25, NOW)).toBe(25_000);
    const past = new Date(NOW - 1_000).toISOString();
    expect(ring.offerDeadlineMs({ ride_id: 'r', offer_expires_at: past }, 25, NOW)).toBe(25_000);
  });

  it('then the payload countdown, then 15 s', () => {
    expect(ring.offerDeadlineMs({ ride_id: 'r', countdown_seconds: 30 }, 0, NOW)).toBe(30_000);
    expect(ring.offerDeadlineMs({ ride_id: 'r' }, undefined, NOW)).toBe(15_000);
  });

  it('clamps to 1..60 s', () => {
    expect(ring.offerDeadlineMs({ ride_id: 'r' }, 600, NOW)).toBe(60_000);
    const soon = new Date(NOW + 200).toISOString();
    expect(ring.offerDeadlineMs({ ride_id: 'r', offer_expires_at: soon }, 0, NOW)).toBe(1_000);
  });
});
