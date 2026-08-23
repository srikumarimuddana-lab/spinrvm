/**
 * app/driver-arriving.tsx — ride-critical "waiting for/tracking driver"
 * screen (searching + driver-en-route states, live map). Pins:
 *  - fetches the ride on mount and polls every 5s
 *  - status-based navigation: driver_arrived -> /driver-arrived,
 *    in_progress -> /ride-in-progress, completed -> /ride-completed,
 *    cancelled -> clears the ride and returns to /(tabs)
 *  - isSearching (no ride / searching / driver_assigned) shows the
 *    "Looking for a driver" section with a free cancel-search button;
 *    hasDriver (driver_accepted/arrived/in_progress) shows the driver
 *    card, PIN, and action row instead
 *  - handleCancel's four status-specific confirm dialogs (in_progress:
 *    full fare; driver_arrived: cancellation fee; driver_accepted: free;
 *    else: cancel search) — each opens the reason sheet on confirm,
 *    which cancels + clears + navigates home; a cancel failure toasts
 *    without navigating
 *  - the hardware back button triggers the same cancel-confirm flow
 *  - handleShareTrip: toasts "Tracking Not Configured" when
 *    trackBaseUrl is unset; otherwise shares a formatted info blob and
 *    never crashes if Share.share rejects
 *  - handleCopyDetails copies driver+vehicle+plate text and toasts
 *  - Message navigates to /chat-driver with rideId
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, Share, BackHandler } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));
jest.mock('@shared/components/ErrorBoundary', () => ({
  ErrorBoundary: ({ children }: any) => children,
}));

jest.mock('react-native-maps', () => {
  const ReactActual = require('react');
  const MapView = ReactActual.forwardRef((props: any, ref: any) => {
    ReactActual.useImperativeHandle(ref, () => ({ fitToCoordinates: jest.fn() }));
    return ReactActual.createElement('MapView', props, props.children);
  });
  const Polygon = () => null;
  return { __esModule: true, default: MapView, Polygon, PROVIDER_GOOGLE: 'google' };
});
jest.mock('react-native-maps-directions', () => () => null);
jest.mock('@shared/components/RouteLine', () => ({ RouteLine: () => null }));
jest.mock('@shared/components/RoutePins', () => ({ RoutePins: () => null }));
jest.mock('@shared/components/CarMarker', () => ({ CarMarker: () => null }));

jest.mock('../components/SafeBottomSheet', () => {
  const ReactActual = require('react');
  const BottomSheet = ReactActual.forwardRef((props: any, _ref: any) => props.children);
  const BottomSheetScrollView = (props: any) => props.children;
  return { __esModule: true, default: BottomSheet, BottomSheetScrollView };
});

jest.mock('../hooks/useAppResumeKey', () => ({ useAppResumeKey: () => 0 }));
jest.mock('@shared/utils/responsive', () => ({ useResponsive: () => ({ sf: (n: number) => n }) }));

jest.mock('../app/_layout', () => ({
  TrackBaseUrlContext: require('react').createContext(null),
}));

const mockBack = jest.fn();
const mockPush = jest.fn();
const mockReplace = jest.fn();
let mockParams: any;
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: mockPush, replace: mockReplace }),
  useLocalSearchParams: () => mockParams,
}));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB', success: '#10B981',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a), post: (...a: any[]) => mockApiPost(...a) },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const mockSetStringAsync = jest.fn();
jest.mock('expo-clipboard', () => ({ setStringAsync: (...a: any[]) => mockSetStringAsync(...a) }));

const mockT = (key: string) => key;
jest.mock('../i18n', () => ({ useTranslation: () => ({ t: mockT }) }));

jest.mock('../components/RiderSOS', () => ({ RiderSOS: () => null }));
jest.mock('../components/FreeCancelTimer', () => ({ FreeCancelTimer: () => null }));

jest.mock('../components/ConfirmSheet', () => (props: any) => {
  const { View, Text: RNText, TouchableOpacity: RNTouchableOpacity } = require('react-native');
  if (!props.visible) return null;
  return (
    <View>
      <RNText>{props.title}</RNText>
      <RNText>{props.message}</RNText>
      {(props.buttons || []).map((b: any, i: number) => (
        <RNTouchableOpacity key={i} onPress={b.onPress || props.onClose} accessibilityLabel={`confirm-${b.text}`}>
          <RNText>{b.text}</RNText>
        </RNTouchableOpacity>
      ))}
    </View>
  );
});

jest.mock('../components/CancelReasonSheet', () => (props: any) => {
  const { View, Text: RNText, TouchableOpacity: RNTouchableOpacity } = require('react-native');
  if (!props.visible) return null;
  return (
    <View>
      <RNText>{props.message}</RNText>
      <RNTouchableOpacity onPress={() => props.onConfirm('Changed my mind')} accessibilityLabel="submit-cancel-reason">
        <RNText>Submit Reason</RNText>
      </RNTouchableOpacity>
    </View>
  );
});

const mockFetchRide = jest.fn();
const mockCancelRide = jest.fn();
const mockClearRide = jest.fn();
const mockTriggerEmergency = jest.fn();
const mockSetActiveRideRouteCoords = jest.fn();
const mockSetActiveDriverRouteCoords = jest.fn();
let mockRideState: any;
jest.mock('../store/rideStore', () => ({
  useRideStore: Object.assign((...a: any[]) => mockRideState, { getState: () => mockRideState }),
}));

import DriverArrivingScreen from '../app/driver-arriving';
import { TrackBaseUrlContext } from '../app/_layout';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const RIDE_SEARCHING = {
  id: 'ride-1', status: 'searching',
  pickup_address: '100 Main St', dropoff_address: '200 Elm St',
};
const RIDE_WITH_DRIVER = {
  id: 'ride-1', status: 'driver_accepted', pickup_otp: '4821',
  pickup_lat: 50.45, pickup_lng: -104.6, dropoff_lat: 50.5, dropoff_lng: -104.5,
  pickup_address: '100 Main St', dropoff_address: '200 Elm St',
  total_fare: '15.00', grand_total: '15.00', cancellation_fee: 4.5, free_cancel_window_seconds: 120,
  driver_accepted_at: '2026-08-22T10:00:00Z',
};
const CURRENT_DRIVER = {
  id: 'driver-1', name: 'Sam Lee', rating: 4.9, vehicle_color: 'Blue', vehicle_make: 'Toyota', vehicle_model: 'Camry',
  license_plate: 'ABC 123', photo_url: null,
};

let mockTrackBaseUrl: string | null = 'https://spinr-track.app';

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(
      <TrackBaseUrlContext.Provider value={mockTrackBaseUrl}>
        <DriverArrivingScreen />
      </TrackBaseUrlContext.Provider>,
    );
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => { try { return JSON.stringify(t.props.children); } catch { return '<circular>'; } }).join(' | ');
}

function findButtonByText(r: TestRenderer.ReactTestRenderer, text: string) {
  return r.root
    .findAllByType(TouchableOpacity)
    .find((n) => n.findAllByType(Text).some((t) => {
      try { return JSON.stringify(t.props.children).includes(text); } catch { return false; }
    }))!;
}

beforeEach(() => {
  jest.clearAllMocks();
  jest.useFakeTimers();
  mockParams = { rideId: 'ride-1' };
  mockTrackBaseUrl = 'https://spinr-track.app';
  mockRideState = {
    currentRide: RIDE_SEARCHING,
    currentDriver: null,
    fetchRide: mockFetchRide,
    triggerEmergency: mockTriggerEmergency,
    driverEtaSeconds: null,
    cancelRide: mockCancelRide,
    clearRide: mockClearRide,
    activeRideRouteCoords: null,
    activeDriverRouteCoords: null,
    setActiveRideRouteCoords: mockSetActiveRideRouteCoords,
    setActiveDriverRouteCoords: mockSetActiveDriverRouteCoords,
    simulateDriverArrival: jest.fn(),
  };
  mockApiGet.mockResolvedValue({ data: [] });
  mockCancelRide.mockResolvedValue(undefined);
  jest.spyOn(Share, 'share').mockResolvedValue({ action: 'sharedAction' } as any);
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.useRealTimers();
  jest.restoreAllMocks();
});

describe('DriverArrivingScreen', () => {
  it('fetches the ride on mount and polls every 5s', async () => {
    await renderScreen();
    expect(mockFetchRide).toHaveBeenCalledWith('ride-1');
    mockFetchRide.mockClear();
    await act(async () => {
      jest.advanceTimersByTime(5000);
      await flush();
    });
    expect(mockFetchRide).toHaveBeenCalledWith('ride-1');
  });

  it('shows the "Looking for a driver" section while searching', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('Looking for a driver');
    expect(() => findButtonByText(r, 'Message')).not.toThrow();
    expect(findButtonByText(r, 'Message')).toBeUndefined();
  });

  it('shows the driver card and PIN once a driver is assigned', async () => {
    mockRideState.currentRide = RIDE_WITH_DRIVER;
    mockRideState.currentDriver = CURRENT_DRIVER;
    const r = await renderScreen();
    expect(allText(r)).toContain('YOUR DRIVER');
    expect(allText(r)).toContain('SHARE THIS PIN WITH YOUR DRIVER');
    expect(allText(r)).not.toContain('Looking for a driver');
  });

  it('redirects to /driver-arrived once the status flips to driver_arrived', async () => {
    mockRideState.currentRide = RIDE_WITH_DRIVER;
    mockRideState.currentDriver = CURRENT_DRIVER;
    const r = await renderScreen();
    await act(async () => {
      mockRideState = { ...mockRideState, currentRide: { ...RIDE_WITH_DRIVER, status: 'driver_arrived' } };
      renderer!.update(<TrackBaseUrlContext.Provider value={mockTrackBaseUrl}><DriverArrivingScreen /></TrackBaseUrlContext.Provider>);
      await flush();
    });
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/driver-arrived', params: { rideId: 'ride-1' } });
  });

  it('redirects to /ride-in-progress once the status flips to in_progress', async () => {
    mockRideState.currentRide = RIDE_WITH_DRIVER;
    mockRideState.currentDriver = CURRENT_DRIVER;
    const r = await renderScreen();
    await act(async () => {
      mockRideState = { ...mockRideState, currentRide: { ...RIDE_WITH_DRIVER, status: 'in_progress' } };
      renderer!.update(<TrackBaseUrlContext.Provider value={mockTrackBaseUrl}><DriverArrivingScreen /></TrackBaseUrlContext.Provider>);
      await flush();
    });
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/ride-in-progress', params: { rideId: 'ride-1' } });
  });

  it('redirects to /ride-completed once the status flips to completed', async () => {
    mockRideState.currentRide = RIDE_WITH_DRIVER;
    const r = await renderScreen();
    await act(async () => {
      mockRideState = { ...mockRideState, currentRide: { ...RIDE_WITH_DRIVER, status: 'completed' } };
      renderer!.update(<TrackBaseUrlContext.Provider value={mockTrackBaseUrl}><DriverArrivingScreen /></TrackBaseUrlContext.Provider>);
      await flush();
    });
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/ride-completed', params: { rideId: 'ride-1' } });
  });

  it('clears the ride and returns home once the status flips to cancelled', async () => {
    mockRideState.currentRide = RIDE_WITH_DRIVER;
    const r = await renderScreen();
    await act(async () => {
      mockRideState = { ...mockRideState, currentRide: { ...RIDE_WITH_DRIVER, status: 'cancelled' } };
      renderer!.update(<TrackBaseUrlContext.Provider value={mockTrackBaseUrl}><DriverArrivingScreen /></TrackBaseUrlContext.Provider>);
      await flush();
    });
    expect(mockClearRide).toHaveBeenCalled();
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('opens the "Cancel search?" confirm for a free/no-driver-yet cancel', async () => {
    const r = await renderScreen();
    const cancelBtn = findButtonByText(r, 'Cancel search');
    act(() => { cancelBtn.props.onPress(); });
    expect(allText(r)).toContain('Cancel search?');
  });

  it('quotes the free-cancel copy for driver_accepted', async () => {
    mockRideState.currentRide = RIDE_WITH_DRIVER;
    mockRideState.currentDriver = CURRENT_DRIVER;
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => { backBtn.props.onPress(); });
    expect(allText(r)).toContain('Cancel for free right now.');
  });

  it('quotes the cancellation fee for driver_arrived', async () => {
    mockRideState.currentRide = { ...RIDE_WITH_DRIVER, status: 'driver_arrived' };
    mockRideState.currentDriver = CURRENT_DRIVER;
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => { backBtn.props.onPress(); });
    expect(allText(r)).toContain('A cancellation fee of $4.50 will be charged.');
  });

  it('quotes the full fare for in_progress', async () => {
    mockRideState.currentRide = { ...RIDE_WITH_DRIVER, status: 'in_progress' };
    mockRideState.currentDriver = CURRENT_DRIVER;
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => { backBtn.props.onPress(); });
    expect(allText(r)).toContain('you will be charged the full fare of $15.00.');
  });

  it('confirming cancellation opens the reason sheet, then submitting cancels + clears + navigates home', async () => {
    mockRideState.currentRide = RIDE_WITH_DRIVER;
    mockRideState.currentDriver = CURRENT_DRIVER;
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => { backBtn.props.onPress(); });
    const confirmBtn = r.root.findByProps({ accessibilityLabel: 'confirm-Cancel (Free)' });
    act(() => { confirmBtn.props.onPress(); });
    const submitBtn = r.root.findByProps({ accessibilityLabel: 'submit-cancel-reason' });
    await act(async () => { await submitBtn.props.onPress(); await flush(); });
    expect(mockCancelRide).toHaveBeenCalledWith('Changed my mind');
    expect(mockClearRide).toHaveBeenCalled();
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('toasts instead of navigating when cancellation fails', async () => {
    mockCancelRide.mockRejectedValue(new Error('server error'));
    mockRideState.currentRide = RIDE_WITH_DRIVER;
    mockRideState.currentDriver = CURRENT_DRIVER;
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => { backBtn.props.onPress(); });
    const confirmBtn = r.root.findByProps({ accessibilityLabel: 'confirm-Cancel (Free)' });
    act(() => { confirmBtn.props.onPress(); });
    const submitBtn = r.root.findByProps({ accessibilityLabel: 'submit-cancel-reason' });
    await act(async () => { await submitBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Cancel Failed', 'Could not cancel the ride. Please try again.', 'danger');
    expect(mockReplace).not.toHaveBeenCalledWith('/(tabs)');
  });

  it('triggers the same cancel-confirm flow on the hardware back button', async () => {
    const addListenerSpy = jest.spyOn(BackHandler, 'addEventListener');
    const r = await renderScreen();
    const handler = addListenerSpy.mock.calls.find((c) => c[0] === 'hardwareBackPress')![1] as () => boolean;
    act(() => { handler(); });
    expect(allText(r)).toContain('Cancel search?');
  });

  it('toasts "Tracking Not Configured" when trackBaseUrl is unset', async () => {
    mockTrackBaseUrl = null;
    mockRideState.currentRide = RIDE_WITH_DRIVER;
    mockRideState.currentDriver = CURRENT_DRIVER;
    const r = await renderScreen();
    const shareButtons = r.root.findAllByType(TouchableOpacity).filter((n) =>
      n.findAllByProps({ name: 'share-outline' }).length > 0
    );
    await act(async () => { await shareButtons[0].props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith(
      'Tracking Not Configured', 'Live trip tracking is not set up yet. Please contact support.', 'warning',
    );
    expect(Share.share).not.toHaveBeenCalled();
  });

  it('shares a formatted trip-info blob and never crashes if Share.share rejects', async () => {
    (Share.share as jest.Mock).mockRejectedValue(new Error('share failed'));
    mockRideState.currentRide = RIDE_WITH_DRIVER;
    mockRideState.currentDriver = CURRENT_DRIVER;
    const r = await renderScreen();
    const shareButtons = r.root.findAllByType(TouchableOpacity).filter((n) =>
      n.findAllByProps({ name: 'share-outline' }).length > 0
    );
    await act(async () => { await shareButtons[0].props.onPress(); await flush(); });
    expect(Share.share).toHaveBeenCalledWith(expect.objectContaining({
      message: expect.stringContaining('Driver: Sam Lee'),
    }));
    expect(r.root).toBeTruthy();
  });

  it('copies driver details and toasts', async () => {
    mockRideState.currentRide = RIDE_WITH_DRIVER;
    mockRideState.currentDriver = CURRENT_DRIVER;
    const r = await renderScreen();
    const copyBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByProps({ name: 'copy-outline' }).length > 0
    )!;
    await act(async () => { await copyBtn.props.onPress(); await flush(); });
    expect(mockSetStringAsync).toHaveBeenCalledWith(expect.stringContaining('Driver: Sam Lee'));
    expect(mockShowToast).toHaveBeenCalledWith('Copied!', 'Driver details copied to clipboard.', 'success');
  });

  it('navigates to /chat-driver with rideId on Message', async () => {
    mockRideState.currentRide = RIDE_WITH_DRIVER;
    mockRideState.currentDriver = CURRENT_DRIVER;
    const r = await renderScreen();
    const msgBtn = findButtonByText(r, 'Message');
    act(() => { msgBtn.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/chat-driver', params: { rideId: 'ride-1' } });
  });
});
