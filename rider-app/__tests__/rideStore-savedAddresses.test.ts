/**
 * Saved addresses in the ride store (2026-09-25 fix):
 *  - logout clears savedAddresses (next account on the phone must not see
 *    the previous rider's Home)
 *  - a non-array GET body doesn't crash and doesn't leave an old list
 *  - a failed GET clears the list and flags savedAddressesLoadFailed
 *  - saving a Home replaces the local Home (server keeps one), by type
 *  - updateSavedAddress PATCHes and merges
 *  - deleteSavedAddress rethrows so the screen can tell the rider
 * Fixture values are synthetic.
 */
let useRideStore: typeof import('../store/rideStore').useRideStore;
const logoutCallbacks: (() => void)[] = [];

jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: {
    getItem: jest.fn(() => Promise.resolve(null)),
    setItem: jest.fn(() => Promise.resolve()),
    removeItem: jest.fn(() => Promise.resolve()),
  },
}));

jest.mock('@shared/store/authStore', () => ({
  __esModule: true,
  useAuthStore: { getState: () => ({ user: null }), subscribe: jest.fn() },
  registerLogoutCallback: jest.fn((cb: () => void) => {
    logoutCallbacks.push(cb);
  }),
}));

const mockGet = jest.fn();
const mockPost = jest.fn();
const mockPatch = jest.fn();
const mockDelete = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    get: (...a: any[]) => mockGet(...a),
    post: (...a: any[]) => mockPost(...a),
    patch: (...a: any[]) => mockPatch(...a),
    delete: (...a: any[]) => mockDelete(...a),
  },
  getApiErrorMessage: (_e: unknown, fallback: string) => fallback,
}));

const HOME = { id: 'h1', user_id: 'u1', name: 'Home', address: '1 Test St', lat: 50, lng: -100, icon: 'home' };
const GYM = { id: 'g1', user_id: 'u1', name: 'Gym', address: '2 Test St', lat: 50.1, lng: -100.1, icon: 'gym' };

beforeAll(() => {
  ({ useRideStore } = require('../store/rideStore'));
});

beforeEach(() => {
  jest.clearAllMocks();
  jest.spyOn(console, 'error').mockImplementation(() => {});
  useRideStore.setState({ savedAddresses: [], savedAddressesLoadFailed: false });
});

it('logout clears saved addresses', () => {
  useRideStore.setState({ savedAddresses: [HOME, GYM] as any, savedAddressesLoadFailed: true });
  for (const cb of logoutCallbacks) cb();
  expect(useRideStore.getState().savedAddresses).toEqual([]);
  expect(useRideStore.getState().savedAddressesLoadFailed).toBe(false);
});

it('a non-array response does not crash and does not keep an old list', async () => {
  useRideStore.setState({ savedAddresses: [HOME] as any });
  mockGet.mockResolvedValueOnce({ data: {} });
  await useRideStore.getState().fetchSavedAddresses();
  expect(useRideStore.getState().savedAddresses).toEqual([]);
  expect(useRideStore.getState().savedAddressesLoadFailed).toBe(true);
});

it('a failed fetch clears the list and flags the failure; a later success resets it', async () => {
  useRideStore.setState({ savedAddresses: [HOME] as any });
  mockGet.mockRejectedValueOnce(new Error('network'));
  await expect(useRideStore.getState().fetchSavedAddresses()).resolves.toEqual([]);
  expect(useRideStore.getState().savedAddresses).toEqual([]);
  expect(useRideStore.getState().savedAddressesLoadFailed).toBe(true);

  mockGet.mockResolvedValueOnce({ data: [GYM] });
  // Resolves with the stored list so callers can decide on fresh data.
  await expect(useRideStore.getState().fetchSavedAddresses()).resolves.toEqual([GYM]);
  expect(useRideStore.getState().savedAddresses).toEqual([GYM]);
  expect(useRideStore.getState().savedAddressesLoadFailed).toBe(false);
});

it('saving a second Home replaces the local Home (matched by type, not label)', async () => {
  useRideStore.setState({ savedAddresses: [HOME, GYM] as any });
  const newHome = { ...HOME, id: 'h2', name: 'My house', address: '3 Test St' };
  mockPost.mockResolvedValueOnce({ data: newHome });
  await useRideStore.getState().addSavedAddress({
    name: 'My house', address: '3 Test St', lat: 50, lng: -100, icon: 'home', place_id: 'pid',
  });
  expect(mockPost).toHaveBeenCalledWith('/addresses', expect.objectContaining({ place_id: 'pid' }));
  expect(useRideStore.getState().savedAddresses.map((a) => a.id)).toEqual(['g1', 'h2']);
});

it('a server in-place replace (same id) updates the entry where it is', async () => {
  useRideStore.setState({ savedAddresses: [HOME, GYM] as any });
  mockPost.mockResolvedValueOnce({ data: { ...HOME, address: '4 Test St' } });
  await useRideStore.getState().addSavedAddress({ name: 'Home', address: '4 Test St', lat: 50, lng: -100, icon: 'home' });
  const list = useRideStore.getState().savedAddresses;
  expect(list.map((a) => a.id)).toEqual(['h1', 'g1']);
  expect(list[0].address).toBe('4 Test St');
});

it('non-singleton places can repeat', async () => {
  useRideStore.setState({ savedAddresses: [GYM] as any });
  mockPost.mockResolvedValueOnce({ data: { ...GYM, id: 'g2' } });
  await useRideStore.getState().addSavedAddress({ name: 'Gym', address: '2 Test St', lat: 50.1, lng: -100.1, icon: 'gym' });
  expect(useRideStore.getState().savedAddresses.map((a) => a.id)).toEqual(['g1', 'g2']);
});

it('updateSavedAddress PATCHes and merges the returned row', async () => {
  useRideStore.setState({ savedAddresses: [HOME, GYM] as any });
  mockPatch.mockResolvedValueOnce({ data: { ...GYM, name: 'Downtown gym' } });
  await useRideStore.getState().updateSavedAddress('g1', { name: 'Downtown gym' });
  expect(mockPatch).toHaveBeenCalledWith('/addresses/g1', { name: 'Downtown gym' });
  expect(useRideStore.getState().savedAddresses[1].name).toBe('Downtown gym');
});

it('savedPlaceType: type wins over label; untyped rows fall back to an exact label', () => {
  const { savedPlaceType, isHomePlace, isWorkPlace } = require('../utils/savedPlaceIcon');
  expect(savedPlaceType({ name: 'My house', icon: 'home' })).toBe('home');
  expect(savedPlaceType({ name: 'Home', icon: 'gym' })).toBeNull();
  expect(savedPlaceType({ name: ' home ', icon: 'location' })).toBe('home');
  expect(savedPlaceType({ name: 'Home Depot', icon: 'location' })).toBeNull();
  expect(isWorkPlace({ name: 'Downtown Office', icon: 'work' })).toBe(true);
  expect(isHomePlace(null)).toBe(false);
});

it('deleteSavedAddress rethrows and keeps the entry when the server refuses', async () => {
  useRideStore.setState({ savedAddresses: [HOME] as any });
  mockDelete.mockRejectedValueOnce(new Error('500'));
  await expect(useRideStore.getState().deleteSavedAddress('h1')).rejects.toThrow('500');
  expect(useRideStore.getState().savedAddresses).toHaveLength(1);
});
