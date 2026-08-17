/**
 * Unit tests for lib/androidAuto/carFixChannel.ts — the module-scope position
 * cache the car surface, the SOS payload and both location tasks share.
 *
 * The behaviour that matters here is not the arithmetic; it is the ordering
 * rules. A seed must never beat a live fix, a subscriber that throws must not
 * silence the others, and the AsyncStorage write must stay throttled by
 * DISTANCE so a moving car refreshes it and a parked one does not.
 */
import {
  _resetCarFixChannel,
  adoptCarFix,
  carFixAgeMs,
  getLastCarFix,
  LAST_LOCATION_KEY,
  metresBetween,
  persistFix,
  publishCarFix,
  seedCarFix,
  subscribeCarFix,
} from '../carFixChannel';

// jest.setup.js mocks this module globally but WITHOUT a `default` export, and
// carFixChannel reads `.default` (the real package's shape). Re-mock locally so
// the write is observable instead of silently swallowed by persistFix's catch.
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: { setItem: jest.fn(() => Promise.resolve()) },
}));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const setItem = require('@react-native-async-storage/async-storage').default
  .setItem as jest.Mock;

const SASKATOON = { latitude: 52.1332, longitude: -106.67, heading: null };
const move = (m: number) => ({ ...SASKATOON, latitude: SASKATOON.latitude + m / 111_320 });

beforeEach(() => {
  _resetCarFixChannel();
  setItem.mockClear();
});

describe('metresBetween', () => {
  it('measures a short northward hop to within a metre', () => {
    expect(metresBetween(SASKATOON, move(100))).toBeCloseTo(100, 0);
  });

  it('is zero for the same point', () => {
    expect(metresBetween(SASKATOON, SASKATOON)).toBe(0);
  });
});

describe('seed vs live ordering', () => {
  it('a seed fills an empty cache', () => {
    expect(seedCarFix(SASKATOON)).toEqual(SASKATOON);
    expect(getLastCarFix()).toEqual(SASKATOON);
  });

  it('a seed NEVER displaces a fix that already landed', () => {
    const live = move(500);
    adoptCarFix(live);
    expect(seedCarFix(SASKATOON)).toEqual(live);
    expect(getLastCarFix()).toEqual(live);
  });

  it('a seed does not reset the age clock', () => {
    // Stamping a seed "now" would tell the staleness watchdog everything is
    // fine when the only thing we hold is an hours-old login position.
    seedCarFix(SASKATOON);
    expect(carFixAgeMs()).toBe(Infinity);
  });

  it('a real fix does start the age clock', () => {
    adoptCarFix(SASKATOON);
    expect(carFixAgeMs()).toBeLessThan(1_000);
  });
});

describe('subscribers', () => {
  it('publishCarFix notifies every listener', () => {
    const a = jest.fn();
    const b = jest.fn();
    subscribeCarFix(a);
    subscribeCarFix(b);
    publishCarFix(SASKATOON);
    expect(a).toHaveBeenCalledWith(SASKATOON);
    expect(b).toHaveBeenCalledWith(SASKATOON);
  });

  it('one listener throwing does not stop the rest', () => {
    const good = jest.fn();
    subscribeCarFix(() => {
      throw new Error('surface torn down mid-publish');
    });
    subscribeCarFix(good);
    expect(() => publishCarFix(SASKATOON)).not.toThrow();
    expect(good).toHaveBeenCalledTimes(1);
  });

  it('unsubscribing stops delivery', () => {
    const seen = jest.fn();
    const off = subscribeCarFix(seen);
    off();
    publishCarFix(SASKATOON);
    expect(seen).not.toHaveBeenCalled();
  });

  it('adoptCarFix does NOT notify — it is for sources already holding the value', () => {
    const seen = jest.fn();
    subscribeCarFix(seen);
    adoptCarFix(SASKATOON);
    expect(seen).not.toHaveBeenCalled();
  });
});

describe('AsyncStorage throttle', () => {
  it('writes the first fix immediately', () => {
    persistFix(SASKATOON);
    expect(setItem).toHaveBeenCalledTimes(1);
    const [key, body] = setItem.mock.calls[0] as unknown as [string, string];
    expect(key).toBe(LAST_LOCATION_KEY);
    // Shape must stay lat/lng for login.tsx + useDriverDashboard, plus our `at`.
    expect(JSON.parse(body)).toMatchObject({ lat: SASKATOON.latitude, lng: SASKATOON.longitude });
    expect(typeof JSON.parse(body).at).toBe('number');
  });

  it('a parked car does not rewrite the cache', () => {
    persistFix(SASKATOON);
    persistFix(move(1));
    persistFix(move(2));
    expect(setItem).toHaveBeenCalledTimes(1);
  });

  it('a moving car trips the distance rule long before the timer', () => {
    persistFix(SASKATOON);
    persistFix(move(60)); // > CACHE_WRITE_DISTANCE_M
    expect(setItem).toHaveBeenCalledTimes(2);
  });

  it('force bypasses the throttle', () => {
    persistFix(SASKATOON);
    persistFix(move(1), true);
    expect(setItem).toHaveBeenCalledTimes(2);
  });
});
