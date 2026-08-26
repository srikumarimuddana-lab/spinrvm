/**
 * app/pick-on-map.tsx — map-drag pickup/dropoff picker. Pins:
 *  - AI mode (ai=1 with aiLat/aiLng): skips the location-permission request
 *    entirely and centers on the assistant's approximate point, geocoding
 *    it immediately
 *  - normal mode: permission denied falls back to a fixed Saskatoon
 *    region; a granted permission centers on the device's GPS fix; a GPS
 *    fetch failure also falls back to the fixed region
 *  - panning debounces the reverse-geocode call by 500ms
 *  - Confirm is disabled while there's no address yet or a geocode is
 *    in flight
 *  - the address-staleness guard: Confirm only carries the geocoded label
 *    when it was resolved for a pin within ~60m of the CURRENT pin --
 *    otherwise it falls back to raw coordinates (aiMode) or a formatted
 *    coordinate string (normal mode), so a stale label never travels
 *    with a moved pin
 *  - aiMode routes the confirmed pin back into the chat via
 *    submitMapPin() + router.back(); normal mode navigates to
 *    /search-destination with the pin params
 *  - Recenter requests permission if not already granted (AI mode may
 *    never have asked) and animates the map to the device's GPS fix
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

const mockAnimateToRegion = jest.fn();
jest.mock('react-native-maps', () => {
  const React = require('react');
  const MapView = React.forwardRef((props: any, ref: any) => {
    React.useImperativeHandle(ref, () => ({ animateToRegion: mockAnimateToRegion }));
    return React.createElement('MapView', props);
  });
  return { __esModule: true, default: MapView, PROVIDER_GOOGLE: 'google' };
});

const mockGetForegroundPermissionsAsync = jest.fn();
const mockRequestForegroundPermissionsAsync = jest.fn();
const mockGetCurrentPositionAsync = jest.fn();
jest.mock('expo-location', () => ({
  getForegroundPermissionsAsync: (...a: any[]) => mockGetForegroundPermissionsAsync(...a),
  requestForegroundPermissionsAsync: (...a: any[]) => mockRequestForegroundPermissionsAsync(...a),
  getCurrentPositionAsync: (...a: any[]) => mockGetCurrentPositionAsync(...a),
}));

const mockBack = jest.fn();
const mockNavigate = jest.fn();
let mockParams: any;
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, navigate: mockNavigate }),
  useLocalSearchParams: () => mockParams,
}));

const COLORS = { primary: '#EF4444', surface: '#FFF', text: '#111', textDim: '#666' };
const mockUseTheme = jest.fn(() => ({ colors: COLORS, isDark: false }));
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => mockUseTheme() }));

const mockApiGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
}));

const mockSubmitMapPin = jest.fn();
jest.mock('../store/aiChatStore', () => ({
  useAiChatStore: { getState: () => ({ submitMapPin: mockSubmitMapPin }) },
}));

import PickOnMapScreen from '../app/pick-on-map';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<PickOnMapScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => { try { return JSON.stringify(t.props.children); } catch { return '<circular>'; } }).join(' | ');
}

function findMapView(r: TestRenderer.ReactTestRenderer) {
  return r.root.find((n) => (n.type as any) === 'MapView');
}

beforeEach(() => {
  jest.clearAllMocks();
  jest.useFakeTimers();
  mockParams = { field: 'dropoff' };
  mockGetForegroundPermissionsAsync.mockResolvedValue({ status: 'granted' });
  mockRequestForegroundPermissionsAsync.mockResolvedValue({ status: 'granted' });
  mockGetCurrentPositionAsync.mockResolvedValue({ coords: { latitude: 50.1, longitude: -104.2 } });
  mockApiGet.mockResolvedValue({ data: { formatted_address: '123 Test St' } });
  mockSubmitMapPin.mockResolvedValue(undefined);
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.useRealTimers();
  // mockReturnValue (not mockReturnValueOnce) is needed for the isDark
  // override below, since it survives multiple re-renders — restore the
  // default here since jest.clearAllMocks() doesn't reset a jest.fn()'s
  // configured return value.
  mockUseTheme.mockReturnValue({ colors: COLORS, isDark: false });
});

describe('PickOnMapScreen', () => {
  it('AI mode centers on the approximate point and geocodes it immediately, without requesting location permission', async () => {
    mockParams = { field: 'pickup', ai: '1', aiLat: '50.5', aiLng: '-104.5' };
    const r = await renderScreen();
    expect(mockGetForegroundPermissionsAsync).not.toHaveBeenCalled();
    expect(mockApiGet).toHaveBeenCalledWith(expect.stringContaining('/maps/reverse-geocode?lat=50.5&lng=-104.5'));
    expect(allText(r)).toContain('123 Test St');
  });

  it('falls back to the fixed Saskatoon region when permission is denied', async () => {
    mockGetForegroundPermissionsAsync.mockResolvedValue({ status: 'denied' });
    mockRequestForegroundPermissionsAsync.mockResolvedValue({ status: 'denied' });
    const r = await renderScreen();
    expect(findMapView(r).props.initialRegion).toEqual({
      latitude: 52.1332, longitude: -106.67, latitudeDelta: 0.02, longitudeDelta: 0.02,
    });
  });

  it('centers on the device GPS fix when permission is granted', async () => {
    const r = await renderScreen();
    expect(findMapView(r).props.initialRegion).toEqual({
      latitude: 50.1, longitude: -104.2, latitudeDelta: 0.008, longitudeDelta: 0.008,
    });
  });

  it('falls back to the fixed region when the GPS fetch fails', async () => {
    mockGetCurrentPositionAsync.mockRejectedValue(new Error('gps unavailable'));
    const r = await renderScreen();
    expect(findMapView(r).props.initialRegion).toEqual({
      latitude: 52.1332, longitude: -106.67, latitudeDelta: 0.02, longitudeDelta: 0.02,
    });
  });

  it('debounces reverse-geocoding on pan by 500ms', async () => {
    const r = await renderScreen();
    mockApiGet.mockClear();
    const map = findMapView(r);
    act(() => {
      map.props.onRegionChangeComplete({ latitude: 51, longitude: -105, latitudeDelta: 0.008, longitudeDelta: 0.008 });
    });
    expect(mockApiGet).not.toHaveBeenCalled();
    await act(async () => {
      jest.advanceTimersByTime(500);
      await flush();
    });
    expect(mockApiGet).toHaveBeenCalledWith(expect.stringContaining('lat=51&lng=-105'));
  });

  it('disables Confirm while there is no address yet (normal mode never auto-geocodes on mount)', async () => {
    const r = await renderScreen();
    const confirmBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Confirm Location')))!;
    expect(confirmBtn.props.disabled).toBe(true);
  });

  it('disables Confirm while a geocode is in flight, then enables it once resolved', async () => {
    let resolveGet: (v: any) => void;
    const r = await renderScreen();
    mockApiGet.mockImplementation(() => new Promise((resolve) => { resolveGet = resolve; }));
    const map = findMapView(r);
    act(() => {
      map.props.onRegionChangeComplete({ latitude: 51, longitude: -105, latitudeDelta: 0.008, longitudeDelta: 0.008 });
    });
    await act(async () => {
      jest.advanceTimersByTime(500);
      await flush();
    });
    const confirmBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Confirm Location')))!;
    expect(confirmBtn.props.disabled).toBe(true);

    await act(async () => {
      resolveGet!({ data: { formatted_address: '123 Test St' } });
      await flush();
    });
    const confirmBtnAfter = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Confirm Location')))!;
    expect(confirmBtnAfter.props.disabled).toBe(false);
  });

  it('AI mode Confirm submits the pin with the address when it matches the current pin, then goes back', async () => {
    mockParams = { field: 'pickup', ai: '1', aiLat: '50.5', aiLng: '-104.5' };
    const r = await renderScreen();
    const confirmBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Confirm Location')))!;
    act(() => {
      confirmBtn.props.onPress();
    });
    expect(mockSubmitMapPin).toHaveBeenCalledWith('pickup', { lat: 50.5, lng: -104.5, address: '123 Test St' });
    expect(mockBack).toHaveBeenCalled();
  });

  it('AI mode Confirm drops a stale address (pin moved > ~60m since the last geocode)', async () => {
    mockParams = { field: 'dropoff', ai: '1', aiLat: '50.5', aiLng: '-104.5' };
    const r = await renderScreen();
    const map = findMapView(r);
    // Pan far away, but resolve the geocode only for the ORIGINAL point --
    // i.e. never let a fresh reverse-geocode complete for the new pin.
    mockApiGet.mockImplementation(() => new Promise(() => {})); // never resolves
    act(() => {
      map.props.onRegionChangeComplete({ latitude: 51.5, longitude: -105.5, latitudeDelta: 0.008, longitudeDelta: 0.008 });
    });
    await act(async () => {
      jest.advanceTimersByTime(500);
      await flush();
    });
    const confirmBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Confirm Location')))!;
    // Confirm is disabled while a fresh geocode is in flight for the moved
    // pin, but calling the handler directly (defence in depth, same as the
    // saved-places screen's own belt-and-suspenders test) must still drop
    // the stale label rather than pairing it with the new coordinate.
    act(() => {
      confirmBtn.props.onPress();
    });
    expect(mockSubmitMapPin).toHaveBeenCalledWith('dropoff', { lat: 51.5, lng: -105.5, address: null });
  });

  it('normal-mode Confirm navigates to /search-destination with the matched address', async () => {
    const r = await renderScreen();
    // Normal mode never auto-geocodes on mount -- pan once so a matching
    // address is actually resolved for the current pin.
    const map = findMapView(r);
    act(() => {
      map.props.onRegionChangeComplete({ latitude: 50.1, longitude: -104.2, latitudeDelta: 0.008, longitudeDelta: 0.008 });
    });
    await act(async () => {
      jest.advanceTimersByTime(500);
      await flush();
    });
    const confirmBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Confirm Location')))!;
    act(() => {
      confirmBtn.props.onPress();
    });
    expect(mockNavigate).toHaveBeenCalledWith({
      pathname: '/search-destination',
      params: {
        mapPickField: 'dropoff',
        mapPickLat: '50.1',
        mapPickLng: '-104.2',
        mapPickAddress: '123 Test St',
      },
    });
  });

  it('recenter requests permission if needed (AI mode may never have asked) and animates the map', async () => {
    mockParams = { field: 'pickup', ai: '1', aiLat: '50.5', aiLng: '-104.5' };
    mockGetForegroundPermissionsAsync.mockResolvedValue({ status: 'undetermined' });
    mockRequestForegroundPermissionsAsync.mockResolvedValue({ status: 'granted' });
    const r = await renderScreen();
    const recenterBtn = r.root.findAllByType(TouchableOpacity)[1]; // [0] back, [1] recenter
    await act(async () => {
      await recenterBtn.props.onPress();
      await flush();
    });
    expect(mockRequestForegroundPermissionsAsync).toHaveBeenCalled();
    expect(mockAnimateToRegion).toHaveBeenCalledWith(
      { latitude: 50.1, longitude: -104.2, latitudeDelta: 0.008, longitudeDelta: 0.008 },
      500,
    );
  });

  it('navigates back when the header back button is pressed', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => {
      backBtn.props.onPress();
    });
    expect(mockBack).toHaveBeenCalled();
  });

  it('falls back the field param to "dropoff" when the route provides none', async () => {
    mockParams = {};
    const r = await renderScreen();
    expect(allText(r)).toContain('Set destination');
  });

  it('falls back the address to a formatted coordinate string when the geocode response has no formatted_address', async () => {
    mockApiGet.mockResolvedValue({ data: {} });
    const r = await renderScreen();
    const map = findMapView(r);
    act(() => {
      map.props.onRegionChangeComplete({ latitude: 51, longitude: -105, latitudeDelta: 0.008, longitudeDelta: 0.008 });
    });
    await act(async () => {
      jest.advanceTimersByTime(500);
      await flush();
    });
    expect(allText(r)).toContain('51.00000, -105.00000');
  });

  it('falls back the address to a formatted coordinate string when the geocode request itself fails', async () => {
    mockApiGet.mockRejectedValue(new Error('network down'));
    const r = await renderScreen();
    const map = findMapView(r);
    act(() => {
      map.props.onRegionChangeComplete({ latitude: 51, longitude: -105, latitudeDelta: 0.008, longitudeDelta: 0.008 });
    });
    await act(async () => {
      jest.advanceTimersByTime(500);
      await flush();
    });
    expect(allText(r)).toContain('51.00000, -105.00000');
  });

  it('clears the pending debounce timer on a second pan before the first fires', async () => {
    const r = await renderScreen();
    mockApiGet.mockClear();
    const map = findMapView(r);
    act(() => {
      map.props.onRegionChangeComplete({ latitude: 51, longitude: -105, latitudeDelta: 0.008, longitudeDelta: 0.008 });
    });
    await act(async () => {
      jest.advanceTimersByTime(200); // still within the 500ms debounce window
    });
    act(() => {
      map.props.onRegionChangeComplete({ latitude: 52, longitude: -106, latitudeDelta: 0.008, longitudeDelta: 0.008 });
    });
    await act(async () => {
      jest.advanceTimersByTime(500);
      await flush();
    });
    // Only the second, latest pan's geocode ever fires -- the first pan's
    // timer was cleared, not left to also fire for the stale coordinates.
    expect(mockApiGet).toHaveBeenCalledTimes(1);
    expect(mockApiGet).toHaveBeenCalledWith(expect.stringContaining('lat=52&lng=-106'));
  });

  it('normal-mode Confirm drops a stale address and falls back to a coordinate string (pin moved after the last successful geocode)', async () => {
    const r = await renderScreen();
    const map = findMapView(r);
    // First pan: resolves normally, geocoding a matching address for this pin.
    act(() => {
      map.props.onRegionChangeComplete({ latitude: 50.1, longitude: -104.2, latitudeDelta: 0.008, longitudeDelta: 0.008 });
    });
    await act(async () => {
      jest.advanceTimersByTime(500);
      await flush();
    });
    // Second pan, far away: never let the fresh geocode complete, so the
    // stale label from the first pan is still what's in state.
    mockApiGet.mockImplementation(() => new Promise(() => {})); // never resolves
    act(() => {
      map.props.onRegionChangeComplete({ latitude: 51.5, longitude: -105.5, latitudeDelta: 0.008, longitudeDelta: 0.008 });
    });
    await act(async () => {
      jest.advanceTimersByTime(500);
      await flush();
    });
    const confirmBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Confirm Location')))!;
    // Confirm is disabled while the fresh geocode for the moved pin is in
    // flight, but calling the handler directly (defence in depth) must still
    // fall back to the coordinate string rather than the stale "123 Test St".
    act(() => {
      confirmBtn.props.onPress();
    });
    expect(mockNavigate).toHaveBeenCalledWith({
      pathname: '/search-destination',
      params: {
        mapPickField: 'dropoff',
        mapPickLat: '51.5',
        mapPickLng: '-105.5',
        mapPickAddress: '51.50000, -105.50000',
      },
    });
  });

  it('recenter skips re-requesting permission when it is already granted', async () => {
    const r = await renderScreen();
    const recenterBtn = r.root.findAllByType(TouchableOpacity)[1];
    await act(async () => {
      await recenterBtn.props.onPress();
      await flush();
    });
    expect(mockRequestForegroundPermissionsAsync).not.toHaveBeenCalled();
    expect(mockAnimateToRegion).toHaveBeenCalled();
  });

  it('recenter bails out silently when permission is still denied after requesting it', async () => {
    mockGetForegroundPermissionsAsync.mockResolvedValue({ status: 'denied' });
    mockRequestForegroundPermissionsAsync.mockResolvedValue({ status: 'denied' });
    const r = await renderScreen();
    mockAnimateToRegion.mockClear();
    const recenterBtn = r.root.findAllByType(TouchableOpacity)[1];
    await act(async () => {
      await recenterBtn.props.onPress();
      await flush();
    });
    expect(mockRequestForegroundPermissionsAsync).toHaveBeenCalled();
    expect(mockAnimateToRegion).not.toHaveBeenCalled();
  });

  it('sets userInterfaceStyle to "dark" on the map when the theme is dark', async () => {
    mockUseTheme.mockReturnValue({ colors: COLORS, isDark: true });
    const r = await renderScreen();
    expect(findMapView(r).props.userInterfaceStyle).toBe('dark');
  });

  it('uses a smaller bottom-card padding on Android', async () => {
    const RN = require('react-native');
    const originalOS = RN.Platform.OS;
    RN.Platform.OS = 'android';
    try {
      const r = await renderScreen();
      const confirmBtn = r.root
        .findAllByType(TouchableOpacity)
        .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Confirm Location')))!;
      // The bottom card is the confirm button's grandparent (card > row-below-map wrapper isn't
      // needed here -- walk up to the View carrying paddingBottom).
      let node: any = confirmBtn.parent;
      while (node && !(node.props && node.props.style && node.props.style.paddingBottom !== undefined)) {
        node = node.parent;
      }
      expect(node!.props.style.paddingBottom).toBe(24);
    } finally {
      RN.Platform.OS = originalOS;
    }
  });
});
