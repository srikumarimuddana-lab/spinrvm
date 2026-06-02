/**
 * Unit tests for car/carNav.tsx — the cross-platform car route-map wiring. The
 * native car module is fully mocked and the real useDriverStore drives state, so
 * these assert the JS contract without a head unit: template selection by ride
 * state, leg-debounced rebuilds with stale-template teardown, button-press
 * hand-off via the emitter (Android `buttonPressed` / iOS `mapButtonPressed`),
 * and installed-app detection.
 */

jest.mock('@g4rb4g3/react-native-carplay', () => {
  class MapTemplate {
    config: Record<string, unknown>;
    destroy = jest.fn();
    constructor(config: Record<string, unknown>) {
      this.config = config;
    }
  }
  class ListTemplate {
    config: Record<string, unknown>;
    destroy = jest.fn();
    constructor(config: Record<string, unknown>) {
      this.config = config;
    }
  }
  return {
    CarPlay: {
      connected: false,
      registerOnConnect: jest.fn(),
      unregisterOnConnect: jest.fn(),
      setRootTemplate: jest.fn(),
      emitter: { addListener: jest.fn(() => ({ remove: jest.fn() })) },
    },
    MapTemplate,
    ListTemplate,
  };
});

import { Linking, Platform } from 'react-native';
import { CarPlay, MapTemplate, ListTemplate } from '@g4rb4g3/react-native-carplay';
import { useDriverStore } from '../store/driverStore';
import { buildIdleTemplate, buildNavMapTemplate, handoffToNav, initCarNav } from '../car/carNav';

const setOS = (os: string) => {
  (Platform as unknown as { OS: string }).OS = os;
};
const setCanOpen = (fn: jest.Mock) => {
  (Linking as unknown as { canOpenURL: jest.Mock }).canOpenURL = fn;
};

// Grab the emitter callback initCarNav registered for a given event.
const emitterCb = (event: string): ((e: unknown) => void) =>
  (CarPlay.emitter.addListener as jest.Mock).mock.calls.find((c) => c[0] === event)?.[1];

const AND_BUTTONS = [
  { id: 'nav-google', provider: 'google' },
  { id: 'nav-waze', provider: 'waze' },
] as never;

const activeRide = (over: Record<string, unknown> = {}) =>
  ({
    ride: {
      id: 'r1',
      pickup_address: 'Pickup',
      dropoff_address: 'Dropoff',
      pickup_lat: 52.13,
      pickup_lng: -106.67,
      dropoff_lat: 52.2,
      dropoff_lng: -106.6,
      planned_route_polyline: [[52.13, -106.67], [52.2, -106.6]],
      ...over,
    },
    rider: { id: 'rider' },
    vehicle_type: { id: 'vt', name: 'Standard' },
  }) as never;

const configOf = (t: unknown) => (t as { config: Record<string, unknown> }).config;

let openURL: jest.SpyInstance;

beforeEach(() => {
  jest.clearAllMocks();
  openURL = jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined as never);
  setCanOpen(jest.fn().mockResolvedValue(false));
  (CarPlay as unknown as { connected: boolean }).connected = false;
  // Stub the cold-launch reconcile so it doesn't clobber the per-test ride state
  // (its real behavior is exercised in the store tests). One test below overrides
  // these to assert initCarNav invokes them.
  useDriverStore.setState({
    rideState: 'idle',
    activeRide: null,
    hydrateDriverRideState: jest.fn().mockResolvedValue(undefined),
    fetchActiveRide: jest.fn().mockResolvedValue(undefined),
  });
  setOS('android');
});

afterEach(() => {
  openURL.mockRestore();
});

describe('buildNavMapTemplate', () => {
  it('builds a MapTemplate with the surface component + the given buttons (icons)', () => {
    const t = buildNavMapTemplate(MapTemplate as never, AND_BUTTONS);
    const cfg = configOf(t);
    expect(t).toBeInstanceOf(MapTemplate);
    expect(cfg.id).toBe('spinr-car-nav');
    expect(typeof cfg.component).toBe('function');
    const btns = cfg.mapButtons as { id: string; image?: unknown }[];
    expect(btns.map((b) => b.id)).toEqual(['nav-google', 'nav-waze']);
    btns.forEach((b) => expect(b.image).toBeDefined());
  });

  it('does NOT use a config press callback (Android routes via the emitter)', () => {
    const cfg = configOf(buildNavMapTemplate(MapTemplate as never, AND_BUTTONS));
    expect(cfg.onMapButtonPressed).toBeUndefined();
  });
});

