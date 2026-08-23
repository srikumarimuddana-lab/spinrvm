/**
 * app/driver/addresses.tsx — driver's saved-address book. Pins:
 *  - GET /addresses on mount; a load failure toasts (never spreads the
 *    raw error body into logs per its own PIPEDA comment) but still
 *    stops the loading spinner
 *  - Add: blocks on an empty name or address, geocodes via the
 *    autocomplete->details session flow, shows a "not found" toast on no
 *    geocode result, else POSTs and closes the modal/resets the form/
 *    re-fetches
 *  - a save failure surfaces its own toast
 *  - Delete: confirms via Alert.alert, then DELETE + re-fetch, with its
 *    own failure toast
 *  - the empty state (with its own Add Address CTA) renders when there
 *    are no addresses
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, Alert } from 'react-native';

import AddressesScreen from '../../app/driver/addresses';

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
  text: '#111', textDim: '#666', border: '#E5E7EB', danger: '#DC2626',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

jest.mock('@shared/utils/placesSession', () => ({ newPlacesSessionToken: () => 'session-1' }));

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

const ADDRESS_1 = { id: 'a1', name: 'Home', address: '100 Main St', lat: 50.45, lng: -104.6, icon: 'home' };

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<AddressesScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => JSON.stringify(t.props.children)).join(' | ');
}

function findButtonByText(r: TestRenderer.ReactTestRenderer, text: string) {
  return r.root
    .findAllByType(TouchableOpacity)
    .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes(text)))!;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApiGet.mockResolvedValue({ data: [] });
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.restoreAllMocks();
});

describe('AddressesScreen', () => {
  it('loads addresses on mount', async () => {
    mockApiGet.mockResolvedValue({ data: [ADDRESS_1] });
    const r = await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/addresses');
    expect(allText(r)).toContain('Home');
    expect(allText(r)).toContain('100 Main St');
  });

  it('shows a toast (and still stops loading) when the load fails', async () => {
    mockApiGet.mockRejectedValue(new Error('network down'));
    const r = await renderScreen();
    expect(mockShowToast).toHaveBeenCalledWith(
      'error',
      'Load Failed',
      'Could not load your saved addresses. Please try again.',
    );
    expect(allText(r)).toContain('No saved addresses');
  });

  it('shows the empty state with its own Add Address CTA', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('No saved addresses');
    const emptyAddBtn = findButtonByText(r, 'Add Address');
    act(() => {
      emptyAddBtn.props.onPress();
    });
    expect(allText(r)).toContain('Add New Address');
  });

  it('blocks adding with an empty name or address', async () => {
    const r = await renderScreen();
    const headerAddBtn = r.root.findAllByType(TouchableOpacity)[1]; // [0] back, [1] header add
    act(() => {
      headerAddBtn.props.onPress();
    });
    const saveBtn = findButtonByText(r, 'Save');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Missing Fields', 'Please fill in both fields');
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it('geocodes and saves a valid address', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/addresses') return Promise.resolve({ data: [] });
      if (url.startsWith('/maps/places/autocomplete')) {
        return Promise.resolve({ data: { predictions: [{ place_id: 'p1', description: '100 Main St' }] } });
      }
      if (url.startsWith('/maps/places/details')) return Promise.resolve({ data: { lat: 50.45, lng: -104.6 } });
      return Promise.reject(new Error('unexpected url ' + url));
    });
    mockApiPost.mockResolvedValue({ data: {} });
    const r = await renderScreen();
    const headerAddBtn = r.root.findAllByType(TouchableOpacity)[1];
    act(() => {
      headerAddBtn.props.onPress();
    });
    const nameInput = r.root.findByProps({ placeholder: 'Enter name' });
    act(() => {
      nameInput.props.onChangeText('Home');
    });
    const addressInput = r.root.findByProps({ placeholder: 'Enter full address' });
    act(() => {
      addressInput.props.onChangeText('100 Main St');
    });
    const saveBtn = findButtonByText(r, 'Save');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/addresses', {
      name: 'Home', address: '100 Main St', lat: 50.45, lng: -104.6, icon: 'home',
    });
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Address Saved', 'Address has been saved.');
  });

  it('shows a "not found" toast when geocoding returns no result', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/addresses') return Promise.resolve({ data: [] });
      if (url.startsWith('/maps/places/autocomplete')) return Promise.resolve({ data: { predictions: [] } });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    const headerAddBtn = r.root.findAllByType(TouchableOpacity)[1];
    act(() => {
      headerAddBtn.props.onPress();
    });
    const nameInput = r.root.findByProps({ placeholder: 'Enter name' });
    act(() => {
      nameInput.props.onChangeText('Somewhere');
    });
    const addressInput = r.root.findByProps({ placeholder: 'Enter full address' });
    act(() => {
      addressInput.props.onChangeText('nowhere');
    });
    const saveBtn = findButtonByText(r, 'Save');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).not.toHaveBeenCalled();
    expect(mockShowToast).toHaveBeenCalledWith(
      'warning',
      'Address not found',
      expect.stringContaining('could not locate'),
    );
  });

  it('shows a toast when the save POST fails', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/addresses') return Promise.resolve({ data: [] });
      if (url.startsWith('/maps/places/autocomplete')) {
        return Promise.resolve({ data: { predictions: [{ place_id: 'p1', description: 'x' }] } });
      }
      if (url.startsWith('/maps/places/details')) return Promise.resolve({ data: { lat: 1, lng: 2 } });
      return Promise.reject(new Error('unexpected'));
    });
    mockApiPost.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const headerAddBtn = r.root.findAllByType(TouchableOpacity)[1];
    act(() => {
      headerAddBtn.props.onPress();
    });
    const nameInput = r.root.findByProps({ placeholder: 'Enter name' });
    act(() => {
      nameInput.props.onChangeText('Home');
    });
    const addressInput = r.root.findByProps({ placeholder: 'Enter full address' });
    act(() => {
      addressInput.props.onChangeText('100 Main St');
    });
    const saveBtn = findButtonByText(r, 'Save');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'error',
      'Save Failed',
      'Could not save your address. Please try again.',
    );
  });

  it('confirms via Alert before deleting, then deletes and re-fetches', async () => {
    mockApiGet.mockResolvedValue({ data: [ADDRESS_1] });
    mockApiDelete.mockResolvedValue({ data: {} });
    const r = await renderScreen();
    const deleteBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByProps({ name: 'trash-outline' }).length > 0)!;
    act(() => {
      deleteBtn.props.onPress();
    });
    expect(Alert.alert).toHaveBeenCalledWith(
      'Delete Address',
      'Are you sure you want to delete this address?',
      expect.any(Array),
    );
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const confirmAction = alertCall[2].find((b: any) => b.style === 'destructive');
    mockApiGet.mockClear();
    await act(async () => {
      await confirmAction.onPress();
      await flush();
    });
    expect(mockApiDelete).toHaveBeenCalledWith('/addresses/a1');
    expect(mockApiGet).toHaveBeenCalledWith('/addresses');
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Address Deleted', 'Address has been removed.');
  });

  it('shows a toast when deletion fails', async () => {
    mockApiGet.mockResolvedValue({ data: [ADDRESS_1] });
    mockApiDelete.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const deleteBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByProps({ name: 'trash-outline' }).length > 0)!;
    act(() => {
      deleteBtn.props.onPress();
    });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const confirmAction = alertCall[2].find((b: any) => b.style === 'destructive');
    await act(async () => {
      await confirmAction.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'error',
      'Delete Failed',
      'Could not delete the address. Please try again.',
    );
  });

  it('navigates back when tapping a saved address', async () => {
    mockApiGet.mockResolvedValue({ data: [ADDRESS_1] });
    const r = await renderScreen();
    const addressRow = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Home')))!;
    act(() => {
      addressRow.props.onPress();
    });
    expect(mockBack).toHaveBeenCalled();
  });

  it('navigates back when the header back button is pressed', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => {
      backBtn.props.onPress();
    });
    expect(mockBack).toHaveBeenCalled();
  });
});
