/* eslint-disable import/first */
/**
 * Sign-out must stop driver location tracking.
 *
 * Regression cover for the logout leak: teardown lived only in
 * toggleOnline(false), so the driver app — unlike the rider app — never
 * registered a logout callback, and signing out left the native task running,
 * the geofence armed, a live JWT cached, and raw coordinates in SQLite.
 */

// Inline jest.fn() in the factory, not a closed-over const: jest hoists the
// factory above the module-level declarations, so a `const` would still be in
// its temporal dead zone when the hoisted import of ../sessionTeardown pulls
// this module in. Retrieved via jest.requireMock below instead.
jest.mock('@shared/store/authStore', () => ({
  registerLogoutCallback: jest.fn(),
}));

jest.mock('../backgroundLocation', () => ({
  stopBackgroundLocation: jest.fn(() => Promise.resolve()),
  stopGeofenceRecovery: jest.fn(() => Promise.resolve()),
}));

jest.mock('../tripLocationRecorder', () => ({
  tripLocationRecorder: { purgeAll: jest.fn(() => Promise.resolve()) },
}));

jest.mock('../crashlytics', () => ({ recordNonFatal: jest.fn() }));

// Android-only tree, lazily required by sessionTeardown; mocked here so this
// suite never pulls expo-location/expo-task-manager in.
jest.mock('../../lib/androidAuto/carLocationTask', () => ({
  stopCarLocationService: jest.fn(() => Promise.resolve()),
}));

jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: { removeItem: jest.fn(() => Promise.resolve()) },
}));

import {
  teardownDriverLocationSession,
  registerDriverSessionTeardown,
  _resetSessionTeardownRegistration,
} from '../sessionTeardown';
import { stopBackgroundLocation, stopGeofenceRecovery } from '../backgroundLocation';
import { tripLocationRecorder } from '../tripLocationRecorder';
import { recordNonFatal } from '../crashlytics';
import { stopCarLocationService } from '../../lib/androidAuto/carLocationTask';
import { LAST_LOCATION_KEY } from '../../lib/androidAuto/carFixChannel';

const mockRegisterLogoutCallback = (
  jest.requireMock('@shared/store/authStore') as { registerLogoutCallback: jest.Mock }
).registerLogoutCallback;
const mockStopBg = stopBackgroundLocation as jest.Mock;
const mockStopGeofence = stopGeofenceRecovery as jest.Mock;
const mockPurge = tripLocationRecorder.purgeAll as jest.Mock;
const mockRecordNonFatal = recordNonFatal as jest.Mock;
const mockStopCarLocation = stopCarLocationService as jest.Mock;
// eslint-disable-next-line @typescript-eslint/no-require-imports
const mockRemoveItem = require('@react-native-async-storage/async-storage').default
  .removeItem as jest.Mock;

beforeEach(() => {
  [mockStopBg, mockStopGeofence, mockPurge, mockRecordNonFatal, mockRegisterLogoutCallback,
   mockStopCarLocation, mockRemoveItem].forEach((m) => {
    m.mockClear();
    m.mockImplementation(() => Promise.resolve());
  });
  _resetSessionTeardownRegistration();
});

