import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { AppState } from 'react-native';
import { ensureFreshToken } from '@shared/api/client';
import { useDriverDashboard } from '../useDriverDashboard';
import * as Location from 'expo-location';
import { startBackgroundLocation, TRIP_CADENCE, IDLE_CADENCE } from '../../utils/backgroundLocation';

const mockAppListeners = new Set<(state: string) => void>();
const mockNetListeners = new Set<(state: object) => void>();
jest.mock('react-native', () => ({
  AppState: { currentState: 'active', addEventListener: (_: string, cb: (state: string) => void) => {
    mockAppListeners.add(cb);
    return { remove: () => mockAppListeners.delete(cb) };
  } },
  Platform: { OS: 'android' }, Dimensions: { get: () => ({ height: 800 }) },
  Animated: { Value: class { setValue() {} }, parallel: () => ({ start() {} }),
    timing: () => ({}), spring: () => ({}) }, Vibration: { vibrate: jest.fn() }, Linking: {},
}), { virtual: true });
jest.mock('@react-native-community/netinfo', () => ({
  addEventListener: (cb: (state: object) => void) => {
    mockNetListeners.add(cb);
    return () => mockNetListeners.delete(cb);
  },
}), { virtual: true });
const mockAuth = { user: { id: 'driver-1' }, token: 'token', driver: { is_online: true },
  refreshProfile: jest.fn(), updateDriverStatus: jest.fn() };
const mockDriver = { rideState: 'idle', incomingRide: null, activeRide: null,
  fetchActiveRide: jest.fn().mockResolvedValue(undefined), hydrateDriverRideState: jest.fn().mockResolvedValue(undefined),
  fetchEarnings: jest.fn(), applyDriverConfig: jest.fn(), resetRideState: jest.fn(),
  setIncomingRide: jest.fn(), acceptRide: jest.fn(), declineRide: jest.fn() };
