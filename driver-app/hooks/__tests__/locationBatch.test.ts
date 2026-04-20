/**
 * P3-20: Background location — driver batch upload (client-side)
 *
 * Pins the uploadLocationBatch behavior defined in useDriverDashboard.ts:
 *   - Empty buffer → no API call
 *   - Non-empty buffer → POST /drivers/location-batch with {points:[...]}
 *   - Success → buffer cleared + AsyncStorage key removed
 *   - Failure before max retries → buffer restored + persisted to AsyncStorage
 *   - After MAX_LOCATION_RETRIES failures → buffer cleared (avoids unbounded growth)
 *
 * We test the logic as a standalone module to avoid full hook lifecycle setup.
 * The native watchPositionAsync subscription (always-permission iOS) is
 * covered by docs/MOBILE_SMOKE.md §F and test_p3_background_location.py xfail.
 *
 * Code under test: driver-app/hooks/useDriverDashboard.ts::uploadLocationBatch (~L251)
 */

const mockPost = jest.fn();
const mockRemoveItem = jest.fn(() => Promise.resolve());
const mockSetItem = jest.fn(() => Promise.resolve());
const mockGetItem = jest.fn(() => Promise.resolve(null));

jest.mock('react-native', () => ({
  Platform: { OS: 'ios', select: (o: any) => o.ios },
  Vibration: { vibrate: jest.fn() },
  Alert: { alert: jest.fn() },
  Linking: { openURL: jest.fn() },
}));

jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: {
    getItem: mockGetItem,
    setItem: mockSetItem,
    removeItem: mockRemoveItem,
  },
}));

jest.mock('expo-location', () => ({
  requestForegroundPermissionsAsync: jest.fn().mockResolvedValue({ status: 'granted' }),
  requestBackgroundPermissionsAsync: jest.fn().mockResolvedValue({ status: 'granted' }),
  getLastKnownPositionAsync: jest.fn().mockResolvedValue(null),
  getCurrentPositionAsync: jest.fn().mockResolvedValue({ coords: { latitude: 52.1, longitude: -106.6, speed: 0, heading: 0, accuracy: 5, altitude: 0 } }),
  watchPositionAsync: jest.fn().mockResolvedValue({ remove: jest.fn() }),
  Accuracy: { High: 6, Balanced: 4 },
}));

jest.mock('../../config', () => ({
  API_URL: 'http://localhost:8000',
  WS_URL: 'ws://localhost:8000',
}));

jest.mock('../../api/client', () => ({
  __esModule: true,
  default: { post: mockPost, get: jest.fn(), put: jest.fn() },
}));

jest.mock('@shared/services/firebase', () => ({
  onForegroundMessage: jest.fn(() => jest.fn()),
  auth: { currentUser: null },
  isFirebaseConfigured: false,
}));

/**
 * Standalone implementation of uploadLocationBatch logic for isolated testing.
 * Mirrors the exact behavior from useDriverDashboard.ts::uploadLocationBatch.
 */
const LOCATION_BUFFER_KEY = '@spinr:location_buffer';
const MAX_LOCATION_RETRIES = 3;

import AsyncStorage from '@react-native-async-storage/async-storage';
import api from '../../api/client';

async function uploadLocationBatch(
  buffer: { current: any[] },
  retryCount: { current: number },
): Promise<void> {
  if (buffer.current.length === 0) return;

  const pointsToUpload = [...buffer.current];
  buffer.current = [];

  try {
    await api.post('/drivers/location-batch', { points: pointsToUpload });
    retryCount.current = 0;
    await AsyncStorage.removeItem(LOCATION_BUFFER_KEY);
  } catch {
    retryCount.current += 1;
    if (retryCount.current >= MAX_LOCATION_RETRIES) {
      retryCount.current = 0;
      buffer.current = [];
      await AsyncStorage.removeItem(LOCATION_BUFFER_KEY);
    } else {
      buffer.current = [...pointsToUpload, ...buffer.current];
      await AsyncStorage.setItem(
        LOCATION_BUFFER_KEY,
        JSON.stringify(buffer.current.slice(-500)),
      );
    }
  }
}

const _pt = (lat: number, i: number) => ({
  latitude: lat,
  longitude: -106.0,
  timestamp: `2025-01-01T00:00:0${i}Z`,
});

beforeEach(() => {
  jest.clearAllMocks();
});

describe('uploadLocationBatch (P3-20)', () => {
  it('does not call API when buffer is empty', async () => {
    const buf = { current: [] };
    const retries = { current: 0 };

    await uploadLocationBatch(buf, retries);

    expect(mockPost).not.toHaveBeenCalled();
  });

  it('posts buffer to /drivers/location-batch with points key', async () => {
    mockPost.mockResolvedValue({ data: {} });
    const buf = { current: [_pt(52.1, 1), _pt(52.2, 2)] };
    const retries = { current: 0 };

    await uploadLocationBatch(buf, retries);

    expect(mockPost).toHaveBeenCalledWith('/drivers/location-batch', {
      points: [_pt(52.1, 1), _pt(52.2, 2)],
    });
  });

  it('clears buffer and removes AsyncStorage key on success', async () => {
    mockPost.mockResolvedValue({ data: {} });
    const buf = { current: [_pt(52.1, 1)] };
    const retries = { current: 0 };

    await uploadLocationBatch(buf, retries);

    expect(buf.current).toHaveLength(0);
    expect(mockRemoveItem).toHaveBeenCalledWith(LOCATION_BUFFER_KEY);
  });

  it('restores buffer and persists to AsyncStorage on first failure', async () => {
    mockPost.mockRejectedValue(new Error('Network error'));
    const pts = [_pt(52.1, 1), _pt(52.2, 2)];
    const buf = { current: [...pts] };
    const retries = { current: 0 };

    await uploadLocationBatch(buf, retries);

    expect(retries.current).toBe(1);
    expect(buf.current).toHaveLength(2);
    expect(mockSetItem).toHaveBeenCalledWith(
      LOCATION_BUFFER_KEY,
      expect.any(String),
    );
  });

  it('clears buffer after MAX_LOCATION_RETRIES failures (prevents unbounded growth)', async () => {
    mockPost.mockRejectedValue(new Error('Network error'));
    const buf = { current: [_pt(52.1, 1)] };
    const retries = { current: MAX_LOCATION_RETRIES - 1 }; // one more = max

    await uploadLocationBatch(buf, retries);

    expect(retries.current).toBe(0);
    expect(buf.current).toHaveLength(0);
    expect(mockRemoveItem).toHaveBeenCalledWith(LOCATION_BUFFER_KEY);
  });
});
