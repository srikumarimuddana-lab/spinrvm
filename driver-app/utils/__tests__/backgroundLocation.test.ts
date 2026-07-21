/* eslint-disable import/first */
/**
 * Background trip recording writes native samples to the durable SQLite outbox
 * before attempting a headless network upload.
 */

let mockBgPermission = 'granted';
const mockAsyncStorage: Record<string, string> = {};
const mockQueuedPoints: Record<string, unknown>[] = [];
let mockSequence = 0;

jest.mock('expo-location', () => ({
  startLocationUpdatesAsync: jest.fn(() => Promise.resolve()),
  hasStartedLocationUpdatesAsync: jest.fn(() => Promise.resolve(true)),
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
  isTaskRegisteredAsync: jest.fn(() => Promise.resolve(false)),
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

jest.mock('../tripLocationOutbox', () => ({
  tripLocationOutbox: {
    startSession: jest.fn(() => Promise.resolve({
      recording_session_id: 'session-1', ride_id: 'ride-1', opened_at: '2026-07-17T22:44:00.000Z', closed_at: null,
    })),
    enqueue: jest.fn(),
    listPendingSessions: jest.fn(),
    peek: jest.fn(),
    acknowledge: jest.fn(),
    pendingCount: jest.fn(),
    closeSession: jest.fn(),
  },
}));

jest.mock('@shared/config/spinr.config', () => ({
  __esModule: true,
  default: { backendUrl: 'https://example.test' },
}));
jest.mock('@shared/services/firebase', () => ({
  initFirebaseServices: jest.fn(() => Promise.resolve()),
  getAppCheckToken: jest.fn(() => Promise.resolve('app-check')),
}));

import {
  startBackgroundLocation,
  updateBackgroundLocationCadence,
  setBackgroundTripActive,
  handleBackgroundLocationTask,
  TRIP_CADENCE,
  IDLE_CADENCE,
} from '../backgroundLocation';
import { tripLocationRecorder } from '../tripLocationRecorder';
import { tripLocationOutbox as mockOutbox } from '../tripLocationOutbox';
import { resetLocationIntegrity } from '../locationIntegrity';
import * as SecureStore from 'expo-secure-store';
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Location from 'expo-location';

const mockedOutbox = mockOutbox as jest.Mocked<typeof mockOutbox>;
const mockStartUpdates = Location.startLocationUpdatesAsync as jest.Mock;
const mockHasStartedLocationUpdates = Location.hasStartedLocationUpdatesAsync as jest.Mock;

const makeLocation = (i: number) => ({
  coords: {
    latitude: 52.1 + i * 0.001,
    longitude: -106.6 - i * 0.001,
    speed: 12,
    heading: 90 + i,
    accuracy: 8,
    altitude: 578,
  },
  timestamp: Date.UTC(2026, 6, 9, 12, 0, i),
});

function configureOutbox(): void {
  mockedOutbox.enqueue.mockImplementation(async (fix) => {
    const point = { ...fix, recording_session_id: 'session-1', sequence_number: mockSequence++ };
    mockQueuedPoints.push(point);
    return point;
  });
  mockedOutbox.listPendingSessions.mockImplementation(async () => (
    mockQueuedPoints.length
      ? [{ recording_session_id: 'session-1', ride_id: 'ride-1', opened_at: '2026-07-17T22:44:00.000Z', closed_at: null }]
      : []
  ));
  mockedOutbox.peek.mockImplementation(async () => mockQueuedPoints as any);
  mockedOutbox.acknowledge.mockImplementation(async (_sessionId, ackedThrough) => {
    for (let index = mockQueuedPoints.length - 1; index >= 0; index -= 1) {
      if (Number(mockQueuedPoints[index]?.sequence_number) <= ackedThrough) mockQueuedPoints.splice(index, 1);
    }
  });
  mockedOutbox.pendingCount.mockImplementation(async (rideId) => (
    mockQueuedPoints.filter((point) => point.ride_id === rideId).length
  ));
}

describe('setBackgroundTripActive', () => {
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

describe('background durable trip recording', () => {
  beforeEach(async () => {
    for (const key of Object.keys(mockAsyncStorage)) delete mockAsyncStorage[key];
    mockQueuedPoints.splice(0, mockQueuedPoints.length);
    mockSequence = 0;
    Object.values(mockedOutbox).forEach((mock) => (mock as jest.Mock).mockClear());
    configureOutbox();
    (global as any).fetch = jest.fn(() => Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ recording_session_id: 'session-1', acked_through: 0, rejected: [] }),
    }));
    resetLocationIntegrity();
    await tripLocationRecorder.startRide('ride-1');
  });

  it('drops spoofing samples (impossible speed) but keeps low/unknown-accuracy fixes', async () => {
    // Regression guard: accuracy is NOT a trust signal. A null/0 or very high
    // accuracy fix — routine for backgrounded driving — must still be recorded,
    // or a trip driven with the app backgrounded loses nearly all its points.
    const nullAccuracy = makeLocation(0);
    (nullAccuracy.coords as any).accuracy = null; // background/coarse fix — keep
    const vague = makeLocation(1);
    vague.coords.accuracy = 500; // poor GPS (tunnel/urban canyon) — keep
    const impossible = makeLocation(2);
    impossible.coords.speed = 120; // > 90 m/s — genuine spoof signal, drop

    await handleBackgroundLocationTask({
      data: { locations: [nullAccuracy, vague, impossible, makeLocation(3)] } as any,
    });

    // 3 kept (null-accuracy, vague, normal), only the impossible-speed dropped.
    expect(mockedOutbox.enqueue).toHaveBeenCalledTimes(3);
    const capturedAts = mockedOutbox.enqueue.mock.calls.map((c: any[]) => c[0].captured_at);
    expect(capturedAts).toEqual([
      new Date(makeLocation(0).timestamp).toISOString(),
      new Date(makeLocation(1).timestamp).toISOString(),
      new Date(makeLocation(3).timestamp).toISOString(),
    ]);
  });

  it('enqueues native sensor timestamps before attempting a headless upload', async () => {
    const callOrder: string[] = [];
    mockedOutbox.enqueue.mockImplementation(async (fix) => {
      callOrder.push('enqueue');
      const point = { ...fix, recording_session_id: 'session-1', sequence_number: mockSequence++ };
      mockQueuedPoints.push(point);
      return point;
    });
    (global as any).fetch = jest.fn(() => {
      callOrder.push('fetch');
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ recording_session_id: 'session-1', acked_through: 0, rejected: [] }),
      });
    });

    await handleBackgroundLocationTask({ data: { locations: [makeLocation(0)] } as any });

    expect(mockedOutbox.enqueue).toHaveBeenCalledWith(expect.objectContaining({
      source: 'background',
      captured_at: new Date(makeLocation(0).timestamp).toISOString(),
      altitude: 578,
    }));
    expect(callOrder).toEqual(['enqueue', 'fetch']);
  });

  it.each([401, 503])('retains queued points when the headless upload returns %i', async (status) => {
    (global as any).fetch = jest.fn(() => Promise.resolve({ ok: false, status }));

    await handleBackgroundLocationTask({ data: { locations: [makeLocation(0)] } as any });

    expect(mockQueuedPoints).toHaveLength(1);
    expect(mockedOutbox.acknowledge).not.toHaveBeenCalled();
  });

  it('deletes only after the server returns an acknowledgement', async () => {
    await handleBackgroundLocationTask({ data: { locations: [makeLocation(0)] } as any });

    expect(mockedOutbox.acknowledge).toHaveBeenCalledWith('session-1', 0, []);
    expect(mockQueuedPoints).toHaveLength(0);
    expect(AsyncStorage.setItem).not.toHaveBeenCalledWith('spinr_bg_location_queue', expect.anything());
  });
});