jest.mock('@shared/store/authStore', () => ({ useAuthStore: Object.assign(() => mockAuth, { getState: () => mockAuth }) }));
jest.mock('../../store/driverStore', () => ({ useDriverStore: Object.assign(() => mockDriver, { getState: () => mockDriver }) }));
jest.mock('../../store/alertPrefsStore', () => ({ useAlertPrefsStore: { getState: () => ({}) } }));
jest.mock('@shared/api/client', () => ({ __esModule: true,
  default: { get: jest.fn().mockResolvedValue({ data: {} }), post: jest.fn().mockResolvedValue({ data: {} }) },
  ensureFreshToken: jest.fn().mockResolvedValue(undefined), getApiErrorMessage: jest.fn(),
}));
jest.mock('@shared/hooks/queries', () => ({ useDriverConfig: () => ({ data: undefined }) }));
// useDriverDashboard now imports queryClient/queryKeys directly (for the
// WS `new_notification` cache merge) — shared/api/queryClient.ts has a
// module-load-time AppState.addEventListener side effect (RN focusManager
// wiring) that this file's minimal virtual `react-native` mock above isn't
// built to support; mock it out the same way @shared/hooks/queries is
// already mocked above, since real TanStack Query behavior isn't under
// test here.
jest.mock('@shared/api/queryClient', () => ({
  queryClient: { setQueriesData: jest.fn() },
  queryKeys: { notifications: { list: ['notifications', 'list'] } },
}));
jest.mock('@shared/config', () => ({ API_URL: 'https://example.test' }));
jest.mock('@shared/config/spinr.config', () => ({ __esModule: true, default: { api: { baseUrl: 'https://example.test' } } }));
jest.mock('@shared/services/firebase', () => ({ onForegroundMessage: () => jest.fn() }));
jest.mock('@shared/services/errorReporting', () => ({ captureException: jest.fn() }));
jest.mock('expo-router', () => ({ router: { push: jest.fn() } }), { virtual: true });
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn().mockResolvedValue(null), setItem: jest.fn().mockResolvedValue(undefined),
  removeItem: jest.fn().mockResolvedValue(undefined),
}), { virtual: true });
jest.mock('../../components/AlertDialog', () => ({ showAlert: jest.fn() }));
jest.mock('../useToast', () => ({ showToast: jest.fn() }));
jest.mock('../../i18n', () => ({ tKey: (key: string) => key }));
jest.mock('../useRideOfferSound', () => {
  const sound = { play: jest.fn(), stop: jest.fn() };
  return { useRideOfferSound: () => sound, setOfferSoundUrl: jest.fn() };
});
jest.mock('../../services/notifeeService', () => ({ dismissRideOfferNotification: jest.fn().mockResolvedValue(undefined) }));
jest.mock('../../services/pendingRideOffer', () => ({ consumePendingRideOffer: jest.fn().mockResolvedValue(null) }));
jest.mock('../../utils/backgroundLocation', () => ({ ...Object.fromEntries([
  'startBackgroundLocation', 'stopBackgroundLocation', 'startGeofenceRecovery', 'stopGeofenceRecovery',
  'setBackgroundTripActive', 'updateBackgroundLocationCadence', 'recoverTripLocation',
].map(name => [name, jest.fn().mockResolvedValue(true)])), TRIP_CADENCE: { timeInterval: 4000 }, IDLE_CADENCE: { timeInterval: 30000 } }));
jest.mock('../../utils/sensorIntegrity', () => ({ startSensorMonitoring: jest.fn(), stopSensorMonitoring: jest.fn(), checkMovementConsistency: () => ({ consistent: true }) }));
jest.mock('../../utils/deviceIntegrity', () => ({ attestDeviceIntegrity: jest.fn() }));
jest.mock('../../utils/tripLocationRecorder', () => ({ tripLocationRecorder: Object.fromEntries([
  'flushPending', 'stopIdleSession', 'setIdleRecordingEnabled', 'startIdleSession', 'startRide',
].map(name => [name, jest.fn().mockResolvedValue(undefined)])) }));
jest.mock('expo-location', () => ({
  Accuracy: { High: 4, Balanced: 3 },
  getForegroundPermissionsAsync: jest.fn().mockResolvedValue({ status: 'denied' }),
  requestForegroundPermissionsAsync: jest.fn().mockResolvedValue({ status: 'denied' }),
  hasServicesEnabledAsync: jest.fn().mockResolvedValue(true),
  getLastKnownPositionAsync: jest.fn().mockResolvedValue(null),
  getCurrentPositionAsync: jest.fn(),
  getBackgroundPermissionsAsync: jest.fn().mockResolvedValue({ status: 'granted' }),
  watchPositionAsync: jest.fn().mockResolvedValue({ remove: jest.fn() }),
}), { virtual: true });

class Socket {
  static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
  static instances: Socket[] = [];
  readyState = Socket.CONNECTING;
  onopen?: () => void;
  onclose?: (event: { code: number }) => void;
  onmessage?: (event: { data: string }) => void;
  send = jest.fn();
  close = jest.fn(() => { this.readyState = Socket.CLOSING; });
  constructor(public url: string) { Socket.instances.push(this); }
  authenticate() {
    this.readyState = Socket.OPEN;
    this.onopen?.();
    this.onmessage?.({ data: JSON.stringify({ type: 'auth_success' }) });
  }
  finishClose() { this.readyState = Socket.CLOSED; this.onclose?.({ code: 1001 }); }
}
let mounted: TestRenderer.ReactTestRenderer | undefined;
const originalSocket = global.WebSocket;
let dashboard: ReturnType<typeof useDriverDashboard>;
function Dashboard() { dashboard = useDriverDashboard(); return null; }
async function mount() { await act(async () => { mounted = TestRenderer.create(React.createElement(Dashboard)); }); }
async function appState(state: string) {
  await act(async () => { (AppState as any).currentState = state; mockAppListeners.forEach(cb => cb(state)); });
}
async function advance(ms: number) { await act(async () => { jest.advanceTimersByTime(ms); }); }
async function networkUp() {
  await act(async () => { mockNetListeners.forEach(cb => cb({ isConnected: true, isInternetReachable: true })); });
}
beforeEach(() => {
  jest.useFakeTimers(); jest.clearAllMocks();
  mockAuth.user = { id: 'driver-1' };
  (AppState as any).currentState = 'active';
  Socket.instances = [];
  global.WebSocket = Socket as any;
  (ensureFreshToken as jest.Mock).mockReset().mockResolvedValue(undefined);
  (Location.getForegroundPermissionsAsync as jest.Mock).mockResolvedValue({ status: 'denied' });
  (Location.getLastKnownPositionAsync as jest.Mock).mockResolvedValue(null);
  (Location.getBackgroundPermissionsAsync as jest.Mock).mockReset().mockResolvedValue({ status: 'granted' });
  mockAuth.driver.is_online = true;
  mockDriver.rideState = 'idle';
  mockDriver.activeRide = null;
});
afterEach(async () => {
  await act(async () => { mounted?.unmount(); }); mounted = undefined;
  jest.clearAllTimers(); jest.useRealTimers(); global.WebSocket = originalSocket;
  mockAppListeners.clear(); mockNetListeners.clear();
});

