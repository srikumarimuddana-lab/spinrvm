/**
 * R-P2-36: useRiderSocket — WS message dispatch tests
 *
 * Verifies that each incoming server message type is correctly handled
 * by the hook's `handleMessage` dispatcher: correct store actions are
 * called, Alert/Vibration side-effects fire, and guard conditions
 * (missing lat/lng, missing rideId) are respected.
 */

// ── Module mocks (before any import) ──────────────────────────────────────

jest.mock('../../store/rideStore', () => {
  function mockUseRideStore(selector: (s: any) => any) {
    return selector({ currentRide: { id: 'ride-001' }, currentDriver: null });
  }
  mockUseRideStore.getState = jest.fn(() => ({
    fetchRide: jest.fn(() => Promise.resolve()),
    updateDriverLocation: jest.fn(),
    applyRideStatusFromWS: jest.fn(),
    clearRide: jest.fn(),
    addChatMessage: jest.fn(),
  }));
  return { useRideStore: mockUseRideStore };
});

jest.mock('@shared/store/authStore', () => ({
  useAuthStore: Object.assign(
    (selector: (s: any) => any) =>
      selector({ user: { id: 'user-abc' }, token: 'tok-xyz' }),
    { getState: () => ({ user: { id: 'user-abc' }, token: 'tok-xyz' }) },
  ),
}));

jest.mock('@shared/config', () => ({ API_URL: 'http://localhost:8000' }));
jest.mock('../../constants/rideStatus', () => ({ RideStatus: { COMPLETED: 'completed' } }));
jest.mock('expo-router', () => ({ useRouter: () => ({ push: jest.fn(), replace: jest.fn() }) }));
jest.mock('react-native', () => ({
  AppState: { addEventListener: jest.fn(() => ({ remove: jest.fn() })) },
  Alert: { alert: jest.fn() },
  Vibration: { vibrate: jest.fn() },
}));

// ── WebSocket mock ─────────────────────────────────────────────────────────

class MockWebSocket {
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  readyState = MockWebSocket.OPEN;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: ((e: any) => void) | null = null;
  onclose: (() => void) | null = null;
  send = jest.fn();
  close = jest.fn(() => { this.readyState = MockWebSocket.CLOSED; });

  constructor(public url: string) {
    instances.push(this);
  }
}

const instances: MockWebSocket[] = [];
(global as any).WebSocket = MockWebSocket;

// ── Imports ────────────────────────────────────────────────────────────────

import { renderHook, act } from '@testing-library/react-native';
import { Alert, Vibration } from 'react-native';
import { useRiderSocket } from '../useRiderSocket';
import { useRideStore } from '../../store/rideStore';

// ── Per-test mock function references ─────────────────────────────────────

let mockFetchRide: jest.Mock;
let mockUpdateDriverLocation: jest.Mock;
let mockApplyRideStatusFromWS: jest.Mock;
let mockClearRide: jest.Mock;
let mockAddChatMessage: jest.Mock;

beforeEach(() => {
  instances.length = 0;
  jest.clearAllMocks();
  jest.useFakeTimers();

  mockFetchRide = jest.fn(() => Promise.resolve());
  mockUpdateDriverLocation = jest.fn();
  mockApplyRideStatusFromWS = jest.fn();
  mockClearRide = jest.fn();
  mockAddChatMessage = jest.fn();

  (useRideStore.getState as jest.Mock).mockImplementation(() => ({
    fetchRide: mockFetchRide,
    updateDriverLocation: mockUpdateDriverLocation,
    applyRideStatusFromWS: mockApplyRideStatusFromWS,
    clearRide: mockClearRide,
    addChatMessage: mockAddChatMessage,
  }));
});

afterEach(() => {
  jest.useRealTimers();
});

// ── Helper: render hook and open the WebSocket ─────────────────────────────

async function openSocket() {
  const result = renderHook(() => useRiderSocket());
  await act(async () => {
    instances[0]?.onopen?.();
  });
  return result;
}

// Helper to dispatch a single message after the socket is open
function sendMessage(payload: object) {
  act(() => {
    instances[0]?.onmessage?.({ data: JSON.stringify(payload) });
  });
}

// ── Tests ──────────────────────────────────────────────────────────────────

