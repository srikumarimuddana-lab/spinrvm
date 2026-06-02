/**
 * Unit tests for car/carNav.tsx — the Android Auto route-map wiring. The native
 * car module is fully mocked and the real useDriverStore drives state, so these
 * assert the JS contract without a head unit: template selection by ride state,
 * leg-debounced root rebuilds, and the Google/Waze hand-off.
 */

// Mock the fork: MapTemplate / ListTemplate store their config so we can assert
// instanceof + shape; CarPlay exposes the connect/root surface.
jest.mock('@g4rb4g3/react-native-carplay', () => {
  class MapTemplate {
    config: Record<string, unknown>;
    constructor(config: Record<string, unknown>) {
      this.config = config;
    }
  }
  class ListTemplate {
    config: Record<string, unknown>;
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
    },
    MapTemplate,
    ListTemplate,
  };
});

import { Linking, Platform } from 'react-native';
import { CarPlay, MapTemplate, ListTemplate } from '@g4rb4g3/react-native-carplay';
import { useDriverStore } from '../store/driverStore';
import {
  buildIdleTemplate,
  buildNavMapTemplate,
  handoffToNav,
  initCarNav,
} from '../car/carNav';

const setOS = (os: string) => {
  (Platform as unknown as { OS: string }).OS = os;
};

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
  (CarPlay as unknown as { connected: boolean }).connected = false;
  useDriverStore.setState({ rideState: 'idle', activeRide: null });
  setOS('android');
});

afterEach(() => {
  openURL.mockRestore();
});

describe('buildNavMapTemplate', () => {
  it('builds a MapTemplate with the surface component + two nav buttons', () => {
    const t = buildNavMapTemplate(MapTemplate as never);
    const cfg = configOf(t);
    expect(t).toBeInstanceOf(MapTemplate);
    expect(cfg.id).toBe('spinr-car-nav');
    expect(typeof cfg.component).toBe('function');
    expect((cfg.mapButtons as { id: string }[]).map((b) => b.id)).toEqual([
      'nav-google',
      'nav-waze',
    ]);
  });

  it('routes the Waze button to Waze and the primary button to Google (Android)', () => {
    useDriverStore.setState({ rideState: 'trip_in_progress', activeRide: activeRide() });
    const cfg = configOf(buildNavMapTemplate(MapTemplate as never));
    const onPress = cfg.onMapButtonPressed as (e: { id: string }) => void;

    onPress({ id: 'nav-waze' });
    expect(openURL).toHaveBeenLastCalledWith('https://waze.com/ul?ll=52.2,-106.6&navigate=yes');

    onPress({ id: 'nav-google' });
    expect(openURL).toHaveBeenLastCalledWith('google.navigation:q=52.2,-106.6');
  });

  it('offers Apple Maps + Waze and routes them on iOS (CarPlay)', () => {
    setOS('ios');
    useDriverStore.setState({ rideState: 'trip_in_progress', activeRide: activeRide() });
    const cfg = configOf(buildNavMapTemplate(MapTemplate as never));
    expect((cfg.mapButtons as { id: string }[]).map((b) => b.id)).toEqual([
      'nav-apple',
      'nav-waze',
    ]);

    const onPress = cfg.onMapButtonPressed as (e: { id: string }) => void;
    onPress({ id: 'nav-apple' });
    expect(openURL).toHaveBeenLastCalledWith('maps://?daddr=52.2,-106.6&dirflg=d');
  });

  it('gives every map button an icon (CarPlay/Android Auto buttons are icons)', () => {
    const cfg = configOf(buildNavMapTemplate(MapTemplate as never));
    for (const b of cfg.mapButtons as { image?: unknown }[]) {
      expect(b.image).toBeDefined();
    }
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
  it('opens the nav app for the active destination', () => {
    useDriverStore.setState({ rideState: 'navigating_to_pickup', activeRide: activeRide() });
    handoffToNav('google');
    expect(openURL).toHaveBeenCalledWith('google.navigation:q=52.13,-106.67');
  });

  it('is a no-op when there is no active route', () => {
    useDriverStore.setState({ rideState: 'idle', activeRide: null });
    handoffToNav('google');
    expect(openURL).not.toHaveBeenCalled();
  });
});

describe('initCarNav', () => {
  it('runs on iOS too (CarPlay shares the phone JS context)', () => {
    setOS('ios');
    const cleanup = initCarNav();
    expect(CarPlay.registerOnConnect).toHaveBeenCalledTimes(1);
    cleanup();
  });

  it('is a no-op off-car (web / Expo Go)', () => {
    setOS('web');
    const cleanup = initCarNav();
    expect(CarPlay.registerOnConnect).not.toHaveBeenCalled();
    cleanup();
  });

  it('sets an idle root on connect, then swaps to the map per leg', () => {
    const cleanup = initCarNav();
    expect(CarPlay.registerOnConnect).toHaveBeenCalledTimes(1);

    // Nothing set until the head unit connects.
    expect(CarPlay.setRootTemplate).not.toHaveBeenCalled();

    // Connect while idle → idle ListTemplate root.
    const onConnect = (CarPlay.registerOnConnect as jest.Mock).mock.calls[0][0];
    onConnect();
    expect(CarPlay.setRootTemplate).toHaveBeenCalledTimes(1);
    expect((CarPlay.setRootTemplate as jest.Mock).mock.calls[0][0]).toBeInstanceOf(ListTemplate);

    // Trip starts → MapTemplate (dropoff leg). Store change fires the subscriber.
    useDriverStore.setState({ rideState: 'trip_in_progress', activeRide: activeRide() });
    expect(CarPlay.setRootTemplate).toHaveBeenCalledTimes(2);
    expect((CarPlay.setRootTemplate as jest.Mock).mock.calls[1][0]).toBeInstanceOf(MapTemplate);

    cleanup();
  });

  it('does NOT rebuild the root on same-leg store ticks', () => {
    const cleanup = initCarNav();
    const onConnect = (CarPlay.registerOnConnect as jest.Mock).mock.calls[0][0];

    useDriverStore.setState({ rideState: 'trip_in_progress', activeRide: activeRide() });
    onConnect(); // dropoff leg → 1 build
    const calls = (CarPlay.setRootTemplate as jest.Mock).mock.calls.length;

    // A second store tick on the same leg (e.g. a location update) → no rebuild.
    useDriverStore.setState({ countdownSeconds: 1 } as never);
    expect((CarPlay.setRootTemplate as jest.Mock).mock.calls.length).toBe(calls);

    cleanup();
  });

  it('cleanup unregisters and stops responding to store changes', () => {
    const cleanup = initCarNav();
    cleanup();
    expect(CarPlay.unregisterOnConnect).toHaveBeenCalledTimes(1);

    (CarPlay.setRootTemplate as jest.Mock).mockClear();
    useDriverStore.setState({ rideState: 'trip_in_progress', activeRide: activeRide() });
    expect(CarPlay.setRootTemplate).not.toHaveBeenCalled();
  });
});