it('does not reconnect after the deliberate background close or a background network event', async () => {
  await mount();
  await act(async () => Socket.instances[0].authenticate());
  await appState('background'); await advance(3000);
  expect(Socket.instances[0].close).toHaveBeenCalled();
  await act(async () => Socket.instances[0].finishClose());
  await advance(2000); await networkUp();
  expect(Socket.instances).toHaveLength(1);
  await appState('active'); await networkUp();
  expect(Socket.instances).toHaveLength(2);
});

it('does not open a socket when token refresh completes after unmount', async () => {
  let resolve!: () => void;
  (ensureFreshToken as jest.Mock).mockReturnValue(new Promise<void>(done => { resolve = done; }));
  await mount();
  await act(async () => { mounted!.unmount(); }); mounted = undefined;
  await act(async () => resolve());
  expect(Socket.instances).toHaveLength(0);
});

it('does not open a socket when token refresh completes in background, then resumes once', async () => {
  let resolve!: () => void;
  (ensureFreshToken as jest.Mock).mockReturnValueOnce(new Promise<void>(done => { resolve = done; }));
  await mount(); await appState('background');
  await act(async () => resolve());
  expect(Socket.instances).toHaveLength(0);
  await appState('active'); await networkUp();
  expect(Socket.instances).toHaveLength(1);
});

it('shows reconnecting before token refresh finishes after a background close', async () => {
  let resolve!: () => void;
  await mount();
  await act(async () => Socket.instances[0].authenticate());
  await appState('background'); await advance(3000);
  await act(async () => Socket.instances[0].finishClose());
  expect(dashboard.connectionState).toBe('disconnected');
  (ensureFreshToken as jest.Mock).mockReturnValueOnce(new Promise<void>(done => { resolve = done; }));
  await appState('active');
  expect(dashboard.connectionState).toBe('reconnecting');
  expect(Socket.instances).toHaveLength(1);
  await act(async () => resolve());
  expect(Socket.instances).toHaveLength(2);
});

it('manual retry opens a new socket when token refresh is still hung', async () => {
  await mount();
  await act(async () => Socket.instances[0].authenticate());
  await appState('background'); await advance(3000);
  await act(async () => Socket.instances[0].finishClose());
  (ensureFreshToken as jest.Mock).mockReturnValue(new Promise<void>(() => {}));
  await appState('active');
  expect(dashboard.connectionState).toBe('reconnecting');
  expect(Socket.instances).toHaveLength(1);
  (ensureFreshToken as jest.Mock).mockResolvedValue(undefined);
  await act(async () => { dashboard.retryConnection(); });
  expect(Socket.instances).toHaveLength(2);
});

