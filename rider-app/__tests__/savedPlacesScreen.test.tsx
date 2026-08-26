/**
 * app/saved-places.tsx — rider's saved-address book. Pins:
 *  - fetchSavedAddresses on mount
 *  - the empty state renders when there are no saved places
 *  - the add form: type-chip selection also seeds the label (only if the
 *    label is still empty); Save is disabled until a place is selected
 *    from autocomplete; a save failure toasts and leaves the form open
 *  - selecting a prediction geocodes via /maps/places/details, rotates
 *    the autocomplete session token, and shows the resolved address
 *  - Delete confirms via ConfirmSheet, then calls deleteSavedAddress
 *  - back nav
 *
 * ConfirmSheet is replaced with a lightweight double (matches
 * scheduledRidesScreen.test.tsx's convention) to bypass @gorhom/bottom-sheet.
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, TextInput } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

const mockBack = jest.fn();
jest.mock('expo-router', () => ({ useRouter: () => ({ back: mockBack }) }));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

let mockPredictions: any[] = [];
const mockClearPredictions = jest.fn();
const mockRotateSessionToken = jest.fn();
jest.mock('@shared/hooks/usePlacesAutocomplete', () => ({
  usePlacesAutocomplete: () => ({
    predictions: mockPredictions,
    loading: false,
    clear: mockClearPredictions,
    rotateSessionToken: mockRotateSessionToken,
    sessionToken: 'session-1',
  }),
}));

const mockFetchSavedAddresses = jest.fn();
const mockAddSavedAddress = jest.fn();
const mockDeleteSavedAddress = jest.fn();
let mockRideState: any;
jest.mock('../store/rideStore', () => ({
  useRideStore: () => mockRideState,
}));

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

import SavedPlacesScreen from '../app/saved-places';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

const PLACE_1 = { id: 'p1', name: 'Home', address: '100 Main St' };

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<SavedPlacesScreen />);
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
  mockPredictions = [];
  mockRideState = {
    savedAddresses: [],
    fetchSavedAddresses: mockFetchSavedAddresses,
    addSavedAddress: mockAddSavedAddress,
    deleteSavedAddress: mockDeleteSavedAddress,
    userLocation: null,
  };
  mockFetchSavedAddresses.mockResolvedValue(undefined);
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('SavedPlacesScreen', () => {
  it('fetches saved addresses on mount', async () => {
    await renderScreen();
    expect(mockFetchSavedAddresses).toHaveBeenCalled();
  });

  it('shows the empty state when there are no saved places', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('No saved places yet');
  });

  it('renders saved places', async () => {
    mockRideState.savedAddresses = [PLACE_1];
    const r = await renderScreen();
    expect(allText(r)).toContain('Home');
    expect(allText(r)).toContain('100 Main St');
  });

  it('opens the add form and seeds the label from a type chip only when the label is empty', async () => {
    const r = await renderScreen();
    const addBtn = findButtonByText(r, 'Add New Place');
    act(() => {
      addBtn.props.onPress();
    });
    const workChip = findButtonByText(r, 'Work');
    act(() => {
      workChip.props.onPress();
    });
    const labelInput = r.root.findByProps({ placeholder: "e.g. Home, Mom's house" });
    expect(labelInput.props.value).toBe('Work');

    // Once the label has been touched, a later chip selection must not
    // overwrite it.
    act(() => {
      labelInput.props.onChangeText('My custom label');
    });
    const gymChip = findButtonByText(r, 'Gym');
    act(() => {
      gymChip.props.onPress();
    });
    expect(r.root.findByProps({ placeholder: "e.g. Home, Mom's house" }).props.value).toBe('My custom label');
  });

  it('disables Save until a place is selected from autocomplete', async () => {
    const r = await renderScreen();
    const addBtn = findButtonByText(r, 'Add New Place');
    act(() => {
      addBtn.props.onPress();
    });
    const saveBtn = findButtonByText(r, 'Save Place');
    expect(saveBtn.props.disabled).toBe(true);
  });

  it('selects a prediction, geocodes it, and rotates the session token', async () => {
    mockApiGet.mockResolvedValue({
      data: { lat: 50.45, lng: -104.6, formatted_address: '100 Main St, Regina, SK' },
    });
    mockPredictions = [{
      place_id: 'pred-1',
      description: '100 Main St, Regina, SK, Canada',
      structured_formatting: { main_text: '100 Main St' },
    }];
    const r = await renderScreen();
    const addBtn = findButtonByText(r, 'Add New Place');
    act(() => {
      addBtn.props.onPress();
    });
    const predBtn = findButtonByText(r, '100 Main St');
    await act(async () => {
      await predBtn.props.onPress();
      await flush();
    });
    expect(mockApiGet).toHaveBeenCalledWith(
      expect.stringContaining('/maps/places/details?place_id=pred-1&session_token=session-1'),
    );
    expect(mockRotateSessionToken).toHaveBeenCalled();
    expect(allText(r)).toContain('100 Main St, Regina, SK');
  });

  it('saves a new place and closes the form', async () => {
    mockApiGet.mockResolvedValue({
      data: { lat: 50.45, lng: -104.6, formatted_address: '100 Main St, Regina, SK' },
    });
    mockPredictions = [{ place_id: 'pred-1', description: '100 Main St', structured_formatting: undefined }];
    mockAddSavedAddress.mockResolvedValue(undefined);
    const r = await renderScreen();
    const addBtn = findButtonByText(r, 'Add New Place');
    act(() => {
      addBtn.props.onPress();
    });
    const predBtn = findButtonByText(r, '100 Main St');
    await act(async () => {
      await predBtn.props.onPress();
      await flush();
    });
    const saveBtn = findButtonByText(r, 'Save Place');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockAddSavedAddress).toHaveBeenCalledWith({
      name: 'Home',
      address: '100 Main St, Regina, SK',
      lat: 50.45,
      lng: -104.6,
      icon: 'home',
    });
    expect(allText(r)).toContain('Add New Place');
  });

  it('shows a toast and leaves the form open on a save failure', async () => {
    mockApiGet.mockResolvedValue({
      data: { lat: 50.45, lng: -104.6, formatted_address: '100 Main St, Regina, SK' },
    });
    mockPredictions = [{ place_id: 'pred-1', description: '100 Main St', structured_formatting: undefined }];
    mockAddSavedAddress.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const addBtn = findButtonByText(r, 'Add New Place');
    act(() => {
      addBtn.props.onPress();
    });
    const predBtn = findButtonByText(r, '100 Main St');
    await act(async () => {
      await predBtn.props.onPress();
      await flush();
    });
    const saveBtn = findButtonByText(r, 'Save Place');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'Save Failed', 'Could not save this place. Please try again.', 'danger',
    );
    expect(allText(r)).toContain('Save Place');
  });

  it('blocks save with a warning toast if somehow submitted with no place selected', async () => {
    // handleSave's own guard: even though Save is disabled via the button
    // prop, calling the handler directly (e.g. a future regression that
    // drops the `disabled` prop) must still be blocked.
    const r = await renderScreen();
    const addBtn = findButtonByText(r, 'Add New Place');
    act(() => {
      addBtn.props.onPress();
    });
    const saveBtn = findButtonByText(r, 'Save Place');
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'Address Required', 'Please search for and select an address', 'warning',
    );
    expect(mockAddSavedAddress).not.toHaveBeenCalled();
  });

  it('confirms via the sheet before deleting, then calls deleteSavedAddress', async () => {
    mockRideState.savedAddresses = [PLACE_1];
    const r = await renderScreen();
    const deleteBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByProps({ name: 'trash-outline' }).length > 0)!;
    act(() => {
      deleteBtn.props.onPress();
    });
    expect(allText(r)).toContain('Remove Place');
    expect(allText(r)).toContain('Remove \\"Home\\" from saved places?');
    const confirmBtn = r.root.findByProps({ accessibilityLabel: 'confirm-Remove' });
    act(() => {
      confirmBtn.props.onPress();
    });
    expect(mockDeleteSavedAddress).toHaveBeenCalledWith('p1');
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