describe('updateBackgroundLocationCadence', () => {
  beforeEach(() => {
    mockStartUpdates.mockClear();
    mockHasStartedLocationUpdates.mockClear();
    mockHasStartedLocationUpdates.mockResolvedValue(true);
    mockBgPermission = 'granted';
  });

  it('starts from native location-service liveness, not task registration', async () => {
    mockHasStartedLocationUpdates.mockResolvedValue(false);

    await expect(startBackgroundLocation()).resolves.toBe(true);

    expect(mockHasStartedLocationUpdates).toHaveBeenCalledWith('spinr-background-location');
    expect(mockStartUpdates).toHaveBeenCalledTimes(1);
  });

  it('re-tunes the running task to dense TRIP_CADENCE using location-service liveness', async () => {
    await updateBackgroundLocationCadence(TRIP_CADENCE);
    expect(mockHasStartedLocationUpdates).toHaveBeenCalledWith('spinr-background-location');
    expect(mockStartUpdates).toHaveBeenCalledTimes(1);
    const opts = mockStartUpdates.mock.calls[0][1];
    expect(opts.timeInterval).toBe(TRIP_CADENCE.timeInterval);
    expect(opts.distanceInterval).toBe(TRIP_CADENCE.distanceInterval);
    expect(opts.accuracy).toBe(TRIP_CADENCE.accuracy);
  });

  it('is a no-op when the native location service is not running', async () => {
    mockHasStartedLocationUpdates.mockResolvedValue(false);
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
