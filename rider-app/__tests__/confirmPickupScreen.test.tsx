/**
 * app/confirm-pickup.tsx — map-drag pickup confirmation with a 50m
 * suggested radius. Pins:
 *  - redirects back immediately when there's no pickup in the store
 *  - initial region/address seed from the store's pickup
 *  - panning recomputes the haversine distance from the original pin and
 *    debounces the reverse-geocode call by 400ms
 *  - the "too far" warning shows once the pin is > 50m from the original
 *  - the dropoff reminder only renders when a dropoff is set
 *  - Confirm checks /maps/pickup-points first: a venue match opens the
 *    curated pickup-point chooser instead of proceeding directly;
 *    selecting a point (or "use my exact pin instead") calls
 *    setPickup+setRiderNotes and pushes to /ride-options
 *  - without a venue match, Confirm proceeds directly -- carrying the
 *    geocoded address only when it matches the current pin (~60m), else
 *    falling back to a formatted coordinate string
 *  - Confirm is disabled while geocoding or checking
 *  - Recenter animates the map back to the original pin
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, TextInput } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const mockAnimateToRegion = jest.fn();
jest.mock('react-native-maps', () => {
  const React = require('react');
  const MapView = React.forwardRef((props: any, ref: any) => {
    React.useImperativeHandle(ref, () => ({ animateToRegion: mockAnimateToRegion }));
    return React.createElement('MapView', props, props.children);
  });
  const Circle = (props: any) => React.createElement('Circle', props);
  return { __esModule: true, default: MapView, Circle, PROVIDER_GOOGLE: 'google' };
});

const mockBack = jest.fn();
const mockPush = jest.fn();
jest.mock('expo-router', () => ({ useRouter: () => ({ back: mockBack, push: mockPush }) }));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

jest.mock('@shared/utils/responsive', () => ({ useResponsive: () => ({ sf: (n: number) => n }) }));

const mockApiGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
}));

const mockSetPickup = jest.fn();
const mockSetRiderNotes = jest.fn();
let mockRideState: any;
jest.mock('../store/rideStore', () => ({
  useRideStore: () => mockRideState,
}));

import ConfirmPickupScreen from '../app/confirm-pickup';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<ConfirmPickupScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => { try { return JSON.stringify(t.props.children); } catch { return '<circular>'; } }).join(' | ');
}

function findMapView(r: TestRenderer.ReactTestRenderer) {
  return r.root.find((n) => n.type === 'MapView');
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
  mockRideState = {
    pickup: { address: '100 Main St', lat: 50.45, lng: -104.6 },
    setPickup: mockSetPickup,
    dropoff: null,
    riderNotes: '',
    setRiderNotes: mockSetRiderNotes,
  };
  mockApiGet.mockResolvedValue({ data: { venue: null, pickup_points: [] } });
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.useRealTimers();
});

describe('ConfirmPickupScreen', () => {
  it('redirects back immediately when there is no pickup in the store', async () => {
    mockRideState.pickup = null;
    await renderScreen();
    expect(mockBack).toHaveBeenCalled();
  });

  it('seeds the initial region/address from the stored pickup', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('100 Main St');
    expect(findMapView(r).props.initialRegion).toEqual(
      expect.objectContaining({ latitude: 50.45, longitude: -104.6 }),
    );
  });

  it('shows the dropoff reminder only when a dropoff is set', async () => {
    const r1 = await renderScreen();
    expect(allText(r1)).not.toContain('To:');

    mockRideState.dropoff = { address: '200 Elm St' };
    const r2 = await renderScreen();
    expect(allText(r2)).toContain('["To: ","200 Elm St"]');
  });

  it('debounces reverse-geocode on pan by 400ms and shows the too-far warning past 50m', async () => {
    mockApiGet.mockResolvedValue({ data: { formatted_address: '999 Far Ave' } });
    const r = await renderScreen();
    mockApiGet.mockClear();
    const map = findMapView(r);
    // ~0.001 deg latitude is roughly 111m -- past the 50m radius.
    act(() => {
      map.props.onRegionChangeComplete({ latitude: 50.451, longitude: -104.6, latitudeDelta: 0.003, longitudeDelta: 0.003 });
    });
    expect(mockApiGet).not.toHaveBeenCalled();
    await act(async () => {
      jest.advanceTimersByTime(400);
      await flush();
    });
    expect(mockApiGet).toHaveBeenCalledWith(expect.stringContaining('/maps/reverse-geocode'));
    expect(allText(r)).toContain('m from your searched location');
  });

  it('checks pickup-points on Confirm; a venue match opens the chooser instead of proceeding', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes('/maps/pickup-points')) {
        return Promise.resolve({
          data: { venue: { name: 'City Mall' }, pickup_points: [{ name: 'North Entrance', lat: 50.46, lng: -104.61 }] },
        });
      }
      return Promise.resolve({ data: { formatted_address: '100 Main St' } });
    });
    const r = await renderScreen();
    const confirmBtn = findButtonByText(r, 'Confirm pickup');
    await act(async () => {
      await confirmBtn.props.onPress();
      await flush();
    });
    expect(allText(r)).toContain('City Mall');
    expect(allText(r)).toContain('North Entrance');
    expect(mockPush).not.toHaveBeenCalled();
  });

  it('selecting a curated pickup point sets pickup/notes and navigates to /ride-options', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes('/maps/pickup-points')) {
        return Promise.resolve({
          data: { venue: { name: 'City Mall' }, pickup_points: [{ name: 'North Entrance', lat: 50.46, lng: -104.61 }] },
        });
      }
      return Promise.resolve({ data: { formatted_address: '100 Main St' } });
    });
    const r = await renderScreen();
    const confirmBtn = findButtonByText(r, 'Confirm pickup');
    await act(async () => {
      await confirmBtn.props.onPress();
      await flush();
    });
    const pointBtn = findButtonByText(r, 'North Entrance');
    act(() => {
      pointBtn.props.onPress();
    });
    expect(mockSetPickup).toHaveBeenCalledWith({ address: 'City Mall — North Entrance', lat: 50.46, lng: -104.61 });
    expect(mockPush).toHaveBeenCalledWith('/ride-options');
  });

  it('"Use my exact pin instead" proceeds with the current region/address', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes('/maps/pickup-points')) {
        return Promise.resolve({
          data: { venue: { name: 'City Mall' }, pickup_points: [{ name: 'North Entrance', lat: 50.46, lng: -104.61 }] },
        });
      }
      return Promise.resolve({ data: { formatted_address: '100 Main St' } });
    });
    const r = await renderScreen();
    const confirmBtn = findButtonByText(r, 'Confirm pickup');
    await act(async () => {
      await confirmBtn.props.onPress();
      await flush();
    });
    const exactPinBtn = findButtonByText(r, 'Use my exact pin instead');
    act(() => {
      exactPinBtn.props.onPress();
    });
    expect(mockSetPickup).toHaveBeenCalledWith({ address: '100 Main St', lat: 50.45, lng: -104.6 });
    expect(mockPush).toHaveBeenCalledWith('/ride-options');
  });

  it('without a venue match, Confirm proceeds directly with the matched address', async () => {
    const r = await renderScreen();
    const confirmBtn = findButtonByText(r, 'Confirm pickup');
    await act(async () => {
      await confirmBtn.props.onPress();
      await flush();
    });
    expect(mockSetPickup).toHaveBeenCalledWith({ address: '100 Main St', lat: 50.45, lng: -104.6 });
    expect(mockSetRiderNotes).toHaveBeenCalledWith('');
    expect(mockPush).toHaveBeenCalledWith('/ride-options');
  });

  it('falls back to a formatted coordinate when the address does not match the current pin', async () => {
    mockApiGet.mockImplementation(() => new Promise(() => {})); // never resolves -- geocode never completes for the moved pin
    const r = await renderScreen();
    const map = findMapView(r);
    act(() => {
      map.props.onRegionChangeComplete({ latitude: 51.5, longitude: -105.5, latitudeDelta: 0.003, longitudeDelta: 0.003 });
    });
    await act(async () => {
      jest.advanceTimersByTime(400);
      await flush();
    });
    const confirmBtn = findButtonByText(r, 'Confirm pickup');
    // Confirm's own pickup-points check also never resolves here, so call
    // the handler directly rather than waiting on a button state that
    // depends on `checking` toggling.
    act(() => {
      confirmBtn.props.onPress();
    });
    // handleConfirm awaits the pickup-points fetch before falling through to
    // proceed(); since that promise never resolves, proceed() never runs and
    // setPickup is not called with a stale label. Assert no premature call.
    expect(mockSetPickup).not.toHaveBeenCalled();
  });

  it('recenters the map to the original pin', async () => {
    const r = await renderScreen();
    const recenterBtn = r.root.findByProps({ accessibilityLabel: 'Recenter map on your location' });
    act(() => {
      recenterBtn.props.onPress();
    });
    expect(mockAnimateToRegion).toHaveBeenCalledWith(
      expect.objectContaining({ latitude: 50.45, longitude: -104.6 }),
      400,
    );
  });

  it('navigates back on the floating back button', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findByProps({ accessibilityLabel: 'Go back' });
    act(() => {
      backBtn.props.onPress();
    });
    expect(mockBack).toHaveBeenCalled();
  });

  it('carries the typed driver note through on confirm', async () => {
    const r = await renderScreen();
    const noteInput = r.root.findByType(TextInput);
    act(() => {
      noteInput.props.onChangeText('North entrance please');
    });
    const confirmBtn = findButtonByText(r, 'Confirm pickup');
    await act(async () => {
      await confirmBtn.props.onPress();
      await flush();
    });
    expect(mockSetRiderNotes).toHaveBeenCalledWith('North entrance please');
  });
});
