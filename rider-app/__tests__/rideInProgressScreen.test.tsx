/**
 * app/ride-in-progress.tsx — ride-critical in-trip screen (live map, ETA,
 * end-ride-early flow, share trip). Pins:
 *  - fetches the ride on mount and redirects home if there's no rideId;
 *    polls every 15s only when NOT WS-connected
 *  - redirects to /ride-completed once the status flips to completed
 *  - handleShareTrip: toasts "Tracking Not Configured" when trackBaseUrl
 *    is unset; otherwise shares a formatted trip-info blob and marks
 *    location sharing active, showing the LIVE banner
 *  - handleCopyTrackingLink copies the tracking link and toasts
 *  - handleOpenTrackingView navigates to /ride-tracking-webview
 *  - the "End Ride" action (and the same dialog via hardware back) opens
 *    a confirm sheet quoting the full fare; confirming POSTs /complete
 *    and refetches, with a failure toast on error
 *  - Message Driver navigates to /chat-driver with rideId
 *  - a driver photo load error falls back to the placeholder icon
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, Image, Share, BackHandler } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));
jest.mock('@shared/components/ErrorBoundary', () => ({
  ErrorBoundary: ({ children }: any) => children,
}));

jest.mock('react-native-maps', () => {
  const ReactActual = require('react');
  const MapView = ReactActual.forwardRef((props: any, ref: any) => {
    ReactActual.useImperativeHandle(ref, () => ({ fitToCoordinates: jest.fn(), animateToRegion: jest.fn() }));
    return ReactActual.createElement('MapView', props, props.children);
  });
  return { __esModule: true, default: MapView, PROVIDER_GOOGLE: 'google' };
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
jest.mock('../components/RiderSOS', () => ({ RiderSOS: () => null }));

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
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB',
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

const mockFetchRide = jest.fn();
const mockTriggerEmergency = jest.fn();
const mockSetActiveRideRouteCoords = jest.fn();
const mockSetLastEtaMin = jest.fn();
let mockRideState: any;
jest.mock('../store/rideStore', () => ({
  useRideStore: Object.assign((...a: any[]) => mockRideState, { getState: () => mockRideState }),
}));

import RideInProgressScreen from '../app/ride-in-progress';
import { TrackBaseUrlContext } from '../app/_layout';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const CURRENT_RIDE = {
  id: 'ride-1', status: 'in_progress', ride_code: 'RIDE001',
  pickup_lat: 50.45, pickup_lng: -104.6, dropoff_lat: 50.5, dropoff_lng: -104.5,
  pickup_address: '100 Main St', dropoff_address: '200 Elm St',
  grand_total: '18.00', total_fare: '18.00', distance_km: 5.2,
};
const CURRENT_DRIVER = {
  id: 'driver-1', name: 'Sam Lee', rating: 4.9, vehicle_color: 'Blue', vehicle_make: 'Toyota', vehicle_model: 'Camry',
  license_plate: 'ABC 123', photo_url: null, lat: 50.46, lng: -104.58,
};

let mockTrackBaseUrl: string | null = 'https://spinr-track.app';

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(
      <TrackBaseUrlContext.Provider value={mockTrackBaseUrl}>
        <RideInProgressScreen />
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
    currentRide: CURRENT_RIDE,
    currentDriver: CURRENT_DRIVER,
    fetchRide: mockFetchRide,
    triggerEmergency: mockTriggerEmergency,
    isLoading: false,
    error: null,
    wsConnected: true,
    activeRideRouteCoords: [{ latitude: 50.46, longitude: -104.58 }, { latitude: 50.5, longitude: -104.5 }],
    lastEtaMin: 8,
    setActiveRideRouteCoords: mockSetActiveRideRouteCoords,
    setLastEtaMin: mockSetLastEtaMin,
  };
  mockApiGet.mockImplementation((url: string) => {
    if (url.includes('/share')) return Promise.resolve({ data: { share_token: 'tok123' } });
    if (url.includes('/live-route')) return Promise.resolve({ data: { polyline: [], eta_seconds: null } });
    return Promise.reject(new Error('unexpected url ' + url));
  });
  mockApiPost.mockResolvedValue({});
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

describe('RideInProgressScreen', () => {
  it('fetches the ride on mount', async () => {
    await renderScreen();
    expect(mockFetchRide).toHaveBeenCalledWith('ride-1');
  });

  it('redirects home when there is no rideId', async () => {
    mockParams = {};
    await renderScreen();
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('polls every 15s when NOT WS-connected', async () => {
    mockRideState.wsConnected = false;
    await renderScreen();
    mockFetchRide.mockClear();
    await act(async () => {
      jest.advanceTimersByTime(15000);
      await flush();
    });
    expect(mockFetchRide).toHaveBeenCalledWith('ride-1');
  });

  it('does not poll when WS-connected', async () => {
    await renderScreen();
    mockFetchRide.mockClear();
    await act(async () => {
      jest.advanceTimersByTime(30000);
      await flush();
    });
    expect(mockFetchRide).not.toHaveBeenCalled();
  });

  it('redirects to /ride-completed once the status flips to completed', async () => {
    const r = await renderScreen();
    await act(async () => {
      mockRideState = { ...mockRideState, currentRide: { ...CURRENT_RIDE, status: 'completed' } };
      renderer!.update(
        <TrackBaseUrlContext.Provider value={mockTrackBaseUrl}>
          <RideInProgressScreen />
        </TrackBaseUrlContext.Provider>,
      );
      await flush();
    });
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/ride-completed', params: { rideId: 'ride-1' } });
  });

  it('toasts "Tracking Not Configured" when trackBaseUrl is unset', async () => {
    mockTrackBaseUrl = null;
    const r = await renderScreen();
    const shareBtn = findButtonByText(r, 'Share Trip');
    await act(async () => { await shareBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith(
      'Tracking Not Configured', 'Live trip tracking is not set up yet. Please contact support.', 'warning',
    );
    expect(Share.share).not.toHaveBeenCalled();
  });

  it('shares the trip and shows the LIVE sharing banner', async () => {
    const r = await renderScreen();
    const shareBtn = findButtonByText(r, 'Share Trip');
    await act(async () => { await shareBtn.props.onPress(); await flush(); });
    expect(Share.share).toHaveBeenCalledWith(expect.objectContaining({
      message: expect.stringContaining('Sam Lee'),
      title: 'Track My Spinr Ride',
    }));
    expect(mockShowToast).toHaveBeenCalledWith('Trip Shared!', 'Your live location is now being shared.', 'success');
    expect(allText(r)).toContain('LIVE');
  });

  it('copies the tracking link and toasts', async () => {
    const r = await renderScreen();
    // Share first, to reveal the copy-link icon in the LIVE banner.
    const shareBtn = findButtonByText(r, 'Share Trip');
    await act(async () => { await shareBtn.props.onPress(); await flush(); });
    const copyBtn = r.root.findByProps({ accessibilityLabel: 'Copy live tracking link' });
    await act(async () => { await copyBtn.props.onPress(); await flush(); });
    expect(mockSetStringAsync).toHaveBeenCalledWith('https://spinr-track.app/tok123');
    expect(mockShowToast).toHaveBeenCalledWith('Copied!', 'Live tracking link copied to clipboard.', 'success');
  });

  it('navigates to /ride-tracking-webview on Live Map', async () => {
    const r = await renderScreen();
    const liveMapBtn = findButtonByText(r, 'Live Map');
    act(() => { liveMapBtn.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/ride-tracking-webview', params: { rideId: 'ride-1' } });
  });

  it('opens the end-ride confirm quoting the full fare, then completes on confirm', async () => {
    const r = await renderScreen();
    const endRideBtn = findButtonByText(r, 'End Ride');
    act(() => { endRideBtn.props.onPress(); });
    expect(allText(r)).toContain('End ride early?');
    expect(allText(r)).toContain('You will be charged the full agreed fare of $18.00. This cannot be undone.');
    const confirmBtn = r.root.findByProps({ accessibilityLabel: 'confirm-End & Pay Full Fare' });
    await act(async () => { await confirmBtn.props.onPress(); await flush(); });
    expect(mockApiPost).toHaveBeenCalledWith('/rides/ride-1/complete');
    expect(mockFetchRide).toHaveBeenCalledWith('ride-1');
  });

  it('toasts a failure when ending the ride fails, without crashing', async () => {
    mockApiPost.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const endRideBtn = findButtonByText(r, 'End Ride');
    act(() => { endRideBtn.props.onPress(); });
    const confirmBtn = r.root.findByProps({ accessibilityLabel: 'confirm-End & Pay Full Fare' });
    await act(async () => { await confirmBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Error', 'Could not end ride. Please try again.', 'danger');
  });

  it('opens the same end-ride confirm on the hardware back button', async () => {
    const addListenerSpy = jest.spyOn(BackHandler, 'addEventListener');
    const r = await renderScreen();
    const handler = addListenerSpy.mock.calls.find((c) => c[0] === 'hardwareBackPress')![1] as () => boolean;
    act(() => { handler(); });
    expect(allText(r)).toContain('End ride early?');
    expect(allText(r)).toContain('Full fare of $18.00 applies. Your driver will continue.');
  });

  it('navigates to /chat-driver with rideId on Message driver', async () => {
    const r = await renderScreen();
    const msgBtn = r.root.findByProps({ accessibilityLabel: 'Message driver' });
    act(() => { msgBtn.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/chat-driver', params: { rideId: 'ride-1' } });
  });

  it('falls back to the placeholder icon when the driver photo fails to load', async () => {
    mockRideState.currentDriver = { ...CURRENT_DRIVER, photo_url: 'https://example.com/x.jpg' };
    const r = await renderScreen();
    const img = r.root.findByType(Image);
    act(() => { img.props.onError(); });
    expect(r.root.findAllByType(Image)).toHaveLength(0);
  });
});
