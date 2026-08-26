/**
 * app/driver/destination-mode.tsx — driver "heading home" destination
 * filter. Pins:
 *  - GET /drivers/destination on mount, populating the address input
 *  - Save: blocks on an empty/whitespace-only address; geocodes via the
 *    autocomplete->details session-token flow, then POST /drivers/destination
 *    on success, or a "not found" toast when geocoding returns nothing
 *  - Clear: confirms via Alert.alert, then DELETE /drivers/destination and
 *    resets local state to inactive
 *  - load/save/clear failures each surface their own toast
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, Alert } from 'react-native';

import DestinationModeScreen from '../../app/driver/destination-mode';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack }),
}));

const COLORS = {
  primary: '#EF4444', background: '#FFF', surface: '#FFF', surfaceLight: '#F5F5F5',
  text: '#111', textDim: '#666', border: '#E5E7EB', error: '#DC2626',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

// Identity translation -- assertions below check on the i18n key, matching
// what `t()` is actually called with, not brittle hardcoded English copy.
// `t` and the object wrapping it must be stable references: the screen's
// fetchDestination is a useCallback keyed on [t], so a factory that
// allocates a fresh `{ t: ... }` (or a fresh `t` closure) on every call
// makes that dependency "change" every render, which retriggers the
// mount effect that calls it -- an infinite render loop that hangs the
// test with no useful stack trace.
const mockT = (key: string) => key;
const mockLanguageState = { t: mockT };
jest.mock('../../store/languageStore', () => ({
  useLanguageStore: () => mockLanguageState,
}));

jest.mock('@shared/utils/placesSession', () => ({
  newPlacesSessionToken: () => 'session-token-1',
}));

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
const mockApiDelete = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    get: (...a: any[]) => mockApiGet(...a),
    post: (...a: any[]) => mockApiPost(...a),
    delete: (...a: any[]) => mockApiDelete(...a),
  },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockShowToast = jest.fn();
jest.mock('../../hooks/useToast', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const INACTIVE = { destination_mode: false, destination_address: null, destination_lat: null, destination_lng: null };
const ACTIVE = { destination_mode: true, destination_address: '123 Main St', destination_lat: 50.45, destination_lng: -104.6 };

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<DestinationModeScreen />);
    await flush();
  });
  return renderer!;
}

function findButtonByChildText(r: TestRenderer.ReactTestRenderer, text: string) {
  return r.root
    .findAllByType(TouchableOpacity)
    .find((node) => node.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes(text)))!;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApiGet.mockResolvedValue({ data: INACTIVE });
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.restoreAllMocks();
});

describe('DestinationModeScreen', () => {
  it('loads the current destination state on mount', async () => {
    mockApiGet.mockResolvedValue({ data: ACTIVE });
    const r = await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/drivers/destination');
    const input = r.root.findByProps({ placeholder: 'destinationMode.addressPlaceholder' });
    expect(input.props.value).toBe('123 Main St');
  });

  it('shows a toast when the initial load fails', async () => {
    mockApiGet.mockRejectedValue(new Error('network down'));
    await renderScreen();
    expect(mockShowToast).toHaveBeenCalledWith(
      'error',
      'destinationMode.loadFailedTitle',
      'destinationMode.loadFailedMsg',
    );
  });

  it('blocks saving an empty address', async () => {
    const r = await renderScreen();
    const saveBtn = findButtonByChildText(r, 'destinationMode.activateBtn');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'warning',
      'destinationMode.missingAddressTitle',
      'destinationMode.missingAddressMsg',
    );
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it('geocodes and saves a valid address', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/destination') return Promise.resolve({ data: INACTIVE });
      if (url.startsWith('/maps/places/autocomplete')) {
        return Promise.resolve({ data: { predictions: [{ place_id: 'p1', description: '123 Main St' }] } });
      }
      if (url.startsWith('/maps/places/details')) {
        return Promise.resolve({ data: { lat: 50.45, lng: -104.6 } });
      }
      return Promise.reject(new Error('unexpected url ' + url));
    });
    mockApiPost.mockResolvedValue({ data: {} });
    const r = await renderScreen();
    const input = r.root.findByProps({ placeholder: 'destinationMode.addressPlaceholder' });
    act(() => {
      input.props.onChangeText('123 Main St');
    });
    const saveBtn = findButtonByChildText(r, 'destinationMode.activateBtn');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/drivers/destination', {
      address: '123 Main St',
      lat: 50.45,
      lng: -104.6,
    });
    expect(mockShowToast).toHaveBeenCalledWith('success', 'destinationMode.savedTitle', 'destinationMode.savedMsg');
  });

  it('shows a "not found" toast when geocoding returns no result', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/destination') return Promise.resolve({ data: INACTIVE });
      if (url.startsWith('/maps/places/autocomplete')) return Promise.resolve({ data: { predictions: [] } });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    const input = r.root.findByProps({ placeholder: 'destinationMode.addressPlaceholder' });
    act(() => {
      input.props.onChangeText('nowhere');
    });
    const saveBtn = findButtonByChildText(r, 'destinationMode.activateBtn');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).not.toHaveBeenCalled();
    expect(mockShowToast).toHaveBeenCalledWith(
      'warning',
      'destinationMode.notFoundTitle',
      'destinationMode.notFoundMsg',
    );
  });

  it('shows a toast when the save POST fails', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/destination') return Promise.resolve({ data: INACTIVE });
      if (url.startsWith('/maps/places/autocomplete')) {
        return Promise.resolve({ data: { predictions: [{ place_id: 'p1', description: 'x' }] } });
      }
      if (url.startsWith('/maps/places/details')) return Promise.resolve({ data: { lat: 1, lng: 2 } });
      return Promise.reject(new Error('unexpected'));
    });
    mockApiPost.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const input = r.root.findByProps({ placeholder: 'destinationMode.addressPlaceholder' });
    act(() => {
      input.props.onChangeText('123 Main St');
    });
    const saveBtn = findButtonByChildText(r, 'destinationMode.activateBtn');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'error',
      'destinationMode.saveFailedTitle',
      'destinationMode.saveFailedMsg',
    );
  });

  it('shows the Clear button only when destination mode is active, and confirms before clearing', async () => {
    mockApiGet.mockResolvedValue({ data: ACTIVE });
    const r = await renderScreen();
    const clearBtn = findButtonByChildText(r, 'destinationMode.clearBtn');
    act(() => {
      clearBtn.props.onPress();
    });
    expect(Alert.alert).toHaveBeenCalledWith(
      'destinationMode.clearConfirmTitle',
      'destinationMode.clearConfirmMsg',
      expect.any(Array),
    );
    expect(mockApiDelete).not.toHaveBeenCalled();
  });

  it('clears the destination after confirming', async () => {
    mockApiGet.mockResolvedValue({ data: ACTIVE });
    mockApiDelete.mockResolvedValue({ data: {} });
    const r = await renderScreen();
    const clearBtn = findButtonByChildText(r, 'destinationMode.clearBtn');
    act(() => {
      clearBtn.props.onPress();
    });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const confirmAction = alertCall[2].find((b: any) => b.style === 'destructive');
    await act(async () => {
      await confirmAction.onPress();
      await flush();
    });
    expect(mockApiDelete).toHaveBeenCalledWith('/drivers/destination');
    expect(mockShowToast).toHaveBeenCalledWith('success', 'destinationMode.clearedTitle', 'destinationMode.clearedMsg');
  });

  it('shows a toast when the clear DELETE fails', async () => {
    mockApiGet.mockResolvedValue({ data: ACTIVE });
    mockApiDelete.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const clearBtn = findButtonByChildText(r, 'destinationMode.clearBtn');
    act(() => {
      clearBtn.props.onPress();
    });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const confirmAction = alertCall[2].find((b: any) => b.style === 'destructive');
    await act(async () => {
      await confirmAction.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'error',
      'destinationMode.clearFailedTitle',
      'destinationMode.clearFailedMsg',
    );
  });

  it('navigates back when the back button is pressed', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => {
      backBtn.props.onPress();
    });
    expect(mockBack).toHaveBeenCalled();
  });
});
