/**
 * Unit tests for lib/androidAuto/carLocationTask.ts.
 *
 * The point of this module is what it REFUSES to do, so that is what these
 * cover: it never prompts for permission, never starts a second foreground
 * service, never runs for a signed-out device, never makes a network call, and
 * never throws at a driver who is looking at a car screen.
 */
import type { CarLocationStart } from '../carLocationTask';

const mockLocation = {
  Accuracy: { High: 6, Balanced: 3 },
  ActivityType: { AutomotiveNavigation: 3 },
  hasStartedLocationUpdatesAsync: jest.fn(),
  getBackgroundPermissionsAsync: jest.fn(),
  startLocationUpdatesAsync: jest.fn(),
  stopLocationUpdatesAsync: jest.fn(),
};
jest.mock('expo-location', () => mockLocation);

// Captured at module load. `jest.clearAllMocks()` in beforeEach would wipe the
// registration call, so record it out of band instead.
const mockDefinedTasks: string[] = [];
jest.mock('expo-task-manager', () => ({
  defineTask: (name: string, fn: unknown) => {
    mockDefinedTasks.push(name);
    if (typeof fn !== 'function') throw new Error('handler must be a function');
  },
}));

const mockIsSessionEnded = jest.fn();
const mockIsBgRunning = jest.fn();
jest.mock('../../../utils/backgroundLocation', () => ({
  FOREGROUND_NOTIFICATION_COLOR: '#6C63FF',
  isSessionEnded: () => mockIsSessionEnded(),
  isBackgroundLocationRunning: () => mockIsBgRunning(),
}));

const mockPublishCarFix = jest.fn();
jest.mock('../carFixChannel', () => ({ publishCarFix: (f: unknown) => mockPublishCarFix(f) }));

const mockRecordNonFatal = jest.fn();
jest.mock('../../../utils/crashlytics', () => ({
  recordNonFatal: (...a: unknown[]) => mockRecordNonFatal(...a),
}));

const mockIntegrity = jest.fn<{ trusted: boolean; reason?: string }, [unknown]>(() => ({
  trusted: true,
}));
jest.mock('../../../utils/locationIntegrity', () => ({
  checkLocationIntegrity: (l: unknown) => mockIntegrity(l),
}));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const mod = require('../carLocationTask');
const {
  CAR_LOCATION_TASK,
  handleCarLocationTask,
  startCarLocationService,
  stopCarLocationService,
  _carLocationMode,
  _resetCarLocationTelemetry,
} = mod as typeof import('../carLocationTask');

/**
 * What expo-location throws from LocationModule.kt:327 whenever
 * AppForegroundedSingleton.isForegrounded is false — which, on a car-only
 * launch, is always. The message is the literal Kotlin string.
 */
const fgsRefusal = () =>
  Object.assign(
    new Error(
      "Couldn't start the foreground service. Foreground service cannot be started " +
        'when the application is in the background',
    ),
    { code: 'ERR_FOREGROUND_SERVICE_START_NOT_ALLOWED' },
  );

const fix = (lat: number, heading: number | null = 90) => ({
  coords: { latitude: lat, longitude: -106.67, heading },
});

beforeEach(async () => {
  jest.clearAllMocks();
  _resetCarLocationTelemetry();
  await stopCarLocationService(); // resets the module's mode between cases
  jest.spyOn(console, 'warn').mockImplementation(() => {});
  jest.spyOn(console, 'log').mockImplementation(() => {});
  mockIntegrity.mockReturnValue({ trusted: true });
  mockIsSessionEnded.mockResolvedValue(false);
  mockIsBgRunning.mockResolvedValue(false);
  mockLocation.hasStartedLocationUpdatesAsync.mockResolvedValue(false);
  mockLocation.getBackgroundPermissionsAsync.mockResolvedValue({ status: 'granted' });
  mockLocation.startLocationUpdatesAsync.mockResolvedValue(undefined);
  mockLocation.stopLocationUpdatesAsync.mockResolvedValue(undefined);
});

