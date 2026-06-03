/**
 * Unit tests for lib/androidAuto/register.ts — the Android Auto orchestration.
 * The native @iternio module, the surface component, the driver store, and the
 * zoom camera store are mocked, so these assert the JS contract without a head
 * unit: a live MapTemplate in every state (idle + navigating), zoom buttons
 * always present (nav hand-off buttons added only while navigating), leg-keyed
 * rebuild de-duping, the not-connected guard, and the button callbacks.
 *
 * Vars referenced inside jest.mock factories are `mock`-prefixed (jest hoisting).
 */
import { Linking, Platform } from 'react-native';

const mockState: { rideState: string; activeRide: unknown } = {
  rideState: 'idle',
  activeRide: null,
};
const mockSubscribe = jest.fn((_fn?: () => void) => jest.fn());
const mockListeners: Record<string, () => void> = {};
let mockConnected = true;
const mockBuilt: { kind: string; config: Record<string, unknown>; setRootTemplate: jest.Mock }[] = [];

const mockZoomIn = jest.fn();
const mockZoomOut = jest.fn();
const mockReset = jest.fn();

jest.mock('../../../store/driverStore', () => ({
  useDriverStore: {
    // Lazy references — the mock factory runs (at import time) before these
    // consts initialize, so they must be read at call time, not captured.
    getState: () => mockState,
    subscribe: (fn: () => void) => mockSubscribe(fn),
  },
}));

// Surface component is passed to the template, never rendered here.
jest.mock('../carSurface', () => ({ CarMapSurface: () => null }));

jest.mock('../carMapCamera', () => ({
  useCarMapCamera: {
    getState: () => ({ zoomIn: mockZoomIn, zoomOut: mockZoomOut, reset: mockReset }),
  },
}));

jest.mock('@iternio/react-native-auto-play', () => {
  const make = (kind: string) =>
    class {
      config: Record<string, unknown>;
      setRootTemplate = jest.fn();
      constructor(config: Record<string, unknown>) {
        this.config = config;
        mockBuilt.push({ kind, config, setRootTemplate: this.setRootTemplate });
      }
    };
  return {
    HybridAutoPlay: {
      addListener: jest.fn((evt: string, cb: () => void) => {
        mockListeners[evt] = cb;
        return { remove: jest.fn() };
      }),
      isConnected: () => mockConnected,
    },
    MapTemplate: make('map'),
    InformationTemplate: make('info'),
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
      planned_route_polyline: [[52.13, -106.67], [52.2, -106.6]],
    },
    rider: { id: 'rider', first_name: 'Alex' },
    vehicle_type: { id: 'vt', name: 'Standard' },
  }) as unknown;

const lastOf = (kind: string) => [...mockBuilt].reverse().find((t) => t.kind === kind);
const buttonsOf = (t: { config: Record<string, unknown> }) =>
  t.config.mapButtons as { onPress: () => void }[];

beforeEach(() => {
  (Platform as unknown as { OS: string }).OS = 'android';
  jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined as never);
  for (const k of Object.keys(mockListeners)) delete mockListeners[k];
  mockBuilt.length = 0;
  mockSubscribe.mockClear();
  mockZoomIn.mockClear();
  mockZoomOut.mockClear();
  mockReset.mockClear();
  mockConnected = true;
  mockState.rideState = 'idle';
  mockState.activeRide = null;
});

afterEach(() => jest.restoreAllMocks());

it('does nothing on non-Android platforms', () => {
  (Platform as unknown as { OS: string }).OS = 'ios';
  registerAutoPlay();
  expect(mockListeners.didConnect).toBeUndefined();
});

it('shows a live MapTemplate with zoom-only buttons when idle', () => {
  registerAutoPlay();
  mockListeners.didConnect();

  const map = lastOf('map');
  expect(map).toBeDefined();
  expect(map!.setRootTemplate).toHaveBeenCalled();
  expect(lastOf('info')).toBeUndefined(); // no more blank status pane

  const buttons = buttonsOf(map!);
  expect(buttons).toHaveLength(2); // zoom out + zoom in only
  buttons[0].onPress();
  buttons[1].onPress();
  expect(mockZoomOut).toHaveBeenCalledTimes(1);
  expect(mockZoomIn).toHaveBeenCalledTimes(1);

  expect(mockReset).toHaveBeenCalled(); // camera reframed on connect
  expect(mockSubscribe).toHaveBeenCalled();
});

it('adds Google + Waze hand-off buttons (before zoom) while navigating', () => {
  mockState.rideState = 'trip_in_progress';
  mockState.activeRide = navRide();
  registerAutoPlay();
  mockListeners.didConnect();

  const map = lastOf('map');
  expect(map).toBeDefined();
  const buttons = buttonsOf(map!);
  expect(buttons).toHaveLength(4); // google, waze, zoom out, zoom in

  buttons[0].onPress(); // Google hand-off
  expect(Linking.openURL).toHaveBeenCalledWith(
    expect.stringContaining('google.navigation:q=52.2,-106.6'),
  );

  buttons[2].onPress(); // zoom out
  buttons[3].onPress(); // zoom in
  expect(mockZoomOut).toHaveBeenCalledTimes(1);
  expect(mockZoomIn).toHaveBeenCalledTimes(1);
});

it('does not rebuild the root when the leg + ride are unchanged', () => {
  mockState.rideState = 'trip_in_progress';
  mockState.activeRide = navRide();
  registerAutoPlay();
  mockListeners.didConnect();
  const apply = mockSubscribe.mock.calls[0][0] as () => void;
  const mapCount = mockBuilt.filter((t) => t.kind === 'map').length;
  apply(); // same state → suppressed
  expect(mockBuilt.filter((t) => t.kind === 'map').length).toBe(mapCount);
});

it('rebuilds the root when transitioning from idle to a navigation leg', () => {
  registerAutoPlay();
  mockListeners.didConnect();
  const apply = mockSubscribe.mock.calls[0][0] as () => void;
  const before = mockBuilt.filter((t) => t.kind === 'map').length;

  mockState.rideState = 'trip_in_progress';
  mockState.activeRide = navRide();
  apply(); // idle → dropoff leg → new root

  expect(mockBuilt.filter((t) => t.kind === 'map').length).toBe(before + 1);
});

it('never sets a template when no car is connected', () => {
  mockConnected = false;
  registerAutoPlay();
  mockListeners.didConnect?.();
  expect(mockBuilt).toHaveLength(0);
});
