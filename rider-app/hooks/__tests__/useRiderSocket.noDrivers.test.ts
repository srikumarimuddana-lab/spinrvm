/**
 * useRiderSocket — "No drivers available" ending + calm searching (Phase 3,
 * .claude/plans/2026-09-25-dispatch-reoffer-and-search-window.md).
 *
 * Pins:
 *   - ride_cancelled with cancellation_type 'no_drivers_found' for the ride on
 *     screen raises the no-drivers prompt instead of the toast, then clears
 *     the ride and goes home exactly as before
 *   - any other cancel keeps today's toast and raises no prompt
 *   - a late ride_cancelled for an older ride (the 2026-09-23 cancel latch)
 *     still does nothing, even when it says no_drivers_found
 *   - driver_timeout no longer toasts; it only refetches
 *   - ride_status_changed(cancelled, reason no_drivers_found) carries the
 *     cause onto the local ride for the searching screen's status effect
 *
 * Drives the real hook through a mocked WebSocket (same harness as
 * useRiderSocket.reconnect.test.ts). The ride store is mocked; the
 * no-drivers store and signal helper are real.
 */
import { renderHook, act } from '@testing-library/react-native';
import { useRiderSocket } from '../useRiderSocket';
import { useRideStore } from '../../store/rideStore';
import { useNoDriversStore } from '../../store/noDriversStore';
import { showToast } from '../../store/toastStore';

jest.mock('@shared/api/client', () => ({
  ensureFreshToken: jest.fn().mockResolvedValue(undefined),
}));

jest.mock('../../store/rideStore', () => {
  function mockUseRideStore(selector: (s: any) => any) {
    return selector({ currentRide: { id: 'ride-new' }, currentDriver: null });
  }
  mockUseRideStore.getState = jest.fn();
  return { useRideStore: mockUseRideStore };
});

jest.mock('../../store/toastStore', () => ({ showToast: jest.fn() }));

jest.mock('@shared/store/authStore', () => ({
  registerLogoutCallback: jest.fn(),
  useAuthStore: Object.assign(
    (selector: (s: any) => any) => selector({ user: { id: 'user-abc' }, token: 'tok-xyz' }),
    { getState: () => ({ user: { id: 'user-abc' }, token: 'tok-xyz' }) },
  ),
}));

jest.mock('@shared/config', () => ({ API_URL: 'http://localhost:8000' }));
jest.mock('@shared/api/queryClient', () => ({
  queryClient: { setQueriesData: jest.fn() },
  queryKeys: { notifications: { list: ['notifications'] } },
}));
const mockRouter = { push: jest.fn(), replace: jest.fn() };
jest.mock('expo-router', () => ({ useRouter: () => mockRouter }));
jest.mock('react-native', () => ({
  AppState: { addEventListener: jest.fn(() => ({ remove: jest.fn() })) },
  Alert: { alert: jest.fn() },
  Vibration: { vibrate: jest.fn() },
}));

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
  constructor(public url: string) { instances.push(this); }
}
const instances: MockWebSocket[] = [];
(global as any).WebSocket = MockWebSocket;

const RIDE_NEW = {
  id: 'ride-new',
  status: 'searching',
  pickup_address: '100 Queen St',
  pickup_lat: 52.13,
  pickup_lng: -106.67,
  dropoff_address: '200 King St',
  dropoff_lat: 52.12,
  dropoff_lng: -106.65,
};

let state: any;
const mockFetchRide = jest.fn(() => Promise.resolve());
const mockClearRide = jest.fn();
const mockApply = jest.fn();

beforeEach(() => {
  instances.length = 0;
  jest.clearAllMocks();
  useNoDriversStore.setState({ enabled: true, prompt: null, _shownRideId: null });
  state = {
    currentRide: RIDE_NEW,
    _clearedRideId: null,
    fetchRide: mockFetchRide,
    updateDriverLocation: jest.fn(),
    applyRideStatusFromWS: mockApply,
    clearRide: mockClearRide,
    addChatMessage: jest.fn(),
    setWsConnected: jest.fn(),
  };
  (useRideStore.getState as jest.Mock).mockImplementation(() => state);
});

async function connectAndSend(message: Record<string, unknown>) {
  renderHook(() => useRiderSocket());
  await act(async () => { await Promise.resolve(); });
  await act(async () => {
    instances[0]?.onmessage?.({ data: JSON.stringify(message) });
  });
}

