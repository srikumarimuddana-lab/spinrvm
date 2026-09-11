/**
 * reassertDispatchTaskUnlocked — the once-a-minute self-heal that re-promotes
 * the shared Android location service by calling startLocationUpdatesAsync on
 * the live task.
 *
 * On Android that call is refused natively unless the activity is resumed
 * (expo-location LocationModule.kt throws ForegroundServiceStartNotAllowedException
 * before touching the task). During the 2026-09-11 test ride the heal fired
 * from the background task every ~60 s with the phone locked, and each attempt
 * was a Sentry error (CRIMSON-SMOKE-7445-PP, all `in_foreground: false`) that
 * could not have repaired anything. The heal must skip while backgrounded and
 * replay exactly once when the app comes back to the foreground.
 */
import { AppState, Platform } from 'react-native';

const appStateListeners: Array<(state: string) => void> = [];
const mockRemove = jest.fn();
jest.mock('react-native/Libraries/AppState/AppState', () => ({
  __esModule: true,
  default: {
    addEventListener: (event: string, cb: (state: string) => void) => {
      if (event === 'change') appStateListeners.push(cb);
      return { remove: mockRemove };
    },
    currentState: 'active',
  },
}));

const mockHasStarted = jest.fn();
const mockGetBgPerms = jest.fn();
const mockStartUpdates = jest.fn();
jest.mock('expo-location', () => ({
  Accuracy: { Lowest: 1, Low: 2, Balanced: 3, High: 4, Highest: 5, BestForNavigation: 6 },
  ActivityType: { AutomotiveNavigation: 2 },
  GeofencingEventType: { Enter: 1, Exit: 2 },
  hasStartedLocationUpdatesAsync: (...a: unknown[]) => mockHasStarted(...a),
  getBackgroundPermissionsAsync: (...a: unknown[]) => mockGetBgPerms(...a),
  startLocationUpdatesAsync: (...a: unknown[]) => mockStartUpdates(...a),
  stopLocationUpdatesAsync: jest.fn(),
  hasStartedGeofencingAsync: jest.fn().mockResolvedValue(false),
  startGeofencingAsync: jest.fn(),
  stopGeofencingAsync: jest.fn(),
  getCurrentPositionAsync: jest.fn(),
  requestBackgroundPermissionsAsync: jest.fn(),
}));
jest.mock('expo-task-manager', () => ({ defineTask: jest.fn() }));
jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn().mockResolvedValue('true'),
  setItemAsync: jest.fn(),
  deleteItemAsync: jest.fn(),
}));
jest.mock('@shared/config/spinr.config', () => ({
  __esModule: true,
  default: { backendUrl: 'https://example.test' },
}));
jest.mock('@shared/services/firebase', () => ({
  getAppCheckToken: jest.fn().mockResolvedValue(null),
  initFirebaseServices: jest.fn().mockResolvedValue(undefined),
}));
jest.mock('../../utils/tripLocationRecorder', () => ({
  drainTerminalAck: jest.fn(),
  TERMINAL_STATUS_CODES: new Set([400, 401, 403, 404, 409, 410, 422]),
  tripLocationRecorder: { flushPending: jest.fn(), recordNativeFix: jest.fn() },
}));
const mockRecordNonFatal = jest.fn();
jest.mock('../../utils/crashlytics', () => ({
  recordNonFatal: (...a: unknown[]) => mockRecordNonFatal(...a),
}));
jest.mock('../../lib/androidAuto/carFixChannel', () => ({ publishCarFix: jest.fn() }));

import {
  reassertDispatchTaskUnlocked,
  _resetDeferredReassert,
} from '../../utils/backgroundLocation';

const AppStateMock = AppState as unknown as { currentState: string };

const flush = async () => {
  for (let i = 0; i < 6; i++) await Promise.resolve();
};

describe('reassertDispatchTaskUnlocked foreground gate', () => {
  beforeEach(() => {
    // Drop the previous case's listener BEFORE clearing mocks, so its
    // remove() call is not counted against this case.
    _resetDeferredReassert();
    jest.clearAllMocks();
    appStateListeners.length = 0;
    AppStateMock.currentState = 'active';
    Platform.OS = 'android';
    mockHasStarted.mockResolvedValue(true);
    mockGetBgPerms.mockResolvedValue({ status: 'granted' });
    mockStartUpdates.mockResolvedValue(undefined);
  });

  it('re-asserts the live task in place while the app is active', async () => {
    await reassertDispatchTaskUnlocked();
    expect(mockStartUpdates).toHaveBeenCalledTimes(1);
    expect(mockStartUpdates.mock.calls[0][0]).toBe('spinr-background-location');
    expect(mockRecordNonFatal).not.toHaveBeenCalled();
    expect(appStateListeners).toHaveLength(0);
  });

  it('skips the native call while backgrounded on Android and records no error', async () => {
    AppStateMock.currentState = 'background';
    await reassertDispatchTaskUnlocked();
    expect(mockStartUpdates).not.toHaveBeenCalled();
    expect(mockGetBgPerms).not.toHaveBeenCalled();
    expect(mockRecordNonFatal).not.toHaveBeenCalled();
    expect(appStateListeners).toHaveLength(1);
  });

  it('installs one listener across repeated backgrounded heals', async () => {
    AppStateMock.currentState = 'background';
    await reassertDispatchTaskUnlocked();
    await reassertDispatchTaskUnlocked();
    await reassertDispatchTaskUnlocked();
    expect(appStateListeners).toHaveLength(1);
    expect(mockStartUpdates).not.toHaveBeenCalled();
  });

  it('replays the deferred heal exactly once when the app becomes active', async () => {
    AppStateMock.currentState = 'background';
    await reassertDispatchTaskUnlocked();

    // A non-active transition is not a foreground.
    appStateListeners.forEach((cb) => cb('inactive'));
    await flush();
    expect(mockStartUpdates).not.toHaveBeenCalled();
    expect(mockRemove).not.toHaveBeenCalled();

    AppStateMock.currentState = 'active';
    appStateListeners.forEach((cb) => cb('active'));
    await flush();
    expect(mockStartUpdates).toHaveBeenCalledTimes(1);
    expect(mockRemove).toHaveBeenCalledTimes(1);

    // The listener is gone: a later foreground does not re-run the heal.
    appStateListeners.forEach((cb) => cb('active'));
    await flush();
    expect(mockStartUpdates).toHaveBeenCalledTimes(1);
  });

  it('does not defer when the task is not running at all', async () => {
    AppStateMock.currentState = 'background';
    mockHasStarted.mockResolvedValue(false);
    await reassertDispatchTaskUnlocked();
    expect(appStateListeners).toHaveLength(0);
    expect(mockStartUpdates).not.toHaveBeenCalled();
  });

  it('leaves iOS behaviour unchanged (no gate)', async () => {
    Platform.OS = 'ios';
    AppStateMock.currentState = 'background';
    await reassertDispatchTaskUnlocked();
    expect(mockStartUpdates).toHaveBeenCalledTimes(1);
    expect(appStateListeners).toHaveLength(0);
  });

  it('still reports a genuine foreground failure', async () => {
    mockStartUpdates.mockRejectedValue(new Error('boom'));
    await reassertDispatchTaskUnlocked();
    expect(mockRecordNonFatal).toHaveBeenCalledWith(
      expect.any(Error),
      expect.objectContaining({ location: 'reassert_failed' }),
    );
  });
});
