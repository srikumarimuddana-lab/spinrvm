/**
 * app/driver/destination-mode.tsx — driver "heading home" destination
 * filter. Pins:
 *  - GET /drivers/destination on mount, populating the address input
 *  - on/off is decided from the server's `active` flag (isDestinationActive),
 *    not the raw stored `destination_mode` flag
 *  - enabled=false (settings.destination_mode_enabled off, migration 482):
 *    the form is replaced by an "unavailable" notice; a stored row can
 *    still be cleared
 *  - address entry is the shared usePlacesAutocomplete typeahead, biased to
 *    the driver's last-known GPS; the driver must pick from the list
 *    (place details -> coords) before Save POSTs /drivers/destination
 *  - search failure ("temporarily unavailable") is distinguished from a
 *    genuine empty result ("no matching address")
 *  - Clear: confirms via Alert.alert, then DELETE /drivers/destination
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

const HOOK_IDLE = {
  predictions: [] as any[],
  loading: false,
  error: null as null | 'unavailable',
  searched: false,
  clear: jest.fn(),
  rotateSessionToken: jest.fn(),
  sessionToken: 'session-token-1',
};
let mockHookState = { ...HOOK_IDLE };
const mockUsePlaces = jest.fn((..._args: any[]) => mockHookState);
jest.mock('@shared/hooks/usePlacesAutocomplete', () => ({
  usePlacesAutocomplete: (...a: any[]) => mockUsePlaces(...a),
}));

const mockGetPerms = jest.fn();
const mockLastKnown = jest.fn();
jest.mock('expo-location', () => ({
  getForegroundPermissionsAsync: (...a: any[]) => mockGetPerms(...a),
  getLastKnownPositionAsync: (...a: any[]) => mockLastKnown(...a),
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
  mockHookState = { ...HOOK_IDLE };
  mockGetPerms.mockResolvedValue({ granted: true });
  mockLastKnown.mockResolvedValue({ coords: { latitude: 52.13, longitude: -106.67 } });
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

  it('refuses to save typed text that was not picked from the list', async () => {
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
    expect(mockApiPost).not.toHaveBeenCalled();
    expect(mockShowToast).toHaveBeenCalledWith(
      'warning',
      'destinationMode.missingAddressTitle',
      'destinationMode.pickFromListMsg',
    );
  });

  it('biases the typeahead to the driver GPS and passes the typed text', async () => {
    const r = await renderScreen();
    const input = r.root.findByProps({ placeholder: 'destinationMode.addressPlaceholder' });
    act(() => {
      input.props.onChangeText('123 Main');
    });
    const lastCall = mockUsePlaces.mock.calls[mockUsePlaces.mock.calls.length - 1];
    expect(lastCall[0]).toBe('123 Main');
    expect(lastCall[1]).toEqual({ lat: 52.13, lng: -106.67, radiusMeters: 50000 });
  });

  it('does not bias (and does not prompt) when location is not already granted', async () => {
    mockGetPerms.mockResolvedValue({ granted: false });
    await renderScreen();
    expect(mockLastKnown).not.toHaveBeenCalled();
    const lastCall = mockUsePlaces.mock.calls[mockUsePlaces.mock.calls.length - 1];
    expect(lastCall[1]).toBeNull();
  });

  it('shows a pick list; picking resolves details and Save posts the picked place', async () => {
    mockHookState = {
      ...HOOK_IDLE,
      searched: true,
      predictions: [
        {
          place_id: 'p1',
          description: '123 Main St, Saskatoon, SK',
          structured_formatting: { main_text: '123 Main St', secondary_text: 'Saskatoon, SK' },
        },
        { place_id: 'p2', description: '123 Main St, Regina, SK' },
      ],
    };
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/destination') return Promise.resolve({ data: { ...INACTIVE, enabled: true, active: false } });
      if (url.startsWith('/maps/places/details')) return Promise.resolve({ data: { lat: 52.12, lng: -106.66 } });
      return Promise.reject(new Error('unexpected url ' + url));
    });
    // Relative to now, not a fixed date: isDestinationActive() treats a past
    // expiry as off, so a hardcoded timestamp turns this test red once it passes.
    mockApiPost.mockResolvedValue({
      data: { destination_expires_at: new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString() },
    });
    const r = await renderScreen();
    const input = r.root.findByProps({ placeholder: 'destinationMode.addressPlaceholder' });
    act(() => {
      input.props.onChangeText('123 main');
    });
    expect(r.root.findByProps({ testID: 'destination-predictions' })).toBeTruthy();
    const row = r.root.findByProps({ testID: 'destination-prediction-p1' });
    await act(async () => {
      await row.props.onPress();
      await flush();
    });
    expect(mockApiGet).toHaveBeenCalledWith('/maps/places/details?place_id=p1&session_token=session-token-1');
    expect(mockHookState.rotateSessionToken).toHaveBeenCalled();
    expect(r.root.findByProps({ placeholder: 'destinationMode.addressPlaceholder' }).props.value).toBe(
      '123 Main St, Saskatoon, SK',
    );

    const saveBtn = findButtonByChildText(r, 'destinationMode.activateBtn');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/drivers/destination', {
      address: '123 Main St, Saskatoon, SK',
      lat: 52.12,
      lng: -106.66,
    });
    expect(mockShowToast).toHaveBeenCalledWith('success', 'destinationMode.savedTitle', 'destinationMode.savedMsg');
    // Now reads as on (server active=true is mirrored locally after the POST).
    expect(r.root.findByProps({ testID: 'destination-mode-status' }).props.children).toBe('destinationMode.activeLabel');
  });

  it('shows "not found" when the picked place has no coordinates', async () => {
    mockHookState = { ...HOOK_IDLE, searched: true, predictions: [{ place_id: 'p1', description: 'Somewhere' }] };
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/destination') return Promise.resolve({ data: INACTIVE });
      if (url.startsWith('/maps/places/details')) return Promise.resolve({ data: { lat: null, lng: null } });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    act(() => {
      r.root.findByProps({ placeholder: 'destinationMode.addressPlaceholder' }).props.onChangeText('somewhere');
    });
    await act(async () => {
      await r.root.findByProps({ testID: 'destination-prediction-p1' }).props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('warning', 'destinationMode.notFoundTitle', 'destinationMode.notFoundMsg');
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it('distinguishes search unavailable (429/5xx) from no matching address', async () => {
    mockHookState = { ...HOOK_IDLE, error: 'unavailable' };
    const r = await renderScreen();
    act(() => {
      r.root.findByProps({ placeholder: 'destinationMode.addressPlaceholder' }).props.onChangeText('123 main');
    });
    expect(r.root.findByProps({ testID: 'destination-search-unavailable' }).props.children).toBe(
      'destinationMode.searchUnavailableMsg',
    );
    expect(r.root.findAllByProps({ testID: 'destination-search-no-match' })).toHaveLength(0);

    mockHookState = { ...HOOK_IDLE, searched: true, predictions: [] };
    act(() => {
      r.root.findByProps({ placeholder: 'destinationMode.addressPlaceholder' }).props.onChangeText('zzzz qqq');
    });
    expect(r.root.findByProps({ testID: 'destination-search-no-match' }).props.children).toBe(
      'destinationMode.noMatchingAddress',
    );
    expect(r.root.findAllByProps({ testID: 'destination-search-unavailable' })).toHaveLength(0);
  });

  it('shows neither message while a search is still pending', async () => {
    mockHookState = { ...HOOK_IDLE, loading: true };
    const r = await renderScreen();
    expect(r.root.findAllByProps({ testID: 'destination-search-no-match' })).toHaveLength(0);
    expect(r.root.findAllByProps({ testID: 'destination-search-unavailable' })).toHaveLength(0);
  });

  it('shows a toast when the save POST fails (e.g. 409 feature switched off)', async () => {
    mockApiGet.mockResolvedValue({ data: { ...ACTIVE, active: true } });
    mockApiPost.mockRejectedValue(
      Object.assign(new Error('Request failed with status code 409'), {
        response: { status: 409, data: { detail: 'Destination mode is not available.' } },
      }),
    );
    const r = await renderScreen();
    const saveBtn = findButtonByChildText(r, 'destinationMode.updateBtn');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/drivers/destination', {
      address: '123 Main St',
      lat: 50.45,
      lng: -104.6,
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'error',
      'destinationMode.saveFailedTitle',
      'destinationMode.saveFailedMsg',
    );
  });

  it('decides on/off from the server active flag, not the raw destination_mode', async () => {
    // Expired row: raw flag still true, server says not active.
    mockApiGet.mockResolvedValue({ data: { ...ACTIVE, active: false, enabled: true } });
    const r = await renderScreen();
    expect(r.root.findByProps({ testID: 'destination-mode-status' }).props.children).toBe(
      'destinationMode.inactiveLabel',
    );
    expect(findButtonByChildText(r, 'destinationMode.activateBtn')).toBeTruthy();
    // The stale row can still be cleared.
    expect(findButtonByChildText(r, 'destinationMode.clearBtn')).toBeTruthy();
  });

  it('reads as on when the server reports active', async () => {
    mockApiGet.mockResolvedValue({ data: { ...ACTIVE, active: true, enabled: true } });
    const r = await renderScreen();
    expect(r.root.findByProps({ testID: 'destination-mode-status' }).props.children).toBe('destinationMode.activeLabel');
  });

  it('when the feature is switched off, shows the unavailable notice, no form, and never searches', async () => {
    mockApiGet.mockResolvedValue({ data: { ...ACTIVE, active: false, enabled: false } });
    const r = await renderScreen();
    expect(r.root.findByProps({ testID: 'destination-mode-unavailable' })).toBeTruthy();
    expect(r.root.findAllByProps({ placeholder: 'destinationMode.addressPlaceholder' })).toHaveLength(0);
    expect(findButtonByChildText(r, 'destinationMode.activateBtn')).toBeUndefined();
    const lastCall = mockUsePlaces.mock.calls[mockUsePlaces.mock.calls.length - 1];
    expect(lastCall[0]).toBe('');
    // A stored row can still be cleared while switched off.
    expect(findButtonByChildText(r, 'destinationMode.clearBtn')).toBeTruthy();
  });

  it('when switched off with nothing stored, shows only the notice', async () => {
    mockApiGet.mockResolvedValue({ data: { ...INACTIVE, active: false, enabled: false } });
    const r = await renderScreen();
    expect(r.root.findByProps({ testID: 'destination-mode-unavailable' })).toBeTruthy();
    expect(findButtonByChildText(r, 'destinationMode.clearBtn')).toBeUndefined();
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
