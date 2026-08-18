/**
 * Unit tests for lib/androidAuto/register.ts — the Android Auto orchestration.
 * The native @iternio module, the surface component, the driver store, and the
 * zoom camera store are mocked, so these assert the JS contract without a head
 * unit: ONE persistent MapTemplate, state-driven map buttons (zoom-only idle,
 * nav+zoom while navigating), the state-driven header progress action (Arrived /
 * Complete / phone-start), and the Lyft-style ride-offer alert (Accept/Decline).
 *
 * Vars referenced inside jest.mock factories are `mock`-prefixed (jest hoisting).
 */
import { Linking, Platform } from 'react-native';

const mockAcceptRide = jest.fn().mockResolvedValue(undefined);
const mockDeclineRide = jest.fn().mockResolvedValue(undefined);
const mockArriveAtPickup = jest.fn().mockResolvedValue(undefined);
const mockCompleteRide = jest.fn().mockResolvedValue(undefined);

const mockState: Record<string, unknown> = {
  rideState: 'idle',
  activeRide: null,
  incomingRide: null,
  countdownSeconds: 15,
  acceptRide: mockAcceptRide,
  declineRide: mockDeclineRide,
  arriveAtPickup: mockArriveAtPickup,
  completeRide: mockCompleteRide,
};
const mockSubscribe = jest.fn((_fn?: () => void) => jest.fn());
const mockListeners: Record<string, (...a: unknown[]) => void> = {};
let mockConnected = true;

type MockTpl = {
  config: Record<string, unknown>;
  setRootTemplate: jest.Mock;
  setMapButtons: jest.Mock;
  setHeaderActions: jest.Mock;
  showAlert: jest.Mock;
  dismissAlert: jest.Mock;
};
const mockTemplates: MockTpl[] = [];

const mockZoomIn = jest.fn();
const mockZoomOut = jest.fn();
const mockReset = jest.fn();
const mockRecenter = jest.fn();
const mockPan = jest.fn();

const mockStartCarLocation = jest.fn().mockResolvedValue('started');
const mockStopCarLocation = jest.fn().mockResolvedValue(undefined);
const mockStartCarSession = jest.fn().mockResolvedValue(undefined);
const mockStopCarSession = jest.fn();

jest.mock('../../../store/driverStore', () => ({
  useDriverStore: {
    getState: () => mockState,
    subscribe: (fn: () => void) => mockSubscribe(fn),
  },
}));

jest.mock('../carSurface', () => ({ CarMapSurface: () => null }));

// Not a location test. Mocking it also keeps the real module's transitive
// utils/backgroundLocation import (SQLite recorder, Firebase App Check) out of
// this suite entirely.
jest.mock('../carLocationTask', () => ({
  startCarLocationService: (...a: unknown[]) => mockStartCarLocation(...a),
  stopCarLocationService: (...a: unknown[]) => mockStopCarLocation(...a),
}));

// Its own suite covers the bootstrap; here we only care that register wires the
// start/stop to connect/disconnect, and that a failing bootstrap cannot take
// the template down with it.
jest.mock('../carSession', () => ({
  startCarSession: (...a: unknown[]) => mockStartCarSession(...a),
  stopCarSession: (...a: unknown[]) => mockStopCarSession(...a),
}));

jest.mock('../carMapCamera', () => ({
  useCarMapCamera: {
    getState: () => ({
      zoomIn: mockZoomIn,
      zoomOut: mockZoomOut,
      reset: mockReset,
      recenter: mockRecenter,
      pan: mockPan,
    }),
  },
}));

jest.mock('@iternio/react-native-auto-play', () => {
  class MapTemplate {
    config: Record<string, unknown>;
    setRootTemplate = jest.fn();
    setMapButtons = jest.fn();
    setHeaderActions = jest.fn();
    showAlert = jest.fn();
    dismissAlert = jest.fn();
    constructor(config: Record<string, unknown>) {
      this.config = config;
      mockTemplates.push(this as unknown as MockTpl);
    }
  }
  return {
    HybridAutoPlay: {
      addListener: jest.fn((evt: string, cb: (...a: unknown[]) => void) => {
        mockListeners[evt] = cb;
        return { remove: jest.fn() };
      }),
      isConnected: () => mockConnected,
    },
    MapTemplate,
    Flag: { Primary: 1, Persistent: 2, Default: 4 },
  };
});

