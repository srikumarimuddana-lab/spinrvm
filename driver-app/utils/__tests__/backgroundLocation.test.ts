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
  hasStartedGeofencingAsync: jest.fn(() => Promise.resolve(false)),
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

jest.mock('../crashlytics', () => ({
  recordNonFatal: jest.fn(),
}));

import {
  startBackgroundLocation,
  updateBackgroundLocationCadence,
  recoverTripLocation,
  setBackgroundTripActive,
  handleBackgroundLocationTask,
  startGeofenceRecovery,
  stopGeofenceRecovery,
  _resetGeofenceDebounce,
  TRIP_CADENCE,
  IDLE_CADENCE,
} from '../backgroundLocation';
import * as TaskManager from 'expo-task-manager';
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

describe('geofence recovery task (NSRangeException guard)', () => {
  let geofenceHandler: (body: { data: any; error: any }) => Promise<void>;

  beforeAll(() => {
    const defineTask = TaskManager.defineTask as jest.Mock;
    const geofenceCall = defineTask.mock.calls.find(
      (call: any[]) => call[0] === 'spinr-geofence-recovery'
    );
    expect(geofenceCall).toBeDefined();
    geofenceHandler = geofenceCall![1];
  });

  beforeEach(() => {
    (Location.hasStartedLocationUpdatesAsync as jest.Mock).mockClear();
    (Location.startGeofencingAsync as jest.Mock).mockClear();
  });

  it('no-ops when data is undefined (nil-coalesced to empty dict by native guard)', async () => {
    await geofenceHandler({ data: undefined, error: null });
    expect(Location.hasStartedLocationUpdatesAsync).not.toHaveBeenCalled();
  });

  it('no-ops when data is an empty object (native nil guard fallback)', async () => {
    await geofenceHandler({ data: {}, error: null });
    expect(Location.hasStartedLocationUpdatesAsync).not.toHaveBeenCalled();
  });

  it('no-ops when eventType is Enter (not Exit)', async () => {
    await geofenceHandler({
      data: { eventType: Location.GeofencingEventType.Enter, region: { identifier: 'spinr-recovery' } },
      error: null,
    });
    expect(Location.hasStartedLocationUpdatesAsync).not.toHaveBeenCalled();
  });

  it('no-ops when eventType is missing from data', async () => {
    await geofenceHandler({
      data: { region: { identifier: 'spinr-recovery' } },
      error: null,
    });
    expect(Location.hasStartedLocationUpdatesAsync).not.toHaveBeenCalled();
  });

  it('handles error parameter gracefully', async () => {
    const consoleSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
    await geofenceHandler({
      data: null,
      error: { message: 'CLError: region monitoring failed' },
    });
    expect(consoleSpy).toHaveBeenCalledWith(
      '[Geofence] Task error:',
      'CLError: region monitoring failed'
    );
    expect(Location.hasStartedLocationUpdatesAsync).not.toHaveBeenCalled();
    consoleSpy.mockRestore();
  });

  it('re-arms background tracking on valid Exit event', async () => {
    (Location.hasStartedLocationUpdatesAsync as jest.Mock).mockResolvedValue(false);
    (SecureStore.getItemAsync as jest.Mock).mockImplementation((key: string) => {
      if (key === 'spinr_bg_trip_active') return Promise.resolve(null);
      if (key === 'bg_access_token') return Promise.resolve('access-token');
      if (key === 'bg_access_token_expires') return Promise.resolve(String(Date.now() + 120_000));
      return Promise.resolve(null);
    });
    mockBgPermission = 'granted';

    await geofenceHandler({
      data: { eventType: Location.GeofencingEventType.Exit, region: { identifier: 'spinr-recovery' } },
      error: null,
    });

    expect(Location.hasStartedLocationUpdatesAsync).toHaveBeenCalledWith('spinr-background-location');
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


describe('recoverTripLocation (P3.2 — location_health nudge)', () => {
  beforeEach(() => {
    mockStartUpdates.mockClear();
    mockHasStartedLocationUpdates.mockReset();
  });

  it('re-asserts the trip cadence when the task is already running', async () => {
    mockHasStartedLocationUpdates.mockResolvedValue(true);
    const running = await recoverTripLocation();
    expect(running).toBe(true);
    // Cadence re-applied in place → startLocationUpdatesAsync called with the
    // dense TRIP_CADENCE interval.
    expect(mockStartUpdates).toHaveBeenCalled();
    const cfg = mockStartUpdates.mock.calls.at(-1)?.[1];
    expect(cfg.timeInterval).toBe(TRIP_CADENCE.timeInterval);
  });

  it('restarts the task at trip cadence when it was killed', async () => {
    // Not running when checked by recover; startBackgroundLocation re-checks.
    mockHasStartedLocationUpdates.mockResolvedValue(false);
    const running = await recoverTripLocation();
    expect(running).toBe(true);
    expect(mockStartUpdates).toHaveBeenCalled();
  });

  it('returns false and never throws on failure', async () => {
    mockHasStartedLocationUpdates.mockRejectedValue(new Error('permission revoked'));
    await expect(recoverTripLocation()).resolves.toBe(false);
  });
});

describe('geofence re-arm gating (NSRangeException fix)', () => {
  const mockStartGeofencing = Location.startGeofencingAsync as jest.Mock;
  const mockStopGeofencing = Location.stopGeofencingAsync as jest.Mock;
  const mockHasStartedGeofencing = (Location as any).hasStartedGeofencingAsync as jest.Mock;
  const CENTRE_KEY = 'spinr_bg_geofence_centre';
  // ~1km east of the armed centre — well past the 150m recentre threshold.
  const FAR_LNG = -106.585;

  let centreStore: string | null;

  beforeEach(() => {
    [mockStartGeofencing, mockStopGeofencing, mockHasStartedGeofencing].forEach((m) => m.mockClear());
    mockStartGeofencing.mockImplementation(() => Promise.resolve());
    mockStopGeofencing.mockImplementation(() => Promise.resolve());
    mockHasStartedGeofencing.mockResolvedValue(false);
    (TaskManager.isTaskRegisteredAsync as jest.Mock).mockClear();
    mockBgPermission = 'granted';
    _resetGeofenceDebounce();

    // The displacement gate reads/writes the armed centre through SecureStore,
    // so it must be stateful here rather than a fixed stub.
    centreStore = null;
    (SecureStore.getItemAsync as jest.Mock).mockImplementation((k: string) =>
      Promise.resolve(k === CENTRE_KEY ? centreStore : null));
    (SecureStore.setItemAsync as jest.Mock).mockImplementation((k: string, v: string) => {
      if (k === CENTRE_KEY) centreStore = v;
      return Promise.resolve();
    });
    (SecureStore.deleteItemAsync as jest.Mock).mockImplementation((k: string) => {
      if (k === CENTRE_KEY) centreStore = null;
      return Promise.resolve();
    });
  });

  it('arms via hasStartedGeofencingAsync, never isTaskRegisteredAsync', async () => {
    await startGeofenceRecovery(52.1, -106.6);
    expect(mockHasStartedGeofencing).toHaveBeenCalledWith('spinr-geofence-recovery');
    expect(TaskManager.isTaskRegisteredAsync).not.toHaveBeenCalled();
    expect(mockStartGeofencing).toHaveBeenCalledTimes(1);
  });

  it('always stops before re-arming so regions cannot accumulate', async () => {
    await startGeofenceRecovery(52.1, -106.6);
    expect(mockStopGeofencing).toHaveBeenCalledWith('spinr-geofence-recovery');
    expect(mockStartGeofencing).toHaveBeenCalledTimes(1);
  });

  it('persists the armed centre so the gate survives a headless wake', async () => {
    await startGeofenceRecovery(52.1, -106.6);
    expect(JSON.parse(centreStore!)).toEqual({ lat: 52.1, lng: -106.6 });
  });

  it('skips the re-arm while armed and the driver has not moved far', async () => {
    await startGeofenceRecovery(52.1, -106.6);
    mockHasStartedGeofencing.mockResolvedValue(true);
    mockStartGeofencing.mockClear();

    const result = await startGeofenceRecovery(52.1001, -106.6001); // ~14m
    expect(result).toBe(true);
    expect(mockStartGeofencing).not.toHaveBeenCalled();
  });

  it('recentres once the driver has moved past the displacement threshold', async () => {
    await startGeofenceRecovery(52.1, -106.6);
    mockHasStartedGeofencing.mockResolvedValue(true);
    mockStartGeofencing.mockClear();

    const result = await startGeofenceRecovery(52.1, FAR_LNG);
    expect(result).toBe(true);
    expect(mockStartGeofencing).toHaveBeenCalledTimes(1);
  });

  it('re-arms when monitoring stopped, even if the driver has not moved', async () => {
    // Regression: gating on elapsed time alone left the driver with no
    // recovery geofence whenever iOS dropped region monitoring.
    await startGeofenceRecovery(52.1, -106.6);
    mockStartGeofencing.mockClear();
    mockHasStartedGeofencing.mockResolvedValue(false);

    const result = await startGeofenceRecovery(52.1, -106.6);
    expect(result).toBe(true);
    expect(mockStartGeofencing).toHaveBeenCalledTimes(1);
  });

  it('re-arms after go-offline then go-online at the same spot', async () => {
    // Regression: stopGeofenceRecovery left gate state stale, so a driver
    // toggling offline and back on went online with no geofence armed while
    // startGeofenceRecovery still reported success.
    mockHasStartedGeofencing.mockResolvedValue(true);
    await startGeofenceRecovery(52.1, -106.6);
    expect(mockStartGeofencing).toHaveBeenCalledTimes(1);

    await stopGeofenceRecovery();
    expect(centreStore).toBeNull();

    mockStartGeofencing.mockClear();
    const result = await startGeofenceRecovery(52.1, -106.6);
    expect(result).toBe(true);
    expect(mockStartGeofencing).toHaveBeenCalledTimes(1);
  });

  it('re-arms rather than assuming armed when the liveness probe throws', async () => {
    await startGeofenceRecovery(52.1, -106.6);
    mockStartGeofencing.mockClear();
    mockHasStartedGeofencing.mockRejectedValue(new Error('CLError'));

    const result = await startGeofenceRecovery(52.1, -106.6);
    expect(result).toBe(true);
    expect(mockStartGeofencing).toHaveBeenCalledTimes(1);
  });

  it('single-flights genuinely concurrent calls into one arm', async () => {
    let resolveArm: (() => void) | null = null;
    mockStartGeofencing.mockImplementationOnce(
      () => new Promise<void>((resolve) => { resolveArm = resolve; }),
    );

    const p1 = startGeofenceRecovery(52.1, -106.6);
    const p2 = startGeofenceRecovery(52.1, -106.6);
    // Let both calls reach the lock before the first arm settles.
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
    mockHasStartedGeofencing.mockResolvedValue(true);
    (resolveArm as unknown as () => void)();
    const [r1, r2] = await Promise.all([p1, p2]);

    expect(r1).toBe(true);
    expect(r2).toBe(true);
    expect(mockStartGeofencing).toHaveBeenCalledTimes(1);
  });

  describe('stop vs. in-flight arm races (generation fence)', () => {
    beforeEach(() => { jest.useFakeTimers(); });
    afterEach(() => { jest.useRealTimers(); });

    it('a stop always supersedes an in-flight arm, even one that only settles after the wait times out', async () => {
      // Regression: dropping (or even awaiting) the in-flight-promise reference
      // alone left a window where a start could land after stop's own disarm
      // call, geofencing a driver who had already gone offline. The generation
      // fence must catch this regardless of how long the arm takes to settle.
      let resolveArm!: () => void;
      mockStartGeofencing.mockImplementationOnce(
        () => new Promise<void>((resolve) => { resolveArm = resolve; }),
      );

      const startPromise = startGeofenceRecovery(52.1, -106.6);
      // Let the arm's call chain reach the hung startGeofencingAsync.
      for (let i = 0; i < 10; i += 1) await Promise.resolve();

      const stopPromise = stopGeofenceRecovery();
      // The arm never settles on its own — stop must give up rather than
      // block the caller (toggleOnline) forever.
      await jest.advanceTimersByTimeAsync(8_000);
      await stopPromise;

      const stopCallsBeforeArmSettles = mockStopGeofencing.mock.calls.length;
      expect(stopCallsBeforeArmSettles).toBeGreaterThan(0); // disarmed without waiting for the arm
      expect(centreStore).toBeNull();

      // The native call finally "returns" well after stop gave up.
      resolveArm();
      const result = await startPromise;

      expect(result).toBe(false); // must not report armed — a stop was requested
      expect(centreStore).toBeNull(); // the late arm must not resurrect the centre
      // Self-correction ran a second disarm rather than leaving the region armed.
      expect(mockStopGeofencing.mock.calls.length).toBeGreaterThan(stopCallsBeforeArmSettles);
    });
  });

  it('stopGeofenceRecovery stops unconditionally and clears the centre', async () => {
    await startGeofenceRecovery(52.1, -106.6);
    mockStopGeofencing.mockClear();

    await stopGeofenceRecovery();
    expect(mockStopGeofencing).toHaveBeenCalledWith('spinr-geofence-recovery');
    expect(centreStore).toBeNull();
    expect(TaskManager.isTaskRegisteredAsync).not.toHaveBeenCalled();
  });

  it('stopGeofenceRecovery never throws when nothing is monitored', async () => {
    mockStopGeofencing.mockRejectedValueOnce(new Error('not monitoring'));
    await expect(stopGeofenceRecovery()).resolves.toBeUndefined();
  });
});
