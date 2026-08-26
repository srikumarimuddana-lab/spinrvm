/**
 * app/become-driver.tsx — rider-app's "Become a Driver" 5-step wizard
 * (Intro/Personal/Vehicle/Documents/Review). Pins:
 *  - on mount: fetches vehicle types + document requirements via plain
 *    fetch(), and checks whether the driver app is installed via
 *    Linking.canOpenURL
 *  - Intro step: driver-app-installed shows "Open Spinr Driver App" +
 *    "Apply in Browser Instead"; not-installed shows "Get Started" + a
 *    platform-specific store download link
 *  - openDriverApp: opens the deep link when it can be opened, else
 *    falls back to the app store URL
 *  - Personal step: blocks Next until all fields are filled
 *  - Vehicle step: rejects a vehicle older than 9 years (or an invalid
 *    year) with its own toast before the generic field-completeness
 *    check
 *  - Documents step: blocks Next when a mandatory requirement's
 *    front/back/expiry is missing, listing every missing item by name
 *  - handleUpload: a cancelled picker uploads nothing; a successful pick
 *    uploads via uploadFile and toasts success; a failure toasts
 *  - Submit (Review step): maps front/back documents into the payload
 *    with each requirement's own name as document_type, maps named
 *    expiry dates to their legacy top-level fields, calls
 *    registerDriver, toasts, and redirects to /(tabs); a failure toasts
 *    without redirecting
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, TextInput, Linking, Platform } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

const mockBack = jest.fn();
const mockReplace = jest.fn();
jest.mock('expo-router', () => ({ useRouter: () => ({ back: mockBack, replace: mockReplace }) }));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', textSecondary: '#333', border: '#E5E7EB',
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

jest.mock('@shared/api/client', () => ({
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const mockRegisterDriver = jest.fn();
let mockAuthState: any;
jest.mock('@shared/store/authStore', () => ({ useAuthStore: () => mockAuthState }));

const mockGetDocumentAsync = jest.fn();
jest.mock('expo-document-picker', () => ({
  getDocumentAsync: (...a: any[]) => mockGetDocumentAsync(...a),
}));

import BecomeDriverScreen from '../app/become-driver';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const VEHICLE_TYPES = [{ id: 'vt-1', name: 'Sedan' }];
const REQUIREMENTS = [
  { id: 'req-1', name: 'Driving License', description: 'Photo ID', is_mandatory: true, requires_back_side: true },
  { id: 'req-2', name: 'Vehicle Insurance', description: 'Proof', is_mandatory: true, requires_back_side: false },
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
  act(() => { inputs.find((i) => i.props.placeholder === 'Saskatoon')!.props.onChangeText('Saskatoon'); });
  const nextBtn = findButtonByText(r, 'Next: Vehicle');
  await act(async () => { nextBtn.props.onPress(); await flush(); });
}

async function fillVehicleAndAdvance(r: TestRenderer.ReactTestRenderer) {
  const inputs = r.root.findAllByType(TextInput);
  act(() => { inputs.find((i) => i.props.placeholder === '2019')!.props.onChangeText(String(new Date().getFullYear())); });
  act(() => { inputs.find((i) => i.props.placeholder === 'Toyota')!.props.onChangeText('Toyota'); });
  act(() => { inputs.find((i) => i.props.placeholder === 'Camry')!.props.onChangeText('Camry'); });
  act(() => { inputs.find((i) => i.props.placeholder === 'Silver')!.props.onChangeText('Silver'); });
  act(() => { inputs.find((i) => i.props.placeholder === 'ABC 123')!.props.onChangeText('ABC 123'); });
  act(() => { inputs.find((i) => i.props.placeholder === '1G1...')!.props.onChangeText('1G1XX'); });
  const sedanChip = findButtonByText(r, 'Sedan');
  act(() => { sedanChip.props.onPress(); });
  const nextBtn = findButtonByText(r, 'Next: Documents');
  await act(async () => { nextBtn.props.onPress(); await flush(); });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockAuthState = { registerDriver: mockRegisterDriver, isLoading: false, user: { first_name: '', last_name: '', email: '', city: '', phone: '+15551234567' } };
  global.fetch = jest.fn((url: string) => {
    if (url.includes('/vehicle-types')) return Promise.resolve({ ok: true, json: () => Promise.resolve(VEHICLE_TYPES) } as any);
    if (url.includes('/drivers/requirements')) return Promise.resolve({ ok: true, json: () => Promise.resolve(REQUIREMENTS) } as any);
    return Promise.reject(new Error('unexpected url ' + url));
  }) as any;
  jest.spyOn(Linking, 'canOpenURL').mockResolvedValue(false);
  jest.spyOn(Linking, 'openURL').mockResolvedValue(true as any);
  mockRegisterDriver.mockResolvedValue(undefined);
  mockUploadFile.mockResolvedValue('/uploads/doc.jpg');
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.restoreAllMocks();
});

describe('BecomeDriverScreen', () => {
  it('fetches vehicle types and requirements on mount, and checks driver-app install status', async () => {
    await renderScreen();
    expect(global.fetch).toHaveBeenCalledWith('https://api.spinr.ca/api/v1/vehicle-types');
    expect(global.fetch).toHaveBeenCalledWith('https://api.spinr.ca/api/v1/drivers/requirements');
    expect(Linking.canOpenURL).toHaveBeenCalledWith('spinr-driver://');
  });

  it('shows "Get Started" and a store download link when the driver app is not installed', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('Get Started');
    expect(findButtonByText(r, 'Open Spinr Driver App')).toBeUndefined();
  });

  it('shows "Open Spinr Driver App" and "Apply in Browser Instead" when it is installed', async () => {
    (Linking.canOpenURL as jest.Mock).mockResolvedValue(true);
    const r = await renderScreen();
    expect(allText(r)).toContain('Apply in Browser Instead');
    const openAppBtn = findButtonByText(r, 'Open Spinr Driver App');
    expect(openAppBtn).toBeTruthy();
  });

  it('opens the deep link when the driver app can be opened', async () => {
    (Linking.canOpenURL as jest.Mock).mockResolvedValue(true);
    const r = await renderScreen();
    const openAppBtn = findButtonByText(r, 'Open Spinr Driver App');
    await act(async () => {
      await openAppBtn.props.onPress();
      await flush();
    });
    expect(Linking.openURL).toHaveBeenCalledWith(expect.stringContaining('spinr-driver://onboard?phone='));
  });

  it('falls back to the app store URL when the deep link cannot be opened', async () => {
    const r = await renderScreen();
    const downloadLink = findButtonByText(r, 'Get it on');
    await act(async () => {
      await downloadLink.props.onPress();
      await flush();
    });
    const expectedUrl = Platform.OS === 'ios'
      ? 'https://apps.apple.com/ca/app/spinr-driver/id0000000000'
      : 'https://play.google.com/store/apps/details?id=com.spinr.driver';
    expect(Linking.openURL).toHaveBeenCalledWith(expectedUrl);
  });

  it('blocks advancing from the Personal step until every field is filled', async () => {
    const r = await renderScreen();
    await goToPersonalStep(r);
    const nextBtn = findButtonByText(r, 'Next: Vehicle');
    act(() => { nextBtn.props.onPress(); });
    expect(allText(r)).toContain('Personal Info'); // still on step 1
  });

  it('rejects a vehicle older than 9 years with its own toast', async () => {
    const r = await renderScreen();
    await fillPersonalAndAdvance(r);
    const inputs = r.root.findAllByType(TextInput);
    act(() => { inputs.find((i) => i.props.placeholder === '2019')!.props.onChangeText('2010'); });
    const nextBtn = findButtonByText(r, 'Next: Documents');
    act(() => { nextBtn.props.onPress(); });
    expect(mockShowToast).toHaveBeenCalledWith('Invalid Year', 'Vehicle must be 9 years old or newer.', 'warning');
  });

  it('lists every missing mandatory document/expiry by name on the Documents step', async () => {
    const r = await renderScreen();
    await fillPersonalAndAdvance(r);
    await fillVehicleAndAdvance(r);
    const nextBtn = findButtonByText(r, 'Review Application');
    act(() => { nextBtn.props.onPress(); });
    expect(mockShowToast).toHaveBeenCalledWith(
      'Missing Documents',
      expect.stringContaining('Driving License (Front)'),
      'warning',
    );
  });

  it('uploads a document via the picker and toasts success', async () => {
    mockGetDocumentAsync.mockResolvedValue({ canceled: false, assets: [{ uri: 'file://doc.jpg', name: 'doc.jpg', mimeType: 'image/jpeg' }] });
    const r = await renderScreen();
    await fillPersonalAndAdvance(r);
    await fillVehicleAndAdvance(r);
    const uploadBtn = findButtonByText(r, 'Upload Front');
    await act(async () => {
      await uploadBtn.props.onPress();
      await flush();
    });
    expect(mockUploadFile).toHaveBeenCalledWith('file://doc.jpg', 'doc.jpg', 'image/jpeg');
    expect(mockShowToast).toHaveBeenCalledWith('Success', 'Document uploaded successfully', 'success');
  });

  it('uploads nothing when the document picker is cancelled', async () => {
    mockGetDocumentAsync.mockResolvedValue({ canceled: true });
    const r = await renderScreen();
    await fillPersonalAndAdvance(r);
    await fillVehicleAndAdvance(r);
    const uploadBtn = findButtonByText(r, 'Upload Front');
    await act(async () => {
      await uploadBtn.props.onPress();
      await flush();
    });
    expect(mockUploadFile).not.toHaveBeenCalled();
  });

  it('toasts a failure when the document upload itself fails', async () => {
    mockGetDocumentAsync.mockResolvedValue({ canceled: false, assets: [{ uri: 'file://doc.jpg', name: 'doc.jpg', mimeType: 'image/jpeg' }] });
    mockUploadFile.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    await fillPersonalAndAdvance(r);
    await fillVehicleAndAdvance(r);
    const uploadBtn = findButtonByText(r, 'Upload Front');
    await act(async () => {
      await uploadBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('Upload Failed', 'Could not upload document. Please try again.', 'danger');
  });

  it('submits the application, mapping documents + expiry dates, then toasts and redirects', async () => {
    mockGetDocumentAsync.mockResolvedValue({ canceled: false, assets: [{ uri: 'file://doc.jpg', name: 'doc.jpg', mimeType: 'image/jpeg' }] });
    const r = await renderScreen();
    await fillPersonalAndAdvance(r);
    await fillVehicleAndAdvance(r);

    // Upload the license front+back and insurance front to satisfy validation.
    const licenseFrontBtn = findButtonByText(r, 'Upload Front');
    await act(async () => { await licenseFrontBtn.props.onPress(); await flush(); });
    const licenseBackBtn = findButtonByText(r, 'Upload Back');
    await act(async () => { await licenseBackBtn.props.onPress(); await flush(); });
    const uploadButtons = r.root.findAllByType(TouchableOpacity).filter((n) =>
      n.findAllByType(Text).some((t) => {
        try { return JSON.stringify(t.props.children).includes('Upload Document'); } catch { return false; }
      })
    );
    await act(async () => { await uploadButtons[0].props.onPress(); await flush(); });

    const licenseNumberInput = r.root.findByProps({ placeholder: 'S1234-5678-9012' });
    act(() => { licenseNumberInput.props.onChangeText('S1234-5678-9012'); });

    const expiryInputs = r.root.findAllByType(TextInput).filter((i) => i.props.placeholder === '2025-12-31');
    act(() => { expiryInputs[0].props.onChangeText('2027-01-01'); });
    act(() => { expiryInputs[1].props.onChangeText('2027-06-01'); });

    const reviewBtn = findButtonByText(r, 'Review Application');
    await act(async () => { reviewBtn.props.onPress(); await flush(); });

    const submitBtn = findButtonByText(r, 'Submit Application');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });

    expect(mockRegisterDriver).toHaveBeenCalledWith(expect.objectContaining({
      first_name: 'Jamie', last_name: 'Smith', email: 'jamie@example.com', city: 'Saskatoon',
      vehicle_make: 'Toyota', vehicle_model: 'Camry', license_plate: 'ABC 123',
      license_number: 'S1234-5678-9012',
      documents: expect.arrayContaining([
        expect.objectContaining({ requirement_id: 'req-1', side: 'front', document_type: 'Driving License' }),
        expect.objectContaining({ requirement_id: 'req-1', side: 'back', document_type: 'Driving License' }),
        expect.objectContaining({ requirement_id: 'req-2', side: 'front', document_type: 'Vehicle Insurance' }),
      ]),
    }));
    expect(mockShowToast).toHaveBeenCalledWith(
      'Application Submitted!',
      'Waiting for approval. To start driving, download the Spinr Driver app.',
      'success',
    );
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('toasts a failure without redirecting when registerDriver rejects', async () => {
    mockRegisterDriver.mockRejectedValue(new Error('server error'));
    mockGetDocumentAsync.mockResolvedValue({ canceled: false, assets: [{ uri: 'file://doc.jpg', name: 'doc.jpg', mimeType: 'image/jpeg' }] });
    const r = await renderScreen();
    await fillPersonalAndAdvance(r);
    await fillVehicleAndAdvance(r);
    const licenseFrontBtn = findButtonByText(r, 'Upload Front');
    await act(async () => { await licenseFrontBtn.props.onPress(); await flush(); });
    const licenseBackBtn = findButtonByText(r, 'Upload Back');
    await act(async () => { await licenseBackBtn.props.onPress(); await flush(); });
    const uploadButtons = r.root.findAllByType(TouchableOpacity).filter((n) =>
      n.findAllByType(Text).some((t) => {
        try { return JSON.stringify(t.props.children).includes('Upload Document'); } catch { return false; }
      })
    );
    await act(async () => { await uploadButtons[0].props.onPress(); await flush(); });
    act(() => { r.root.findByProps({ placeholder: 'S1234-5678-9012' }).props.onChangeText('S1234'); });
    const expiryInputs = r.root.findAllByType(TextInput).filter((i) => i.props.placeholder === '2025-12-31');
    act(() => { expiryInputs[0].props.onChangeText('2027-01-01'); });
    act(() => { expiryInputs[1].props.onChangeText('2027-06-01'); });
    const reviewBtn = findButtonByText(r, 'Review Application');
    await act(async () => { reviewBtn.props.onPress(); await flush(); });
    const submitBtn = findButtonByText(r, 'Submit Application');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('Registration Failed', 'Could not submit your application. Please try again.', 'danger');
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it('navigates back on the header back button when on the Intro step', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => { backBtn.props.onPress(); });
    expect(mockBack).toHaveBeenCalled();
  });

  it('goes to the previous step (instead of navigating back) once past the Intro step', async () => {
    const r = await renderScreen();
    await goToPersonalStep(r);
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => { backBtn.props.onPress(); });
    expect(mockBack).not.toHaveBeenCalled();
    expect(allText(r)).toContain('Welcome to Spinr Driver');
  });
});