// eslint-disable-next-line import/first -- must follow the jest.mock() calls above
import registerAutoPlay from '../register';

const navRide = () =>
  ({
    ride: {
      id: 'r1',
      pickup_address: '101 Pickup St',
      dropoff_address: '202 Dropoff Ave',
      pickup_lat: 52.13,
      pickup_lng: -106.67,
      dropoff_lat: 52.2,
      dropoff_lng: -106.6,
      total_fare: '14.50',
      distance_km: 3.2,
      duration_minutes: 8,
      planned_route_polyline: [[52.13, -106.67], [52.2, -106.6]],
    },
    rider: { id: 'rider', first_name: 'Alex', rating: 4.9 },
    vehicle_type: { id: 'vt', name: 'Standard' },
  }) as unknown;

const lastTpl = () => mockTemplates[mockTemplates.length - 1];
const lastMapButtons = (t: MockTpl) =>
  t.setMapButtons.mock.calls.at(-1)?.[0] as { onPress: () => void }[];
const lastHeader = (t: MockTpl) =>
  t.setHeaderActions.mock.calls.at(-1)?.[0] as
    | { android?: { title: string; onPress: () => void }[] }
    | undefined;

beforeEach(() => {
  (Platform as unknown as { OS: string }).OS = 'android';
  jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined as never);
  for (const k of Object.keys(mockListeners)) delete mockListeners[k];
  mockTemplates.length = 0;
  mockSubscribe.mockClear();
  mockZoomIn.mockClear();
  mockZoomOut.mockClear();
  mockReset.mockClear();
  mockAcceptRide.mockClear();
  mockDeclineRide.mockClear();
  mockArriveAtPickup.mockClear();
  mockCompleteRide.mockClear();
  mockStartCarLocation.mockClear();
  mockStopCarLocation.mockClear();
  mockStartCarSession.mockClear();
  mockStopCarSession.mockClear();
  mockConnected = true;
  mockState.rideState = 'idle';
  mockState.activeRide = null;
  mockState.incomingRide = null;
});

afterEach(() => {
  // Every test here registers a fresh listener set and most connect a head unit;
  // without a matching disconnect the session's timers (cold-start poll,
  // post-connect chrome refresh) stay armed and fire into the NEXT test — or
  // after the run, which is what "Cannot log after tests are done" was.
  try {
    mockListeners.didDisconnect?.();
  } catch {
    /* a suite that never registered has nothing to tear down */
  }
  jest.restoreAllMocks();
});

it('does nothing on non-Android platforms', () => {
  (Platform as unknown as { OS: string }).OS = 'ios';
  registerAutoPlay();
  expect(mockListeners.didConnect).toBeUndefined();
});

it('creates ONE live MapTemplate with zoom-only buttons and no header action when idle', () => {
  registerAutoPlay();
  mockListeners.didConnect();

  const t = lastTpl();
  expect(mockTemplates).toHaveLength(1);
  expect(t.setRootTemplate).toHaveBeenCalled();

  const buttons = lastMapButtons(t);
  // Recenter is always present — once the head unit can pan (onDidPan), a
  // driver who has dragged away needs a way back regardless of ride state.
  expect(buttons).toHaveLength(3); // recenter + zoom out + zoom in
  buttons[0].onPress(); // recenter
  buttons[1].onPress(); // zoom out
  buttons[2].onPress(); // zoom in
  expect(mockRecenter).toHaveBeenCalledTimes(1);
  expect(mockZoomOut).toHaveBeenCalledTimes(1);
  expect(mockZoomIn).toHaveBeenCalledTimes(1);

  expect(lastHeader(t)).toBeUndefined(); // no progress action when idle
  expect(mockReset).toHaveBeenCalled();
  expect(mockSubscribe).toHaveBeenCalled();
});

