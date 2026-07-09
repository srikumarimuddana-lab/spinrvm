/**
 * Background-location cadence control — trip-distance capture fix.
 *
 * Pins that updateBackgroundLocationCadence re-tunes the *running* background
 * task to the dense TRIP_CADENCE during a trip (and is a no-op when the task
 * isn't registered). This is what keeps a backgrounded trip well-sampled now
 * that POST /drivers/location-batch persists every point as a breadcrumb — the
 * foreground watchPositionAsync stops firing the moment the app backgrounds.
 */

const mockStartUpdates = jest.fn((..._args: any[]) => Promise.resolve());
let mockTaskRegistered = true;
let mockBgPermission = 'granted';
const mockAsyncStorage: Record<string, string> = {};

jest.mock('expo-location', () => ({
  startLocationUpdatesAsync: (...args: any[]) => mockStartUpdates(...args),
  stopLocationUpdatesAsync: jest.fn(() => Promise.resolve()),
  getBackgroundPermissionsAsync: jest.fn(() => Promise.resolve({ status: mockBgPermission })),
  requestBackgroundPermissionsAsync: jest.fn(() => Promise.resolve({ status: 'granted' })),
  getCurrentPositionAsync: jest.fn(() => Promise.resolve(null)),
  startGeofencingAsync: jest.fn(() => Promise.resolve()),
  stopGeofencingAsync: jest.fn(() => Promise.resolve()),
  Accuracy: { High: 6, Balanced: 4 },
  ActivityType: { AutomotiveNavigation: 3 },
  GeofencingEventType: { Enter: 1, Exit: 2 },
}));

jest.mock('expo-task-manager', () => ({
  defineTask: jest.fn(),
  isTaskRegisteredAsync: jest.fn(() => Promise.resolve(mockTaskRegistered)),
}));

jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn((key: string) => {
    if (key === 'bg_access_token') return Promise.resolve('access-token');
    if (key === 'bg_access_token_expires') return Promise.resolve(String(Date.now() + 120_000));
    return Promise.resolve(null);
  }),
  setItemAsync: jest.fn(() => Promise.resolve()),
  deleteItemAsync: jest.fn(() => Promise.resolve()),
}));

jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn((key: string) => Promise.resolve(mockAsyncStorage[key] ?? null)),
  setItem: jest.fn((key: string, value: string) => {
    mockAsyncStorage[key] = value;
    return Promise.resolve();
  }),
  removeItem: jest.fn((key: string) => {
    delete mockAsyncStorage[key];
    return Promise.resolve();
  }),
}));

jest.mock('@shared/config', () => ({ API_URL: 'https://example.test' }), { virtual: true });
jest.mock('@shared/config/spinr.config', () => ({
  __esModule: true,
  default: { backendUrl: 'https://example.test' },
}));
jest.mock('@shared/services/firebase', () => ({
  initFirebaseServices: jest.fn(() => Promise.resolve()),
  getAppCheckToken: jest.fn(() => Promise.resolve('app-check')),
}));

import {
  updateBackgroundLocationCadence,
  setBackgroundTripActive,
  handleBackgroundLocationTask,
  TRIP_CADENCE,
  IDLE_CADENCE,
} from '../backgroundLocation';
import * as SecureStore from 'expo-secure-store';
import AsyncStorage from '@react-native-async-storage/async-storage';

const makeLocation = (i: number) => ({
  coords: {
    latitude: 52.1 + i * 0.001,
    longitude: -106.6 - i * 0.001,
    speed: 12,
    heading: 90 + i,
    accuracy: 8,
  },
  timestamp: Date.UTC(2026, 6, 9, 12, 0, i),
});

describe('setBackgroundTripActive (killed-app recovery cadence)', () => {
  beforeEach(() => {
    (SecureStore.setItemAsync as jest.Mock).mockClear();
    (SecureStore.deleteItemAsync as jest.Mock).mockClear();
  });

  it('persists the trip flag when a ride is active', async () => {
    await setBackgroundTripActive(true);
    expect(SecureStore.setItemAsync).toHaveBeenCalledWith('spinr_bg_trip_active', 'true');
  });

  it('clears the trip flag when idle', async () => {
    await setBackgroundTripActive(false);
    expect(SecureStore.deleteItemAsync).toHaveBeenCalledWith('spinr_bg_trip_active');
  });
});

