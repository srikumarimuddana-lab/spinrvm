/**
 * app/driver-arrived.tsx — ride-critical "driver has arrived" screen
 * (OTP handoff, cancellation-fee flow, live map). Pins:
 *  - fetchRide on mount; polls every 15s only when NOT WS-connected (no
 *    interval when wsConnected is true)
 *  - redirects to /ride-in-progress once the ride status flips to
 *    in_progress
 *  - Cancel (header back button, or the footer link) opens a confirm
 *    sheet quoting the server-provided cancellation fee (not a
 *    client-computed one); confirming opens the reason sheet; submitting
 *    a reason cancels + clears the ride + navigates home; a cancel
 *    failure toasts instead of navigating
 *  - the hardware back button also triggers the same cancel-confirm flow
 *  - OTP: tapping the PIN copies it to the clipboard and toasts
 *  - Message Driver navigates to /chat-driver with rideId
 *  - Share Trip shares a formatted info blob (driver, vehicle, plate,
 *    addresses, OTP) and never crashes if Share.share rejects
 *  - a driver photo load error falls back to the placeholder icon
 *  - no currentRide yet shows a loading/retry state that calls fetchRide
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, Image, Share, BackHandler } from 'react-native';
import MapView from 'react-native-maps';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));
jest.mock('@shared/components/ErrorBoundary', () => ({
  ErrorBoundary: ({ children }: any) => children,
}));

const mockFitToCoordinates = jest.fn();
jest.mock('react-native-maps', () => {
  const React = require('react');
  const MapView = React.forwardRef((props: any, ref: any) => {
    React.useImperativeHandle(ref, () => ({ fitToCoordinates: mockFitToCoordinates }));
    return React.createElement('MapView', props, props.children);
  });
  return { __esModule: true, default: MapView, PROVIDER_GOOGLE: 'google' };
});
jest.mock('react-native-maps-directions', () => () => null);
jest.mock('@shared/components/RouteLine', () => ({ RouteLine: () => null }));
jest.mock('@shared/components/RoutePins', () => ({ RoutePins: () => null }));
jest.mock('@shared/components/CarMarker', () => ({ CarMarker: () => null }));

jest.mock('../components/SafeBottomSheet', () => {
  const React = require('react');
  const BottomSheet = React.forwardRef((props: any, _ref: any) => props.children);
  const BottomSheetScrollView = (props: any) => props.children;
  return { __esModule: true, default: BottomSheet, BottomSheetScrollView };
});

jest.mock('../hooks/useAppResumeKey', () => ({ useAppResumeKey: () => 0 }));

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

const mockApiPost = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { post: (...a: any[]) => mockApiPost(...a) },
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
let mockRideState: any;
jest.mock('../store/rideStore', () => ({
  useRideStore: () => mockRideState,
}));

import DriverArrivedScreen from '../app/driver-arrived';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const CURRENT_RIDE = {
  id: 'ride-1', status: 'driver_arrived', pickup_otp: '4821',
  pickup_lat: 50.45, pickup_lng: -104.6, dropoff_lat: 50.5, dropoff_lng: -104.5,
  pickup_address: '100 Main St', dropoff_address: '200 Elm St',
  total_fare: '15.00', distance_km: 3.2, duration_minutes: 8,
  cancellation_fee: 4.5, free_cancel_window_seconds: 120,
};
const CURRENT_DRIVER = {
  name: 'Sam Lee', rating: 4.9, vehicle_color: 'Blue', vehicle_make: 'Toyota', vehicle_model: 'Camry',
  license_plate: 'ABC 123', photo_url: null,
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<DriverArrivedScreen />);
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
  mockRideState = {
    currentRide: CURRENT_RIDE,
    currentDriver: CURRENT_DRIVER,
    fetchRide: mockFetchRide,
    cancelRide: mockCancelRide,
    clearRide: mockClearRide,
    triggerEmergency: mockTriggerEmergency,
    wsConnected: true,
  };
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

describe('DriverArrivedScreen', () => {
  it('fetches the ride on mount', async () => {
    await renderScreen();
    expect(mockFetchRide).toHaveBeenCalledWith('ride-1');
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
    mockRideState.wsConnected = true;
    await renderScreen();
    mockFetchRide.mockClear();
    await act(async () => {
      jest.advanceTimersByTime(30000);
      await flush();
    });
    expect(mockFetchRide).not.toHaveBeenCalled();
  });

  it('redirects to /ride-in-progress once the ride status flips to in_progress', async () => {
    const r = await renderScreen();
    await act(async () => {
      mockRideState = { ...mockRideState, currentRide: { ...CURRENT_RIDE, status: 'in_progress' } };
      renderer!.update(<DriverArrivedScreen />);
      await flush();
    });
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/ride-in-progress', params: { rideId: 'ride-1' } });
  });

  it('opens the confirm sheet quoting the server-provided cancellation fee on Cancel Ride', async () => {
    const r = await renderScreen();
    const cancelLink = findButtonByText(r, 'Cancel Ride');
    act(() => {
      cancelLink.props.onPress();
    });
    expect(allText(r)).toContain('Driver is waiting');
    expect(allText(r)).toContain('Your driver has arrived. A cancellation fee of $4.50 will be charged.');
  });

  it('confirming cancellation opens the reason sheet, then submitting cancels + clears + navigates home', async () => {
    const r = await renderScreen();
    const cancelLink = findButtonByText(r, 'Cancel Ride');
    act(() => {
      cancelLink.props.onPress();
    });
    const confirmBtn = r.root.findByProps({ accessibilityLabel: 'confirm-Cancel & Pay $4.50' });
    act(() => {
      confirmBtn.props.onPress();
    });
    expect(allText(r)).toContain('Submit Reason');
    const submitBtn = r.root.findByProps({ accessibilityLabel: 'submit-cancel-reason' });
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockCancelRide).toHaveBeenCalledWith('Changed my mind');
    expect(mockClearRide).toHaveBeenCalled();
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('toasts instead of navigating when cancellation fails', async () => {
    mockCancelRide.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const cancelLink = findButtonByText(r, 'Cancel Ride');
    act(() => {
      cancelLink.props.onPress();
    });
    const confirmBtn = r.root.findByProps({ accessibilityLabel: 'confirm-Cancel & Pay $4.50' });
    act(() => {
      confirmBtn.props.onPress();
    });
    const submitBtn = r.root.findByProps({ accessibilityLabel: 'submit-cancel-reason' });
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('Could not cancel', 'Could not cancel your ride. Please try again.', 'danger');
    expect(mockReplace).not.toHaveBeenCalledWith('/(tabs)');
  });

  it('triggers the same cancel-confirm flow on the hardware back button', async () => {
    const addListenerSpy = jest.spyOn(BackHandler, 'addEventListener');
    const r = await renderScreen();
    const handler = addListenerSpy.mock.calls.find((c) => c[0] === 'hardwareBackPress')![1] as () => boolean;
    act(() => {
      handler();
    });
    expect(allText(r)).toContain('Driver is waiting');
  });

  it('copies the OTP and toasts when tapped', async () => {
    const r = await renderScreen();
    const otpTouchable = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.props.onPress?.name === 'handleCopyOtp' || n.findAllByType(Text).some((t) => {
        try { return JSON.stringify(t.props.children) === '"4"'; } catch { return false; }
      }));
    // Fall back to calling handleCopyOtp indirectly via the header PIN
    // touchable found by structure: the OTP box wraps all 4 digit views.
    const pinWrapper = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => {
        try { return t.props.children === '4' || t.props.children === '8' || t.props.children === '2' || t.props.children === '1'; } catch { return false; }
      }))!;
    await act(async () => {
      await pinWrapper.props.onPress();
      await flush();
    });
    expect(mockSetStringAsync).toHaveBeenCalledWith('4821');
    expect(mockShowToast).toHaveBeenCalledWith('Copied!', 'OTP copied to clipboard.', 'success');
  });

  it('navigates to /chat-driver with rideId on Message Driver', async () => {
    const r = await renderScreen();
    const msgBtn = findButtonByText(r, 'Message Driver');
    act(() => {
      msgBtn.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/chat-driver', params: { rideId: 'ride-1' } });
  });

  it('shares a formatted trip-info blob and never crashes if Share.share rejects', async () => {
    (Share.share as jest.Mock).mockRejectedValue(new Error('share failed'));
    const r = await renderScreen();
    const shareButtons = r.root.findAllByType(TouchableOpacity).filter((n) =>
      n.findAllByProps({ name: 'share-outline' }).length > 0
    );
    const shareBtn = shareButtons[0];
    await act(async () => {
      await shareBtn.props.onPress();
      await flush();
    });
    expect(Share.share).toHaveBeenCalledWith({
      message: expect.stringContaining('Driver: Sam Lee'),
    });
    expect(r.root).toBeTruthy(); // did not crash
  });

  it('falls back to the placeholder icon when the driver photo fails to load', async () => {
    mockRideState.currentDriver = { ...CURRENT_DRIVER, photo_url: 'https://example.com/x.jpg' };
    const r = await renderScreen();
    const img = r.root.findByType(Image);
    act(() => {
      img.props.onError();
    });
    expect(r.root.findAllByType(Image)).toHaveLength(0);
  });

  it('shows a loading state with a retry button when there is no currentRide yet', async () => {
    mockRideState.currentRide = null;
    const r = await renderScreen();
    expect(allText(r)).toContain('Loading map…');
    mockFetchRide.mockClear();
    const retryBtn = findButtonByText(r, 'Retry');
    act(() => {
      retryBtn.props.onPress();
    });
    expect(mockFetchRide).toHaveBeenCalledWith('ride-1');
  });
});