afterEach(() => jest.restoreAllMocks());

describe('task registration', () => {
  it('defines the task at module load, under its own distinct name', () => {
    // Must not collide with 'spinr-background-location' — a shared name would
    // mean one service and one set of options for two different jobs.
    expect(CAR_LOCATION_TASK).toBe('spinr-car-location');
    expect(mockDefinedTasks).toContain(CAR_LOCATION_TASK);
    expect(mockDefinedTasks).not.toContain('spinr-background-location');
  });
});

describe('handler', () => {
  it('publishes the newest sample only', async () => {
    // A deferred batch replayed in order would rewind the marker through
    // positions the driver has already left.
    await handleCarLocationTask({ data: { locations: [fix(1), fix(2), fix(3)] as never } });
    expect(mockPublishCarFix).toHaveBeenCalledTimes(1);
    expect(mockPublishCarFix).toHaveBeenCalledWith({
      latitude: 3,
      longitude: -106.67,
      heading: 90,
    });
  });

  it('makes NO network call — this must never transmit an offline driver', async () => {
    const fetchSpy = jest.fn();
    const original = global.fetch;
    global.fetch = fetchSpy as never;
    try {
      await handleCarLocationTask({ data: { locations: [fix(1)] as never } });
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      global.fetch = original;
    }
  });

  it('drops an untrusted (mocked) sample', async () => {
    mockIntegrity.mockReturnValue({ trusted: false, reason: 'mock_location_detected' });
    await handleCarLocationTask({ data: { locations: [fix(1)] as never } });
    expect(mockPublishCarFix).not.toHaveBeenCalled();
  });

  it('carries a null heading through rather than inventing one', async () => {
    await handleCarLocationTask({ data: { locations: [fix(1, null)] as never } });
    expect(mockPublishCarFix).toHaveBeenCalledWith(expect.objectContaining({ heading: null }));
  });

  it('ignores an error payload and an empty batch without throwing', async () => {
    await expect(handleCarLocationTask({ error: { message: 'no signal' } })).resolves.toBeUndefined();
    await expect(handleCarLocationTask({ data: { locations: [] } })).resolves.toBeUndefined();
    expect(mockPublishCarFix).not.toHaveBeenCalled();
  });
});

describe('start guards', () => {
  const expectNoStart = async (expected: CarLocationStart) => {
    await expect(startCarLocationService()).resolves.toBe(expected);
    expect(mockLocation.startLocationUpdatesAsync).not.toHaveBeenCalled();
  };

  it('starts when nothing is in the way', async () => {
    await expect(startCarLocationService()).resolves.toBe('started');
    const [task, opts] = mockLocation.startLocationUpdatesAsync.mock.calls[0];
    expect(task).toBe(CAR_LOCATION_TASK);
    expect(opts.foregroundService.notificationBody).toMatch(/car screen/i);
    expect(opts.pausesUpdatesAutomatically).toBe(false);
  });

  it('never prompts — a dialog raised from a head unit is not acceptable', async () => {
    mockLocation.getBackgroundPermissionsAsync.mockResolvedValue({ status: 'denied' });
    await expectNoStart('no-permission');
    expect(
      (mockLocation as Record<string, unknown>).requestBackgroundPermissionsAsync,
    ).toBeUndefined();
  });

  it('defers to the dispatch service rather than adding a second notification', async () => {
    mockIsBgRunning.mockResolvedValue(true);
    await expectNoStart('piggyback');
  });

  it('does not run for a signed-out device', async () => {
    mockIsSessionEnded.mockResolvedValue(true);
    await expectNoStart('session-ended');
  });

  it('is idempotent when already running', async () => {
    mockLocation.hasStartedLocationUpdatesAsync.mockResolvedValue(true);
    await expectNoStart('already-running');
  });

});

