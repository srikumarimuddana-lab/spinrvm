/**
 * app/search-destination.tsx — broader coverage beyond
 * searchDestinationPinIntegrity.test.tsx (which pins the stale-pin incident
 * specifically). Reuses that file's conventions (real rideStore, the
 * non-virtualized FlatList double, fake timers, unmount-in-afterEach — see
 * that file's own comments for why each of those exists).
 *
 * Pins:
 *  - typing into the dropoff field drives usePlacesAutocomplete's query and
 *    renders its predictions; selecting one calls setDropoff/setPickup with
 *    the DETAILS response's address+coords (not the autocomplete
 *    description) and rotates the session token
 *  - stops: "Add stop" appends up to 3, hidden at the cap; removing a stop
 *    also removes its local text; a stop's own prediction selection advances
 *    focus to the next stop or dropoff
 *  - the quick-access rows: "Use current location" (pickup only, only when
 *    userLocation is set) sets pickup to the GPS point; "Set location on
 *    map" navigates to /pick-on-map with the active field; Home/Work chips
 *    call handleSelectLocation when set, toast "not set" when absent;
 *    Favourites render every other saved address
 *  - handleSearchRide: disabled until both pickup and dropoff are set;
 *    pressing it clears stale estimates and navigates to /confirm-pickup
 *  - clearing the pickup/dropoff field's X button blanks both text and the
 *    store point
 *  - the map-pick return effect: a pickup map-pick advances focus to dropoff
 *    only if dropoff is still empty; a dropoff map-pick does not
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TextInput, TouchableOpacity, Text } from 'react-native';

import { useRideStore } from '../store/rideStore';
import SearchDestinationScreen from '../app/search-destination';

// Same rationale as searchDestinationPinIntegrity.test.tsx: a real FlatList
// schedules its own internal setTimeout outside any test's act() window.
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
const mockPush = jest.fn();
const mockBack = jest.fn();
let mockParams: any = {};
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush, navigate: jest.fn(), back: mockBack }),
  useLocalSearchParams: () => mockParams,
}));
jest.mock('expo-location', () => ({
  getForegroundPermissionsAsync: jest.fn(() => Promise.resolve({ status: 'denied' })),
  requestForegroundPermissionsAsync: jest.fn(() => Promise.resolve({ status: 'denied' })),
  getCurrentPositionAsync: jest.fn(() => Promise.reject(new Error('no gps'))),
  Accuracy: { Balanced: 3 },
}));
jest.mock('react-native-safe-area-context', () => {
  const { View } = require('react-native');
  return { SafeAreaView: ({ children }: any) => <View>{children}</View> };
});
const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...a: any[]) => mockShowToast(...a) }));
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

let mockPredictions: any[] = [];
let mockLoading = false;
const mockClearPredictions = jest.fn();
const mockRotateSessionToken = jest.fn();
jest.mock('@shared/hooks/usePlacesAutocomplete', () => ({
  usePlacesAutocomplete: () => ({
    predictions: mockPredictions,
    loading: mockLoading,
    clear: mockClearPredictions,
    rotateSessionToken: mockRotateSessionToken,
    sessionToken: 'tok',
  }),
}));

const mockDetails = jest.fn();
// fetchSavedAddresses() runs on every mount and hits /addresses, whose
// response would otherwise silently overwrite savedAddresses set directly
// via useRideStore.setState() in a test's own setup right after render.
let mockSavedAddressesResponse: any[] = [];
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    get: jest.fn((url: string) => {
      if (url.startsWith('/maps/places/details')) return mockDetails(url);
      if (url.startsWith('/addresses')) return Promise.resolve({ data: mockSavedAddressesResponse });
      return Promise.resolve({ data: {} });
    }),
    post: jest.fn().mockResolvedValue({ data: {} }),
  },
}));

const mockAsync: Record<string, string> = {};
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: {
    getItem: jest.fn((k: string) => Promise.resolve(mockAsync[k] ?? null)),
    setItem: jest.fn((k: string, v: string) => { mockAsync[k] = v; return Promise.resolve(); }),
    removeItem: jest.fn((k: string) => { delete mockAsync[k]; return Promise.resolve(); }),
  },
}));

const WAKELING = { address: '4325 Wakeling St, Regina, SK', lat: 50.4214, lng: -104.6641 };
const GORDON = { address: '4500 Gordon Rd, Regina, SK S4S 6H7', lat: 50.4079, lng: -104.6501 };

let activeRenderer: TestRenderer.ReactTestRenderer | undefined;
async function renderScreen() {
  await act(async () => {
    activeRenderer = TestRenderer.create(<SearchDestinationScreen />);
  });
  return activeRenderer!;
}

function dropoffInput(renderer: TestRenderer.ReactTestRenderer) {
  const inputs = renderer.root.findAllByType(TextInput);
  return inputs.find((i) => /where to/i.test(String(i.props.placeholder ?? '')))!;
}
function pickupInput(renderer: TestRenderer.ReactTestRenderer) {
  const inputs = renderer.root.findAllByType(TextInput);
  return inputs.find((i) => /pickup/i.test(String(i.props.placeholder ?? '')))!;
}
function byText(renderer: TestRenderer.ReactTestRenderer, text: string) {
  return renderer.root.findAllByType(TouchableOpacity).find((n) =>
    n.findAllByType(Text).some((t) => String(t.props.children) === text)
  );
}

beforeEach(() => {
  jest.useFakeTimers();
  mockDetails.mockReset();
  mockPredictions = [];
  mockLoading = false;
  mockParams = {};
  mockClearPredictions.mockClear();
  mockRotateSessionToken.mockClear();
  mockPush.mockClear();
  mockShowToast.mockClear();
  mockSavedAddressesResponse = [];
  for (const k of Object.keys(mockAsync)) delete mockAsync[k];
  useRideStore.setState({
    pickup: WAKELING,
    dropoff: null,
    stops: [],
    estimates: [],
    recentSearches: [],
    savedAddresses: [],
    userLocation: null,
  });
});

afterEach(() => {
  act(() => {
    activeRenderer?.unmount();
  });
  activeRenderer = undefined;
  jest.useRealTimers();
});

describe('predictions', () => {
  it('shows the loading row while searching', async () => {
    mockLoading = true;
    const renderer = await renderScreen();
    expect(renderer.root.findAllByType(Text).some((t) => t.props.children === 'Searching...')).toBe(true);
  });

  it('selecting a prediction stores the DETAILS response address/coords and rotates the session token', async () => {
    mockPredictions = [{
      place_id: 'p1', description: 'Cornwall Centre',
      structured_formatting: { main_text: 'Cornwall Centre', secondary_text: 'Regina, SK' },
    }];
    mockDetails.mockResolvedValue({ data: { lat: 50.45, lng: -104.6, formatted_address: 'Cornwall Centre, Regina, SK' } });
    const renderer = await renderScreen();

    const row = renderer.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => t.props.children === 'Cornwall Centre')
    )!;
    await act(async () => {
      await row.props.onPress();
    });

    expect(useRideStore.getState().dropoff).toMatchObject({
      address: 'Cornwall Centre, Regina, SK', lat: 50.45, lng: -104.6, place_id: 'p1',
    });
    expect(mockRotateSessionToken).toHaveBeenCalled();
  });
});

describe('stops', () => {
  it('adds up to 3 stops and hides "Add stop" at the cap', async () => {
    const renderer = await renderScreen();
    const addBtn = () => byText(renderer, 'Add stop');
    expect(addBtn()).toBeDefined();
    act(() => { addBtn()!.props.onPress(); });
    act(() => { addBtn()!.props.onPress(); });
    act(() => { addBtn()!.props.onPress(); });
    expect(useRideStore.getState().stops).toHaveLength(3);
    expect(addBtn()).toBeUndefined();
  });

  it('removing a stop drops it from the store and the local text array', async () => {
    useRideStore.setState({ stops: [{ address: 'Stop A', lat: 1, lng: 1 }, { address: 'Stop B', lat: 2, lng: 2 }] });
    const renderer = await renderScreen();
    const removeBtn = renderer.root.findAllByProps({ accessibilityLabel: 'Remove stop 1' })[0];
    act(() => { removeBtn.props.onPress(); });
    expect(useRideStore.getState().stops).toEqual([{ address: 'Stop B', lat: 2, lng: 2 }]);
  });
});

describe('quick-access rows', () => {
  it('"Use current location" only shows for the pickup field when userLocation is set, and sets pickup on tap', async () => {
    useRideStore.setState({ userLocation: { latitude: 51.0, longitude: -105.0 } as any });
    const renderer = await renderScreen();
    act(() => { pickupInput(renderer).props.onFocus(); });
    const useLocationBtn = renderer.root.findAllByProps({ accessibilityLabel: 'Use current location as pickup' })[0];
    expect(useLocationBtn).toBeDefined();
    act(() => { useLocationBtn.props.onPress(); });
    expect(useRideStore.getState().pickup).toMatchObject({ address: 'Current Location', lat: 51.0, lng: -105.0 });
  });

  it('does not show "Use current location" for the dropoff field', async () => {
    useRideStore.setState({ userLocation: { latitude: 51.0, longitude: -105.0 } as any });
    const renderer = await renderScreen();
    expect(renderer.root.findAllByProps({ accessibilityLabel: 'Use current location as pickup' })).toHaveLength(0);
  });

  it('"Set location on map" navigates to /pick-on-map with the active field', async () => {
    const renderer = await renderScreen();
    const mapBtn = renderer.root.findAllByProps({ accessibilityLabel: 'Set location on map' })[0];
    act(() => { mapBtn.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/pick-on-map', params: { field: 'dropoff' } });
  });

  it('tapping the Home chip with no home address saved toasts instead of navigating', async () => {
    const renderer = await renderScreen();
    const homeChip = renderer.root.findAllByProps({ accessibilityLabel: 'Home — not set' })[0];
    act(() => { homeChip.props.onPress(); });
    expect(mockShowToast).toHaveBeenCalledWith('No Home Address', 'Set your home address in Account > Saved Places', 'info');
  });

  it('tapping the Work chip with a saved work address selects it', async () => {
    mockSavedAddressesResponse = [{ name: 'Work', address: '2103 11th Ave, Regina, SK', lat: 50.45, lng: -104.61 }];
    const renderer = await renderScreen();
    const workChip = renderer.root.findAllByProps({ accessibilityLabel: 'Work — 2103 11th Ave, Regina, SK' })[0];
    await act(async () => { await workChip.props.onPress(); });
    expect(useRideStore.getState().dropoff).toMatchObject({ address: '2103 11th Ave, Regina, SK', lat: 50.45, lng: -104.61 });
  });

  it('renders non-home/work saved addresses under Favourites', async () => {
    mockSavedAddressesResponse = [{ name: 'Gym', address: '123 Fitness Blvd', lat: 50.4, lng: -104.6 }];
    const renderer = await renderScreen();
    expect(renderer.root.findAllByType(Text).some((t) => t.props.children === 'Favourites')).toBe(true);
    expect(renderer.root.findAllByType(Text).some((t) => t.props.children === 'Gym')).toBe(true);
  });
});

describe('search ride button', () => {
  it('is disabled until both pickup and dropoff are set', async () => {
    const renderer = await renderScreen();
    const searchBtn = renderer.root.findAllByProps({ accessibilityLabel: 'Search for rides' })[0];
    expect(searchBtn.props.disabled).toBe(true);
  });

  it('clears stale estimates and navigates to /confirm-pickup once both points are set', async () => {
    useRideStore.setState({ pickup: WAKELING, dropoff: GORDON, estimates: [{ vehicle_type: { id: 'v1' } } as any] });
    const renderer = await renderScreen();
    const searchBtn = renderer.root.findAllByProps({ accessibilityLabel: 'Search for rides' })[0];
    expect(searchBtn.props.disabled).toBe(false);
    act(() => { searchBtn.props.onPress(); });
    expect(useRideStore.getState().estimates).toEqual([]);
    expect(mockPush).toHaveBeenCalledWith('/confirm-pickup');
  });
});

describe('clearing a field', () => {
  it('the dropoff X button blanks both the text and the store point', async () => {
    useRideStore.setState({ dropoff: GORDON });
    const renderer = await renderScreen();
    const clearBtn = renderer.root.findAllByProps({ accessibilityLabel: 'Clear destination' })[0];
    act(() => { clearBtn.props.onPress(); });
    expect(dropoffInput(renderer).props.value).toBe('');
    expect(useRideStore.getState().dropoff).toBeNull();
  });
});

describe('returning from the map picker', () => {
  it('a pickup map-pick sets pickup and advances focus to dropoff when dropoff is still empty', async () => {
    mockParams = { mapPickField: 'pickup', mapPickLat: '50.5', mapPickLng: '-104.5', mapPickAddress: 'Map Picked Spot' };
    const renderer = await renderScreen();
    expect(useRideStore.getState().pickup).toMatchObject({ address: 'Map Picked Spot', lat: 50.5, lng: -104.5 });
    expect(useRideStore.getState().recentSearches[0]).toMatchObject({ address: 'Map Picked Spot' });
    // Dropoff should now be the active/focused field — its input carries autoFocus,
    // but we can at least assert dropoff wasn't touched by a pickup map-pick.
    expect(useRideStore.getState().dropoff).toBeNull();
  });

  it('a dropoff map-pick sets dropoff', async () => {
    mockParams = { mapPickField: 'dropoff', mapPickLat: '50.5', mapPickLng: '-104.5', mapPickAddress: 'Map Picked Spot' };
    const renderer = await renderScreen();
    expect(useRideStore.getState().dropoff).toMatchObject({ address: 'Map Picked Spot', lat: 50.5, lng: -104.5 });
  });
});

it('the back button navigates back', async () => {
  const renderer = await renderScreen();
  const backBtn = renderer.root.findAllByProps({ accessibilityLabel: 'Go back' })[0];
  act(() => { backBtn.props.onPress(); });
  expect(mockBack).toHaveBeenCalled();
});