describe('teardownDriverLocationSession', () => {
  it('stops the task, disarms the fence, and purges the outbox', async () => {
    await teardownDriverLocationSession();

    expect(mockStopBg).toHaveBeenCalledTimes(1);
    expect(mockStopGeofence).toHaveBeenCalledTimes(1);
    expect(mockPurge).toHaveBeenCalledTimes(1);
  });

  it('stops producing fixes before purging, so nothing re-enqueues after the delete', async () => {
    const order: string[] = [];
    mockStopBg.mockImplementation(async () => { order.push('stopBg'); });
    mockStopGeofence.mockImplementation(async () => { order.push('stopGeofence'); });
    mockPurge.mockImplementation(async () => { order.push('purge'); });

    await teardownDriverLocationSession();

    expect(order).toEqual(['stopBg', 'stopGeofence', 'purge']);
  });

  it('still disarms the fence and purges when stopping the task throws', async () => {
    mockStopBg.mockRejectedValue(new Error('task not registered'));

    await expect(teardownDriverLocationSession()).resolves.toBeUndefined();

    // Each step alone leaves a signed-out driver tracked, so one failure must
    // not skip the others.
    expect(mockStopGeofence).toHaveBeenCalledTimes(1);
    expect(mockPurge).toHaveBeenCalledTimes(1);
    expect(mockRecordNonFatal).toHaveBeenCalled();
  });

  it('still purges when disarming the fence throws', async () => {
    mockStopGeofence.mockRejectedValue(new Error('not monitoring'));

    await expect(teardownDriverLocationSession()).resolves.toBeUndefined();
    expect(mockPurge).toHaveBeenCalledTimes(1);
  });

  it('reports a failed purge rather than swallowing it', async () => {
    mockPurge.mockRejectedValue(new Error('database is locked'));

    await expect(teardownDriverLocationSession()).resolves.toBeUndefined();
    expect(mockRecordNonFatal).toHaveBeenCalledWith(
      expect.any(Error),
      expect.objectContaining({ teardown: 'purge_outbox' }),
    );
  });

  it('never rejects, so it cannot block the user from signing out', async () => {
    mockStopBg.mockRejectedValue(new Error('a'));
    mockStopGeofence.mockRejectedValue(new Error('b'));
    mockPurge.mockRejectedValue(new Error('c'));

    await expect(teardownDriverLocationSession()).resolves.toBeUndefined();
  });
});

describe('registerDriverSessionTeardown', () => {
  it('registers the teardown as a logout callback', () => {
    registerDriverSessionTeardown();

    expect(mockRegisterLogoutCallback).toHaveBeenCalledTimes(1);
    expect(mockRegisterLogoutCallback).toHaveBeenCalledWith(teardownDriverLocationSession);
  });

  it('is idempotent, so a re-imported module cannot double-register', () => {
    registerDriverSessionTeardown();
    registerDriverSessionTeardown();
    registerDriverSessionTeardown();

    expect(mockRegisterLogoutCallback).toHaveBeenCalledTimes(1);
  });

  it('runs teardown when the registered callback is invoked', async () => {
    registerDriverSessionTeardown();
    const callback = mockRegisterLogoutCallback.mock.calls[0][0] as () => Promise<void>;

    await callback();

    expect(mockStopBg).toHaveBeenCalledTimes(1);
    expect(mockStopGeofence).toHaveBeenCalledTimes(1);
    expect(mockPurge).toHaveBeenCalledTimes(1);
  });
});

describe('Android Auto display-only task', () => {
  it('is stopped on sign-out', async () => {
    // Its registration is persisted by expo-task-manager and restored on process
    // start, so leaving it running lets a location broadcast relaunch the app
    // headlessly and resume tracking a signed-out driver — and on the
    // no-foreground-service fallback path there is no notification to show it.
    await teardownDriverLocationSession();
    expect(mockStopCarLocation).toHaveBeenCalledTimes(1);
  });

  it('clears the shared last-known position', async () => {
    // The SQLite purge does not cover this — different store. It is the one
    // coordinate pair that survives sign-out precisely because it is a display
    // cache rather than trip data.
    await teardownDriverLocationSession();
    expect(mockRemoveItem).toHaveBeenCalledWith(LAST_LOCATION_KEY);
  });

  it('a failure to stop it does not skip the purge that follows', async () => {
    mockStopCarLocation.mockRejectedValueOnce(new Error('native module absent'));

    await expect(teardownDriverLocationSession()).resolves.toBeUndefined();

    expect(mockPurge).toHaveBeenCalledTimes(1);
    expect(mockRemoveItem).toHaveBeenCalledWith(LAST_LOCATION_KEY);
    expect(mockRecordNonFatal).toHaveBeenCalledWith(expect.any(Error), {
      domain: 'drivers',
      surface: 'driver-app',
      teardown: 'stop_car_location',
    });
  });

  it('a failure to clear the cache is reported, not swallowed', async () => {
    mockRemoveItem.mockRejectedValueOnce(new Error('storage unavailable'));
    await expect(teardownDriverLocationSession()).resolves.toBeUndefined();
    expect(mockRecordNonFatal).toHaveBeenCalledWith(expect.any(Error), {
      domain: 'drivers',
      surface: 'driver-app',
      teardown: 'clear_last_location',
    });
  });
});
