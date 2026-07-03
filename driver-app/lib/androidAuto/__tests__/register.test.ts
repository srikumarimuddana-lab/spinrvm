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

jest.mock('../../../store/driverStore', () => ({
  useDriverStore: {
    getState: () => mockState,
    subscribe: (fn: () => void) => mockSubscribe(fn),
  },
}));

jest.mock('../carSurface', () => ({ CarMapSurface: () => null }));

jest.mock('../carMapCamera', () => ({
  useCarMapCamera: {
    getState: () => ({ zoomIn: mockZoomIn, zoomOut: mockZoomOut, reset: mockReset }),
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
  mockConnected = true;
  mockState.rideState = 'idle';
  mockState.activeRide = null;
  mockState.incomingRide = null;
});

afterEach(() => jest.restoreAllMocks());

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
  expect(buttons).toHaveLength(2); // zoom out + zoom in only
  buttons[0].onPress();
  buttons[1].onPress();
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
  expect(buttons).toHaveLength(3); // navigate, zoom out, zoom in

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

// The missing-native-module degrade path is covered in register.degrade.test.ts:
// it needs a THROWING @iternio mock as the hoisted module factory, and doing
// that in this file via doMock/dontMock surgery leaves the module registry
// without its baseline mock for any test appended later (order-dependent
// breakage) — so it lives in its own file with its own registry.