it('opens a socket after the token-refresh cap if refresh never returns', async () => {
  await mount();
  await act(async () => Socket.instances[0].authenticate());
  await appState('background'); await advance(3000);
  await act(async () => Socket.instances[0].finishClose());
  (ensureFreshToken as jest.Mock).mockReturnValue(new Promise<void>(() => {}));
  await appState('active');
  expect(Socket.instances).toHaveLength(1);
  await advance(15_000);
  expect(Socket.instances).toHaveLength(2);
});

it('does not abort an in-flight handshake when retry is tapped', async () => {
  await mount();
  await act(async () => Socket.instances[0].authenticate());
  const live = Socket.instances[0];
  await act(async () => { dashboard.retryConnection(); });
  expect(live.close).not.toHaveBeenCalled();
  expect(Socket.instances).toHaveLength(1);
});

it('retains the socket during a short background transition and iOS inactive state', async () => {
  await mount();
  await act(async () => Socket.instances[0].authenticate());
  await appState('background'); await advance(1000); await appState('active'); await advance(3000);
  await appState('inactive'); await advance(3000);
  expect(Socket.instances).toHaveLength(1);
  expect(Socket.instances[0].close).not.toHaveBeenCalled();
});

it('resumes when the current refresh check completes and ignores the obsolete attempt', async () => {
  let resolve!: () => void;
  (ensureFreshToken as jest.Mock).mockReturnValueOnce(new Promise<void>(done => { resolve = done; }));
  await mount(); await appState('background'); await appState('active');
  expect(Socket.instances).toHaveLength(1);
  await act(async () => resolve());
  expect(Socket.instances).toHaveLength(1);
});

it('keeps foreground network failures retryable and ignores messages from a replaced socket', async () => {
  await mount();
  const old = Socket.instances[0];
  await act(async () => old.authenticate());
  await act(async () => old.finishClose());
  await advance(2000);
  expect(Socket.instances).toHaveLength(2);
  await act(async () => old.onmessage?.({ data: JSON.stringify({ type: 'ride_cancelled' }) }));
  expect(mockDriver.resetRideState).not.toHaveBeenCalled();
});


it('creates only one socket when obsolete and current attempts share the pending auth refresh', async () => {
  let resolve!: () => void;
  (ensureFreshToken as jest.Mock).mockReturnValue(new Promise<void>(done => { resolve = done; }));
  await mount(); await appState('background'); await appState('active'); await networkUp();
  expect(Socket.instances).toHaveLength(0);
  await act(async () => resolve());
  expect(Socket.instances).toHaveLength(1);
});

it('invalidates an old account attempt when the dashboard changes users', async () => {
  let resolve!: () => void;
  (ensureFreshToken as jest.Mock).mockReturnValueOnce(new Promise<void>(done => { resolve = done; }));
  await mount();
  mockAuth.user = { id: 'driver-2' };
  await act(async () => { mounted!.update(React.createElement(Dashboard)); });
  expect(Socket.instances).toHaveLength(1);
  expect(Socket.instances[0].url).toContain('/ws/driver/driver-2');
  await act(async () => resolve());
  expect(Socket.instances).toHaveLength(1);
});

const fix = (timestamp = Date.now(), latitude = 50.45) => ({ timestamp,
  coords: { latitude, longitude: -104.6, accuracy: 8, heading: 90, speed: 10, altitude: 0, altitudeAccuracy: 0 } });

it('feeds a fresh resume measurement to the marker using its capture time', async () => {
  (Location.getForegroundPermissionsAsync as jest.Mock).mockResolvedValue({ status: 'granted' });
  (Location.getCurrentPositionAsync as jest.Mock).mockResolvedValue(fix());
  await mount();
  const received = jest.fn(); dashboard.markerFixFeed.subscribe(received);
  await appState('background'); await advance(60_000);
  const current = fix(Date.now() - 1000, 50.46);
  (Location.getCurrentPositionAsync as jest.Mock).mockResolvedValue(current);
  await appState('active');
  expect(received).toHaveBeenLastCalledWith(expect.objectContaining({ latitude: 50.46, timestampMs: current.timestamp }));
});