describe('useRiderSocket — no-drivers ending', () => {
  it('raises the prompt (no toast) for a no-drivers auto-cancel of the ride on screen', async () => {
    await connectAndSend({
      type: 'ride_cancelled',
      ride_id: 'ride-new',
      reason: 'No nearby drivers available. Your ride has been automatically cancelled.',
      cancellation_type: 'no_drivers_found',
    });

    expect(useNoDriversStore.getState().prompt).toEqual({
      rideId: 'ride-new',
      pickup: { address: '100 Queen St', lat: 52.13, lng: -106.67 },
      dropoff: { address: '200 King St', lat: 52.12, lng: -106.65 },
    });
    expect(showToast).not.toHaveBeenCalled();
    expect(mockClearRide).toHaveBeenCalled();
    expect(mockRouter.replace).toHaveBeenCalledWith('/(tabs)');
  });

  it("accepts the sweeper's reason-only payload", async () => {
    await connectAndSend({ type: 'ride_cancelled', ride_id: 'ride-new', reason: 'no_drivers_found' });
    expect(useNoDriversStore.getState().prompt?.rideId).toBe('ride-new');
    expect(showToast).not.toHaveBeenCalled();
  });

  it('keeps the toast and raises no prompt for a driver cancel', async () => {
    await connectAndSend({ type: 'ride_cancelled', ride_id: 'ride-new', reason: 'driver_cancelled' });
    expect(useNoDriversStore.getState().prompt).toBeNull();
    expect(showToast).toHaveBeenCalledWith(
      'Ride Cancelled',
      'Your driver has cancelled the ride. We apologize for the inconvenience.',
      'warning',
    );
    expect(mockClearRide).toHaveBeenCalled();
  });

  it('ignores a late no-drivers cancel for an older ride (cancel latch)', async () => {
    state._clearedRideId = 'ride-old';
    await connectAndSend({ type: 'ride_cancelled', ride_id: 'ride-old', cancellation_type: 'no_drivers_found' });
    expect(useNoDriversStore.getState().prompt).toBeNull();
    expect(showToast).not.toHaveBeenCalled();
    expect(mockClearRide).not.toHaveBeenCalled();
    expect(mockRouter.replace).not.toHaveBeenCalled();
  });

  it('ignores the echo of a cancel the rider already made on this ride', async () => {
    state.currentRide = null;
    state._clearedRideId = 'ride-new';
    await connectAndSend({ type: 'ride_cancelled', ride_id: 'ride-new', cancellation_type: 'rider_cancel' });
    expect(useNoDriversStore.getState().prompt).toBeNull();
    expect(showToast).not.toHaveBeenCalled();
    expect(mockRouter.replace).not.toHaveBeenCalled();
  });

  it('driver_timeout refetches without a toast', async () => {
    await connectAndSend({ type: 'driver_timeout', ride_id: 'ride-new', message: "Driver didn't respond." });
    expect(showToast).not.toHaveBeenCalled();
    expect(mockFetchRide).toHaveBeenCalledWith('ride-new');
  });

  describe('with the sheet switched off (rider_no_drivers_sheet_enabled false)', () => {
    beforeEach(() => useNoDriversStore.setState({ enabled: false }));

    it('keeps the original toast and raises no prompt for a no-drivers cancel', async () => {
      await connectAndSend({
        type: 'ride_cancelled',
        ride_id: 'ride-new',
        reason: 'auto_cancelled',
        cancellation_type: 'no_drivers_found',
      });
      expect(useNoDriversStore.getState().prompt).toBeNull();
      expect(showToast).toHaveBeenCalledWith('Ride Cancelled', 'No drivers were available. Please try again.', 'warning');
      expect(mockRouter.replace).toHaveBeenCalledWith('/(tabs)');
    });

    it('driver_timeout keeps its original toast', async () => {
      await connectAndSend({ type: 'driver_timeout', ride_id: 'ride-new', message: "Driver didn't respond." });
      expect(showToast).toHaveBeenCalledWith(
        'Driver Unavailable',
        'The driver did not respond in time. Finding another driver\u2026',
        'info',
      );
      expect(mockFetchRide).toHaveBeenCalledWith('ride-new');
    });
  });

  it('ride_status_changed(cancelled, no_drivers_found) carries the cause onto the ride', async () => {
    await connectAndSend({
      type: 'ride_status_changed',
      ride_id: 'ride-new',
      status: 'cancelled',
      reason: 'no_drivers_found',
      is_auto: true,
      version: 7,
    });
    expect(mockApply).toHaveBeenCalledWith('ride-new', 'cancelled', {
      version: 7,
      cancellation_type: 'no_drivers_found',
    });
  });

  it('ride_status_changed for other transitions is unchanged', async () => {
    await connectAndSend({ type: 'ride_status_changed', ride_id: 'ride-new', status: 'driver_accepted' });
    expect(mockApply).toHaveBeenCalledWith('ride-new', 'driver_accepted', undefined);
  });
});
