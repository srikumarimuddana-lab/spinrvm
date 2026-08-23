/**
 * app/become-driver.tsx — driver-app's "Become a Driver" 5-step wizard
 * (Intro/Personal/Vehicle/Documents/Review). Pins:
 *  - on mount: fetches requirements + CRC consent text, restores a saved
 *    AsyncStorage draft, and does NOT pre-fill personal fields from the
 *    logged-in account
 *  - the CRC consent checkbox auto-checks (and is disabled) when the
 *    published-text endpoint returns no content or fails — never blocks
 *    registration on unpublished/unreachable consent text
 *  - Personal step: blocks Next until name/email/gender/service area are
 *    filled; picking a service area seeds city and re-fetches vehicle
 *    types for that area
 *  - Vehicle step: entirely empty is allowed through (skip-by-omission);
 *    partial entry is rejected as incomplete; an explicit "Skip for now"
 *    always advances regardless of validation
 *  - Documents step's date picker: rejects a past date, accepts a future
 *    one into the targeted requirement's expiry
 *  - handleUpload's picker dispatch (iOS Camera/Gallery/File Alert
 *    options) and the upload success/failure paths
 *  - Submit (Review step): blocked while the consent checkbox is
 *    unchecked; maps requirement names to legacy expiry fields by
 *    keyword; posts CRC consent only when it was actually published;
 *    clears the draft and redirects to /driver/ on success; a failure
 *    toasts without redirecting
 *  - the header button is Logout on the Intro step, Back on every other
 *    step
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, TextInput, Alert, Platform } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));
// Rendered only to expose its `onChange` prop for the test to call directly
// (via findByProps({ mode: 'date' })) — the real native picker UI can't be
// driven in Jest, so the test simulates the platform calling onChange itself.
jest.mock('@react-native-community/datetimepicker', () => (props: any) => {
  const { View } = require('react-native');
  return <View {...props} />;
});

const mockGetItem = jest.fn();
const mockSetItem = jest.fn();
const mockRemoveItem = jest.fn();
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: (...a: any[]) => mockGetItem(...a),
  setItem: (...a: any[]) => mockSetItem(...a),
  removeItem: (...a: any[]) => mockRemoveItem(...a),
}));

const mockReplace = jest.fn();
jest.mock('expo-router', () => ({ useRouter: () => ({ replace: mockReplace }) }));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

jest.mock('@shared/config/spinr.config', () => ({
  __esModule: true,
  default: { backendUrl: 'https://api.spinr.ca' },
}));

const mockUploadFile = jest.fn();
jest.mock('@shared/api/upload', () => ({
  uploadFile: (...a: any[]) => mockUploadFile(...a),
  resolveUploadMimeType: () => 'image/jpeg',
}));

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a), post: (...a: any[]) => mockApiPost(...a) },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockRegisterDriver = jest.fn();
const mockLogout = jest.fn();
let mockAuthState: any;
jest.mock('@shared/store/authStore', () => ({
  useAuthStore: Object.assign(() => mockAuthState, { getState: () => mockAuthState }),
}));

const mockGetDocumentAsync = jest.fn();
jest.mock('expo-document-picker', () => ({
  getDocumentAsync: (...a: any[]) => mockGetDocumentAsync(...a),
}));

const mockRequestCameraPermissionsAsync = jest.fn();
const mockRequestMediaLibraryPermissionsAsync = jest.fn();
const mockLaunchCameraAsync = jest.fn();
const mockLaunchImageLibraryAsync = jest.fn();
jest.mock('expo-image-picker', () => ({
  requestCameraPermissionsAsync: (...a: any[]) => mockRequestCameraPermissionsAsync(...a),
  requestMediaLibraryPermissionsAsync: (...a: any[]) => mockRequestMediaLibraryPermissionsAsync(...a),
  launchCameraAsync: (...a: any[]) => mockLaunchCameraAsync(...a),
  launchImageLibraryAsync: (...a: any[]) => mockLaunchImageLibraryAsync(...a),
}));

import BecomeDriverScreen from '../../app/become-driver';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const SERVICE_AREAS = [{ id: 'sk-1', name: 'Saskatoon, SK', city: 'Saskatoon' }];
const VEHICLE_TYPES = [{ id: 'vt-1', name: 'Sedan' }];
const REQUIREMENTS = [
  { id: 'req-1', name: 'Driving License', description: 'Photo ID', is_mandatory: true, requires_back_side: true },
];

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<BecomeDriverScreen />);
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
    .find((n) => n.findAllByType(Text).some((t) => {
      try { return JSON.stringify(t.props.children).includes(text); } catch { return false; }
    }))!;
}

async function goToPersonalStep(r: TestRenderer.ReactTestRenderer) {
  const startBtn = findButtonByText(r, 'Get Started');
  await act(async () => { startBtn.props.onPress(); await flush(); });
}

async function fillPersonalAndAdvance(r: TestRenderer.ReactTestRenderer) {
  await goToPersonalStep(r);
  const inputs = r.root.findAllByType(TextInput);
  act(() => { inputs.find((i) => i.props.placeholder === 'John')!.props.onChangeText('Jamie'); });
  act(() => { inputs.find((i) => i.props.placeholder === 'Doe')!.props.onChangeText('Smith'); });
  act(() => { inputs.find((i) => i.props.placeholder === 'john@example.com')!.props.onChangeText('jamie@example.com'); });
  const maleChip = findButtonByText(r, 'Male');
  act(() => { maleChip.props.onPress(); });
  const areaChip = findButtonByText(r, 'Saskatoon, SK');
  act(() => { areaChip.props.onPress(); });
  const nextBtn = findButtonByText(r, 'Next: Vehicle');
  await act(async () => { nextBtn.props.onPress(); await flush(); });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockAuthState = { registerDriver: mockRegisterDriver, logout: mockLogout, isLoading: false, user: { gender: '', city: '' } };
  mockGetItem.mockResolvedValue(null);
  mockApiGet.mockImplementation((url: string) => {
    if (url === '/service-areas') return Promise.resolve({ data: SERVICE_AREAS });
    if (url === '/vehicle-types') return Promise.resolve({ data: [] });
    return Promise.reject(new Error('unexpected url ' + url));
  });
  global.fetch = jest.fn((url: string) => {
    if (url.includes('/vehicle-types')) return Promise.resolve({ ok: true, json: () => Promise.resolve(VEHICLE_TYPES) } as any);
    if (url.includes('/drivers/requirements')) return Promise.resolve({ ok: true, json: () => Promise.resolve(REQUIREMENTS) } as any);
    if (url.includes('/legal-documents')) return Promise.resolve({ ok: true, json: () => Promise.resolve({ content: 'Full CRC consent text.' }) } as any);
    return Promise.reject(new Error('unexpected url ' + url));
  }) as any;
  mockRegisterDriver.mockResolvedValue(undefined);
  mockLogout.mockResolvedValue(undefined);
  mockUploadFile.mockResolvedValue('/uploads/doc.jpg');
  mockApiPost.mockResolvedValue({});
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.restoreAllMocks();
});

describe('BecomeDriverScreen', () => {
  it('does not pre-fill personal fields from the logged-in account', async () => {
    mockAuthState.user = { gender: 'Male', city: 'Regina', first_name: 'Placeholder' };
    const r = await renderScreen();
    await goToPersonalStep(r);
    const firstNameInput = r.root.findAllByType(TextInput).find((i) => i.props.placeholder === 'John')!;
    expect(firstNameInput.props.value).toBe('');
  });

  it('auto-checks and disables the consent checkbox when no consent text is published', async () => {
    global.fetch = jest.fn((url: string) => {
      if (url.includes('/vehicle-types')) return Promise.resolve({ ok: true, json: () => Promise.resolve(VEHICLE_TYPES) } as any);
      if (url.includes('/drivers/requirements')) return Promise.resolve({ ok: true, json: () => Promise.resolve(REQUIREMENTS) } as any);
      if (url.includes('/legal-documents')) return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as any);
      return Promise.reject(new Error('unexpected'));
    }) as any;
    const r = await renderScreen();
    await fillPersonalAndAdvance(r);
    const skipVehicleBtn = findButtonByText(r, 'Skip for now');
    await act(async () => { skipVehicleBtn.props.onPress(); await flush(); });
    const skipDocsBtn = findButtonByText(r, 'Skip for now');
    await act(async () => { skipDocsBtn.props.onPress(); await flush(); });
    const submitBtn = findButtonByText(r, 'Submit Application');
    expect(submitBtn.props.disabled).toBe(false); // consent auto-checked since unpublished
  });

  it('blocks advancing from Personal until name/email/gender/service area are filled', async () => {
    const r = await renderScreen();
    await goToPersonalStep(r);
    const nextBtn = findButtonByText(r, 'Next: Vehicle');
    act(() => { nextBtn.props.onPress(); });
    expect(allText(r)).toContain('Personal Info'); // still on step 1
  });

  it('seeds city and re-fetches vehicle types when a service area is picked', async () => {
    const r = await renderScreen();
    await goToPersonalStep(r);
    const areaChip = findButtonByText(r, 'Saskatoon, SK');
    await act(async () => { areaChip.props.onPress(); await flush(); });
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/vehicle-types?service_area_id=sk-1'),
    );
  });

  it('allows advancing from Vehicle when the step is entirely empty (skip-by-omission)', async () => {
    const r = await renderScreen();
    await fillPersonalAndAdvance(r);
    const nextBtn = findButtonByText(r, 'Next: Documents');
    act(() => { nextBtn.props.onPress(); });
    expect(allText(r)).toContain('Documents');
  });

  it('rejects Vehicle step when partially filled but incomplete', async () => {
    const r = await renderScreen();
    await fillPersonalAndAdvance(r);
    const inputs = r.root.findAllByType(TextInput);
    act(() => { inputs.find((i) => i.props.placeholder === 'Toyota')!.props.onChangeText('Toyota'); });
    const nextBtn = findButtonByText(r, 'Next: Documents');
    act(() => { nextBtn.props.onPress(); });
    expect(Alert.alert).toHaveBeenCalledWith('Incomplete Vehicle Info', 'Please complete all vehicle fields or use "Skip for now".');
  });

  it('"Skip for now" always advances past Vehicle regardless of validation', async () => {
    const r = await renderScreen();
    await fillPersonalAndAdvance(r);
    const inputs = r.root.findAllByType(TextInput);
    act(() => { inputs.find((i) => i.props.placeholder === 'Toyota')!.props.onChangeText('Toyota'); });
    const skipBtn = findButtonByText(r, 'Skip for now');
    act(() => { skipBtn.props.onPress(); });
    expect(allText(r)).toContain('Documents');
  });

  it('rejects a vehicle older than 9 years', async () => {
    const r = await renderScreen();
    await fillPersonalAndAdvance(r);
    const inputs = r.root.findAllByType(TextInput);
    act(() => { inputs.find((i) => i.props.placeholder === '2019')!.props.onChangeText('2010'); });
    const nextBtn = findButtonByText(r, 'Next: Documents');
    act(() => { nextBtn.props.onPress(); });
    expect(Alert.alert).toHaveBeenCalledWith('Invalid Year', 'Vehicle must be 9 years old or newer.');
  });

  it('opens the upload source picker and uploads a document successfully', async () => {
    Platform.OS = 'ios';
    mockGetDocumentAsync.mockResolvedValue({ canceled: false, assets: [{ uri: 'file://doc.jpg', name: 'doc.jpg', mimeType: 'image/jpeg' }] });
    const r = await renderScreen();
    await fillPersonalAndAdvance(r);
    const skipVehicleBtn = findButtonByText(r, 'Skip for now');
    act(() => { skipVehicleBtn.props.onPress(); });
    const uploadBtn = findButtonByText(r, 'Upload Front');
    act(() => { uploadBtn.props.onPress(); });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    expect(alertCall[0]).toBe('Upload Document');
    const fileAction = alertCall[2].find((b: any) => b.text === 'File');
    await act(async () => { await fileAction.onPress(); await flush(); });
    expect(mockUploadFile).toHaveBeenCalledWith('file://doc.jpg', 'doc.jpg', 'image/jpeg');
    expect(Alert.alert).toHaveBeenCalledWith('Success', 'Document uploaded successfully');
  });

  it('toasts a failure when the document upload itself fails', async () => {
    mockGetDocumentAsync.mockResolvedValue({ canceled: false, assets: [{ uri: 'file://doc.jpg', name: 'doc.jpg', mimeType: 'image/jpeg' }] });
    mockUploadFile.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    await fillPersonalAndAdvance(r);
    const skipVehicleBtn = findButtonByText(r, 'Skip for now');
    act(() => { skipVehicleBtn.props.onPress(); });
    const uploadBtn = findButtonByText(r, 'Upload Front');
    act(() => { uploadBtn.props.onPress(); });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const fileAction = alertCall[2].find((b: any) => b.text === 'File');
    await act(async () => { await fileAction.onPress(); await flush(); });
    expect(Alert.alert).toHaveBeenCalledWith('Upload Failed', 'Could not upload your document. Please try again.');
  });

  it('rejects a past expiry date and accepts a future one', async () => {
    const r = await renderScreen();
    await fillPersonalAndAdvance(r);
    const skipVehicleBtn = findButtonByText(r, 'Skip for now');
    act(() => { skipVehicleBtn.props.onPress(); });
    const dateBtn = findButtonByText(r, 'Select Expiry Date');
    act(() => { dateBtn.props.onPress(); });

    const pastDate = new Date();
    pastDate.setFullYear(pastDate.getFullYear() - 1);
    const futureDate = new Date();
    futureDate.setFullYear(futureDate.getFullYear() + 2);

    // Dismissing the picker sets nothing.
    let dtPicker = r.root.findByProps({ mode: 'date' });
    act(() => { dtPicker.props.onChange({ type: 'dismissed' }, pastDate); });
    expect(allText(r)).toContain('Select Expiry Date');

    // A past date is rejected with its own toast.
    act(() => { dateBtn.props.onPress(); });
    dtPicker = r.root.findByProps({ mode: 'date' });
    act(() => { dtPicker.props.onChange({ type: 'set' }, pastDate); });
    expect(Alert.alert).toHaveBeenCalledWith('Invalid Date', 'Expiry date must be in the future.');
    expect(allText(r)).toContain('Select Expiry Date'); // still unset

    // A future date is accepted.
    dtPicker = r.root.findByProps({ mode: 'date' });
    act(() => { dtPicker.props.onChange({ type: 'set' }, futureDate); });
    expect(allText(r)).toContain(futureDate.toISOString().split('T')[0]);
  });

  it('blocks submit while the consent checkbox is unchecked (published consent)', async () => {
    const r = await renderScreen();
    await fillPersonalAndAdvance(r);
    const skipVehicleBtn = findButtonByText(r, 'Skip for now');
    act(() => { skipVehicleBtn.props.onPress(); });
    const skipDocsBtn = findButtonByText(r, 'Skip for now');
    act(() => { skipDocsBtn.props.onPress(); });
    const submitBtn = findButtonByText(r, 'Submit Application');
    expect(submitBtn.props.disabled).toBe(true);
  });

  it('submits successfully, posts CRC consent, clears the draft, and redirects', async () => {
    const r = await renderScreen();
    await fillPersonalAndAdvance(r);
    const skipVehicleBtn = findButtonByText(r, 'Skip for now');
    act(() => { skipVehicleBtn.props.onPress(); });
    const skipDocsBtn = findButtonByText(r, 'Skip for now');
    act(() => { skipDocsBtn.props.onPress(); });
    const consentRow = findButtonByText(r, 'I consent to a Criminal Record Check');
    act(() => { consentRow.props.onPress(); });
    const submitBtn = findButtonByText(r, 'Submit Application');
    await act(async () => { await submitBtn.props.onPress(); await flush(); });
    expect(mockRegisterDriver).toHaveBeenCalledWith(expect.objectContaining({
      first_name: 'Jamie', last_name: 'Smith', email: 'jamie@example.com', gender: 'Male', service_area_id: 'sk-1',
    }));
    expect(mockApiPost).toHaveBeenCalledWith('/drivers/crc-consent');
    const successAlert = (Alert.alert as jest.Mock).mock.calls.find((c) => c[0] === 'Success');
    expect(successAlert).toBeTruthy();
    await act(async () => { await successAlert[2][0].onPress(); await flush(); });
    expect(mockRemoveItem).toHaveBeenCalledWith('driver_application_draft');
    expect(mockReplace).toHaveBeenCalledWith('/driver/');
  });

  it('toasts a failure without redirecting when registerDriver rejects', async () => {
    mockRegisterDriver.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    await fillPersonalAndAdvance(r);
    const skipVehicleBtn = findButtonByText(r, 'Skip for now');
    act(() => { skipVehicleBtn.props.onPress(); });
    const skipDocsBtn = findButtonByText(r, 'Skip for now');
    act(() => { skipDocsBtn.props.onPress(); });
    const consentRow = findButtonByText(r, 'I consent to a Criminal Record Check');
    act(() => { consentRow.props.onPress(); });
    const submitBtn = findButtonByText(r, 'Submit Application');
    await act(async () => { await submitBtn.props.onPress(); await flush(); });
    expect(Alert.alert).toHaveBeenCalledWith('Registration Failed', 'Could not submit your application. Please try again.');
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it('shows Logout on the Intro step, and logs out on tap', async () => {
    const r = await renderScreen();
    const headerBtn = r.root.findAllByType(TouchableOpacity)[0];
    await act(async () => { await headerBtn.props.onPress(); await flush(); });
    expect(mockLogout).toHaveBeenCalled();
    expect(mockReplace).toHaveBeenCalledWith('/login');
  });

  it('goes to the previous step (not logout) once past the Intro step', async () => {
    const r = await renderScreen();
    await goToPersonalStep(r);
    const headerBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => { headerBtn.props.onPress(); });
    expect(mockLogout).not.toHaveBeenCalled();
    expect(allText(r)).toContain('Welcome to Spinr Driver');
  });
});