describe('buildIdleTemplate', () => {
  it('builds a ListTemplate idle root', () => {
    const t = buildIdleTemplate(ListTemplate as never);
    expect(t).toBeInstanceOf(ListTemplate);
    expect(configOf(t).id).toBe('spinr-car-idle');
  });
});

describe('handoffToNav', () => {
  it('opens the nav app for the active destination (Android)', () => {
    useDriverStore.setState({ rideState: 'navigating_to_pickup', activeRide: activeRide() });
    handoffToNav('google');
    expect(openURL).toHaveBeenCalledWith('google.navigation:q=52.13,-106.67');
  });

  it('uses the iOS schemes on CarPlay (Apple Maps + Google Maps)', () => {
    setOS('ios');
    useDriverStore.setState({ rideState: 'trip_in_progress', activeRide: activeRide() });
    handoffToNav('apple');
    expect(openURL).toHaveBeenLastCalledWith('maps://?daddr=52.2,-106.6&dirflg=d');
    handoffToNav('google');
    expect(openURL).toHaveBeenLastCalledWith(
      'comgooglemaps://?daddr=52.2,-106.6&directionsmode=driving'
    );
  });

  it('is a no-op when there is no active route', () => {
    useDriverStore.setState({ rideState: 'idle', activeRide: null });
    handoffToNav('google');
    expect(openURL).not.toHaveBeenCalled();
  });
});

describe('button hand-off via the car emitter', () => {
  it('routes the Android `buttonPressed` ({buttonId}) event to the nav app', () => {
    useDriverStore.setState({ rideState: 'trip_in_progress', activeRide: activeRide() });
    const cleanup = initCarNav();

    const cb = emitterCb('buttonPressed');
    expect(typeof cb).toBe('function');
    cb({ buttonId: 'nav-waze' });
    expect(openURL).toHaveBeenLastCalledWith('https://waze.com/ul?ll=52.2,-106.6&navigate=yes');
    cb({ buttonId: 'nav-google' });
    expect(openURL).toHaveBeenLastCalledWith('google.navigation:q=52.2,-106.6');
    cleanup();
  });

  it('routes the iOS `mapButtonPressed` ({id}) event to the nav app', () => {
    setOS('ios');
    useDriverStore.setState({ rideState: 'trip_in_progress', activeRide: activeRide() });
    const cleanup = initCarNav();

    emitterCb('mapButtonPressed')({ id: 'nav-apple' });
    expect(openURL).toHaveBeenLastCalledWith('maps://?daddr=52.2,-106.6&dirflg=d');
    cleanup();
  });

  it('ignores a press event with no id', () => {
    useDriverStore.setState({ rideState: 'trip_in_progress', activeRide: activeRide() });
    const cleanup = initCarNav();
    emitterCb('buttonPressed')({});
    expect(openURL).not.toHaveBeenCalled();
    cleanup();
  });

  it('ignores presses for ids that are not current nav buttons', () => {
    useDriverStore.setState({ rideState: 'trip_in_progress', activeRide: activeRide() });
    const cleanup = initCarNav();
    emitterCb('buttonPressed')({ buttonId: 'some-other-car-action' });
    expect(openURL).not.toHaveBeenCalled();
    cleanup();
  });
});

