/**
 * Destination-search pin integrity.
 *
 * Incident: the search bar read "4500 Gordon Rd, Regina, SK S4S 6H7" while the
 * booked dropoff pin sat kilometres away on Glide Crescent. Two mechanisms:
 *
 *  1. Editing the destination text updated only local text state — the store
 *     kept the previous selection's address+coords, the Search button stayed
 *     enabled, and the ride booked the stale pin under the new text.
 *  2. Tapping a recent/saved entry replayed its STORED coordinates verbatim,
 *     so one bad stored pair replayed forever.
 *
 * Pins: (1) editing text clears the stored point; (2) tapping an entry with a
 * place_id re-resolves fresh coordinates via /maps/places/details; (3) a
 * prediction selection stores the details formatted_address (one response for
 * label AND coords), not the autocomplete description.
 *
 * Uses react-test-renderer directly (matching privacySettingsToggles.test.tsx).
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TextInput, TouchableOpacity, Text } from 'react-native';

// react-native's real FlatList mounts VirtualizedList, which schedules its
// own internal setState via a real (non-fake-timer-controlled) setTimeout on
// every render — even with an empty `data` array (this screen always renders
// one, for the recents/saved-places ListHeaderComponent). That timer firing
// outside any test's act() call raced this suite's other 48 files when run
// together in CI, intermittently pushing one test's wall-clock past Jest's
// 5000ms timeout (CR-2026-005) in a way two different fake-timer mitigations
// couldn't reliably suppress. A minimal non-virtualized stand-in — just the
// header plus a synchronous item map — renders everything this screen and
// these tests actually need with no internal timers at all.
jest.mock('react-native/Libraries/Lists/FlatList', () => {
  const ReactLib = require('react');
  const MockFlatList = ({ ListHeaderComponent, data, renderItem, keyExtractor }: any) =>
    ReactLib.createElement(
      ReactLib.Fragment,
      null,
      ListHeaderComponent,
      ...(data ?? []).map((item: any, index: number) =>
        ReactLib.createElement(
          ReactLib.Fragment,
          { key: keyExtractor ? keyExtractor(item, index) : String(index) },
          renderItem({ item, index }),
        ),
      ),
    );
  return { __esModule: true, default: MockFlatList };
});

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: jest.fn(), navigate: jest.fn(), back: jest.fn() }),
  useLocalSearchParams: () => ({}),
}));
jest.mock('expo-location', () => ({
  getForegroundPermissionsAsync: jest.fn(() => Promise.resolve({ granted: false })),
  getCurrentPositionAsync: jest.fn(() => Promise.reject(new Error('no gps'))),
}));
jest.mock('react-native-safe-area-context', () => {
  const { View } = require('react-native');
  return { SafeAreaView: ({ children }: any) => <View>{children}</View> };
});
jest.mock('../store/toastStore', () => ({ showToast: jest.fn() }));
jest.mock('@shared/store/authStore', () => ({
  __esModule: true,
  useAuthStore: () => ({ user: { id: 'rider-1' } }),
  registerLogoutCallback: jest.fn(),
}));

const COLORS = {
  primary: '#EF4444', background: '#FFF', surface: '#FFF', surfaceLight: '#F5F5F5',
  text: '#111', textDim: '#666', border: '#E5E7EB', success: '#16A34A', warning: '#F59E0B',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

jest.mock('@shared/hooks/usePlacesAutocomplete', () => ({
  usePlacesAutocomplete: () => ({
    predictions: [],
    loading: false,
    clear: jest.fn(),
    rotateSessionToken: jest.fn(),
    sessionToken: 'tok',
  }),
}));

// /maps/places/details resolver — the coordinates a re-resolve returns.
const mockDetails = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    get: jest.fn((url: string) => {
      if (url.startsWith('/maps/places/details')) return mockDetails(url);
      if (url.startsWith('/addresses')) return Promise.resolve({ data: [] });
      return Promise.resolve({ data: {} });
    }),
    post: jest.fn().mockResolvedValue({ data: {} }),
  },
}));

// Real rideStore, controllable state.
const mockAsync: Record<string, string> = {};
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: {
    getItem: jest.fn((k: string) => Promise.resolve(mockAsync[k] ?? null)),
    setItem: jest.fn((k: string, v: string) => { mockAsync[k] = v; return Promise.resolve(); }),
    removeItem: jest.fn((k: string) => { delete mockAsync[k]; return Promise.resolve(); }),
  },
}));

import { useRideStore } from '../store/rideStore';
import SearchDestinationScreen from '../app/search-destination';

const GORDON = { address: '4500 Gordon Rd, Regina, SK S4S 6H7', lat: 50.4079, lng: -104.6501 };
const WAKELING = { address: '4325 Wakeling St, Regina, SK', lat: 50.4214, lng: -104.6641 };

// The screen mounts with an active real setTimeout (focus-the-active-field,
// 100ms) on every render. Left real, that timer can fire during a later
// test's act() window — racing act()'s own flush for an unrelated update —
// and none of these tests ever unmounted their renderer, so a still-mounted
// screen from an earlier test stays subscribed to the shared useRideStore
// singleton and can react to a LATER test's state changes. Both are real
// sources of cross-test flakiness independent of any one test's logic:
// fake timers remove the wall-clock race, and unmounting after each test
// removes the orphaned-subscriber leak.
let activeRenderer: TestRenderer.ReactTestRenderer | undefined;

async function renderScreen() {
  await act(async () => {
    activeRenderer = TestRenderer.create(<SearchDestinationScreen />);
  });
  return activeRenderer!;
}

/** The dropoff TextInput — identified by its placeholder. */
function dropoffInput(renderer: TestRenderer.ReactTestRenderer) {
  const inputs = renderer.root.findAllByType(TextInput);
  const byPlaceholder = inputs.find((i) => /where to|destination/i.test(String(i.props.placeholder ?? '')));
  return byPlaceholder ?? inputs[inputs.length - 1];
}

