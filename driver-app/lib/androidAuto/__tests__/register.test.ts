/**
 * Unit tests for lib/androidAuto/register.ts — the Android Auto orchestration.
 * The native @iternio module, the surface component, and the store are mocked,
 * so these assert the JS contract without a head unit: surface selection by ride
 * state (MapTemplate while navigating, InformationTemplate otherwise), leg-keyed
 * rebuild de-duping, the not-connected guard, and nav-button hand-off.
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

beforeEach(() => {
  (Platform as unknown as { OS: string }).OS = 'android';
  jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined as never);
  for (const k of Object.keys(mockListeners)) delete mockListeners[k];
  mockBuilt.length = 0;
  mockSubscribe.mockClear();
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

it('shows the InformationTemplate status screen when idle', () => {
  registerAutoPlay();
  mockListeners.didConnect();
  const info = lastOf('info');
  expect(info).toBeDefined();
  expect((info!.config.title as { text: string }).text).toBe('Spinr Driver');
  expect(info!.setRootTemplate).toHaveBeenCalled();
  expect(lastOf('map')).toBeUndefined();
  expect(mockSubscribe).toHaveBeenCalled();
});

it('shows a MapTemplate with Google + Waze hand-off buttons while navigating', () => {
  mockState.rideState = 'trip_in_progress';
  mockState.activeRide = navRide();
  registerAutoPlay();
  mockListeners.didConnect();

  const map = lastOf('map');
  expect(map).toBeDefined();
  expect(map!.setRootTemplate).toHaveBeenCalled();
  const buttons = map!.config.mapButtons as { onPress: () => void }[];
  expect(buttons).toHaveLength(2);

  buttons[0].onPress();
  expect(Linking.openURL).toHaveBeenCalledWith(
    expect.stringContaining('google.navigation:q=52.2,-106.6'),
  );
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

it('never sets a template when no car is connected', () => {
  mockConnected = false;
  registerAutoPlay();
  mockListeners.didConnect?.();
  expect(mockBuilt).toHaveLength(0);
});