it('adds a single Navigate hand-off (before zoom) and a Complete action while in trip', () => {
  mockState.rideState = 'trip_in_progress';
  mockState.activeRide = navRide();
  registerAutoPlay();
  mockListeners.didConnect();

  const t = lastTpl();
  const buttons = lastMapButtons(t);
  // Exactly Android Auto's action-strip cap. If this ever needs a fifth entry,
  // something has to be dropped rather than appended.
  expect(buttons).toHaveLength(4); // navigate, recenter, zoom out, zoom in

  buttons[0].onPress(); // Navigate → Google hand-off (platform default)
  expect(Linking.openURL).toHaveBeenCalledWith(
    expect.stringContaining('google.navigation:q=52.2,-106.6'),
  );

  const header = lastHeader(t);
  expect(header?.android?.[0].title).toBe('Complete trip');
  header?.android?.[0].onPress();
  expect(mockCompleteRide).toHaveBeenCalledWith('r1');
});

it('shows an Arrived header action while heading to pickup', () => {
  mockState.rideState = 'navigating_to_pickup';
  mockState.activeRide = navRide();
  registerAutoPlay();
  mockListeners.didConnect();

  const header = lastHeader(lastTpl());
  expect(header?.android?.[0].title).toBe('Arrived');
  header?.android?.[0].onPress();
  expect(mockArriveAtPickup).toHaveBeenCalledWith('r1');
});

it('routes the OTP start-trip to the phone at pickup (no completeRide from the car)', () => {
  mockState.rideState = 'arrived_at_pickup';
  mockState.activeRide = navRide();
  registerAutoPlay();
  mockListeners.didConnect();

  const header = lastHeader(lastTpl());
  expect(header?.android?.[0].title).toBe('Start on phone');
  header?.android?.[0].onPress(); // shows an info alert, never starts the trip
  expect(lastTpl().showAlert).toHaveBeenCalled();
  expect(mockCompleteRide).not.toHaveBeenCalled();
});

it('shows a high-priority ride-offer alert with working Accept / Decline', () => {
  mockState.rideState = 'ride_offered';
  mockState.incomingRide = {
    ride_id: 'r1',
    pickup_address: '101 Pickup St',
    fare: '14.50',
    duration_minutes: 8,
    rider_name: 'Alex',
    rider_rating: 4.9,
    surge_multiplier: 1.5,
  };
  registerAutoPlay();
  mockListeners.didConnect();

  const t = lastTpl();
  expect(t.showAlert).toHaveBeenCalledTimes(1);
  const alert = t.showAlert.mock.calls[0][0];
  expect(alert.priority).toBe('high');
  expect(alert.title.text).toContain('Alex');
  expect(alert.primaryAction.title).toBe('Accept');
  expect(alert.secondaryAction.title).toBe('Decline');

  alert.primaryAction.onPress();
  expect(mockAcceptRide).toHaveBeenCalledWith('r1');
  alert.secondaryAction.onPress();
  expect(mockDeclineRide).toHaveBeenCalledWith('r1');
});

it('does not re-show the offer alert for the same ride on repeated ticks', () => {
  mockState.rideState = 'ride_offered';
  mockState.incomingRide = { ride_id: 'r1', pickup_address: 'x', fare: '9.00' };
  registerAutoPlay();
  mockListeners.didConnect();
  const t = lastTpl();
  const apply = mockSubscribe.mock.calls[0][0] as () => void;
  apply(); // same offer → suppressed (iternio re-shows on update)
  expect(t.showAlert).toHaveBeenCalledTimes(1);
});

it('dismisses the offer alert once the offer state is left', () => {
  mockState.rideState = 'ride_offered';
  mockState.incomingRide = { ride_id: 'r1', pickup_address: 'x', fare: '9.00' };
  registerAutoPlay();
  mockListeners.didConnect();
  const t = lastTpl();
  const apply = mockSubscribe.mock.calls[0][0] as () => void;

  mockState.rideState = 'navigating_to_pickup';
  mockState.incomingRide = null;
  mockState.activeRide = navRide();
  apply();
  expect(t.dismissAlert).toHaveBeenCalled();
});

