/**
 * app/driver/(tabs)/index.tsx — driver's live dispatch/safety dashboard
 * (the largest remaining screen in the B37 coverage-widening pass,
 * deliberately deferred to its own session: ~15 stacked hooks/components).
 * Every dependency (store, hooks, map, dashboard panel components, SOS/
 * Safety) is mocked to isolate this file's own render/handler logic —
 * the underlying hooks (useDriverDashboard, useDemandHeatmap,
 * useAirportZones, useDriverSafetyTrigger, etc.) each own their own
 * complexity and are out of scope here.
 *
 * Pins:
 *  - the location gate: denied -> fallback with Retry + Open Settings;
 *    unavailable -> fallback with Retry only, no Settings button; still
 *    resolving (no coords, no denied/unavailable status) -> spinner
 *  - the error effect: a store `error` toasts and calls clearError
 *  - rideState-driven panel switch: idle -> DriverIdlePanel (wired to
 *    toggleOnline); ride_offered -> RideOfferPanel (onAccept ->
 *    acceptRide(ride_id), onDecline -> declineRide(ride_id, reason));
 *    navigating_to_pickup/arrived_at_pickup/trip_in_progress ->
 *    ActiveRidePanel (onVerifyOTP -> verifyOTP, onArriveAtPickup ->
 *    arriveAtPickup with the driver's current lat/lng, onStartRide ->
 *    startRide, onCancelRide -> cancelRide(id, reason)); trip_completed
 *    -> TripCompletedPanel (onDone -> resetRideState, onRateRider ->
 *    rateRider)
 *  - requestRideCompletion: a confirmationRequired result opens the
 *    off-route confirm modal instead of finishing; picking a reason
 *    re-calls completeRide with that confirmation and closes the modal
 *    on a non-confirmation-required result
 *  - SOS: discreetSosEnabled off (default) renders SOSButton, wired to
 *    POST /rides/:id/emergency and returning the response body (not
 *    void); discreetSosEnabled on renders the SafetyShield/SafetyOverlay
 *    pair instead — only during an active-ride rideState
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { Text, TouchableOpacity, Linking, Platform } from 'react-native';

const appStateListeners: Array<(state: string) => void> = [];
jest.mock('react-native/Libraries/AppState/AppState', () => ({
  __esModule: true,
  default: {
    addEventListener: (event: string, cb: (state: string) => void) => {
      if (event === 'change') appStateListeners.push(cb);
      return { remove: jest.fn() };
    },
    currentState: 'active',
  },
}));

// expo-router's real module pulls in an ESM dependency Jest cannot parse.
// index.tsx uses only useFocusEffect (remount the car marker on tab refocus);
// run the effect once on mount, like a screen that is focused from the start,
// and expose the callback so a test can simulate a blur/refocus cycle.
let mockFocusEffectCb: (() => void | (() => void)) | null = null;
jest.mock('expo-router', () => ({
  useFocusEffect: (cb: () => void | (() => void)) => {
    mockFocusEffectCb = cb;
    const ReactActual = require('react');
    ReactActual.useEffect(() => cb(), [cb]);
  },
}));

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));
jest.mock('@shared/components/ErrorBoundary', () => ({
  ErrorBoundary: ({ children }: any) => children,
}));
jest.mock('@tanstack/react-query', () => ({
  QueryClientProvider: ({ children }: any) => children,
}));
jest.mock('@shared/api/queryClient', () => ({ queryClient: {} }));
jest.mock('@shared/hooks/useExitOnBackPress', () => ({ useExitOnBackPress: () => {} }));

jest.mock('react-native-maps', () => {
  const ReactActual = require('react');
  const MapView = ReactActual.forwardRef((props: any, ref: any) => {
    // Empty deps: a real native ref's imperative handle is a stable object
    // across renders. Without deps here, every re-render (e.g. state
    // updates the polyline effect itself makes) installed a brand-new
    // fitToCoordinates/animateToRegion pair, silently discarding whatever
    // had just been called on the previous instance before a test could
    // assert on it.
    ReactActual.useImperativeHandle(
      ref,
      () => ({ fitToCoordinates: jest.fn(), animateToRegion: jest.fn(), animateCamera: jest.fn() }),
      [],
    );
    return ReactActual.createElement('MapView', props, props.children);
  });
  const Polygon = (props: any) =>
    ReactActual.createElement('Polygon', { accessibilityLabel: 'map-polygon', ...props });
  return {
    __esModule: true, default: MapView, PROVIDER_GOOGLE: 'google',
    Polygon,
  };
});
jest.mock('react-native-maps-directions', () => () => null);
jest.mock('@shared/components/RouteLine', () => ({ RouteLine: () => null }));
jest.mock('@shared/components/RoutePins', () => ({ RoutePins: () => null }));
const mockCarMarkerMounts = jest.fn();
jest.mock('../../components/CarMarker', () => ({
  // Records each MOUNT (not render) so a test can assert the marker was
  // remounted — the tab-refocus fix works by changing its key.
  CarMarker: () => {
    const ReactActual = require('react');
    ReactActual.useEffect(() => { mockCarMarkerMounts(); }, []);
    return null;
  },
  resolveMarkerVariant: () => 'sedan',
}));
jest.mock('../../hooks/liveRouteShared', () => ({
  clearLiveRoute: jest.fn(),
  publishLiveRoute: jest.fn(),
  registerLiveRoutePublisher: jest.fn(() => jest.fn()),
}));

const COLORS = {
  primary: '#EF4444', background: '#FFF', surface: '#FFF', surfaceLight: '#F5F5F5',
  text: '#111', textDim: '#666', border: '#E5E7EB', success: '#10B981',
  heatmapRamp: ['#eee', '#ddd', '#ccc', '#bbb', '#aaa'],
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockT = (key: string) => key;
jest.mock('../../store/languageStore', () => ({ useLanguageStore: () => ({ t: mockT }) }));

let mockAuthState: any;
jest.mock('@shared/store/authStore', () => ({
  useAuthStore: (sel: any) => sel(mockAuthState),
}));

let mockVehicleTypeState: any;
jest.mock('@shared/store/vehicleTypeStore', () => ({
  useVehicleTypeStore: (sel: any) => sel(mockVehicleTypeState),
}));

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a), post: (...a: any[]) => mockApiPost(...a) },
  isAppCheckTokenReady: () => Promise.resolve(true),
}));

const mockShowToast = jest.fn();
jest.mock('../../hooks/useToast', () => ({ showToast: (...a: any[]) => mockShowToast(...a) }));

const mockAcceptRide = jest.fn();
const mockDeclineRide = jest.fn();
const mockArriveAtPickup = jest.fn();
const mockVerifyOTP = jest.fn();
const mockStartRide = jest.fn();
const mockCompleteRide = jest.fn();
const mockCancelRide = jest.fn();
const mockResetRideState = jest.fn();
const mockClearError = jest.fn();
const mockRateRider = jest.fn();
const mockSetCountdown = jest.fn();
let mockDriverState: any;
jest.mock('../../store/driverStore', () => {
  function useDriverStore(selector?: (s: any) => any) {
    return selector ? selector((useDriverStore as any).__state) : (useDriverStore as any).__state;
  }
  useDriverStore.getState = () => (useDriverStore as any).__state;
  return { useDriverStore };
});

const mockToggleOnline = jest.fn();
const mockOpenNavigation = jest.fn();
const mockRefreshLocation = jest.fn();
let mockDashboardState: any;
jest.mock('../../hooks/useDriverDashboard', () => ({
  useDriverDashboard: () => mockDashboardState,
}));

const mockSetHeatmapLayer = jest.fn();
let mockHeatmapState: any;
jest.mock('../../hooks/useDemandHeatmap', () => ({
  useDemandHeatmap: () => mockHeatmapState,
}));
let mockAirportZonesState: any;
jest.mock('../../hooks/useAirportZones', () => ({
  useAirportZones: () => mockAirportZonesState,
}));

const mockSafetyTrigger = jest.fn();
jest.mock('../../hooks/useDriverSafetyTrigger', () => ({
  useDriverSafetyTrigger: () => ({ trigger: mockSafetyTrigger }),
}));
let mockDiscreetSosEnabled = false;
jest.mock('../../hooks/useDriverDiscreetSosFlag', () => ({
  useDriverDiscreetSosFlag: () => mockDiscreetSosEnabled,
}));

jest.mock('@shared/components/SOSButton', () => ({
  SOSButton: (props: any) => {
    const { TouchableOpacity: RNTouchableOpacity, Text: RNText } = require('react-native');
    return (
      <RNTouchableOpacity accessibilityLabel="sos-button" onPress={() => props.onTrigger(props.rideId, 52.1, -106.6, 'idem-1')}>
        <RNText>SOS</RNText>
      </RNTouchableOpacity>
    );
  },
}));
jest.mock('@shared/components/SafetyShield', () => ({
  SafetyShield: (props: any) => {
    const { TouchableOpacity: RNTouchableOpacity, Text: RNText } = require('react-native');
    return (
      <RNTouchableOpacity accessibilityLabel="safety-shield" onPress={props.onOpenOverlay}>
        <RNText>Shield</RNText>
      </RNTouchableOpacity>
    );
  },
}));
jest.mock('@shared/components/SafetyOverlay', () => ({
  SafetyOverlay: (props: any) => {
    const { Text: RNText, TouchableOpacity: RNTouchableOpacity } = require('react-native');
    return props.visible ? (
      <RNTouchableOpacity accessibilityLabel="safety-overlay-close" onPress={props.onClose}>
        <RNText>SafetyOverlayOpen</RNText>
      </RNTouchableOpacity>
    ) : null;
  },
}));

jest.mock('../../components/panels/RideOfferPanel', () => ({
  RideOfferPanel: (props: any) => {
    const { View, TouchableOpacity: RNTouchableOpacity, Text: RNText } = require('react-native');
    return (
      <View>
        <RNText>RideOfferPanel</RNText>
        <RNTouchableOpacity accessibilityLabel="offer-accept" onPress={props.onAccept}>
          <RNText>Accept</RNText>
        </RNTouchableOpacity>
        <RNTouchableOpacity accessibilityLabel="offer-decline" onPress={() => props.onDecline('too_far')}>
          <RNText>Decline</RNText>
        </RNTouchableOpacity>
      </View>
    );
  },
}));

jest.mock('../../components/dashboard', () => {
  const { View, TouchableOpacity: RNTouchableOpacity, Text: RNText } = require('react-native');
  return {
    DriverTopBar: (props: any) => <RNText accessibilityLabel="top-bar">{`unread:${props.unreadNotifCount}`}</RNText>,
    DriverIdlePanel: (props: any) => (
      <RNTouchableOpacity accessibilityLabel="idle-toggle" onPress={props.onToggleOnline}>
        <RNText>{props.isOnline ? 'Online' : 'Offline'}</RNText>
      </RNTouchableOpacity>
    ),
    ActiveRidePanel: (props: any) => (
      <View>
        <RNText>ActiveRidePanel:{props.rideState}</RNText>
        <RNTouchableOpacity accessibilityLabel="verify-otp" onPress={() => props.onVerifyOTP('1234')}>
          <RNText>Verify</RNText>
        </RNTouchableOpacity>
        <RNTouchableOpacity accessibilityLabel="arrive" onPress={props.onArriveAtPickup}>
          <RNText>Arrive</RNText>
        </RNTouchableOpacity>
        <RNTouchableOpacity accessibilityLabel="start-ride" onPress={props.onStartRide}>
          <RNText>Start</RNText>
        </RNTouchableOpacity>
        <RNTouchableOpacity accessibilityLabel="complete-ride" onPress={props.onCompleteRide}>
          <RNText>Complete</RNText>
        </RNTouchableOpacity>
        <RNTouchableOpacity accessibilityLabel="cancel-ride" onPress={() => props.onCancelRide('rider_no_show')}>
          <RNText>Cancel</RNText>
        </RNTouchableOpacity>
      </View>
    ),
    TripCompletedPanel: (props: any) => (
      <View>
        <RNText>TripCompletedPanel</RNText>
        <RNTouchableOpacity accessibilityLabel="trip-done" onPress={props.onDone}>
          <RNText>Done</RNText>
        </RNTouchableOpacity>
        <RNTouchableOpacity accessibilityLabel="rate-rider" onPress={() => props.onRateRider(5)}>
          <RNText>Rate</RNText>
        </RNTouchableOpacity>
      </View>
    ),
    MapControls: () => null,
    DemandLegend: (props: any) => <RNText accessibilityLabel="demand-legend">{`layer:${props.layer}`}</RNText>,
    ForecastStrip: (props: any) => <RNText accessibilityLabel="forecast-strip">{`forecast:${props.forecast.length}`}</RNText>,
    HeatmapCells: (props: any) => <RNText accessibilityLabel="heatmap-cells">{`cells:${props.cells.length}`}</RNText>,
    HeatmapGradientOverlay: (props: any) => (
      <RNText accessibilityLabel="heatmap-gradient-overlay">{`cells:${props.cells.length}`}</RNText>
    ),
    HotspotChips: (props: any) => (
      <RNTouchableOpacity accessibilityLabel="hotspot-chip" onPress={() => props.onPress(52.15, -106.65)}>
        <RNText>{`hotspots:${props.hotspots.length}`}</RNText>
      </RNTouchableOpacity>
    ),
  };
});

import { useDriverStore as mockedUseDriverStore } from '../../store/driverStore';
import { RideOfferPanel as MockRideOfferPanel } from '../../components/panels/RideOfferPanel';
import { MapControls as MockMapControls } from '../../components/dashboard';
import { clearLiveRoute as mockClearLiveRoute, publishLiveRoute as mockPublishLiveRoute } from '../../hooks/liveRouteShared';
import DriverDashboardScreen from '../../app/driver/(tabs)/index';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

const LOCATION = { coords: { latitude: 52.1, longitude: -106.6, heading: 0 } };

function resetState() {
  mockAuthState = {
    driver: { id: 'drv-1', vehicle_type_id: 'vt-1', service_area_id: 'area-1' },
    user: { first_name: 'Jamie' },
  };
  mockVehicleTypeState = { byId: {} };
  mockDriverState = {
    rideState: 'idle',
    incomingRide: null,
    activeRide: null,
    completedRide: null,
    countdownSeconds: 15,
    configuredCountdownSeconds: 15,
    setCountdown: mockSetCountdown,
    acceptRide: mockAcceptRide,
    declineRide: mockDeclineRide,
    arriveAtPickup: mockArriveAtPickup,
    verifyOTP: mockVerifyOTP,
    startRide: mockStartRide,
    completeRide: mockCompleteRide,
    cancelRide: mockCancelRide,
    resetRideState: mockResetRideState,
    clearError: mockClearError,
    earnings: 0,
    rateRider: mockRateRider,
    isLoading: false,
    error: null,
    isCancellingRide: false,
  };
  (mockedUseDriverStore as any).__state = mockDriverState;
  mockDashboardState = {
    isOnline: true,
    connectionState: 'connected',
    location: LOCATION,
    locationStatus: 'granted',
    otpInput: '',
    setOtpInput: jest.fn(),
    toggleOnline: mockToggleOnline,
    openNavigation: mockOpenNavigation,
    mapRef: { current: null },
    currentRegionRef: { current: null },
    pulseAnim: { current: null },
    slideUpAnim: { current: null },
    fadeAnim: { current: null },
    wsError: null,
    wsLatency: null,
    refreshLocation: mockRefreshLocation,
  };
  mockDiscreetSosEnabled = false;
  mockHeatmapState = {
    cells: [], status: 'idle', visible: false, surge: null, isV2: false,
    layer: 'demand', setLayer: mockSetHeatmapLayer, forecast: [], hotspots: [],
    cellLatDeg: 0.01, cellLngDeg: 0.01,
  };
  mockAirportZonesState = { zones: [], activeZone: null };
}

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<DriverDashboardScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => JSON.stringify(t.props.children)).join(' | ');
}

beforeEach(() => {
  jest.clearAllMocks();
  appStateListeners.length = 0;
  resetState();
  mockApiGet.mockImplementation((url: string) => {
    if (url === '/notifications?limit=1') return Promise.resolve({ data: { unread_count: 3 } });
    if (url === '/service-areas') return Promise.resolve({ data: [] });
    return Promise.resolve({ data: {} });
  });
  mockApiPost.mockResolvedValue({ data: { contacts_notified: true } });
  mockCompleteRide.mockResolvedValue({ confirmationRequired: false });
});

afterEach(() => {
  act(() => { renderer?.unmount(); });
  renderer = null;
});

describe('DriverDashboardScreen', () => {
  it('shows a spinner while location is still resolving', async () => {
    mockDashboardState.location = null;
    mockDashboardState.locationStatus = 'unknown';
    const r = await renderScreen();
    expect(allText(r)).toContain('"home.gettingLocation"');
  });

  it('shows the denied fallback with Retry and Open Settings when permission is denied', async () => {
    mockDashboardState.location = null;
    mockDashboardState.locationStatus = 'denied';
    const r = await renderScreen();
    expect(allText(r)).toContain('"home.locationDeniedTitle"');
    expect(allText(r)).toContain('"home.openSettings"');
    expect(allText(r)).toContain('"home.retryLocation"');
  });

  it('shows the unavailable fallback with Retry only (no Settings button) when the GPS fix failed', async () => {
    mockDashboardState.location = null;
    mockDashboardState.locationStatus = 'unavailable';
    const r = await renderScreen();
    expect(allText(r)).toContain('"home.locationUnavailableTitle"');
    expect(allText(r)).not.toContain('"home.openSettings"');
  });

  it('retry calls refreshLocation from the fallback state', async () => {
    mockDashboardState.location = null;
    mockDashboardState.locationStatus = 'unavailable';
    const r = await renderScreen();
    const retryBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => JSON.stringify(t.props.children) === '"home.retryLocation"')
    )!;
    act(() => { retryBtn.props.onPress(); });
    expect(mockRefreshLocation).toHaveBeenCalledWith(true);
  });

  it('toasts and clears the store error when one is set', async () => {
    mockDriverState.error = 'Something broke';
    await renderScreen();
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Something Went Wrong', 'Something broke');
    expect(mockClearError).toHaveBeenCalled();
  });

  it('fetches unread notifications once App Check is ready and passes the count to DriverTopBar', async () => {
    const r = await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/notifications?limit=1');
    expect(allText(r)).toContain('"unread:3"');
  });

  it('idle state renders DriverIdlePanel wired to toggleOnline', async () => {
    const r = await renderScreen();
    const toggle = r.root.findByProps({ accessibilityLabel: 'idle-toggle' });
    act(() => { toggle.props.onPress(); });
    expect(mockToggleOnline).toHaveBeenCalled();
  });

  it('ride_offered renders RideOfferPanel; accept/decline call the store actions with the offer id', async () => {
    mockDriverState.rideState = 'ride_offered';
    mockDriverState.incomingRide = {
      ride_id: 'ride-1', pickup_address: 'A', dropoff_address: 'B',
      pickup_lat: 52.1, pickup_lng: -106.6, dropoff_lat: 52.2, dropoff_lng: -106.7, fare: '20.00',
    };
    const r = await renderScreen();
    act(() => { r.root.findByProps({ accessibilityLabel: 'offer-accept' }).props.onPress(); });
    expect(mockAcceptRide).toHaveBeenCalledWith('ride-1');
    act(() => { r.root.findByProps({ accessibilityLabel: 'offer-decline' }).props.onPress(); });
    expect(mockDeclineRide).toHaveBeenCalledWith('ride-1', 'too_far');
  });

  it('an active ride renders ActiveRidePanel; each action calls its store handler', async () => {
    mockDriverState.rideState = 'navigating_to_pickup';
    mockDriverState.activeRide = { ride: { id: 'ride-1' }, rider: { name: 'Alex' } };
    const r = await renderScreen();
    expect(allText(r)).toContain('["ActiveRidePanel:","navigating_to_pickup"]');

    act(() => { r.root.findByProps({ accessibilityLabel: 'verify-otp' }).props.onPress(); });
    expect(mockVerifyOTP).toHaveBeenCalledWith('ride-1', '1234');

    act(() => { r.root.findByProps({ accessibilityLabel: 'arrive' }).props.onPress(); });
    expect(mockArriveAtPickup).toHaveBeenCalledWith('ride-1', 52.1, -106.6);

    act(() => { r.root.findByProps({ accessibilityLabel: 'start-ride' }).props.onPress(); });
    expect(mockStartRide).toHaveBeenCalledWith('ride-1');

    act(() => { r.root.findByProps({ accessibilityLabel: 'cancel-ride' }).props.onPress(); });
    expect(mockCancelRide).toHaveBeenCalledWith('ride-1', 'rider_no_show');
  });

  it('completing a ride with no confirmation required just calls completeRide', async () => {
    mockDriverState.rideState = 'trip_in_progress';
    mockDriverState.activeRide = { ride: { id: 'ride-1' }, rider: { name: 'Alex' } };
    mockCompleteRide.mockResolvedValue({ confirmationRequired: false });
    const r = await renderScreen();
    await act(async () => {
      r.root.findByProps({ accessibilityLabel: 'complete-ride' }).props.onPress();
      await flush();
    });
    expect(mockCompleteRide).toHaveBeenCalledWith('ride-1', undefined);
    expect(allText(r)).not.toContain('"Confirm trip completion"');
  });

  it('an off-route completion opens the confirm modal; picking a reason re-completes with it and closes', async () => {
    mockDriverState.rideState = 'trip_in_progress';
    mockDriverState.activeRide = { ride: { id: 'ride-1' }, rider: { name: 'Alex' } };
    mockCompleteRide.mockResolvedValueOnce({ confirmationRequired: true });
    const r = await renderScreen();
    await act(async () => {
      r.root.findByProps({ accessibilityLabel: 'complete-ride' }).props.onPress();
      await flush();
    });
    expect(allText(r)).toContain('"Confirm trip completion"');

    mockCompleteRide.mockResolvedValueOnce({ confirmationRequired: false });
    const reasonBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => JSON.stringify(t.props.children) === '"Rider asked to stop here"')
    )!;
    await act(async () => {
      reasonBtn.props.onPress();
      await flush();
    });
    expect(mockCompleteRide).toHaveBeenLastCalledWith('ride-1', 'rider_requested_stop');
    expect(allText(r)).not.toContain('"Confirm trip completion"');
  });

  it('"Keep trip open" closes the modal without re-completing', async () => {
    mockDriverState.rideState = 'trip_in_progress';
    mockDriverState.activeRide = { ride: { id: 'ride-1' }, rider: { name: 'Alex' } };
    mockCompleteRide.mockResolvedValueOnce({ confirmationRequired: true });
    const r = await renderScreen();
    await act(async () => {
      r.root.findByProps({ accessibilityLabel: 'complete-ride' }).props.onPress();
      await flush();
    });
    const keepOpenBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => JSON.stringify(t.props.children) === '"Keep trip open"')
    )!;
    act(() => { keepOpenBtn.props.onPress(); });
    expect(allText(r)).not.toContain('"Confirm trip completion"');
    expect(mockCompleteRide).toHaveBeenCalledTimes(1);
  });

  it('trip_completed renders TripCompletedPanel; Done resets state, Rate calls rateRider', async () => {
    mockDriverState.rideState = 'trip_completed';
    mockDriverState.completedRide = { id: 'ride-1', total_fare: 25 };
    const r = await renderScreen();
    act(() => { r.root.findByProps({ accessibilityLabel: 'trip-done' }).props.onPress(); });
    expect(mockResetRideState).toHaveBeenCalled();
    act(() => { r.root.findByProps({ accessibilityLabel: 'rate-rider' }).props.onPress(); });
    expect(mockRateRider).toHaveBeenCalledWith(5);
  });

  it('renders the plain SOSButton during an active ride when the discreet flag is off, POSTing to /rides/:id/emergency and returning the response body', async () => {
    mockDiscreetSosEnabled = false;
    mockDriverState.rideState = 'trip_in_progress';
    mockDriverState.activeRide = { ride: { id: 'ride-1' }, rider: { name: 'Alex' } };
    const r = await renderScreen();
    const sos = r.root.findByProps({ accessibilityLabel: 'sos-button' });
    let result: any;
    await act(async () => {
      result = await sos.props.onPress();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/rides/ride-1/emergency', {
      latitude: 52.1, longitude: -106.6, idempotency_key: 'idem-1',
    });
  });

  it('renders the SafetyShield/SafetyOverlay pair instead of SOSButton when the discreet flag is on', async () => {
    mockDiscreetSosEnabled = true;
    mockDriverState.rideState = 'trip_in_progress';
    mockDriverState.activeRide = { ride: { id: 'ride-1' }, rider: { name: 'Alex' } };
    const r = await renderScreen();
    expect(r.root.findAllByProps({ accessibilityLabel: 'sos-button' })).toHaveLength(0);
    const shield = r.root.findByProps({ accessibilityLabel: 'safety-shield' });
    act(() => { shield.props.onPress(); });
    expect(allText(r)).toContain('"SafetyOverlayOpen"');
  });

  it('neither SOSButton nor SafetyShield renders while idle', async () => {
    const r = await renderScreen();
    expect(r.root.findAllByProps({ accessibilityLabel: 'sos-button' })).toHaveLength(0);
    expect(r.root.findAllByProps({ accessibilityLabel: 'safety-shield' })).toHaveLength(0);
  });
});

describe('demand heatmap overlay (idle only)', () => {
  // HeatmapGradientOverlay (the Skia gradient overlay, HM-32) is kill-switched
  // as of 2026-09-10 pending investigation of a driver-app 2.0.03 100% iOS
  // crash (TurboModuleRegistry.getEnforcing 'RNSkiaModule') — see the
  // driver dashboard screen's render-site comment and
  // docs/change-log/2026-09-10-skia-heatmap-crash-kill-switch.md. HeatmapCells
  // (react-native-maps' native <Heatmap> on Android, a Skia-free Circle
  // fallback on iOS — pre-dates HM-32) is back to rendering on both
  // platforms, exactly as it did before HM-32 ever touched this screen.
  const originalPlatformOS = Platform.OS;
  afterEach(() => {
    Platform.OS = originalPlatformOS;
  });

  it('renders HeatmapCells on Android when cells are present', async () => {
    Platform.OS = 'android';
    mockHeatmapState.cells = [{ lat: 52.1, lng: -106.6, weight: 0.5 }];
    const r = await renderScreen();
    expect(r.root.findByProps({ accessibilityLabel: 'heatmap-cells' })).toBeTruthy();
    expect(r.root.findAllByProps({ accessibilityLabel: 'heatmap-gradient-overlay' })).toHaveLength(0);
  });

  it('omits HeatmapCells with zero cells (Android)', async () => {
    Platform.OS = 'android';
    const r = await renderScreen();
    expect(r.root.findAllByProps({ accessibilityLabel: 'heatmap-cells' })).toHaveLength(0);
  });

  it('renders HeatmapCells on iOS too when cells are present, never HeatmapGradientOverlay (Skia kill-switched)', async () => {
    Platform.OS = 'ios';
    mockHeatmapState.cells = [{ lat: 52.1, lng: -106.6, weight: 0.5 }];
    const r = await renderScreen();
    expect(r.root.findByProps({ accessibilityLabel: 'heatmap-cells' })).toBeTruthy();
    expect(r.root.findAllByProps({ accessibilityLabel: 'heatmap-gradient-overlay' })).toHaveLength(0);
  });

  it('omits HeatmapCells with zero cells (iOS)', async () => {
    Platform.OS = 'ios';
    const r = await renderScreen();
    expect(r.root.findAllByProps({ accessibilityLabel: 'heatmap-cells' })).toHaveLength(0);
    expect(r.root.findAllByProps({ accessibilityLabel: 'heatmap-gradient-overlay' })).toHaveLength(0);
  });

  it('never renders HeatmapGradientOverlay at all, on any platform (Skia kill-switch, 2026-09-10)', async () => {
    Platform.OS = 'ios';
    mockHeatmapState.cells = [{ lat: 52.1, lng: -106.6, weight: 0.5 }];
    const r = await renderScreen();
    expect(r.root.findAllByProps({ accessibilityLabel: 'heatmap-gradient-overlay' })).toHaveLength(0);
  });

  it('never renders the DemandLegend pill — removed entirely (was overlapping the SOS button)', async () => {
    mockHeatmapState.visible = true;
    const r = await renderScreen();
    expect(r.root.findAllByProps({ accessibilityLabel: 'demand-legend' })).toHaveLength(0);
  });

  it('renders ForecastStrip and HotspotChips only for the v2 heatmap pipeline', async () => {
    mockHeatmapState.isV2 = true;
    mockHeatmapState.forecast = [{ hour: 1, demand: 0.5 }];
    mockHeatmapState.hotspots = [{ lat: 52.1, lng: -106.6, label: 'Downtown' }];
    const r = await renderScreen();
    expect(r.root.findByProps({ accessibilityLabel: 'forecast-strip' })).toBeTruthy();
    expect(r.root.findByProps({ accessibilityLabel: 'hotspot-chip' })).toBeTruthy();
  });

  it('omits ForecastStrip/HotspotChips for the legacy (non-v2) heatmap', async () => {
    mockHeatmapState.isV2 = false;
    mockHeatmapState.hotspots = [{ lat: 52.1, lng: -106.6, label: 'Downtown' }];
    const r = await renderScreen();
    expect(r.root.findAllByProps({ accessibilityLabel: 'forecast-strip' })).toHaveLength(0);
    expect(r.root.findAllByProps({ accessibilityLabel: 'hotspot-chip' })).toHaveLength(0);
  });

  it('tapping a hotspot chip re-centers the map (no crash — mapRef.current is null in this harness)', async () => {
    mockHeatmapState.isV2 = true;
    mockHeatmapState.hotspots = [{ lat: 52.1, lng: -106.6, label: 'Downtown' }];
    const r = await renderScreen();
    const chip = r.root.findByProps({ accessibilityLabel: 'hotspot-chip' });
    expect(() => act(() => { chip.props.onPress(); })).not.toThrow();
  });

  it('none of the heatmap overlay renders once a ride is active (idle-only gating)', async () => {
    mockDriverState.rideState = 'trip_in_progress';
    mockDriverState.activeRide = { ride: { id: 'ride-1' }, rider: { name: 'Alex' } };
    mockHeatmapState.cells = [{ lat: 52.1, lng: -106.6, weight: 0.5 }];
    mockHeatmapState.visible = true;
    mockHeatmapState.isV2 = true;
    mockHeatmapState.hotspots = [{ lat: 52.1, lng: -106.6, label: 'Downtown' }];
    const r = await renderScreen();
    // HeatmapCells/HeatmapGradientOverlay are asserted here too, not just
    // their sibling widgets: the render site itself must gate on
    // rideState === 'idle' rather than relying only on useDemandHeatmap
    // clearing `cells` internally — see the driver dashboard screen's
    // comment at this render site for why (this exact test previously
    // mocked non-empty cells during an active ride without ever checking
    // heatmap-cells stayed absent).
    expect(r.root.findAllByProps({ accessibilityLabel: 'heatmap-cells' })).toHaveLength(0);
    expect(r.root.findAllByProps({ accessibilityLabel: 'heatmap-gradient-overlay' })).toHaveLength(0);
    expect(r.root.findAllByProps({ accessibilityLabel: 'demand-legend' })).toHaveLength(0);
    expect(r.root.findAllByProps({ accessibilityLabel: 'hotspot-chip' })).toHaveLength(0);
  });
});

describe('service-area surge polygon', () => {
  const POLY = [{ lat: 52.1, lng: -106.6 }, { lat: 52.2, lng: -106.6 }, { lat: 52.2, lng: -106.7 }];

  it('renders no polygon when the ride has no service_area_polygon', async () => {
    const r = await renderScreen();
    expect(r.root.findAllByProps({ accessibilityLabel: 'map-polygon' })).toHaveLength(0);
  });

  it('uses the success (calm) color below the first surge tier', async () => {
    mockDriverState.activeRide = { ride: { id: 'r1' }, service_area_polygon: POLY };
    const r = await renderScreen();
    const polygon = r.root.findByProps({ accessibilityLabel: 'map-polygon' });
    expect(polygon.props.strokeColor).toBe(`${COLORS.success}A6`);
  });

  it('uses the heatmap ramp color once surge reaches the first tier (1.25x)', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/notifications?limit=1') return Promise.resolve({ data: { unread_count: 3 } });
      if (url === '/service-areas') return Promise.resolve({ data: [{ id: 'area-1', surge_active: true, surge_multiplier: 1.25 }] });
      return Promise.resolve({ data: {} });
    });
    mockDriverState.activeRide = { ride: { id: 'r1' }, service_area_polygon: POLY };
    const r = await renderScreen();
    const polygon = r.root.findByProps({ accessibilityLabel: 'map-polygon' });
    expect(polygon.props.strokeColor).not.toBe(`${COLORS.success}A6`);
  });

  it('reads the polygon from incomingRide (not activeRide) while an offer is pending', async () => {
    mockDriverState.rideState = 'ride_offered';
    mockDriverState.incomingRide = { ride_id: 'r1', service_area_polygon: POLY };
    const r = await renderScreen();
    expect(r.root.findByProps({ accessibilityLabel: 'map-polygon' })).toBeTruthy();
  });

  it('renders nothing for a polygon with fewer than 3 points', async () => {
    mockDriverState.activeRide = { ride: { id: 'r1' }, service_area_polygon: [{ lat: 52.1, lng: -106.6 }] };
    const r = await renderScreen();
    expect(r.root.findAllByProps({ accessibilityLabel: 'map-polygon' })).toHaveLength(0);
  });
});

describe('airport zone chip', () => {
  it('shows the active airport zone name while idle', async () => {
    mockAirportZonesState = { zones: [], activeZone: { id: 'yxe', name: 'Saskatoon Airport' } };
    const r = await renderScreen();
    expect(allText(r)).toContain('Saskatoon Airport');
  });

  it('is hidden with no active zone', async () => {
    mockAirportZonesState = { zones: [], activeZone: null };
    const r = await renderScreen();
    expect(allText(r)).not.toContain('Airport');
  });

  it('is hidden once a ride is active even with an active zone', async () => {
    mockDriverState.rideState = 'trip_in_progress';
    mockDriverState.activeRide = { ride: { id: 'ride-1' }, rider: { name: 'Alex' } };
    mockAirportZonesState = { zones: [], activeZone: { id: 'yxe', name: 'Saskatoon Airport' } };
    const r = await renderScreen();
    expect(allText(r)).not.toContain('Saskatoon Airport');
  });

  it('renders airport sub-zone polygons while idle', async () => {
    mockAirportZonesState = {
      zones: [{ id: 'yxe', polygon: [{ lat: 52.1, lng: -106.6 }, { lat: 52.2, lng: -106.6 }, { lat: 52.2, lng: -106.7 }] }],
      activeZone: null,
    };
    const r = await renderScreen();
    const polygons = r.root.findAllByProps({ accessibilityLabel: 'map-polygon' });
    expect(polygons.some((p) => p.props.strokeColor === '#0ea5e9')).toBe(true);
  });

  it('omits airport sub-zone polygons with fewer than 3 points', async () => {
    mockAirportZonesState = {
      zones: [{ id: 'yxe', polygon: [{ lat: 52.1, lng: -106.6 }] }],
      activeZone: null,
    };
    const r = await renderScreen();
    const polygons = r.root.findAllByProps({ accessibilityLabel: 'map-polygon' });
    expect(polygons.some((p) => p.props.strokeColor === '#0ea5e9')).toBe(false);
  });
});

describe('denied-location Open Settings button', () => {
  it('calls Linking.openSettings when tapped', async () => {
    const openSettingsSpy = jest.spyOn(Linking, 'openSettings').mockImplementation(() => Promise.resolve());
    mockDashboardState.location = null;
    mockDashboardState.locationStatus = 'denied';
    const r = await renderScreen();
    const openSettingsBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => JSON.stringify(t.props.children) === '"home.openSettings"')
    )!;
    act(() => { openSettingsBtn.props.onPress(); });
    expect(openSettingsSpy).toHaveBeenCalled();
    openSettingsSpy.mockRestore();
  });
});

describe('ride-offer countdown timer', () => {
  beforeEach(() => { jest.useFakeTimers(); });
  afterEach(() => { jest.useRealTimers(); });

  it('re-seeds from countdownSeconds and ticks down every second, pushing 0 back to the store at zero', async () => {
    mockDriverState.rideState = 'ride_offered';
    mockDriverState.countdownSeconds = 2;
    mockDriverState.configuredCountdownSeconds = 2;
    mockDriverState.incomingRide = {
      ride_id: 'ride-1', pickup_lat: 52.1, pickup_lng: -106.6, dropoff_lat: 52.2, dropoff_lng: -106.7,
    };
    const r = await renderScreen();
    expect(r.root.findByType(MockRideOfferPanel).props.countdownSeconds).toBe(2);

    await act(async () => { jest.advanceTimersByTime(1000); });
    expect(r.root.findByType(MockRideOfferPanel).props.countdownSeconds).toBe(1);

    await act(async () => { jest.advanceTimersByTime(1000); });
    expect(r.root.findByType(MockRideOfferPanel).props.countdownSeconds).toBe(0);
    expect(mockSetCountdown).toHaveBeenCalledWith(0);
  });

  it('resyncs the displayed countdown from offer_expires_at when the app returns to the foreground', async () => {
    mockDriverState.rideState = 'ride_offered';
    mockDriverState.countdownSeconds = 15;
    mockDriverState.configuredCountdownSeconds = 15;
    mockDriverState.incomingRide = {
      ride_id: 'ride-1', pickup_lat: 52.1, pickup_lng: -106.6, dropoff_lat: 52.2, dropoff_lng: -106.7,
      offer_expires_at: new Date(Date.now() + 5000).toISOString(),
    };
    const r = await renderScreen();
    await act(async () => {
      appStateListeners.forEach((cb) => cb('active'));
    });
    const panel = r.root.findByType(MockRideOfferPanel);
    expect(panel.props.countdownSeconds).toBeGreaterThanOrEqual(4);
    expect(panel.props.countdownSeconds).toBeLessThanOrEqual(5);
  });

  it('pushes 0 to the store on a foreground resync once the offer has already expired', async () => {
    mockDriverState.rideState = 'ride_offered';
    mockDriverState.countdownSeconds = 15;
    mockDriverState.configuredCountdownSeconds = 15;
    mockDriverState.incomingRide = {
      ride_id: 'ride-1', pickup_lat: 52.1, pickup_lng: -106.6, dropoff_lat: 52.2, dropoff_lng: -106.7,
      offer_expires_at: new Date(Date.now() - 5000).toISOString(),
    };
    await renderScreen();
    await act(async () => {
      appStateListeners.forEach((cb) => cb('active'));
    });
    expect(mockSetCountdown).toHaveBeenCalledWith(0);
  });

  it('ignores a foreground resync outside of ride_offered (no incomingRide)', async () => {
    mockDriverState.rideState = 'idle';
    await renderScreen();
    await act(async () => {
      appStateListeners.forEach((cb) => cb('active'));
    });
    expect(mockSetCountdown).not.toHaveBeenCalled();
  });
});

describe('OSRM live route polling', () => {
  it('draws the OSRM polyline and publishes it to the shared live-route store on success', async () => {
    mockDriverState.rideState = 'navigating_to_pickup';
    mockDriverState.activeRide = { ride: { id: 'ride-1' }, rider: { name: 'Alex' } };
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/notifications?limit=1') return Promise.resolve({ data: { unread_count: 0 } });
      if (url === '/service-areas') return Promise.resolve({ data: [] });
      if (url === '/rides/ride-1/live-route') {
        return Promise.resolve({
          data: {
            polyline: [[52.1, -106.6], [52.15, -106.62], [52.2, -106.65]],
            eta_seconds: 300,
            distance_km: 4.2,
          },
        });
      }
      return Promise.resolve({ data: {} });
    });
    await renderScreen();
    expect(mockPublishLiveRoute).toHaveBeenCalledWith(expect.objectContaining({
      destination: 'pickup',
      rideId: 'ride-1',
      etaMinutes: 5,
      distanceKm: 4.2,
    }));
  });

  it('falls back to Google Directions (clears the shared route) when OSRM has no routable polyline', async () => {
    mockDriverState.rideState = 'navigating_to_pickup';
    mockDriverState.activeRide = { ride: { id: 'ride-1' }, rider: { name: 'Alex' } };
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/notifications?limit=1') return Promise.resolve({ data: { unread_count: 0 } });
      if (url === '/service-areas') return Promise.resolve({ data: [] });
      if (url === '/rides/ride-1/live-route') {
        return Promise.resolve({ data: { polyline: [], eta_seconds: null, distance_km: null } });
      }
      return Promise.resolve({ data: {} });
    });
    await renderScreen();
    expect(mockClearLiveRoute).toHaveBeenCalled();
  });

  it('falls back to Google Directions when the live-route fetch throws', async () => {
    mockDriverState.rideState = 'navigating_to_pickup';
    mockDriverState.activeRide = { ride: { id: 'ride-1' }, rider: { name: 'Alex' } };
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/notifications?limit=1') return Promise.resolve({ data: { unread_count: 0 } });
      if (url === '/service-areas') return Promise.resolve({ data: [] });
      if (url === '/rides/ride-1/live-route') return Promise.reject(new Error('OSRM down'));
      return Promise.resolve({ data: {} });
    });
    await expect(renderScreen()).resolves.toBeTruthy();
    expect(mockClearLiveRoute).toHaveBeenCalled();
  });

  it('does not poll OSRM outside an active-ride rideState', async () => {
    mockDriverState.rideState = 'idle';
    await renderScreen();
    expect(mockApiGet).not.toHaveBeenCalledWith('/rides/ride-1/live-route');
  });
});

describe('saved planned-route polyline reuse', () => {
  it('fits the map to the saved polyline for a ride_offered offer instead of calling Directions', async () => {
    // MapView is mocked with useImperativeHandle, which installs a fresh
    // { fitToCoordinates, animateToRegion } onto mapRef.current on every
    // render (like the real ref callback would) — so the effect's own
    // fitToCoordinates call is asserted via that installed mock, not one
    // pre-seeded here (a pre-seeded object would just get overwritten).
    mockDriverState.rideState = 'ride_offered';
    mockDriverState.incomingRide = {
      ride_id: 'ride-1', pickup_lat: 52.1, pickup_lng: -106.6, dropoff_lat: 52.2, dropoff_lng: -106.7,
      planned_route_polyline: [[52.1, -106.6], [52.15, -106.63], [52.2, -106.7]],
    };
    await renderScreen();
    expect(mockDashboardState.mapRef.current.fitToCoordinates).toHaveBeenCalledWith(
      [
        { latitude: 52.1, longitude: -106.6 },
        { latitude: 52.15, longitude: -106.63 },
        { latitude: 52.2, longitude: -106.7 },
      ],
      expect.objectContaining({ animated: true }),
    );
  });

  it('drops the route when the saved polyline has fewer than 2 usable points', async () => {
    mockDriverState.rideState = 'ride_offered';
    mockDriverState.incomingRide = {
      ride_id: 'ride-1', pickup_lat: 52.1, pickup_lng: -106.6, dropoff_lat: 52.2, dropoff_lng: -106.7,
      planned_route_polyline: [[52.1, -106.6], 'not-a-point'],
    };
    await expect(renderScreen()).resolves.toBeTruthy();
    expect(mockDashboardState.mapRef.current.fitToCoordinates).not.toHaveBeenCalled();
  });
});

describe('SafetyOverlay close', () => {
  it('closes the overlay via its onClose callback', async () => {
    mockDiscreetSosEnabled = true;
    mockDriverState.rideState = 'trip_in_progress';
    mockDriverState.activeRide = { ride: { id: 'ride-1' }, rider: { name: 'Alex' } };
    const r = await renderScreen();
    act(() => { r.root.findByProps({ accessibilityLabel: 'safety-shield' }).props.onPress(); });
    expect(allText(r)).toContain('"SafetyOverlayOpen"');
    act(() => { r.root.findByProps({ accessibilityLabel: 'safety-overlay-close' }).props.onPress(); });
    expect(allText(r)).not.toContain('"SafetyOverlayOpen"');
  });
});

describe('trip-completion confirm modal onRequestClose', () => {
  it('closes the modal on the platform back gesture (onRequestClose)', async () => {
    mockDriverState.rideState = 'trip_in_progress';
    mockDriverState.activeRide = { ride: { id: 'ride-1' }, rider: { name: 'Alex' } };
    mockCompleteRide.mockResolvedValueOnce({ confirmationRequired: true });
    const r = await renderScreen();
    await act(async () => {
      r.root.findByProps({ accessibilityLabel: 'complete-ride' }).props.onPress();
      await flush();
    });
    expect(allText(r)).toContain('"Confirm trip completion"');
    const modal = r.root.findByProps({ transparent: true, animationType: 'fade' });
    act(() => { modal.props.onRequestClose(); });
    expect(allText(r)).not.toContain('"Confirm trip completion"');
  });
});

describe('map recenter control', () => {
  it('MapControls onRecenter calls refreshLocation(false)', async () => {
    const r = await renderScreen();
    act(() => { r.root.findByType(MockMapControls).props.onRecenter(); });
    expect(mockRefreshLocation).toHaveBeenCalledWith(false);
  });

  // Drive → Profile → Drive lost the car marker: Expo Tabs detach the native
  // MapView on blur, and on re-attach react-native-maps re-adds the marker from
  // its frozen custom-view snapshot, which comes back blank. Three attempts to
  // make the snapshot survive failed in live testing; what was OBSERVED to bring
  // the car back is a remount (offline → online bumps mapKey). So the fix
  // remounts the marker on every refocus after the first. This pins that a
  // blur/refocus cycle mounts a fresh CarMarker, and that startup does not.
  it('remounts CarMarker when the Drive tab regains focus, not on first focus', async () => {
    mockCarMarkerMounts.mockClear();
    await renderScreen();
    expect(mockCarMarkerMounts).toHaveBeenCalledTimes(1);
    expect(mockFocusEffectCb).toBeTruthy();

    // The mock already ran the focus callback once on mount (first focus, no
    // remount). Invoking it again is the refocus after a tab switch.
    act(() => { mockFocusEffectCb!(); });
    expect(mockCarMarkerMounts).toHaveBeenCalledTimes(2);

    // And each further return to the tab remounts again.
    act(() => { mockFocusEffectCb!(); });
    expect(mockCarMarkerMounts).toHaveBeenCalledTimes(3);
  });

  it('updates currentRegionRef on MapView onRegionChange', async () => {
    const r = await renderScreen();
    const mapView = r.root.findByType('MapView' as any);
    act(() => {
      mapView.props.onRegionChange({ latitudeDelta: 0.02, longitudeDelta: 0.03 });
    });
    expect(mockDashboardState.currentRegionRef.current).toEqual({ latitudeDelta: 0.02, longitudeDelta: 0.03 });
  });

  // Crash guard (Sentry CRIMSON-SMOKE-7445-SF): react-native-maps'
  // applyBaseMapPadding calls GoogleMap.setPadding with no null check, so a
  // mapPadding UPDATE between layout and onMapReady is an unhandled NPE that
  // blanks the whole React surface — the "white screen when I reopen the app
  // on a pending offer" report. mapPadding is derived from rideState, and
  // consumePendingOffer flips rideState exactly inside that window. So the prop
  // must be withheld until the map says it is ready.
  it('withholds mapPadding until onMapReady, then supplies it', async () => {
    const r = await renderScreen();
    const before = r.root.findByType('MapView' as any);
    expect(before.props.mapPadding).toBeUndefined();

    act(() => {
      before.props.onMapReady();
    });

    const after = r.root.findByType('MapView' as any);
    expect(after.props.mapPadding).toEqual(
      expect.objectContaining({ top: 0, left: 0, right: 0 }),
    );
    // idle state reserves the bottom strip for the HUD — its presence proves
    // the rideState-derived value came through, not a placeholder. (Its numeric
    // value is not asserted: the safe-area mock here returns no insets.bottom,
    // so the pre-existing `+ insets.bottom` formula yields NaN in Jest only.)
    expect(after.props.mapPadding).toHaveProperty('bottom');
  });

  // Crash guard (Sentry CRIMSON-SMOKE-7445-PV): removing the MapView from the
  // tree while the activity is paused runs GoogleMap.onPause on a map with no
  // lifecycle state — a main-thread NPE that killed the process twice during
  // the 2026-09-11 test ride, both with in_foreground:false. The remount that
  // offline → online triggers must therefore wait for the app to be active.
  // A remounted MapView is a fresh instance that has not fired onMapReady, so
  // mapPadding going back to undefined is the observable proof of a remount.
  describe('MapView remount while the app is backgrounded', () => {
    const AppStateMock = require('react-native/Libraries/AppState/AppState').default;

    afterEach(() => {
      AppStateMock.currentState = 'active';
    });

    async function renderOfflineAndReady() {
      mockDashboardState = { ...mockDashboardState, isOnline: false };
      const r = await renderScreen();
      act(() => { r.root.findByType('MapView' as any).props.onMapReady(); });
      expect(r.root.findByType('MapView' as any).props.mapPadding).toBeDefined();
      return r;
    }

    it('remounts immediately on offline → online while active', async () => {
      const r = await renderOfflineAndReady();
      mockDashboardState = { ...mockDashboardState, isOnline: true };
      await act(async () => { r.update(<DriverDashboardScreen />); await flush(); });
      expect(r.root.findByType('MapView' as any).props.mapPadding).toBeUndefined();
    });

    it('defers the remount while backgrounded and replays it on the next active', async () => {
      const r = await renderOfflineAndReady();

      AppStateMock.currentState = 'background';
      mockDashboardState = { ...mockDashboardState, isOnline: true };
      await act(async () => { r.update(<DriverDashboardScreen />); await flush(); });
      // Still the same, ready MapView — nothing was torn down in the background.
      expect(r.root.findByType('MapView' as any).props.mapPadding).toBeDefined();

      // A non-active transition must not release it either.
      act(() => { appStateListeners.forEach((cb) => cb('inactive')); });
      expect(r.root.findByType('MapView' as any).props.mapPadding).toBeDefined();

      AppStateMock.currentState = 'active';
      act(() => { appStateListeners.forEach((cb) => cb('active')); });
      expect(r.root.findByType('MapView' as any).props.mapPadding).toBeUndefined();

      // Replayed once, not on every later foreground.
      act(() => { r.root.findByType('MapView' as any).props.onMapReady(); });
      act(() => { appStateListeners.forEach((cb) => cb('active')); });
      expect(r.root.findByType('MapView' as any).props.mapPadding).toBeDefined();
    });
  });
});

describe('follow-camera throttle (CAMERA_ANIM_MS coalescing)', () => {
  // rideState defaults to 'idle' (a COURSE_UP_RIDE_STATES member) and
  // CarMarker is mocked to `() => null`, so onBearingChange/onPositionChange
  // never fire — camBearingRef and markerPosRef stay null throughout, which
  // means the effect's `center` is always exactly the current location's
  // raw coords (no ahead-offset applied). That makes each animateCamera
  // call's center a direct, assertable proxy for which location update it
  // was fired for.
  beforeEach(() => {
    jest.useFakeTimers();
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  it('fires the first camera update immediately on mount (leading edge)', async () => {
    await renderScreen();
    expect(mockDashboardState.mapRef.current.animateCamera).toHaveBeenCalledTimes(1);
  });

  it('coalesces location updates arriving within the throttle window into one trailing call', async () => {
    const r = await renderScreen();
    const animateCamera = mockDashboardState.mapRef.current.animateCamera;
    expect(animateCamera).toHaveBeenCalledTimes(1);

    // Two more fixes arrive 100ms apart — well under the 700ms window. iOS's
    // distanceFilter-based watchPositionAsync has no time floor (see the
    // effect's own comment in index.tsx), so this is a realistic cadence
    // during driving, not a contrived one.
    act(() => {
      mockDashboardState.location = { coords: { latitude: 52.1001, longitude: -106.6, heading: 0 } };
      jest.advanceTimersByTime(100);
      r.update(<DriverDashboardScreen />);
    });
    expect(animateCamera).toHaveBeenCalledTimes(1); // still throttled — no second call yet

    act(() => {
      mockDashboardState.location = { coords: { latitude: 52.1002, longitude: -106.6, heading: 0 } };
      jest.advanceTimersByTime(100);
      r.update(<DriverDashboardScreen />);
    });
    expect(animateCamera).toHaveBeenCalledTimes(1); // still coalesced

    // Advance past the remaining window (700ms - 200ms elapsed so far).
    act(() => {
      jest.advanceTimersByTime(500);
    });
    expect(animateCamera).toHaveBeenCalledTimes(2);
    // The trailing call must carry the LATEST location, not the first
    // update's — a fast-changing position must never be silently dropped.
    const lastCall = animateCamera.mock.calls[animateCamera.mock.calls.length - 1][0];
    expect(lastCall.center.latitude).toBeCloseTo(52.1002, 4);
  });

  it('fires immediately again once the throttle window has fully elapsed with no pending update', async () => {
    const r = await renderScreen();
    const animateCamera = mockDashboardState.mapRef.current.animateCamera;
    expect(animateCamera).toHaveBeenCalledTimes(1);

    act(() => {
      jest.advanceTimersByTime(800); // past CAMERA_ANIM_MS, nothing pending
    });
    act(() => {
      mockDashboardState.location = { coords: { latitude: 52.11, longitude: -106.6, heading: 0 } };
      r.update(<DriverDashboardScreen />);
    });
    expect(animateCamera).toHaveBeenCalledTimes(2); // leading edge again, no wait
  });
});

describe('offline<->online camera framing', () => {
  // rideState stays 'idle' across this toggle (only `isOnline` changes), and
  // going offline halts watchPositionAsync entirely (useDriverDashboard.ts),
  // so before this fix nothing ever re-framed the camera on this transition
  // — it just stayed wherever the follow-camera effect had last left it.
  // CarMarker is mocked to `() => null` (see top-of-file comment), so
  // camBearingRef/markerPosRef stay null and `center`/`heading` are always
  // derived from the raw location fix, same as the throttle tests above.
  it('zooms out on going offline', async () => {
    const r = await renderScreen();
    const animateCamera = mockDashboardState.mapRef.current.animateCamera;
    animateCamera.mockClear(); // drop the mount-time leading-edge call

    act(() => {
      mockDashboardState = { ...mockDashboardState, isOnline: false };
      r.update(<DriverDashboardScreen />);
    });

    expect(animateCamera).toHaveBeenCalledTimes(1);
    const call = animateCamera.mock.calls[0][0];
    expect(call.zoom).toBe(14);
    expect(call.heading).toBe(0);
    expect(call.center).toEqual({ latitude: LOCATION.coords.latitude, longitude: LOCATION.coords.longitude });
  });

  it('zooms back in on returning online', async () => {
    mockDashboardState = { ...mockDashboardState, isOnline: false };
    const r = await renderScreen();
    const animateCamera = mockDashboardState.mapRef.current.animateCamera;
    animateCamera.mockClear();

    act(() => {
      mockDashboardState = { ...mockDashboardState, isOnline: true };
      r.update(<DriverDashboardScreen />);
    });

    expect(animateCamera).toHaveBeenCalledTimes(1);
    expect(animateCamera.mock.calls[0][0].zoom).toBe(17.5); // FOLLOW_ZOOM_TIERS[0] — stopped/idle tier
  });

  it('does not fire on a re-render where isOnline is unchanged', async () => {
    const r = await renderScreen();
    const animateCamera = mockDashboardState.mapRef.current.animateCamera;
    animateCamera.mockClear();

    act(() => {
      mockDashboardState = { ...mockDashboardState, wsLatency: 42 }; // unrelated re-render
      r.update(<DriverDashboardScreen />);
    });

    expect(animateCamera).not.toHaveBeenCalled();
  });
});