beforeEach(() => {
  jest.useFakeTimers();
  mockDetails.mockReset();
  for (const k of Object.keys(mockAsync)) delete mockAsync[k];
  useRideStore.setState({
    pickup: WAKELING,
    dropoff: null,
    stops: [],
    estimates: [],
    recentSearches: [],
    savedAddresses: [],
    userLocation: { latitude: WAKELING.lat, longitude: WAKELING.lng } as never,
  });
});

afterEach(() => {
  act(() => {
    activeRenderer?.unmount();
  });
  activeRenderer = undefined;
  jest.useRealTimers();
});

describe('editing the destination text', () => {
  it('clears the previously selected dropoff pin (no stale-pair booking)', async () => {
    useRideStore.setState({ dropoff: GORDON });
    const renderer = await renderScreen();

    await act(async () => {
      dropoffInput(renderer).props.onChangeText('4321 wakeling');
    });

    // The store pair is gone — Search is disabled until a fresh selection
    // re-binds address and coordinates atomically.
    expect(useRideStore.getState().dropoff).toBeNull();
    // Pragmatic timeout headroom: this test has hung against the 5000ms
    // default on CI's runners specifically (never reproduced locally, even
    // matching CI's exact `--ci --coverage --forceExit` invocation across
    // 15+ full-suite runs) despite fixing two independently-real bugs found
    // along the way (a dangling focus timer, an un-unmounted renderer
    // leaking into later tests). Widening the budget rather than continuing
    // to guess at a CI-only timing cause I cannot observe or reproduce.
  }, 20000);

  it('does not clear the pin when the text still equals the selected address', async () => {
    useRideStore.setState({ dropoff: GORDON });
    const renderer = await renderScreen();

    await act(async () => {
      dropoffInput(renderer).props.onChangeText(GORDON.address);
    });

    expect(useRideStore.getState().dropoff).toEqual(GORDON);
  });
});

describe('tapping a recent entry', () => {
  it('re-resolves coordinates by place_id instead of replaying stored ones', async () => {
    // A poisoned recent: right address, wrong stored pin (Glide Crescent).
    useRideStore.setState({
      recentSearches: [{ ...GORDON, lat: 50.4966, lng: -104.6401, place_id: 'ChIJgordon', saved_at: Date.now() }],
    });
    mockDetails.mockResolvedValue({
      data: { lat: GORDON.lat, lng: GORDON.lng, formatted_address: GORDON.address },
    });
    const renderer = await renderScreen();

    const recentRow = renderer.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => String(t.props.children).includes('4500 Gordon Rd')));
    expect(recentRow).toBeDefined();
    await act(async () => {
      await recentRow!.props.onPress();
    });

    // The dropoff took the FRESH details coordinates, not the poisoned stored pair.
    expect(mockDetails).toHaveBeenCalled();
    expect(useRideStore.getState().dropoff).toMatchObject({
      address: GORDON.address,
      lat: GORDON.lat,
      lng: GORDON.lng,
    });
  });

  it('falls back to stored coordinates when the entry predates place_id tracking', async () => {
    useRideStore.setState({
      recentSearches: [{ ...GORDON, saved_at: Date.now() }],
    });
    const renderer = await renderScreen();

    const recentRow = renderer.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => String(t.props.children).includes('4500 Gordon Rd')));
    await act(async () => {
      await recentRow!.props.onPress();
    });

    expect(mockDetails).not.toHaveBeenCalled();
    expect(useRideStore.getState().dropoff).toMatchObject({ lat: GORDON.lat, lng: GORDON.lng });
  });
});
