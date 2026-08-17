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
} = mod as typeof import('../carLocationTask');

const fix = (lat: number, heading: number | null = 90) => ({
  coords: { latitude: lat, longitude: -106.67, heading },
});

beforeEach(() => {
  jest.clearAllMocks();
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

  it('swallows an Android 12+ background-FGS-start refusal', async () => {
    mockLocation.startLocationUpdatesAsync.mockRejectedValue(
      new Error('ForegroundServiceStartNotAllowedException'),
    );
    await expect(startCarLocationService()).resolves.toBe('unavailable');
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