describe('initCarNav', () => {
  it('is a no-op off-car (web / Expo Go)', () => {
    setOS('web');
    const cleanup = initCarNav();
    expect(CarPlay.registerOnConnect).not.toHaveBeenCalled();
    cleanup();
  });

  it('runs on iOS too (CarPlay shares the phone JS context)', () => {
    setOS('ios');
    const cleanup = initCarNav();
    expect(CarPlay.registerOnConnect).toHaveBeenCalledTimes(1);
    cleanup();
  });

  it('hydrates and reconciles the ride against the server on init (cold launch)', async () => {
    const hydrate = jest.fn().mockResolvedValue(undefined);
    const fetch = jest.fn().mockResolvedValue(undefined);
    useDriverStore.setState({ hydrateDriverRideState: hydrate, fetchActiveRide: fetch });
    const cleanup = initCarNav();
    await new Promise((r) => setImmediate(r));
    expect(hydrate).toHaveBeenCalled();
    expect(fetch).toHaveBeenCalled();
    cleanup();
  });

  it('ignores store changes while the car is disconnected', () => {
    // connected = false (beforeEach). On iOS initCarNav is mounted by the phone
    // layout with no CarPlay session — store changes must not push templates.
    const cleanup = initCarNav();
    useDriverStore.setState({ rideState: 'trip_in_progress', activeRide: activeRide() });
    expect(CarPlay.setRootTemplate).not.toHaveBeenCalled();
    cleanup();
  });

  it('uses a MapTemplate idle root on iOS (CarPlay needs a CPMapTemplate)', () => {
    setOS('ios');
    (CarPlay as unknown as { connected: boolean }).connected = true;
    const cleanup = initCarNav(); // connected → auto onConnect → idle root
    expect((CarPlay.setRootTemplate as jest.Mock).mock.calls[0][0]).toBeInstanceOf(MapTemplate);
    cleanup();
  });

  it('sets an idle root on connect, then swaps to the map per leg', () => {
    const cleanup = initCarNav();
    expect(CarPlay.setRootTemplate).not.toHaveBeenCalled();

    (CarPlay as unknown as { connected: boolean }).connected = true;
    const onConnect = (CarPlay.registerOnConnect as jest.Mock).mock.calls[0][0];
    onConnect();
    expect(CarPlay.setRootTemplate).toHaveBeenCalledTimes(1);
    expect((CarPlay.setRootTemplate as jest.Mock).mock.calls[0][0]).toBeInstanceOf(ListTemplate);

    useDriverStore.setState({ rideState: 'trip_in_progress', activeRide: activeRide() });
    expect(CarPlay.setRootTemplate).toHaveBeenCalledTimes(2);
    expect((CarPlay.setRootTemplate as jest.Mock).mock.calls[1][0]).toBeInstanceOf(MapTemplate);
    cleanup();
  });

  it('destroys the previous template when rebuilding the root (no listener leak)', () => {
    (CarPlay as unknown as { connected: boolean }).connected = true;
    const cleanup = initCarNav();
    const onConnect = (CarPlay.registerOnConnect as jest.Mock).mock.calls[0][0];
    onConnect(); // idle ListTemplate
    const idleTpl = (CarPlay.setRootTemplate as jest.Mock).mock.calls[0][0];

    useDriverStore.setState({ rideState: 'trip_in_progress', activeRide: activeRide() }); // → map
    expect(idleTpl.destroy).toHaveBeenCalledTimes(1);
    cleanup();
  });

  it('rebuilds when the ride changes even on the same leg', () => {
    (CarPlay as unknown as { connected: boolean }).connected = true;
    const cleanup = initCarNav();
    useDriverStore.setState({ rideState: 'trip_in_progress', activeRide: activeRide({ id: 'r1' }) });
    const before = (CarPlay.setRootTemplate as jest.Mock).mock.calls.length;
    // Same leg (trip_in_progress) but a DIFFERENT ride id → must rebuild + recenter.
    useDriverStore.setState({ activeRide: activeRide({ id: 'r2' }) });
    expect((CarPlay.setRootTemplate as jest.Mock).mock.calls.length).toBe(before + 1);
    cleanup();
  });

  it('does NOT rebuild the root on same-leg store ticks', () => {
    (CarPlay as unknown as { connected: boolean }).connected = true;
    const cleanup = initCarNav();
    useDriverStore.setState({ rideState: 'trip_in_progress', activeRide: activeRide() });
    const calls = (CarPlay.setRootTemplate as jest.Mock).mock.calls.length;
    useDriverStore.setState({ countdownSeconds: 1 } as never);
    expect((CarPlay.setRootTemplate as jest.Mock).mock.calls.length).toBe(calls);
    cleanup();
  });

  it('cleanup unregisters, removes button listeners, and stops responding', () => {
    (CarPlay as unknown as { connected: boolean }).connected = true;
    const cleanup = initCarNav();
    const sub = (CarPlay.emitter.addListener as jest.Mock).mock.results[0].value;
    cleanup();
    expect(CarPlay.unregisterOnConnect).toHaveBeenCalledTimes(1);
    expect(sub.remove).toHaveBeenCalled();

    (CarPlay.setRootTemplate as jest.Mock).mockClear();
    useDriverStore.setState({ rideState: 'trip_in_progress', activeRide: activeRide() });
    expect(CarPlay.setRootTemplate).not.toHaveBeenCalled();
  });

  it("rebuilds with the driver's installed nav apps once detected (CarPlay)", async () => {
    setOS('ios');
    setCanOpen(jest.fn().mockResolvedValue(true)); // driver has Google Maps + Waze
    useDriverStore.setState({ rideState: 'trip_in_progress', activeRide: activeRide() });
    (CarPlay as unknown as { connected: boolean }).connected = true;

    const cleanup = initCarNav();
    await new Promise((r) => setImmediate(r));

    const calls = (CarPlay.setRootTemplate as jest.Mock).mock.calls;
    const lastCfg = configOf(calls[calls.length - 1][0]);
    expect((lastCfg.mapButtons as { id: string }[]).map((b) => b.id)).toEqual([
      'nav-apple',
      'nav-google',
      'nav-waze',
    ]);
    cleanup();
  });
});
