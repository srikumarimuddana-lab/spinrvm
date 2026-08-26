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
  bearingBetween,
  CARRIED_HEADING_MAX_AGE_MS,
  MAX_COURSE_BASELINE_AGE_MS,
  MIN_COURSE_MOVE_M,
  resolveHeading,
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

describe('heading resolution', () => {
  const at = (heading: number | null) => ({ ...SASKATOON, heading });
  const FRESH = 0;
  const STALE = CARRIED_HEADING_MAX_AGE_MS + 1;
  /** A point `m` metres due north — so the true course is 0°. */
  const north = (m: number, heading: number | null = null) => ({ ...move(m), heading });

  it('keeps a fix that already has a GPS course', () => {
    const r = resolveHeading(at(271), at(90), FRESH);
    expect(r.fix.heading).toBe(271);
    expect(r.source).toBe('gps');
  });

  it('derives the course from movement when the fix has none', () => {
    // THE fix for the head unit: one-shot getCurrentPositionAsync carries no
    // course, and on Android Auto it is the dominant path — but the direction
    // between two fixes is always available and always current.
    const r = resolveHeading(north(50), SASKATOON, FRESH, FRESH);
    expect(r.source).toBe('derived');
    expect(r.fix.heading).toBeCloseTo(0, 0);
  });

  it('refuses to derive across a STALE baseline', () => {
    // A seed can be a login position from an hour ago, and a resumed process
    // holds the fix it was suspended on. The direction across either is the
    // journey's, not the car's — and it would then be trusted as freshly
    // established. Better to admit we have no course.
    const r = resolveHeading(north(500), SASKATOON, Infinity, MAX_COURSE_BASELINE_AGE_MS + 1);
    expect(r.source).toBe('none');
    expect(r.fix.heading).toBeNull();
  });

  it('a seeded previous fix is never a baseline (its age clock is Infinity)', () => {
    seedCarFix(SASKATOON);
    const merged = adoptCarFix(north(500));
    expect(merged.heading).toBeNull();
  });

  it('a derived course beats a stale carried one', () => {
    const r = resolveHeading(north(50), at(271), STALE, FRESH);
    expect(r.source).toBe('derived');
    expect(r.fix.heading).toBeCloseTo(0, 0);
  });

  it('ignores sub-threshold movement — noise is not a course', () => {
    const r = resolveHeading(north(MIN_COURSE_MOVE_M - 2), at(271), FRESH);
    expect(r.source).toBe('carried');
    expect(r.fix.heading).toBe(271);
  });

  it('carries a RECENT bearing onto a course-less fix that has not moved', () => {
    const r = resolveHeading(at(null), at(271), FRESH);
    expect(r.source).toBe('carried');
    expect(r.fix.heading).toBe(271);
  });

  it('EXPIRES a carried bearing rather than pointing the wrong way all trip', () => {
    // The regression this test exists for: with the watcher starved, one early
    // bearing was republished on every watchdog fix forever, so the icon sat
    // pointing north while the driver drove west. Past the age limit the honest
    // answer is null — the marker then shows its own travel direction.
    const r = resolveHeading(at(null), at(271), STALE);
    expect(r.source).toBe('none');
    expect(r.fix.heading).toBeNull();
  });

  it('treats expo’s -1 "unknown" as no bearing, in both directions', () => {
    expect(resolveHeading(at(-1), at(271), FRESH).fix.heading).toBe(271);
    expect(resolveHeading(at(null), at(-1), FRESH).fix.heading).toBeNull();
  });

  it('has nothing to carry or derive on the very first fix', () => {
    const r = resolveHeading(at(null), null, Infinity);
    expect(r.source).toBe('none');
    expect(r.fix.heading).toBeNull();
  });

  it('never carries POSITION forward — only the bearing', () => {
    const moved = { ...move(500), heading: null };
    const { fix } = resolveHeading(moved, at(271), FRESH);
    expect(fix.latitude).toBe(moved.latitude);
    expect(fix.longitude).toBe(moved.longitude);
  });

  it('adoptCarFix returns the merged fix so callers render the true bearing', () => {
    adoptCarFix(at(271));
    const merged = adoptCarFix(at(null));
    expect(merged.heading).toBe(271);
    expect(getLastCarFix()?.heading).toBe(271);
  });

  it('a real turn replaces the carried bearing', () => {
    adoptCarFix(at(271));
    expect(adoptCarFix(at(15)).heading).toBe(15);
  });

  it('a carried bearing does not renew its own clock on every watchdog fix', () => {
    // Each course-less fix must AGE the bearing, not restamp it. Restamping is
    // how "carry the last bearing" became "carry the first bearing forever".
    jest.useFakeTimers();
    try {
      jest.setSystemTime(new Date('2026-08-21T00:00:00Z'));
      adoptCarFix(at(271));
      // Watchdog ticks: course-less, stationary, every 3s past the limit.
      for (let t = 3; t <= CARRIED_HEADING_MAX_AGE_MS / 1000 + 3; t += 3) {
        jest.setSystemTime(new Date(`2026-08-21T00:00:${String(t).padStart(2, '0')}Z`));
        adoptCarFix(at(null));
      }
      expect(getLastCarFix()?.heading).toBeNull();
    } finally {
      jest.useRealTimers();
    }
  });

  it('publishCarFix hands subscribers the merged fix, not the raw one', () => {
    adoptCarFix(at(271));
    const seen: (number | null)[] = [];
    subscribeCarFix((f) => seen.push(f.heading));
    publishCarFix(at(null));
    expect(seen).toEqual([271]);
  });
});

describe('bearingBetween', () => {
  it('reads the four cardinals', () => {
    const from = SASKATOON;
    const dLat = 1 / 111_320;
    const dLng = 1 / (111_320 * Math.cos((from.latitude * Math.PI) / 180));
    const p = (dy: number, dx: number) => ({
      latitude: from.latitude + dy * dLat * 200,
      longitude: from.longitude + dx * dLng * 200,
      heading: null,
    });
    expect(bearingBetween(from, p(1, 0))).toBeCloseTo(0, 0);
    expect(bearingBetween(from, p(0, 1))).toBeCloseTo(90, 0);
    expect(bearingBetween(from, p(-1, 0))).toBeCloseTo(180, 0);
    expect(bearingBetween(from, p(0, -1))).toBeCloseTo(270, 0);
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
