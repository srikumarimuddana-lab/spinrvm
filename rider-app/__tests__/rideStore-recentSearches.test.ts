/**
 * Recent-searches hygiene (v2).
 *
 * Incident: a backend defect once wrote an address+coordinate pair whose
 * coordinate did not match the address ("4500 Gordon Rd" labeled a pin on
 * Glide Crescent). v1 recents stored such pairs unversioned, never expiring,
 * never validated, deduped byte-exactly, and surviving logout — so one bad
 * write replayed on every tap, forever. These tests pin the v2 contract:
 *
 *  - the legacy 'recent_searches' key is ABANDONED (poisoned pairs purged)
 *  - entries carry saved_at and expire after 90 days
 *  - malformed / (0,0) / out-of-range entries are dropped on load
 *  - dedupe is by normalized address, not byte-exact match
 *  - logout clears recents (per-person data, not per-device)
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

let useRideStore: typeof import('../store/rideStore').useRideStore;
let logoutCallbacks: (() => void)[] = [];

jest.mock('@react-native-async-storage/async-storage', () => {
  const store: Record<string, string> = {};
  return {
    __esModule: true,
    default: {
      getItem: jest.fn((k: string) => Promise.resolve(store[k] ?? null)),
      setItem: jest.fn((k: string, v: string) => {
        store[k] = v;
        return Promise.resolve();
      }),
      removeItem: jest.fn((k: string) => {
        delete store[k];
        return Promise.resolve();
      }),
      __raw: store,
    },
  };
});

jest.mock('@shared/store/authStore', () => ({
  __esModule: true,
  useAuthStore: { getState: () => ({ user: null }), subscribe: jest.fn() },
  registerLogoutCallback: jest.fn((cb: () => void) => {
    logoutCallbacks.push(cb);
  }),
}));

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    get: jest.fn().mockResolvedValue({ data: {} }),
    post: jest.fn().mockResolvedValue({ data: {} }),
    patch: jest.fn().mockResolvedValue({ data: {} }),
    delete: jest.fn().mockResolvedValue({ data: {} }),
  },
}));

const raw = (): Record<string, string> => (AsyncStorage as any).__raw;

beforeAll(() => {
  ({ useRideStore } = require('../store/rideStore'));
});

const DAY = 24 * 60 * 60 * 1000;
const GOOD = { address: '4500 Gordon Rd, Regina, SK S4S 6H7', lat: 50.4079, lng: -104.6501 };

beforeEach(() => {
  for (const k of Object.keys(raw())) delete raw()[k];
  useRideStore.setState({ recentSearches: [] });
});

describe('legacy key abandonment', () => {
  it('never reads the v1 key — a poisoned legacy pair cannot replay', async () => {
    raw()['recent_searches'] = JSON.stringify([
      { address: '4500 Gordon Rd, Regina, SK S4S 6H7', lat: 50.4966, lng: -104.6401 }, // Glide Cres pin
    ]);
    await useRideStore.getState().loadRecentSearches();
    expect(useRideStore.getState().recentSearches).toEqual([]);
  });

  it('writes to the v2 key only', () => {
    useRideStore.getState().addRecentSearch(GOOD);
    expect(raw()['recent_searches_v2']).toBeDefined();
    expect(raw()['recent_searches']).toBeUndefined();
  });
});

describe('expiry and validation on load', () => {
  it('drops entries older than 90 days and keeps fresh ones', async () => {
    raw()['recent_searches_v2'] = JSON.stringify([
      { ...GOOD, saved_at: Date.now() - 1 * DAY },
      { address: 'Old Place, Regina', lat: 50.45, lng: -104.6, saved_at: Date.now() - 120 * DAY },
    ]);
    await useRideStore.getState().loadRecentSearches();
    const loaded = useRideStore.getState().recentSearches;
    expect(loaded).toHaveLength(1);
    expect(loaded[0].address).toBe(GOOD.address);
  });

  it('treats a missing saved_at as expired (pre-timestamp data cannot linger)', async () => {
    raw()['recent_searches_v2'] = JSON.stringify([{ ...GOOD }]);
    await useRideStore.getState().loadRecentSearches();
    expect(useRideStore.getState().recentSearches).toEqual([]);
  });

  it('drops malformed, (0,0), and out-of-range entries', async () => {
    const now = Date.now();
    raw()['recent_searches_v2'] = JSON.stringify([
      { address: '', lat: 50.4, lng: -104.6, saved_at: now },
      { address: 'Null Island', lat: 0, lng: 0, saved_at: now },
      { address: 'Bad lat', lat: 91, lng: -104.6, saved_at: now },
      { address: 'NaN', lat: NaN, lng: -104.6, saved_at: now },
      'not-an-object',
      { ...GOOD, saved_at: now },
    ]);
    await useRideStore.getState().loadRecentSearches();
    const loaded = useRideStore.getState().recentSearches;
    expect(loaded).toHaveLength(1);
    expect(loaded[0].address).toBe(GOOD.address);
  });
});

describe('normalized dedupe', () => {
  it('collapses case/whitespace variants of one address to the newest entry', () => {
    const store = useRideStore.getState();
    store.addRecentSearch({ address: '4500 gordon rd,  Regina, SK S4S 6H7 ', lat: 1, lng: 1 });
    store.addRecentSearch(GOOD);
    const recents = useRideStore.getState().recentSearches;
    expect(recents).toHaveLength(1);
    // The newest write wins — a corrected pair replaces a stale one.
    expect(recents[0].lat).toBe(GOOD.lat);
    expect(recents[0].saved_at).toBeGreaterThan(0);
  });

  it('caps the list at 10', () => {
    const store = useRideStore.getState();
    for (let i = 0; i < 12; i++) {
      store.addRecentSearch({ address: `Place ${i}, Regina`, lat: 50 + i * 0.01, lng: -104.6 });
    }
    expect(useRideStore.getState().recentSearches).toHaveLength(10);
    expect(useRideStore.getState().recentSearches[0].address).toBe('Place 11, Regina');
  });

  it('preserves place_id so a tap can re-resolve fresh coordinates', () => {
    useRideStore.getState().addRecentSearch({ ...GOOD, place_id: 'ChIJwalmart' });
    expect(useRideStore.getState().recentSearches[0].place_id).toBe('ChIJwalmart');
    expect(JSON.parse(raw()['recent_searches_v2'])[0].place_id).toBe('ChIJwalmart');
  });
});

describe('logout', () => {
  it('clears recents from state and storage — the next account inherits nothing', () => {
    useRideStore.getState().addRecentSearch(GOOD);
    expect(raw()['recent_searches_v2']).toBeDefined();
    for (const cb of logoutCallbacks) cb();
    expect(useRideStore.getState().recentSearches).toEqual([]);
    expect(raw()['recent_searches_v2']).toBeUndefined();
  });
});