it('does not rebuild chrome when the state + ride are unchanged', () => {
  mockState.rideState = 'trip_in_progress';
  mockState.activeRide = navRide();
  registerAutoPlay();
  mockListeners.didConnect();
  const t = lastTpl();
  const calls = t.setMapButtons.mock.calls.length;
  const apply = mockSubscribe.mock.calls[0][0] as () => void;
  apply(); // same key → suppressed
  expect(t.setMapButtons.mock.calls.length).toBe(calls);
});

it('never creates a template when no car is connected', () => {
  mockConnected = false;
  registerAutoPlay();
  mockListeners.didConnect?.();
  expect(mockTemplates).toHaveLength(0);
});

describe('display-only location service', () => {
  it('starts on connect — the in-hook watcher alone is throttled car-only', () => {
    registerAutoPlay();
    mockListeners.didConnect();
    // Deliberately not a call count. registerAutoPlay's already-connected branch
    // and the didConnect listener can both fire for one connection, and a head
    // unit swapping to the reversing camera re-fires it again; the service is
    // idempotent (it returns 'already-running'), so re-asking is the cheap
    // self-heal, not a bug to assert against.
    expect(mockStartCarLocation).toHaveBeenCalled();
  });

  it('stops on disconnect, so the notification dies with the screen it drew for', () => {
    registerAutoPlay();
    mockListeners.didConnect();
    mockListeners.didDisconnect();
    expect(mockStopCarLocation).toHaveBeenCalledTimes(1);
  });

  it('disconnect cancels the post-connect chrome refreshes', () => {
    // Those two timers exist to re-push chrome at a template that came up before
    // its assets resolved. Left armed past a disconnect they fire at a template
    // that is already null — and they outlive the test run, which is how the
    // leak showed up.
    jest.useFakeTimers();
    try {
      registerAutoPlay();
      mockListeners.didConnect();
      expect(jest.getTimerCount()).toBeGreaterThan(0);
      mockListeners.didDisconnect();
      expect(jest.getTimerCount()).toBe(0);
    } finally {
      jest.useRealTimers();
    }
  });

  it('is never started for a head unit that is not actually connected', () => {
    mockConnected = false;
    registerAutoPlay();
    mockListeners.didConnect?.();
    expect(mockStartCarLocation).not.toHaveBeenCalled();
  });
});

describe('headless car session', () => {
  it('bootstraps on connect — no phone screen mounts to load this data', () => {
    registerAutoPlay();
    mockListeners.didConnect();
    expect(mockStartCarSession).toHaveBeenCalled();
  });

  it('tears down on disconnect', () => {
    registerAutoPlay();
    mockListeners.didConnect();
    mockListeners.didDisconnect();
    expect(mockStopCarSession).toHaveBeenCalled();
  });

  it('a failing bootstrap still leaves a working map and buttons', async () => {
    // The whole premise is that the baseline needs no session: a signed-out or
    // offline driver must still get a map, a marker and a header.
    const errSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
    mockStartCarSession.mockRejectedValueOnce(new Error('no network'));
    registerAutoPlay();
    expect(() => mockListeners.didConnect()).not.toThrow();
    await Promise.resolve();
    expect(mockTemplates).toHaveLength(1);
    expect(lastTpl().setMapButtons).toHaveBeenCalled();
    errSpy.mockRestore();
  });

  it('a rejected start cannot take the car screen down with it', async () => {
    // The reversing-camera path re-fires didConnect, and a throw here would
    // abort onConnect before apply() — leaving the driver with no header
    // actions at all, SOS included.
    const errSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
    mockStartCarLocation.mockRejectedValueOnce(new Error('FGS start refused'));
    registerAutoPlay();
    expect(() => mockListeners.didConnect()).not.toThrow();
    await Promise.resolve();
    expect(mockTemplates).toHaveLength(1);
    expect(lastTpl().setRootTemplate).toHaveBeenCalled();
    errSpy.mockRestore();
  });
});

// The missing-native-module degrade path is covered in register.degrade.test.ts:
// it needs a THROWING @iternio mock as the hoisted module factory, and doing
// that in this file via doMock/dontMock surgery leaves the module registry
// without its baseline mock for any test appended later (order-dependent
// breakage) — so it lives in its own file with its own registry.
