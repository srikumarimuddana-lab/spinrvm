import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { AppState } from 'react-native';
import { ensureFreshToken } from '@shared/api/client';
import { useDriverDashboard } from '../useDriverDashboard';

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
jest.mock('../../utils/backgroundLocation', () => Object.fromEntries([
  'startBackgroundLocation', 'stopBackgroundLocation', 'startGeofenceRecovery', 'stopGeofenceRecovery',
  'setBackgroundTripActive', 'updateBackgroundLocationCadence', 'recoverTripLocation',
].map(name => [name, jest.fn().mockResolvedValue(undefined)])));
jest.mock('../../utils/sensorIntegrity', () => ({ startSensorMonitoring: jest.fn(), stopSensorMonitoring: jest.fn() }));
jest.mock('../../utils/deviceIntegrity', () => ({ attestDeviceIntegrity: jest.fn() }));
jest.mock('../../utils/tripLocationRecorder', () => ({ tripLocationRecorder: Object.fromEntries([
  'flushPending', 'stopIdleSession', 'setIdleRecordingEnabled', 'startIdleSession',
].map(name => [name, jest.fn().mockResolvedValue(undefined)])) }));
jest.mock('expo-location', () => ({
  Accuracy: { High: 4, Balanced: 3 },
  getForegroundPermissionsAsync: jest.fn().mockResolvedValue({ status: 'denied' }),
  requestForegroundPermissionsAsync: jest.fn().mockResolvedValue({ status: 'denied' }),
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
function Dashboard() { useDriverDashboard(); return null; }
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