describe('background location offline queue', () => {
  beforeEach(() => {
    for (const key of Object.keys(mockAsyncStorage)) delete mockAsyncStorage[key];
    (global as any).fetch = jest.fn(() => Promise.resolve({ ok: true, status: 200 }));
    (AsyncStorage.getItem as jest.Mock).mockClear();
    (AsyncStorage.setItem as jest.Mock).mockClear();
    (AsyncStorage.removeItem as jest.Mock).mockClear();
  });

  it('persists background points when upload fails', async () => {
    (global as any).fetch = jest.fn(() => Promise.reject(new Error('offline')));

    await handleBackgroundLocationTask({ data: { locations: [makeLocation(0), makeLocation(1)] } as any });

    expect(AsyncStorage.setItem).toHaveBeenCalledWith(
      'spinr_bg_location_queue',
      expect.stringContaining('"tracking_phase":"background"')
    );
    const queued = JSON.parse(mockAsyncStorage.spinr_bg_location_queue);
    expect(queued).toHaveLength(2);
  });

  it('replays queued background points before new points and clears queue on success', async () => {
    mockAsyncStorage.spinr_bg_location_queue = JSON.stringify([
      {
        lat: 52,
        lng: -106,
        speed: 10,
        heading: 45,
        accuracy: 9,
        timestamp: '2026-07-09T11:59:00.000Z',
        tracking_phase: 'background',
      },
    ]);

    await handleBackgroundLocationTask({ data: { locations: [makeLocation(0)] } as any });

    expect(global.fetch).toHaveBeenCalledWith(
      'https://example.test/api/v1/drivers/location-batch',
      expect.objectContaining({
        body: expect.any(String),
      })
    );
    const body = JSON.parse((global.fetch as jest.Mock).mock.calls[0][1].body);
    expect(body.points).toHaveLength(2);
    expect(body.points[0].timestamp).toBe('2026-07-09T11:59:00.000Z');
    expect(AsyncStorage.removeItem).toHaveBeenCalledWith('spinr_bg_location_queue');
  });
});

describe('updateBackgroundLocationCadence', () => {
  beforeEach(() => {
    mockStartUpdates.mockClear();
    mockTaskRegistered = true;
    mockBgPermission = 'granted';
  });

  it('re-tunes the running task to dense TRIP_CADENCE', async () => {
    await updateBackgroundLocationCadence(TRIP_CADENCE);
    expect(mockStartUpdates).toHaveBeenCalledTimes(1);
    const opts = mockStartUpdates.mock.calls[0][1];
    expect(opts.timeInterval).toBe(TRIP_CADENCE.timeInterval); // 4000ms
    expect(opts.distanceInterval).toBe(TRIP_CADENCE.distanceInterval); // 10m
    expect(opts.accuracy).toBe(TRIP_CADENCE.accuracy); // High
  });

  it('relaxes back to coarse IDLE_CADENCE', async () => {
    await updateBackgroundLocationCadence(IDLE_CADENCE);
    const opts = mockStartUpdates.mock.calls[0][1];
    expect(opts.timeInterval).toBe(IDLE_CADENCE.timeInterval); // 30000ms
    expect(opts.distanceInterval).toBe(IDLE_CADENCE.distanceInterval); // 50m
  });

  it('is a no-op when the background task is not registered', async () => {
    mockTaskRegistered = false;
    await updateBackgroundLocationCadence(TRIP_CADENCE);
    expect(mockStartUpdates).not.toHaveBeenCalled();
  });

  it('is a no-op when background permission was revoked', async () => {
    mockBgPermission = 'denied';
    await updateBackgroundLocationCadence(TRIP_CADENCE);
    expect(mockStartUpdates).not.toHaveBeenCalled();
  });

  it('TRIP_CADENCE samples denser than IDLE_CADENCE', () => {
    expect(TRIP_CADENCE.timeInterval!).toBeLessThan(IDLE_CADENCE.timeInterval!);
    expect(TRIP_CADENCE.distanceInterval!).toBeLessThan(IDLE_CADENCE.distanceInterval!);
  });
});
