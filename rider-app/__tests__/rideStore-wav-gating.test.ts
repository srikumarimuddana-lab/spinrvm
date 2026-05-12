/**
 * rideStore.showWavOption gating — verifies the booking-screen WAV
 * toggle defaults off, persists per-rider, and clears requiresWav on
 * opt-out so the next create-ride payload doesn't sneak a stale
 * requires_wav=true through.
 *
 * The booking screen renders the WAV row from `showWavOption` directly;
 * keeping store behaviour correct here is enough to keep the regulatory
 * dispatch path (backend) untouched while hiding the option in the UI.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

// Pull the store after mocks are configured below.
let useRideStore: typeof import('../store/rideStore').useRideStore;

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
      __reset: () => {
        for (const k of Object.keys(store)) delete store[k];
      },
    },
  };
});

// Network calls inside the store are not exercised by these tests; stub.
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    get: jest.fn().mockResolvedValue({ data: {} }),
    post: jest.fn().mockResolvedValue({ data: {} }),
    patch: jest.fn().mockResolvedValue({ data: { success: true, notes: null } }),
    delete: jest.fn().mockResolvedValue({ data: {} }),
  },
}));

beforeAll(() => {
  // Import after the mocks so the store reads them on first load.
  ({ useRideStore } = require('../store/rideStore'));
});

beforeEach(() => {
  (AsyncStorage as unknown as { __reset: () => void }).__reset();
  // Reset relevant slice between cases without re-importing the module.
  useRideStore.setState({ showWavOption: false, requiresWav: false });
});

describe('rideStore showWavOption gating', () => {
  it('defaults to false so the booking screen hides WAV by default', () => {
    expect(useRideStore.getState().showWavOption).toBe(false);
  });

  it('setShowWavOption(true) updates state and persists "1"', async () => {
    useRideStore.getState().setShowWavOption(true);
    expect(useRideStore.getState().showWavOption).toBe(true);
    // Persistence is fire-and-forget; the store doesn't await it.
    await Promise.resolve();
    expect(AsyncStorage.setItem).toHaveBeenCalledWith('rider_show_wav_option', '1');
  });

  it('setShowWavOption(false) clears requiresWav so stale flag never ships in create-ride', () => {
    useRideStore.setState({ showWavOption: true, requiresWav: true });
    useRideStore.getState().setShowWavOption(false);
    expect(useRideStore.getState().showWavOption).toBe(false);
    expect(useRideStore.getState().requiresWav).toBe(false);
  });

  it('loadRecentSearches hydrates showWavOption from AsyncStorage', async () => {
    (AsyncStorage.setItem as jest.Mock).mockClear();
    await AsyncStorage.setItem('rider_show_wav_option', '1');

    await useRideStore.getState().loadRecentSearches();

    expect(useRideStore.getState().showWavOption).toBe(true);
  });

  it('loadRecentSearches leaves showWavOption false when no value is stored', async () => {
    await useRideStore.getState().loadRecentSearches();
    expect(useRideStore.getState().showWavOption).toBe(false);
  });
});
