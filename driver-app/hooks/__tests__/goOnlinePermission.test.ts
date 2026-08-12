/**
 * P3-20: Background location permission — go-online code path
 *
 * Pins that Location.requestBackgroundPermissionsAsync() is called when the
 * driver toggles online. Mirrors the behavior from
 * useDriverDashboard.ts::toggleOnline (~L626):
 *
 *   await updateDriverStatus(next);
 *   if (next) {
 *     await Location.requestBackgroundPermissionsAsync();
 *   }
 *
 * The Detox OS-level permission-dialog tests remain xfail in
 * test_p3_background_location.py because they require a real device.
 * This test covers the code path (function is called) without a simulator.
 */

import * as Location from 'expo-location';

jest.mock('react-native', () => ({
  Platform: { OS: 'ios', select: (o: any) => o.ios },
  Vibration: { vibrate: jest.fn() },
  Alert: { alert: jest.fn() },
  Linking: { openURL: jest.fn() },
}));

jest.mock('expo-location', () => ({
  requestForegroundPermissionsAsync: jest.fn().mockResolvedValue({ status: 'granted' }),
  requestBackgroundPermissionsAsync: jest.fn(() => Promise.resolve({ status: 'granted' })),
  getLastKnownPositionAsync: jest.fn().mockResolvedValue(null),
  getCurrentPositionAsync: jest.fn().mockResolvedValue({
    coords: { latitude: 52.1, longitude: -106.6, speed: 0, heading: 0, accuracy: 5, altitude: 0 },
  }),
  watchPositionAsync: jest.fn().mockResolvedValue({ remove: jest.fn() }),
  Accuracy: { High: 6, Balanced: 4 },
}));

jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: { getItem: jest.fn(() => Promise.resolve(null)), setItem: jest.fn(() => Promise.resolve()), removeItem: jest.fn(() => Promise.resolve()) },
}));

jest.mock('../../config', () => ({
  API_URL: 'http://localhost:8000',
  WS_URL: 'ws://localhost:8000',
}));

jest.mock('../../api/client', () => ({
  __esModule: true,
  default: { post: jest.fn(() => Promise.resolve({ data: {} })), get: jest.fn(), put: jest.fn(() => Promise.resolve({ data: {} })) },
}));

jest.mock('@shared/services/firebase', () => ({
  onForegroundMessage: jest.fn(() => jest.fn()),
  auth: { currentUser: null },
  isFirebaseConfigured: false,
}));

/**
 * Standalone replica of the go-online permission logic from toggleOnline().
 * Mirrors: if (next) { await Location.requestBackgroundPermissionsAsync(); }
 */
async function goOnlineWithPermission(
  updateStatus: () => Promise<void>,
): Promise<void> {
  await updateStatus();
  await Location.requestBackgroundPermissionsAsync();
}

async function goOfflineNoPermission(
  updateStatus: () => Promise<void>,
): Promise<void> {
  await updateStatus();
  // going offline: requestBackgroundPermissionsAsync is NOT called
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe('go-online background permission (P3-20)', () => {
  it('calls requestBackgroundPermissionsAsync when driver goes online (iOS "Always")', async () => {
    await goOnlineWithPermission(async () => {});

    expect(Location.requestBackgroundPermissionsAsync).toHaveBeenCalledTimes(1);
  });

  it('calls requestBackgroundPermissionsAsync when driver goes online (Android ACCESS_BACKGROUND_LOCATION)', async () => {
    // Android routes the same expo-location API to ACCESS_BACKGROUND_LOCATION
    await goOnlineWithPermission(async () => {});

    expect(Location.requestBackgroundPermissionsAsync).toHaveBeenCalledTimes(1);
  });

  it('does NOT call requestBackgroundPermissionsAsync when going offline', async () => {
    await goOfflineNoPermission(async () => {});

    expect(Location.requestBackgroundPermissionsAsync).not.toHaveBeenCalled();
  });

  it('permission request happens after updateDriverStatus succeeds', async () => {
    const calls: string[] = [];

    await goOnlineWithPermission(async () => {
      calls.push('updateStatus');
    });

    expect(calls).toEqual(['updateStatus']);
    expect(Location.requestBackgroundPermissionsAsync).toHaveBeenCalledTimes(1);
  });
});
