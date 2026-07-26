/**
 * aiChatStore.deviceLocation — the chat's per-message position source.
 * Pins: a FRESH GPS fix (getCurrentPositionAsync, Balanced) is preferred
 * over the OS's last-known cache, the cache is only a fallback on
 * timeout/failure, and no permission means null (never a prompt).
 *
 * Incident regression: the old getLastKnownPositionAsync-only source
 * returned a drifting coarse fix that moved the assistant's pickup pin
 * between messages.
 */

jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: { getItem: jest.fn(), setItem: jest.fn(), removeItem: jest.fn() },
}));
jest.mock('@shared/api/client', () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() } }));
jest.mock('../utils/aiChat', () => ({ __esModule: true, streamChat: jest.fn() }));
jest.mock('expo-location', () => ({
  __esModule: true,
  Accuracy: { Balanced: 3 },
  getForegroundPermissionsAsync: jest.fn(),
  getCurrentPositionAsync: jest.fn(),
  getLastKnownPositionAsync: jest.fn(),
}));

import * as Location from 'expo-location';
import { deviceLocation } from '../store/aiChatStore';

const mockLocation = Location as jest.Mocked<typeof Location>;

const FIX = (lat: number, lng: number) =>
  ({ coords: { latitude: lat, longitude: lng } }) as Awaited<
    ReturnType<typeof Location.getCurrentPositionAsync>
  >;

beforeEach(() => {
  jest.clearAllMocks();
  mockLocation.getForegroundPermissionsAsync.mockResolvedValue({ granted: true } as never);
});

describe('deviceLocation', () => {
  it('prefers a fresh Balanced-accuracy fix over the cache', async () => {
    mockLocation.getCurrentPositionAsync.mockResolvedValue(FIX(50.4079, -104.6501));
    mockLocation.getLastKnownPositionAsync.mockResolvedValue(FIX(1, 1));

    await expect(deviceLocation()).resolves.toEqual({ lat: 50.4079, lng: -104.6501 });
    expect(mockLocation.getCurrentPositionAsync).toHaveBeenCalledWith({ accuracy: 3 });
    expect(mockLocation.getLastKnownPositionAsync).not.toHaveBeenCalled();
  });

  it('falls back to the cached fix when the fresh read fails', async () => {
    mockLocation.getCurrentPositionAsync.mockRejectedValue(new Error('gps off'));
    mockLocation.getLastKnownPositionAsync.mockResolvedValue(FIX(50.45, -104.62));

    await expect(deviceLocation()).resolves.toEqual({ lat: 50.45, lng: -104.62 });
    expect(mockLocation.getLastKnownPositionAsync).toHaveBeenCalledWith({ maxAge: 5 * 60 * 1000 });
  });

  it('falls back to the cached fix when the fresh read times out', async () => {
    jest.useFakeTimers();
    try {
      mockLocation.getCurrentPositionAsync.mockReturnValue(new Promise(() => {}) as never);
      mockLocation.getLastKnownPositionAsync.mockResolvedValue(FIX(50.45, -104.62));

      const pending = deviceLocation();
      await jest.advanceTimersByTimeAsync(4001);
      await expect(pending).resolves.toEqual({ lat: 50.45, lng: -104.62 });
    } finally {
      jest.useRealTimers();
    }
  });

  it('returns null when both sources fail', async () => {
    mockLocation.getCurrentPositionAsync.mockRejectedValue(new Error('gps off'));
    mockLocation.getLastKnownPositionAsync.mockResolvedValue(null);

    await expect(deviceLocation()).resolves.toBeNull();
  });

  it('returns null without permission and never reads a position', async () => {
    mockLocation.getForegroundPermissionsAsync.mockResolvedValue({ granted: false } as never);

    await expect(deviceLocation()).resolves.toBeNull();
    expect(mockLocation.getCurrentPositionAsync).not.toHaveBeenCalled();
    expect(mockLocation.getLastKnownPositionAsync).not.toHaveBeenCalled();
  });
});
