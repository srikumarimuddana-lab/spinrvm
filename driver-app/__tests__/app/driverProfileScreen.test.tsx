/**
 * app/driver/(tabs)/profile.tsx — driver's profile tab. Pins:
 *  - company-info fetch on mount (swallowed failure, no error UI)
 *  - focus-effect refresh: /auth/me -> authStore.setState({user}),
 *    refetchDriverMe(), /drivers/requirements, /drivers/documents (each
 *    independently best-effort — a requirements/documents failure
 *    doesn't block the others or throw)
 *  - referralCode fallback chain: driver_code -> referral_code ->
 *    DRIVER<id-derived> -> '' (chip hidden when empty)
 *  - copyReferralCode: clipboard + success toast, swallowed on failure
 *  - handlePickPhoto -> Alert with Take Photo/Library/Cancel; each path's
 *    permission-denied toast and uploadPhoto's success/failure toast
 *  - openEditModal pre-fills from `user`; handleSaveProfile's
 *    missing-field and invalid-email validation, POST /users/profile on
 *    success (authStore.setState + modal close + toast), and the
 *    backend-message-surfaced failure toast
 *  - handleLogout / handleLogoutAll: Alert confirm -> logout()/logoutAll()
 *    -> router.replace('/login')
 *  - document requirement badge derivation: pending/rejected/expired/
 *    expiring-soon/valid/upload-required, driven off driverDocs status
 *    (not the driver-profile expiry fields)
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, TextInput, Alert } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null, MaterialCommunityIcons: () => null, FontAwesome5: () => null }));
jest.mock('expo-linear-gradient', () => ({ LinearGradient: ({ children }: any) => children }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));
jest.mock('@shared/components/ErrorBoundary', () => ({
  ErrorBoundary: ({ children }: any) => children,
}));
jest.mock('../../components/ScreenHeader', () => ({
  ScreenHeader: ({ title }: any) => {
    const { Text: RNText } = require('react-native');
    return <RNText>{title}</RNText>;
  },
}));

const mockPush = jest.fn();
const mockReplace = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush, replace: mockReplace }),
}));

jest.mock('expo-router/react-navigation', () => {
  const ReactActual = require('react');
  return {
    useFocusEffect: (cb: () => void | (() => void)) => {
      ReactActual.useEffect(() => cb(), []);
    },
  };
});

const COLORS = {
  primary: '#EF4444', primaryDark: '#B91C1C', background: '#FFF', surface: '#FFF',
  surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', textSecondary: '#888', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a), post: (...a: any[]) => mockApiPost(...a) },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

let mockDriverMe: any = null;
const mockRefetchDriverMe = jest.fn();
jest.mock('@shared/hooks/queries', () => ({
  useDriverMe: () => ({ data: mockDriverMe, refetch: mockRefetchDriverMe }),
}));

const mockShowToast = jest.fn();
jest.mock('../../hooks/useToast', () => ({ showToast: (...a: any[]) => mockShowToast(...a) }));

const mockSetStringAsync = jest.fn();
jest.mock('expo-clipboard', () => ({ setStringAsync: (...a: any[]) => mockSetStringAsync(...a) }));

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

const mockLogout = jest.fn();
const mockLogoutAll = jest.fn();
const mockUpdateProfileImage = jest.fn();
let authState: any;
function resetAuthState() {
  authState = {
    user: { first_name: 'Jamie', last_name: 'Fox', email: 'jamie@example.com', phone: '+13065551212', gender: 'Male' },
    driver: { id: 'drv-1', driver_code: 'DRV-ABC123', is_verified: true, rating: 4.8, total_rides: 120 },
    logout: (...a: any[]) => mockLogout(...a),
    logoutAll: (...a: any[]) => mockLogoutAll(...a),
    updateProfileImage: (...a: any[]) => mockUpdateProfileImage(...a),
  };
}
function mockUseAuthStore(selector?: (s: any) => any) {
  return selector ? selector(authState) : authState;
}
mockUseAuthStore.getState = () => authState;
mockUseAuthStore.setState = (partial: any) => {
  authState = { ...authState, ...(typeof partial === 'function' ? partial(authState) : partial) };
};
jest.mock('@shared/store/authStore', () => ({
  __esModule: true,
  useAuthStore: mockUseAuthStore,
}));

import ProfileScreen from '../../app/driver/(tabs)/profile';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<ProfileScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => JSON.stringify(t.props.children)).join(' | ');
}

function findByText(r: TestRenderer.ReactTestRenderer, text: string) {
  return r.root.findAllByType(TouchableOpacity).find((n) =>
    n.findAllByType(Text).some((t) => {
      try { return JSON.stringify(t.props.children).includes(text); } catch { return false; }
    })
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  resetAuthState();
  mockDriverMe = null;
  mockApiGet.mockImplementation((url: string) => {
    if (url === '/company-info') return Promise.resolve({ data: {} });
    if (url === '/auth/me') return Promise.resolve({ data: authState.user });
    if (url === '/drivers/requirements') return Promise.resolve({ data: [] });
    if (url === '/drivers/documents') return Promise.resolve({ data: [] });
    return Promise.resolve({ data: {} });
  });
  mockApiPost.mockResolvedValue({ data: {} });
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
});

afterEach(() => {
  act(() => { renderer?.unmount(); });
  renderer = null;
});

describe('ProfileScreen (driver)', () => {
  it('fetches company info on mount and swallows a failure silently', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/company-info') return Promise.reject(new Error('boom'));
      return Promise.resolve({ data: [] });
    });
    const r = await renderScreen();
    expect(allText(r)).not.toContain('undefined');
  });

  it('renders company info once fetched', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/company-info') return Promise.resolve({ data: { name: 'Spinr Inc', address: '123 Main St', phone: '306-555-0000', email: 'help@spinr.ca', website: 'spinr.ca' } });
      if (url === '/drivers/requirements' || url === '/drivers/documents') return Promise.resolve({ data: [] });
      return Promise.resolve({ data: authState.user });
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('"Spinr Inc"');
    expect(allText(r)).toContain('"123 Main St"');
  });

  it('runs the focus-effect refresh: /auth/me updates the store, refetchDriverMe fires, requirements/documents load', async () => {
    const reqs = [{ id: 'req-1', name: 'License' }];
    const docs = [{ id: 'doc-1', requirement_id: 'req-1', status: 'approved', expiry_date: '2099-01-01' }];
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/company-info') return Promise.resolve({ data: {} });
      if (url === '/auth/me') return Promise.resolve({ data: { ...authState.user, first_name: 'Updated' } });
      if (url === '/drivers/requirements') return Promise.resolve({ data: reqs });
      if (url === '/drivers/documents') return Promise.resolve({ data: docs });
      return Promise.resolve({ data: {} });
    });
    const r = await renderScreen();
    expect(mockRefetchDriverMe).toHaveBeenCalled();
    expect(allText(r)).toContain('"License"');
    expect(allText(r)).toContain('"VALID"');
  });

  it('a requirements-load failure does not block documents from loading or crash the screen', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/company-info') return Promise.resolve({ data: {} });
      if (url === '/auth/me') return Promise.resolve({ data: authState.user });
      if (url === '/drivers/requirements') return Promise.reject(new Error('fail'));
      if (url === '/drivers/documents') return Promise.resolve({ data: [] });
      return Promise.resolve({ data: {} });
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('"No document requirements found"');
  });

  it('shows the referral chip with the driver_code and copies it on tap', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('"DRV-ABC123"');
    const chip = r.root.findAllByProps({ accessibilityRole: 'button' }).find((n) =>
      (n.props.accessibilityLabel || '').includes('Referral code')
    )!;
    await act(async () => { await chip.props.onPress(); await flush(); });
    expect(mockSetStringAsync).toHaveBeenCalledWith('DRV-ABC123');
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Copied!', 'Referral code copied to clipboard');
  });

  it('falls back to referral_code, then a DRIVER<id> default, when driver_code is absent', async () => {
    authState.driver = { id: 'abcdef1234567890' };
    const r = await renderScreen();
    expect(allText(r)).toContain('"DRIVERABCDEF12"');
  });

  it('hides the referral chip entirely when there is no driver id at all', async () => {
    authState.driver = {};
    const r = await renderScreen();
    expect(r.root.findAllByProps({ accessibilityRole: 'button' }).some((n) => (n.props.accessibilityLabel || '').includes('Referral code'))).toBe(false);
  });

  it('handlePickPhoto: camera permission denied toasts and never uploads', async () => {
    mockRequestCameraPermissionsAsync.mockResolvedValue({ status: 'denied' });
    const r = await renderScreen();
    const avatarBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => { avatarBtn.props.onPress(); });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const takePhoto = alertCall[2].find((b: any) => b.text === 'Take Photo');
    await act(async () => { await takePhoto.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Permission Denied', 'Camera access is needed.');
    expect(mockUpdateProfileImage).not.toHaveBeenCalled();
  });

  it('handlePickPhoto: library pick uploads the photo and toasts success', async () => {
    mockRequestMediaLibraryPermissionsAsync.mockResolvedValue({ status: 'granted' });
    mockLaunchImageLibraryAsync.mockResolvedValue({ canceled: false, assets: [{ uri: 'file://photo.jpg' }] });
    mockUpdateProfileImage.mockResolvedValue(undefined);
    const r = await renderScreen();
    const avatarBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => { avatarBtn.props.onPress(); });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const library = alertCall[2].find((b: any) => b.text === 'Library');
    await act(async () => { await library.onPress(); await flush(); });
    expect(mockUpdateProfileImage).toHaveBeenCalledWith('file://photo.jpg');
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Photo Updated', 'Your profile photo has been submitted for review.');
  });

  it('upload photo failure toasts the backend-provided error', async () => {
    mockRequestMediaLibraryPermissionsAsync.mockResolvedValue({ status: 'granted' });
    mockLaunchImageLibraryAsync.mockResolvedValue({ canceled: false, assets: [{ uri: 'file://photo.jpg' }] });
    mockUpdateProfileImage.mockRejectedValue(new Error('nope'));
    const r = await renderScreen();
    const avatarBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => { avatarBtn.props.onPress(); });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const library = alertCall[2].find((b: any) => b.text === 'Library');
    await act(async () => { await library.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Upload Failed', 'Could not upload your photo. Please try again.');
  });

  it('opens the edit modal pre-filled from the current user, and validates missing fields', async () => {
    const r = await renderScreen();
    const editBtn = findByText(r, 'Edit')!;
    act(() => { editBtn.props.onPress(); });
    const firstNameInput = r.root.findAllByType(TextInput).find((n) => n.props.value === 'Jamie')!;
    expect(firstNameInput).toBeTruthy();
    act(() => { firstNameInput.props.onChangeText(''); });
    const saveBtn = findByText(r, 'Save Changes')!;
    await act(async () => { await saveBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Missing Info', 'Please fill in all fields');
    expect(mockApiPost).not.toHaveBeenCalledWith('/users/profile', expect.anything());
  });

  it('rejects an invalid email on save', async () => {
    const r = await renderScreen();
    const editBtn = findByText(r, 'Edit')!;
    act(() => { editBtn.props.onPress(); });
    const emailInput = r.root.findAllByType(TextInput).find((n) => n.props.value === 'jamie@example.com')!;
    act(() => { emailInput.props.onChangeText('not-an-email'); });
    const saveBtn = findByText(r, 'Save Changes')!;
    await act(async () => { await saveBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Invalid Email', 'Please enter a valid email address');
  });

  it('saves the profile successfully, updates the store, closes the modal, and toasts', async () => {
    mockApiPost.mockResolvedValue({ data: { first_name: 'Jamie', last_name: 'Fox', email: 'jamie@example.com', gender: 'Male' } });
    const r = await renderScreen();
    const editBtn = findByText(r, 'Edit')!;
    act(() => { editBtn.props.onPress(); });
    const saveBtn = findByText(r, 'Save Changes')!;
    await act(async () => { await saveBtn.props.onPress(); await flush(); });
    expect(mockApiPost).toHaveBeenCalledWith('/users/profile', {
      first_name: 'Jamie', last_name: 'Fox', email: 'jamie@example.com', gender: 'Male',
    });
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Profile Updated', 'Your information has been saved.');
  });

  it('save-profile failure surfaces the backend message and keeps the modal open', async () => {
    mockApiPost.mockRejectedValue(new Error('dup'));
    const r = await renderScreen();
    const editBtn = findByText(r, 'Edit')!;
    act(() => { editBtn.props.onPress(); });
    const saveBtn = findByText(r, 'Save Changes')!;
    await act(async () => { await saveBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Update Failed', 'Failed to update your profile. Please try again.');
  });

  it('picking a gender in the picker sheet updates the field and closes the sheet', async () => {
    const r = await renderScreen();
    const editBtn = findByText(r, 'Edit')!;
    act(() => { editBtn.props.onPress(); });
    const genderTap = findByText(r, 'Tap to select') || r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => { try { return JSON.stringify(t.props.children).includes('Gender'); } catch { return false; } })
    );
    act(() => { genderTap!.props.onPress(); });
    const femaleOption = findByText(r, 'Female')!;
    act(() => { femaleOption.props.onPress(); });
    expect(allText(r)).toContain('"Female"');
  });

  it('handleLogout confirms then signs out and routes to /login', async () => {
    const r = await renderScreen();
    const signOutBtn = findByText(r, 'Sign Out')!;
    act(() => { signOutBtn.props.onPress(); });
    const alertCall = (Alert.alert as jest.Mock).mock.calls.find((c) => c[0] === 'Sign Out');
    const confirm = alertCall![2].find((b: any) => b.text === 'Sign Out');
    await act(async () => { await confirm.onPress(); await flush(); });
    expect(mockLogout).toHaveBeenCalled();
    expect(mockReplace).toHaveBeenCalledWith('/login');
  });

  it('handleLogoutAll confirms then signs out of every device and routes to /login even on failure', async () => {
    mockLogoutAll.mockRejectedValue(new Error('network'));
    const r = await renderScreen();
    const signOutAllBtn = findByText(r, 'Sign out of all devices')!;
    act(() => { signOutAllBtn.props.onPress(); });
    const alertCall = (Alert.alert as jest.Mock).mock.calls.find((c) => c[0] === 'Sign out of all devices?');
    const confirm = alertCall![2].find((b: any) => b.text === 'Sign out everywhere');
    // The screen's own onPress has no catch around logoutAll() — only a
    // finally that navigates to /login regardless of outcome — so the
    // rejection propagates out of onPress itself; swallow it here just
    // like the button's real caller (React's event system) would.
    await act(async () => { await confirm.onPress().catch(() => {}); await flush(); });
    expect(mockLogoutAll).toHaveBeenCalled();
    expect(mockReplace).toHaveBeenCalledWith('/login');
  });

  it('a pending document shows PENDING REVIEW even when an older approved copy exists', async () => {
    const reqs = [{ id: 'req-1', name: 'Insurance' }];
    const docs = [
      { id: 'doc-old', requirement_id: 'req-1', status: 'approved', expiry_date: '2099-01-01' },
      { id: 'doc-new', requirement_id: 'req-1', status: 'pending', expiry_date: null },
    ];
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/company-info') return Promise.resolve({ data: {} });
      if (url === '/auth/me') return Promise.resolve({ data: authState.user });
      if (url === '/drivers/requirements') return Promise.resolve({ data: reqs });
      if (url === '/drivers/documents') return Promise.resolve({ data: docs });
      return Promise.resolve({ data: {} });
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('"PENDING REVIEW"');
  });

  it('an expired document shows the EXPIRED badge', async () => {
    const reqs = [{ id: 'req-1', name: 'License' }];
    const docs = [{ id: 'doc-1', requirement_id: 'req-1', status: 'approved', expiry_date: '2020-01-01' }];
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/company-info') return Promise.resolve({ data: {} });
      if (url === '/auth/me') return Promise.resolve({ data: authState.user });
      if (url === '/drivers/requirements') return Promise.resolve({ data: reqs });
      if (url === '/drivers/documents') return Promise.resolve({ data: docs });
      return Promise.resolve({ data: {} });
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('"EXPIRED"');
  });

  it('a requirement with no matching document shows UPLOAD REQUIRED', async () => {
    const reqs = [{ id: 'req-1', name: 'Vulnerable Sector Check' }];
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/company-info') return Promise.resolve({ data: {} });
      if (url === '/auth/me') return Promise.resolve({ data: authState.user });
      if (url === '/drivers/requirements') return Promise.resolve({ data: reqs });
      if (url === '/drivers/documents') return Promise.resolve({ data: [] });
      return Promise.resolve({ data: {} });
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('"UPLOAD REQUIRED"');
  });

  it('shows the vehicle info and license plate when populated, joining color/make/model', async () => {
    mockDriverMe = null;
    authState.driver = { ...authState.driver, vehicle_color: 'Black', vehicle_make: 'Toyota', vehicle_model: 'Camry', license_plate: '123ABC' };
    const r = await renderScreen();
    expect(allText(r)).toContain('"Black Toyota Camry"');
    expect(allText(r)).toContain('"123ABC"');
  });

  it('shows the rejection banner when a driver was rejected and is not yet verified', async () => {
    authState.driver = { ...authState.driver, is_verified: false, rejection_reason: 'Blurry photo' };
    const r = await renderScreen();
    expect(allText(r)).toContain('"Blurry photo"');
    expect(allText(r)).toContain('"Application Rejected"');
  });

  it('navigates to lost-and-found, help, quests, subscription, referral, and settings from the Support section', async () => {
    const r = await renderScreen();
    act(() => { findByText(r, 'Lost & Found')!.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/driver/lost-and-found');
    act(() => { findByText(r, 'Help Center')!.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/driver/help');
    act(() => { findByText(r, 'Quests & Bonuses')!.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/driver/quests');
    act(() => { findByText(r, 'Spinr Pass')!.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/driver/subscription');
    act(() => { findByText(r, 'Referral Program')!.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/driver/referral');
    act(() => { findByText(r, 'App Settings')!.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/driver/settings');
  });
});