describe('Android refusing a background foreground-service start', () => {
  // This is not an edge case. expo-location gates the FGS start on
  // AppForegroundedSingleton.isForegrounded (LocationModule.kt:327), and that
  // singleton is set ONLY by Activity lifecycle hooks (:374-380). An Android
  // Auto car-only launch creates no Activity, so the refusal is guaranteed on
  // the exact path this module exists for.
  const arrangeRefusal = () => {
    mockLocation.startLocationUpdatesAsync
      .mockRejectedValueOnce(fgsRefusal())
      .mockResolvedValueOnce(undefined);
  };

  it('falls back to a task with no notification rather than giving up', async () => {
    arrangeRefusal();

    await expect(startCarLocationService()).resolves.toBe('started-no-notification');

    expect(mockLocation.startLocationUpdatesAsync).toHaveBeenCalledTimes(2);
    const [, first] = mockLocation.startLocationUpdatesAsync.mock.calls[0];
    const [, second] = mockLocation.startLocationUpdatesAsync.mock.calls[1];
    expect(first.foregroundService).toBeDefined();
    expect(second.foregroundService).toBeUndefined();
    // The ONLY difference is the service block — accuracy and cadence must not
    // silently change when we degrade.
    expect(second.accuracy).toBe(first.accuracy);
    expect(second.timeInterval).toBe(first.timeInterval);
    expect(second.distanceInterval).toBe(first.distanceInterval);
  });

  it('reports the refusal once per process, not once per attempt', async () => {
    // startCarLocationService runs on every connect AND every 60s tick.
    arrangeRefusal();
    await startCarLocationService();
    expect(mockRecordNonFatal).toHaveBeenCalledTimes(1);
    expect(mockRecordNonFatal.mock.calls[0][1]).toMatchObject({
      reason: 'foreground_service_start_refused',
    });

    mockLocation.hasStartedLocationUpdatesAsync.mockResolvedValue(true);
    arrangeRefusal();
    await startCarLocationService();
    expect(mockRecordNonFatal).toHaveBeenCalledTimes(1);
  });

  it('keeps re-attempting the upgrade while degraded', async () => {
    // Android stops refusing once the phone app is foregrounded, so a degraded
    // task is not finished business the way a healthy one is.
    arrangeRefusal();
    await startCarLocationService();
    expect(_carLocationMode()).toBe('no-notification');

    mockLocation.hasStartedLocationUpdatesAsync.mockResolvedValue(true);
    mockLocation.startLocationUpdatesAsync.mockResolvedValue(undefined);

    await expect(startCarLocationService()).resolves.toBe('started');
    expect(_carLocationMode()).toBe('foreground-service');
  });

  it('does not re-attempt once the foreground service is actually running', async () => {
    await expect(startCarLocationService()).resolves.toBe('started');
    mockLocation.hasStartedLocationUpdatesAsync.mockResolvedValue(true);
    await expect(startCarLocationService()).resolves.toBe('already-running');
  });

  it('reports fgs-refused when the fallback fails too', async () => {
    mockLocation.startLocationUpdatesAsync
      .mockRejectedValueOnce(fgsRefusal())
      .mockRejectedValueOnce(new Error('permission revoked mid-call'));
    await expect(startCarLocationService()).resolves.toBe('fgs-refused');
  });

  it('does not mistake an unrelated failure for a refusal', async () => {
    mockLocation.startLocationUpdatesAsync.mockRejectedValue(new Error('some other native error'));
    await expect(startCarLocationService()).resolves.toBe('unavailable');
    expect(mockLocation.startLocationUpdatesAsync).toHaveBeenCalledTimes(1);
    expect(mockRecordNonFatal).not.toHaveBeenCalled();
  });
});

describe('stop', () => {
  it('stops the task', async () => {
    await stopCarLocationService();
    expect(mockLocation.stopLocationUpdatesAsync).toHaveBeenCalledWith(CAR_LOCATION_TASK);
  });

  it('swallows the throw expo raises when the task was never started', async () => {
    mockLocation.stopLocationUpdatesAsync.mockRejectedValue(new Error('not started'));
    await expect(stopCarLocationService()).resolves.toBeUndefined();
  });
});