it('does not create fresh marker measurements from an old cached coordinate', async () => {
  await mount();
  const received = jest.fn(); dashboard.markerFixFeed.subscribe(received);
  const callback = (Location.watchPositionAsync as jest.Mock).mock.calls.at(-1)[1];
  await act(async () => callback(fix()));
  received.mockClear();
  await advance(7500);
  expect(received).not.toHaveBeenCalled();
});

it('does not label a stale last-known position healthy after resume failure', async () => {
  (Location.getForegroundPermissionsAsync as jest.Mock).mockResolvedValue({ status: 'granted' });
  (Location.getCurrentPositionAsync as jest.Mock).mockRejectedValue(new Error('GPS unavailable'));
  (Location.getLastKnownPositionAsync as jest.Mock).mockResolvedValue(fix(Date.now() - 120_000));
  await mount();
  expect(dashboard.locationStatus).toBe('unavailable');
});

it('ensures native tracking for an already-online mount and foreground resume', async () => {
  await mount();
  expect(startBackgroundLocation).toHaveBeenCalledWith(IDLE_CADENCE, expect.any(Function));
  (startBackgroundLocation as jest.Mock).mockClear();
  await appState('background'); await appState('active');
  expect(startBackgroundLocation).toHaveBeenCalledTimes(1);
});

it('restores active-trip native tracking at trip cadence', async () => {
  mockDriver.rideState = 'trip_in_progress';
  mockDriver.activeRide = { ride: { id: 'ride-1' } } as any;
  await mount();
  expect(startBackgroundLocation).toHaveBeenCalledWith(TRIP_CADENCE, expect.any(Function));
});

it('does not restart or prompt when offline or background permission is denied', async () => {
  mockAuth.driver.is_online = false;
  await mount(); await appState('background'); await appState('active');
  expect(startBackgroundLocation).not.toHaveBeenCalled();
  await act(async () => mounted!.unmount()); mounted = undefined;
  mockAuth.driver.is_online = true;
  (Location.getBackgroundPermissionsAsync as jest.Mock).mockResolvedValue({ status: 'denied' });
  await mount(); await appState('background'); await appState('active');
  expect(startBackgroundLocation).not.toHaveBeenCalled();
});

it('cancels recovery when permission lookup finishes after unmount', async () => {
  let resolve!: (value: object) => void;
  (Location.getBackgroundPermissionsAsync as jest.Mock).mockReturnValue(new Promise(done => { resolve = done; }));
  await mount();
  await act(async () => mounted!.unmount()); mounted = undefined;
  await act(async () => resolve({ status: 'granted' }));
  expect(startBackgroundLocation).not.toHaveBeenCalled();
});

it('replaces the foreground watcher on resume and removes a late subscription', async () => {
  let resolve!: (value: object) => void;
  const remove = jest.fn();
  (Location.watchPositionAsync as jest.Mock).mockReturnValueOnce(new Promise(done => { resolve = done; }));
  await mount(); await appState('background'); await appState('active');
  expect(Location.watchPositionAsync).toHaveBeenCalledTimes(2);
  await act(async () => resolve({ remove }));
  expect(remove).toHaveBeenCalledTimes(1);
});

it('does not let a cached watcher callback rewind the fresh resume position', async () => {
  (Location.getForegroundPermissionsAsync as jest.Mock).mockResolvedValue({ status: 'granted' });
  const fresh = fix();
  (Location.getCurrentPositionAsync as jest.Mock).mockResolvedValue(fresh);
  await mount();
  const received = jest.fn(); dashboard.markerFixFeed.subscribe(received);
  const callback = (Location.watchPositionAsync as jest.Mock).mock.calls.at(-1)[1];
  await act(async () => callback(fix(fresh.timestamp - 10_000, 50.44)));
  expect(received).not.toHaveBeenCalled();
  expect(dashboard.location?.coords.latitude).toBe(fresh.coords.latitude);
});
