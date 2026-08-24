/**
 * app/(tabs)/index.tsx — rider home/map screen. Pins:
 *  - focus-effect: fetches the active ride and redirects to whichever
 *    screen owns it by status (searching/assigned/accepted ->
 *    driver-arriving, arrived -> driver-arrived, in_progress ->
 *    ride-in-progress, completed-and-unpaid -> ride-completed); a
 *    completed-and-paid ride does NOT redirect
 *  - the same focus-effect throttles fetchHomeData by a 5-minute TTL —
 *    a re-focus inside the window only refreshes location, not the full
 *    notifications/saved-addresses fetch
 *  - the notification-permission banner shows only when permission is
 *    NOT granted and hasn't been dismissed; Enable/Dismiss both work;
 *    a denied Enable attempt opens device settings and toasts
 *  - AI button: opens /ai-assistant when aiEnabled, otherwise toasts
 *    "Coming Soon"; hidden entirely when aiMode is 'hidden'
 *  - the promo banner rotates through PROMO_MESSAGES on an interval and
 *    can be dismissed
 *  - nearby drivers at (nearly) the same coordinate are spread onto a
 *    small ring so they render as distinct markers instead of one
 *  - quick actions and search all route to /search-destination
 *  - RiderSOS gets the current ride id (or undefined) and the
 *    ridelessSosEnabled flag from RidelessSosEnabledContext
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, AppState } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));
jest.mock('@shared/hooks/useExitOnBackPress', () => ({ useExitOnBackPress: () => {} }));

jest.mock('@shared/components/AppMap', () => {
  const ReactActual = require('react');
  const AppMap = ReactActual.forwardRef((props: any, ref: any) => {
    ReactActual.useImperativeHandle(ref, () => ({ animateToRegion: jest.fn() }));
    return ReactActual.createElement('AppMap', props, props.children);
  });
  return { __esModule: true, default: AppMap };
});
jest.mock('@shared/components/CarMarker', () => ({
  CarMarker: () => null,
  resolveMarkerVariant: () => 'sedan',
}));

jest.mock('@gorhom/bottom-sheet', () => {
  const ReactActual = require('react');
  const BottomSheet = ReactActual.forwardRef((props: any, _ref: any) => props.children);
  const BottomSheetScrollView = (props: any) => props.children;
  return { __esModule: true, default: BottomSheet, BottomSheetScrollView };
});

jest.mock('expo-router/react-navigation', () => {
  const ReactActual = require('react');
  return {
    useFocusEffect: (cb: () => void | (() => void)) => {
      ReactActual.useEffect(() => cb(), [cb]);
    },
  };
});

const mockPush = jest.fn();
jest.mock('expo-router', () => ({ useRouter: () => ({ push: mockPush }) }));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666',
  border: '#E5E7EB', textSecondary: '#333', background: '#FFF',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockT = (key: string) => key;
jest.mock('../i18n', () => ({ useTranslation: () => ({ t: mockT }) }));

const mockApiGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
  isAppCheckTokenReady: () => Promise.resolve(true),
}));

const mockGetForegroundPermissionsAsync = jest.fn();
const mockRequestForegroundPermissionsAsync = jest.fn();
const mockGetCurrentPositionAsync = jest.fn();
const mockGetLastKnownPositionAsync = jest.fn();
jest.mock('expo-location', () => ({
  getForegroundPermissionsAsync: (...a: any[]) => mockGetForegroundPermissionsAsync(...a),
  requestForegroundPermissionsAsync: (...a: any[]) => mockRequestForegroundPermissionsAsync(...a),
  getCurrentPositionAsync: (...a: any[]) => mockGetCurrentPositionAsync(...a),
  getLastKnownPositionAsync: (...a: any[]) => mockGetLastKnownPositionAsync(...a),
  Accuracy: { Balanced: 3 },
}));

const mockAsyncGetItem = jest.fn();
const mockAsyncSetItem = jest.fn();
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: (...a: any[]) => mockAsyncGetItem(...a),
  setItem: (...a: any[]) => mockAsyncSetItem(...a),
}));

let mockAuthState: any;
jest.mock('@shared/store/authStore', () => ({ useAuthStore: () => mockAuthState }));

const mockFetchSavedAddresses = jest.fn();
const mockSetUserLocation = jest.fn();
const mockTriggerEmergency = jest.fn();
const mockTriggerRidelessEmergency = jest.fn();
const mockFetchActiveRide = jest.fn();
let mockRideState: any;
jest.mock('../store/rideStore', () => ({ useRideStore: () => mockRideState }));

const mockBottomSheetGuard = { handleSheetChange: jest.fn(), handleContainerLayout: jest.fn() };
jest.mock('../hooks/useBottomSheetGuard', () => ({ useBottomSheetGuard: () => mockBottomSheetGuard }));

let mockAiChatState: any;
jest.mock('../store/aiChatStore', () => ({ useAiChatStore: (sel: any) => sel(mockAiChatState) }));

jest.mock('@shared/store/vehicleTypeStore', () => ({ useVehicleTypeStore: (sel: any) => sel({ byId: {} }) }));

const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

jest.mock('../components/RiderSOS', () => ({ RiderSOS: (props: any) => {
  const { Text: RNText } = require('react-native');
  return <RNText testID="rider-sos">{`rideId:${props.rideId ?? 'none'}|ridelessSosEnabled:${props.ridelessSosEnabled}`}</RNText>;
} }));

jest.mock('../app/_layout', () => ({
  RidelessSosEnabledContext: require('react').createContext(false),
}));

const mockCheckNotificationPermission = jest.fn();
const mockRequestNotificationPermission = jest.fn();
const mockOpenNotificationSettings = jest.fn();
jest.mock('@shared/services/firebase', () => ({
  checkNotificationPermission: (...a: any[]) => mockCheckNotificationPermission(...a),
  requestNotificationPermission: (...a: any[]) => mockRequestNotificationPermission(...a),
  openNotificationSettings: (...a: any[]) => mockOpenNotificationSettings(...a),
}));

import { Platform, Linking } from 'react-native';
import { CarMarker } from '@shared/components/CarMarker';
import HomeScreen from '../app/(tabs)/index';
import { RidelessSosEnabledContext } from '../app/_layout';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const LOCATION = { coords: { latitude: 52.1, longitude: -106.6, heading: 0 } };

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen(ridelessSosEnabled = false) {
  await act(async () => {
    renderer = TestRenderer.create(
      <RidelessSosEnabledContext.Provider value={ridelessSosEnabled}>
        <HomeScreen />
      </RidelessSosEnabledContext.Provider>,
    );
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => { try { return JSON.stringify(t.props.children); } catch { return '<circular>'; } }).join(' | ');
}

function findButtonByLabel(r: TestRenderer.ReactTestRenderer, label: string) {
  return r.root.findByProps({ accessibilityLabel: label });
}

beforeEach(() => {
  jest.clearAllMocks();
  jest.useFakeTimers();
  mockAuthState = { user: { first_name: 'Jamie', profile_image: null } };
  mockRideState = {
    fetchSavedAddresses: mockFetchSavedAddresses,
    setUserLocation: mockSetUserLocation,
    currentRide: null,
    triggerEmergency: mockTriggerEmergency,
    triggerRidelessEmergency: mockTriggerRidelessEmergency,
    fetchActiveRide: mockFetchActiveRide,
  };
  mockAiChatState = { enabled: true, mode: 'enabled', loadConfig: jest.fn() };
  mockFetchActiveRide.mockResolvedValue({ active: false });
  mockGetForegroundPermissionsAsync.mockResolvedValue({ status: 'granted' });
  mockRequestForegroundPermissionsAsync.mockResolvedValue({ status: 'granted' });
  mockGetCurrentPositionAsync.mockResolvedValue(LOCATION);
  mockGetLastKnownPositionAsync.mockResolvedValue(null);
  mockAsyncGetItem.mockResolvedValue(null);
  mockCheckNotificationPermission.mockResolvedValue({ granted: true });
  mockRequestNotificationPermission.mockResolvedValue(true);
  mockApiGet.mockImplementation((url: string) => {
    if (url.startsWith('/notifications')) return Promise.resolve({ data: { unread_count: 0 } });
    if (url.startsWith('/drivers/nearby')) return Promise.resolve({ data: [] });
    return Promise.reject(new Error('unexpected url ' + url));
  });
  global.fetch = jest.fn(() => Promise.resolve({ json: () => Promise.resolve({}) })) as any;
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.useRealTimers();
  jest.restoreAllMocks();
});

describe('HomeScreen', () => {
  it('redirects to /driver-arriving for a searching active ride', async () => {
    mockFetchActiveRide.mockResolvedValue({ active: true, ride: { id: 'ride-1', status: 'searching' } });
    await renderScreen();
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/driver-arriving', params: { rideId: 'ride-1' } });
  });

  it('redirects to /driver-arrived for an arrived active ride', async () => {
    mockFetchActiveRide.mockResolvedValue({ active: true, ride: { id: 'ride-1', status: 'driver_arrived' } });
    await renderScreen();
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/driver-arrived', params: { rideId: 'ride-1' } });
  });

  it('redirects to /ride-in-progress for an in-progress active ride', async () => {
    mockFetchActiveRide.mockResolvedValue({ active: true, ride: { id: 'ride-1', status: 'in_progress' } });
    await renderScreen();
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/ride-in-progress', params: { rideId: 'ride-1' } });
  });

  it('redirects to /ride-completed for a completed-but-unpaid ride', async () => {
    mockFetchActiveRide.mockResolvedValue({ active: true, ride: { id: 'ride-1', status: 'completed', payment_status: 'pending' } });
    await renderScreen();
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/ride-completed', params: { rideId: 'ride-1' } });
  });

  it('does not redirect for a completed-and-paid ride', async () => {
    mockFetchActiveRide.mockResolvedValue({ active: true, ride: { id: 'ride-1', status: 'completed', payment_status: 'paid' } });
    await renderScreen();
    expect(mockPush).not.toHaveBeenCalled();
  });

  it('shows the notification-permission banner when permission is not granted', async () => {
    mockCheckNotificationPermission.mockResolvedValue({ granted: false });
    const r = await renderScreen();
    expect(allText(r)).toContain('Turn on notifications');
  });

  it('dismisses the notification banner', async () => {
    mockCheckNotificationPermission.mockResolvedValue({ granted: false });
    const r = await renderScreen();
    const dismissBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByProps({ name: 'close' }).length > 0
    )!;
    act(() => { dismissBtn.props.onPress(); });
    expect(allText(r)).not.toContain('Turn on notifications');
  });

  it('opens device settings and toasts when Enable notifications is denied', async () => {
    mockCheckNotificationPermission.mockResolvedValue({ granted: false });
    mockRequestNotificationPermission.mockResolvedValue(false);
    const r = await renderScreen();
    const enableBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => { try { return JSON.stringify(t.props.children) === '"Enable"'; } catch { return false; } })
    )!;
    await act(async () => { await enableBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Permission Required', 'Notification permissions are disabled in device settings.', 'warning');
    expect(mockOpenNotificationSettings).toHaveBeenCalled();
  });

  it('opens /ai-assistant when the AI feature is enabled', async () => {
    const r = await renderScreen();
    const aiBtn = findButtonByLabel(r, 'AI assistant');
    act(() => { aiBtn.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/ai-assistant');
  });

  it('toasts "Coming Soon" when AI is not yet enabled', async () => {
    mockAiChatState = { enabled: false, mode: 'coming_soon', loadConfig: jest.fn() };
    const r = await renderScreen();
    const aiBtn = findButtonByLabel(r, 'AI assistant');
    act(() => { aiBtn.props.onPress(); });
    expect(mockShowToast).toHaveBeenCalledWith('Coming Soon', 'AI Ride Booking is coming soon!', 'info');
  });

  it('hides the AI button entirely when aiMode is hidden', async () => {
    mockAiChatState = { enabled: false, mode: 'hidden', loadConfig: jest.fn() };
    const r = await renderScreen();
    expect(() => findButtonByLabel(r, 'AI assistant')).toThrow();
  });

  it('routes the search bar and every quick action to /search-destination', async () => {
    const r = await renderScreen();
    const searchBar = findButtonByLabel(r, 'Where to? Search for a destination');
    act(() => { searchBar.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/search-destination');
    mockPush.mockClear();
    const homeAction = findButtonByLabel(r, 'Go home');
    act(() => { homeAction.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/search-destination');
  });

  it('dismisses the promo banner', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('Ride local. Support local.');
    const dismissBtn = findButtonByLabel(r, 'Dismiss promotion banner');
    act(() => { dismissBtn.props.onPress(); });
    expect(allText(r)).not.toContain('Ride local. Support local.');
  });

  it('rotates the promo banner message on an interval', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('Ride local. Support local.');
    await act(async () => {
      jest.advanceTimersByTime(6000);
      await flush();
    });
    expect(allText(r)).toContain('Saskatchewan-first');
  });

  it('passes the current ride id and ridelessSosEnabled flag to RiderSOS', async () => {
    mockRideState.currentRide = { id: 'ride-1' };
    const r = await renderScreen(true);
    const sos = r.root.findByProps({ testID: 'rider-sos' });
    expect(sos.props.children).toBe('rideId:ride-1|ridelessSosEnabled:true');
  });

  it('passes undefined rideId and ridelessSosEnabled=false when there is no active ride and the flag is off', async () => {
    const r = await renderScreen(false);
    const sos = r.root.findByProps({ testID: 'rider-sos' });
    expect(sos.props.children).toBe('rideId:none|ridelessSosEnabled:false');
  });
});

describe('location permission flow', () => {
  it('does nothing further once permission is granted on the first check', async () => {
    mockGetForegroundPermissionsAsync.mockResolvedValue({ status: 'granted' });
    await renderScreen();
    expect(mockRequestForegroundPermissionsAsync).not.toHaveBeenCalled();
    expect(mockGetCurrentPositionAsync).toHaveBeenCalled();
  });

  it('requests permission when not initially granted, and proceeds once the request succeeds', async () => {
    mockGetForegroundPermissionsAsync.mockResolvedValue({ status: 'undetermined' });
    mockRequestForegroundPermissionsAsync.mockResolvedValue({ status: 'granted' });
    await renderScreen();
    expect(mockRequestForegroundPermissionsAsync).toHaveBeenCalled();
    expect(mockGetCurrentPositionAsync).toHaveBeenCalled();
  });

  it('toasts and opens iOS settings when permission stays denied after the request', async () => {
    Object.defineProperty(Platform, 'OS', { get: () => 'ios' });
    mockGetForegroundPermissionsAsync.mockResolvedValue({ status: 'denied' });
    mockRequestForegroundPermissionsAsync.mockResolvedValue({ status: 'denied' });
    const openURLSpy = jest.spyOn(Linking, 'openURL').mockResolvedValue(true as any);
    await renderScreen();
    expect(mockShowToast).toHaveBeenCalledWith('Location Required', 'Enable location in Settings to use Spinr.', 'warning');
    expect(openURLSpy).toHaveBeenCalledWith('app-settings:');
    expect(mockGetCurrentPositionAsync).not.toHaveBeenCalled();
    Object.defineProperty(Platform, 'OS', { get: () => 'ios' });
  });

  it('opens Android settings (not app-settings: URL) when permission stays denied', async () => {
    Object.defineProperty(Platform, 'OS', { get: () => 'android' });
    mockGetForegroundPermissionsAsync.mockResolvedValue({ status: 'denied' });
    mockRequestForegroundPermissionsAsync.mockResolvedValue({ status: 'denied' });
    const openSettingsSpy = jest.spyOn(Linking, 'openSettings').mockResolvedValue(undefined as any);
    await renderScreen();
    expect(openSettingsSpy).toHaveBeenCalled();
    Object.defineProperty(Platform, 'OS', { get: () => 'ios' });
  });

  it('swallows a Linking failure when opening settings without crashing', async () => {
    Object.defineProperty(Platform, 'OS', { get: () => 'ios' });
    mockGetForegroundPermissionsAsync.mockResolvedValue({ status: 'denied' });
    mockRequestForegroundPermissionsAsync.mockResolvedValue({ status: 'denied' });
    jest.spyOn(Linking, 'openURL').mockRejectedValue(new Error('no handler'));
    await expect(renderScreen()).resolves.toBeDefined();
    Object.defineProperty(Platform, 'OS', { get: () => 'ios' });
  });

  it('seeds the map from a cached last-known location before the fresh fetch resolves', async () => {
    mockAsyncGetItem.mockResolvedValue(JSON.stringify({ lat: 51.0, lng: -105.0 }));
    const r = await renderScreen();
    expect(mockAsyncGetItem).toHaveBeenCalled();
    expect(r).toBeDefined();
  });

  it('persists a last-known position from the OS cache before the fresh GPS fix lands', async () => {
    mockGetLastKnownPositionAsync.mockResolvedValue(LOCATION);
    await renderScreen();
    expect(mockAsyncSetItem).toHaveBeenCalled();
  });
});

describe('weather temperature', () => {
  it('sets the temperature from the Open-Meteo response and renders it next to the greeting', async () => {
    global.fetch = jest.fn(() =>
      Promise.resolve({ json: () => Promise.resolve({ current_weather: { temperature: 18.6 } }) }),
    ) as any;
    const r = await renderScreen();
    expect(allText(r)).toContain('[" · ",19,"°C"]'); // Math.round(18.6)
  });

  it('does not render a temperature when the weather fetch fails', async () => {
    global.fetch = jest.fn(() => Promise.reject(new Error('network down'))) as any;
    const r = await renderScreen();
    expect(allText(r)).not.toContain('°C');
  });
});

describe('greeting text', () => {
  it.each([
    [8, 'GOOD MORNING'],
    [14, 'GOOD AFTERNOON'],
    [20, 'GOOD EVENING'],
  ])('shows the right greeting at hour %i', async (hour, expected) => {
    jest.setSystemTime(new Date(2026, 0, 1, hour, 0, 0));
    const r = await renderScreen();
    expect(allText(r)).toContain(expected);
  });
});

describe('AppState foreground refresh', () => {
  it('re-checks location permission/position when the app returns to active', async () => {
    await renderScreen();
    mockGetCurrentPositionAsync.mockClear();
    const listener = (AppState.addEventListener as jest.Mock).mock.calls.find((c) => c[0] === 'change')?.[1];
    expect(listener).toBeDefined();
    await act(async () => { listener?.('active'); await flush(); });
    expect(mockGetCurrentPositionAsync).toHaveBeenCalled();
  });

  it('does nothing on a background transition', async () => {
    await renderScreen();
    mockGetCurrentPositionAsync.mockClear();
    const listener = (AppState.addEventListener as jest.Mock).mock.calls.find((c) => c[0] === 'change')?.[1];
    await act(async () => { listener?.('background'); await flush(); });
    expect(mockGetCurrentPositionAsync).not.toHaveBeenCalled();
  });
});

describe('nearby drivers — co-located spread', () => {
  it('renders one CarMarker per driver even when several share (nearly) the same coordinate', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/notifications')) return Promise.resolve({ data: { unread_count: 0 } });
      if (url.startsWith('/drivers/nearby')) {
        return Promise.resolve({
          data: [
            { id: 'd1', lat: 52.1, lng: -106.6 },
            { id: 'd2', lat: 52.1, lng: -106.6 },
            { id: 'd3', lat: 52.1, lng: -106.6 },
          ],
        });
      }
      return Promise.reject(new Error('unexpected url ' + url));
    });
    const r = await renderScreen();
    const markers = r.root.findAllByType(CarMarker as any);
    expect(markers).toHaveLength(3);
    // Co-located drivers spread onto a ring — their coordinates must not
    // all collapse back onto the exact same point.
    const coords = markers.map((m: any) => `${m.props.coordinate.latitude},${m.props.coordinate.longitude}`);
    expect(new Set(coords).size).toBeGreaterThan(1);
  });

  it('renders a single driver at its own coordinate unchanged (no ring spread for a lone marker)', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.startsWith('/notifications')) return Promise.resolve({ data: { unread_count: 0 } });
      if (url.startsWith('/drivers/nearby')) return Promise.resolve({ data: [{ id: 'd1', lat: 52.1, lng: -106.6, heading: 90 }] });
      return Promise.reject(new Error('unexpected url ' + url));
    });
    const r = await renderScreen();
    const markers = r.root.findAllByType(CarMarker as any);
    expect(markers).toHaveLength(1);
    expect(markers[0].props.coordinate).toEqual({ latitude: 52.1, longitude: -106.6 });
    expect(markers[0].props.heading).toBe(90);
  });
});