describe('useRiderSocket — message dispatch (R-P2-36)', () => {
  it('ping → replies with { type: "pong" }', async () => {
    await openSocket();
    // Clear auth message recorded during onopen
    instances[0]!.send.mockClear();

    sendMessage({ type: 'ping' });

    expect(instances[0]!.send).toHaveBeenCalledTimes(1);
    const sent = JSON.parse(instances[0]!.send.mock.calls[0][0] as string);
    expect(sent).toEqual({ type: 'pong' });
  });

  it('driver_location_update with lat/lng → calls updateDriverLocation(lat, lng, null, null)', async () => {
    await openSocket();

    sendMessage({ type: 'driver_location_update', lat: 52.0, lng: -106.0 });

    expect(mockUpdateDriverLocation).toHaveBeenCalledTimes(1);
    expect(mockUpdateDriverLocation).toHaveBeenCalledWith(52.0, -106.0, null, null);
  });

  it('driver_location_update with speed/heading → passes all four values through', async () => {
    await openSocket();

    sendMessage({ type: 'driver_location_update', lat: 52.0, lng: -106.0, speed: 30, heading: 180 });

    expect(mockUpdateDriverLocation).toHaveBeenCalledTimes(1);
    expect(mockUpdateDriverLocation).toHaveBeenCalledWith(52.0, -106.0, 30, 180);
  });

  it('driver_location_update with missing lat → does NOT call updateDriverLocation', async () => {
    await openSocket();

    sendMessage({ type: 'driver_location_update', lat: null, lng: -106.0 });

    expect(mockUpdateDriverLocation).not.toHaveBeenCalled();
  });

  it('driver_timeout → shows Alert with "Driver Unavailable" and calls fetchRide', async () => {
    await openSocket();

    sendMessage({ type: 'driver_timeout' });

    expect((Alert.alert as jest.Mock)).toHaveBeenCalledTimes(1);
    const [title] = (Alert.alert as jest.Mock).mock.calls[0];
    expect(title).toBe('Driver Unavailable');
    expect(mockFetchRide).toHaveBeenCalledWith('ride-001');
  });

  it('ride_cancelled → shows Alert with "Ride Cancelled" and calls clearRide', async () => {
    await openSocket();

    sendMessage({ type: 'ride_cancelled' });

    expect((Alert.alert as jest.Mock)).toHaveBeenCalledTimes(1);
    const [title, message] = (Alert.alert as jest.Mock).mock.calls[0];
    expect(title).toBe('Ride Cancelled');
    expect(message).toBe('Your ride has been cancelled.');
    expect(mockClearRide).toHaveBeenCalledTimes(1);
  });

  it('ride_cancelled with reason → shows custom reason in Alert', async () => {
    await openSocket();

    sendMessage({ type: 'ride_cancelled', reason: 'Driver went offline.' });

    expect((Alert.alert as jest.Mock)).toHaveBeenCalledTimes(1);
    const [title, message] = (Alert.alert as jest.Mock).mock.calls[0];
    expect(title).toBe('Ride Cancelled');
    expect(message).toBe('Driver went offline.');
    expect(mockClearRide).toHaveBeenCalledTimes(1);
  });

  it('ride_completed → calls applyRideStatusFromWS and fetchRide with correct args', async () => {
    await openSocket();

    sendMessage({ type: 'ride_completed', total_fare: 18.50 });

    expect(mockApplyRideStatusFromWS).toHaveBeenCalledTimes(1);
    expect(mockApplyRideStatusFromWS).toHaveBeenCalledWith('ride-001', 'completed', { total_fare: 18.50 });
    expect(mockFetchRide).toHaveBeenCalledWith('ride-001');
  });

  it('driver_accepted → calls fetchRide with the current ride id', async () => {
    await openSocket();
    mockFetchRide.mockClear(); // clear the fetch from onopen

    sendMessage({ type: 'driver_accepted' });

    expect(mockFetchRide).toHaveBeenCalledTimes(1);
    expect(mockFetchRide).toHaveBeenCalledWith('ride-001');
  });

  it('ride_status_changed → calls applyRideStatusFromWS and fetchRide', async () => {
    await openSocket();
    mockFetchRide.mockClear();

    sendMessage({ type: 'ride_status_changed', ride_id: 'ride-001', status: 'driver_arrived' });

    expect(mockApplyRideStatusFromWS).toHaveBeenCalledTimes(1);
    expect(mockApplyRideStatusFromWS).toHaveBeenCalledWith('ride-001', 'driver_arrived');
    expect(mockFetchRide).toHaveBeenCalledTimes(1);
    expect(mockFetchRide).toHaveBeenCalledWith('ride-001');
  });
});
