/**
 * app/vehicle-info.tsx — driver vehicle info form (triggers re-verification
 * on save). Pins:
 *  - vehicle types are fetched scoped to the driver's service_area_id;
 *    no area selected -> no fetch, empty list (never falls back to the
 *    unfiltered global list, per the file's own comment about a real bug)
 *  - a fetch failure toasts and shows the picker's own error/retry state
 *  - the form is pre-seeded from the driver prop, and re-seeds whenever it
 *    changes
 *  - Save is disabled until vehicle type/make/model/year/plate are all
 *    filled; submitting invalid toasts instead of opening the confirm
 *  - a valid submit confirms via Alert ("Update & Verify" is destructive),
 *    then calls updateDriverMe.mutateAsync with vehicle_year coerced to a
 *    number, refetches both legacy store fetchers, toasts, and navigates
 *    back; a missing auth token blocks the mutation with its own error
 *    toast (the fresh-login race guard); a mutation failure toasts
 *  - selecting a vehicle type from the picker sets the form and closes
 *    the modal
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, TextInput, Alert } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('expo-linear-gradient', () => ({ LinearGradient: ({ children }: any) => children }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const mockBack = jest.fn();
jest.mock('expo-router', () => ({ useRouter: () => ({ back: mockBack }) }));

const COLORS = {
  primary: '#EF4444', primaryDark: '#B91C1C', background: '#FFF', surface: '#FFF', surfaceLight: '#F5F5F5',
  text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockShowToast = jest.fn();
jest.mock('../../hooks/useToast', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const mockFetchDriverProfile = jest.fn();
const mockRefreshProfile = jest.fn();
let mockAuthState: any;
jest.mock('@shared/store/authStore', () => ({
  useAuthStore: () => mockAuthState,
}));

const mockMutateAsync = jest.fn();
jest.mock('@shared/hooks/queries', () => ({
  useUpdateDriverMe: () => ({ mutateAsync: (...a: any[]) => mockMutateAsync(...a) }),
}));

import VehicleInfoScreen from '../../app/vehicle-info';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const VEHICLE_TYPE_SEDAN = { id: 'vt-1', name: 'Sedan', description: 'Standard 4-door', capacity: 4, icon: 'car' };
const VEHICLE_TYPE_XL = { id: 'vt-2', name: 'XL', description: 'Larger group rides', capacity: 6, icon: 'car' };

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<VehicleInfoScreen />);
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
    .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes(text)))!;
}

function findFieldByPlaceholder(r: TestRenderer.ReactTestRenderer, placeholder: string) {
  return r.root.findByProps({ placeholder });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockAuthState = {
    driver: { service_area_id: 'area-1', vehicle_type_id: '', vehicle_make: '', vehicle_model: '', vehicle_year: '', vehicle_color: '', vehicle_vin: '', license_plate: '' },
    token: 'a-token',
    fetchDriverProfile: mockFetchDriverProfile,
    refreshProfile: mockRefreshProfile,
  };
  mockApiGet.mockResolvedValue({ data: [VEHICLE_TYPE_SEDAN, VEHICLE_TYPE_XL] });
  mockMutateAsync.mockResolvedValue(undefined);
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.restoreAllMocks();
});

describe('VehicleInfoScreen', () => {
  it("fetches vehicle types scoped to the driver's service area", async () => {
    await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/vehicle-types?service_area_id=area-1');
  });

  it('does not fetch and shows an empty list when no service area is selected', async () => {
    mockAuthState.driver.service_area_id = null;
    const r = await renderScreen();
    expect(mockApiGet).not.toHaveBeenCalled();
    const typeBox = findButtonByText(r, 'Tap to select');
    act(() => {
      typeBox.props.onPress();
    });
    expect(allText(r)).toContain('No vehicle types are set up for your service area yet. Please contact support.');
  });

  it('toasts and shows the picker error/retry state on a fetch failure', async () => {
    mockApiGet.mockRejectedValue(new Error('down'));
    const r = await renderScreen();
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Error', 'Could not load vehicle types. Check your connection.');
    const typeBox = findButtonByText(r, 'Tap to select');
    act(() => {
      typeBox.props.onPress();
    });
    expect(allText(r)).toContain("Couldn't load vehicle types. Check your connection and try again.");

    mockApiGet.mockResolvedValue({ data: [VEHICLE_TYPE_SEDAN] });
    const retryBtn = findButtonByText(r, 'Try Again');
    await act(async () => {
      await retryBtn.props.onPress();
      await flush();
    });
    expect(allText(r)).toContain('Sedan');
  });

  it('pre-seeds the form from the driver prop', async () => {
    mockAuthState.driver = {
      ...mockAuthState.driver,
      vehicle_type_id: 'vt-1', vehicle_make: 'Toyota', vehicle_model: 'Camry', vehicle_year: 2021,
      vehicle_color: 'Silver', license_plate: 'ABC 123',
    };
    const r = await renderScreen();
    expect(allText(r)).toContain('Sedan');
    expect(allText(r)).toContain('2021 Toyota Camry');
    expect(allText(r)).toContain('ABC 123');
  });

  it('disables Save until all required fields are filled', async () => {
    const r = await renderScreen();
    const saveBtn = findButtonByText(r, 'Save Vehicle Info');
    expect(saveBtn.props.disabled).toBe(true);
  });

  it('toasts instead of confirming when submitted with missing fields', async () => {
    const r = await renderScreen();
    const saveBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Save Vehicle Info'))
    )!;
    await act(async () => {
      await saveBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('warning', 'Missing Information', 'Please fill in all required fields marked with *');
    expect(Alert.alert).not.toHaveBeenCalled();
  });

  async function fillValidForm(r: TestRenderer.ReactTestRenderer) {
    const typeBox = findButtonByText(r, 'Tap to select');
    act(() => {
      typeBox.props.onPress();
    });
    const sedanOption = findButtonByText(r, 'Sedan');
    act(() => {
      sedanOption.props.onPress();
    });
    act(() => {
      findFieldByPlaceholder(r, 'e.g. Toyota').props.onChangeText('Toyota');
    });
    act(() => {
      findFieldByPlaceholder(r, 'e.g. Camry').props.onChangeText('Camry');
    });
    act(() => {
      findFieldByPlaceholder(r, '2020').props.onChangeText('2021');
    });
    act(() => {
      findFieldByPlaceholder(r, 'ABC 123').props.onChangeText('XYZ 999');
    });
  }

  it('selecting a vehicle type from the picker sets the form and closes the modal', async () => {
    const r = await renderScreen();
    const typeBox = findButtonByText(r, 'Tap to select');
    act(() => {
      typeBox.props.onPress();
    });
    expect(allText(r)).toContain('Select Vehicle Type');
    const sedanOption = findButtonByText(r, 'Sedan');
    act(() => {
      sedanOption.props.onPress();
    });
    expect(allText(r)).not.toContain('Select Vehicle Type');
    expect(allText(r)).toContain('Sedan');
  });

  it('confirms via Alert, then saves and navigates back on success', async () => {
    const r = await renderScreen();
    await fillValidForm(r);
    const saveBtn = findButtonByText(r, 'Save Vehicle Info');
    act(() => {
      saveBtn.props.onPress();
    });
    expect(Alert.alert).toHaveBeenCalledWith(
      'Update Vehicle Info',
      expect.stringContaining("Changing your vehicle information will require admin re-verification"),
      expect.any(Array),
    );
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const confirmAction = alertCall[2].find((b: any) => b.text === 'Update & Verify');
    await act(async () => {
      await confirmAction.onPress();
      await flush();
    });
    expect(mockMutateAsync).toHaveBeenCalledWith(expect.objectContaining({
      vehicle_type_id: 'vt-1', vehicle_make: 'Toyota', vehicle_model: 'Camry', vehicle_year: 2021, license_plate: 'XYZ 999',
    }));
    expect(mockFetchDriverProfile).toHaveBeenCalled();
    expect(mockRefreshProfile).toHaveBeenCalled();
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Vehicle Saved', 'Vehicle information updated. Please wait for admin approval.');
    expect(mockBack).toHaveBeenCalled();
  });

  it('blocks the mutation with an error toast when the auth token is missing (fresh-login race guard)', async () => {
    mockAuthState.token = null;
    const r = await renderScreen();
    await fillValidForm(r);
    const saveBtn = findButtonByText(r, 'Save Vehicle Info');
    act(() => {
      saveBtn.props.onPress();
    });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const confirmAction = alertCall[2].find((b: any) => b.text === 'Update & Verify');
    await act(async () => {
      await confirmAction.onPress();
      await flush();
    });
    expect(mockMutateAsync).not.toHaveBeenCalled();
    // getApiErrorMessage is stubbed to always return its fallback (matching
    // this suite's convention elsewhere) -- in the real app this surfaces
    // the thrown error's own "Authentication token not available..."
    // message instead, since the source passes it through getApiErrorMessage.
    expect(mockShowToast).toHaveBeenCalledWith(
      'error', 'Update Failed', 'Could not update your vehicle info. Please try again.',
    );
  });

  it('toasts on a mutation failure', async () => {
    mockMutateAsync.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    await fillValidForm(r);
    const saveBtn = findButtonByText(r, 'Save Vehicle Info');
    act(() => {
      saveBtn.props.onPress();
    });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const confirmAction = alertCall[2].find((b: any) => b.text === 'Update & Verify');
    await act(async () => {
      await confirmAction.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Update Failed', 'Could not update your vehicle info. Please try again.');
    expect(mockBack).not.toHaveBeenCalled();
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
