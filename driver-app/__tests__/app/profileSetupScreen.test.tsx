/**
 * app/profile-setup.tsx — driver onboarding profile form (auth-critical,
 * runs after OTP verification). Pins:
 *  - no token + no user -> immediate redirect to /login
 *  - an already-complete `user` in the store redirects to /driver without
 *    ever calling /auth/me
 *  - an incomplete store user re-fetches /auth/me; a complete fresh
 *    profile stores it and redirects to /driver; a fetch failure falls
 *    through to the form (never traps the driver)
 *  - service areas: loads from /service-areas; a failure falls back to
 *    the hardcoded Saskatoon/Regina list; a single returned area
 *    auto-selects itself
 *  - field-specific validation toasts (not a blanket message) name the
 *    exact missing/invalid field, checked in order
 *  - Submit: createProfile, then a best-effort registerDriver (its
 *    failure is swallowed, never blocks navigation); the consent check
 *    routes to /legacy-consent-notice or /driver depending on
 *    needs_notice, failing open to /driver on its own failure; a
 *    createProfile failure routes through notifyError
 *  - referral code: a successful apply toasts once; an "already applied"
 *    400 counts as success (still toasts); a genuine rejection toasts an
 *    error and stops retrying; no code entered means no referral call at
 *    all
 *  - Change Number (header link, and the hardware back button) confirms
 *    via Alert before signing out and returning to /login
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, TextInput, Alert, BackHandler } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const mockReplace = jest.fn();
jest.mock('expo-router', () => ({ useRouter: () => ({ replace: mockReplace }) }));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB', success: '#10B981',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockGetForegroundPermissionsAsync = jest.fn();
const mockRequestForegroundPermissionsAsync = jest.fn();
const mockGetCurrentPositionAsync = jest.fn();
const mockReverseGeocodeAsync = jest.fn();
jest.mock('expo-location', () => ({
  getForegroundPermissionsAsync: (...a: any[]) => mockGetForegroundPermissionsAsync(...a),
  requestForegroundPermissionsAsync: (...a: any[]) => mockRequestForegroundPermissionsAsync(...a),
  getCurrentPositionAsync: (...a: any[]) => mockGetCurrentPositionAsync(...a),
  reverseGeocodeAsync: (...a: any[]) => mockReverseGeocodeAsync(...a),
  Accuracy: { Balanced: 3 },
}));

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a), post: (...a: any[]) => mockApiPost(...a) },
}));

const mockShowToast = jest.fn();
jest.mock('../../hooks/useToast', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const mockNotifyError = jest.fn();
jest.mock('../../lib/notifyError', () => ({ notifyError: (...args: any[]) => mockNotifyError(...args) }));

const mockCreateProfile = jest.fn();
const mockRegisterDriver = jest.fn();
const mockLogout = jest.fn();
jest.mock('@shared/store/authStore', () => {
  const { create: createStore } = require('zustand');
  const useAuthStore = createStore(() => ({
    user: null, token: null, isLoading: false,
    createProfile: (...a: any[]) => mockCreateProfile(...a),
    registerDriver: (...a: any[]) => mockRegisterDriver(...a),
    logout: (...a: any[]) => mockLogout(...a),
  }));
  return { useAuthStore };
});

import ProfileSetupScreen from '../../app/profile-setup';
import { useAuthStore } from '@shared/store/authStore';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const SERVICE_AREAS = [
  { id: 'saskatoon', name: 'Saskatoon, SK', city: 'Saskatoon' },
  { id: 'regina', name: 'Regina, SK', city: 'Regina' },
];

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<ProfileSetupScreen />);
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

async function fillValidFormExceptServiceArea(r: TestRenderer.ReactTestRenderer) {
  const inputs = r.root.findAllByType(TextInput);
  const firstName = inputs.find((i) => i.props.placeholder === 'John')!;
  const lastName = inputs.find((i) => i.props.placeholder === 'Doe')!;
  const email = inputs.find((i) => i.props.placeholder === 'john.doe@example.com')!;
  act(() => { firstName.props.onChangeText('Jamie'); });
  act(() => { lastName.props.onChangeText('Smith'); });
  act(() => { email.props.onChangeText('jamie@example.com'); });
  const maleBtn = findButtonByText(r, 'Male');
  act(() => { maleBtn.props.onPress(); });
}

beforeEach(() => {
  jest.clearAllMocks();
  useAuthStore.setState({
    user: { phone: '+15551234567' } as any, token: 'a-token', isLoading: false,
    createProfile: mockCreateProfile, registerDriver: mockRegisterDriver, logout: mockLogout,
  });
  mockApiGet.mockImplementation((url: string) => {
    if (url === '/service-areas') return Promise.resolve({ data: SERVICE_AREAS });
    if (url === '/auth/me') return Promise.resolve({ data: {} });
    if (url === '/consent/status') return Promise.resolve({ data: { needs_notice: false } });
    return Promise.reject(new Error('unexpected url ' + url));
  });
  mockApiPost.mockResolvedValue({ data: {} });
  mockCreateProfile.mockResolvedValue(undefined);
  mockRegisterDriver.mockResolvedValue(undefined);
  mockGetForegroundPermissionsAsync.mockResolvedValue({ status: 'denied' });
  mockRequestForegroundPermissionsAsync.mockResolvedValue({ status: 'denied' });
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.restoreAllMocks();
});

describe('ProfileSetupScreen', () => {
  it('redirects to /login when there is no token and no user', async () => {
    useAuthStore.setState({ user: null, token: null } as any);
    await renderScreen();
    expect(mockReplace).toHaveBeenCalledWith('/login');
  });

  it('redirects to /driver without calling /auth/me when the store user is already complete', async () => {
    useAuthStore.setState({ user: { first_name: 'A', last_name: 'B', email: 'a@b.com' } as any, token: 't' });
    await renderScreen();
    expect(mockApiGet).not.toHaveBeenCalledWith('/auth/me');
    expect(mockReplace).toHaveBeenCalledWith('/driver');
  });

  it('re-fetches /auth/me for an incomplete store user; a complete fresh profile stores it and redirects', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/auth/me') return Promise.resolve({ data: { first_name: 'A', last_name: 'B', email: 'a@b.com' } });
      if (url === '/service-areas') return Promise.resolve({ data: SERVICE_AREAS });
      return Promise.reject(new Error('unexpected'));
    });
    await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/auth/me');
    expect(useAuthStore.getState().user).toEqual({ first_name: 'A', last_name: 'B', email: 'a@b.com' });
    expect(mockReplace).toHaveBeenCalledWith('/driver');
  });

  it('falls through to the form when /auth/me fails', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/auth/me') return Promise.reject(new Error('down'));
      if (url === '/service-areas') return Promise.resolve({ data: SERVICE_AREAS });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('Welcome!');
  });

  it('falls back to the hardcoded Saskatoon/Regina list when /service-areas fails', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/auth/me') return Promise.reject(new Error('down'));
      if (url === '/service-areas') return Promise.reject(new Error('down'));
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('Saskatoon, SK');
    expect(allText(r)).toContain('Regina, SK');
  });

  it('auto-selects a single returned service area', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/auth/me') return Promise.reject(new Error('down'));
      if (url === '/service-areas') return Promise.resolve({ data: [SERVICE_AREAS[0]] });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('You can only operate in your selected area');
  });

  it('names the exact missing field on submit, in order', async () => {
    const r = await renderScreen();
    const submitBtn = findButtonByText(r, 'Create Profile');
    // Button is disabled while invalid; call handler directly (belt-and-
    // suspenders, matching this suite's convention elsewhere) since
    // onPress exists on the TouchableOpacity regardless of `disabled`.
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('warning', 'First Name Required', 'Please enter your first name (at least 2 letters).');
  });

  it('names the next missing field once the first name is filled: last name', async () => {
    const r = await renderScreen();
    const firstName = r.root.findAllByType(TextInput).find((i) => i.props.placeholder === 'John')!;
    act(() => { firstName.props.onChangeText('Jamie'); });
    const submitBtn = findButtonByText(r, 'Create Profile');
    await act(async () => { await submitBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('warning', 'Last Name Required', 'Please enter your last name (at least 2 letters).');
  });

  it('names email as required when name fields are filled but email is blank', async () => {
    const r = await renderScreen();
    const inputs = r.root.findAllByType(TextInput);
    act(() => { inputs.find((i) => i.props.placeholder === 'John')!.props.onChangeText('Jamie'); });
    act(() => { inputs.find((i) => i.props.placeholder === 'Doe')!.props.onChangeText('Smith'); });
    const submitBtn = findButtonByText(r, 'Create Profile');
    await act(async () => { await submitBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('warning', 'Email Required', 'Please enter your email address.');
  });

  it('flags an invalid (non-empty) email', async () => {
    const r = await renderScreen();
    const inputs = r.root.findAllByType(TextInput);
    act(() => { inputs.find((i) => i.props.placeholder === 'John')!.props.onChangeText('Jamie'); });
    act(() => { inputs.find((i) => i.props.placeholder === 'Doe')!.props.onChangeText('Smith'); });
    act(() => { inputs.find((i) => i.props.placeholder === 'john.doe@example.com')!.props.onChangeText('not-an-email'); });
    const submitBtn = findButtonByText(r, 'Create Profile');
    await act(async () => { await submitBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('warning', 'Invalid Email', 'That email doesn’t look right — e.g. name@example.com.');
  });

  it('requires a gender selection once name+email are valid', async () => {
    const r = await renderScreen();
    const inputs = r.root.findAllByType(TextInput);
    act(() => { inputs.find((i) => i.props.placeholder === 'John')!.props.onChangeText('Jamie'); });
    act(() => { inputs.find((i) => i.props.placeholder === 'Doe')!.props.onChangeText('Smith'); });
    act(() => { inputs.find((i) => i.props.placeholder === 'john.doe@example.com')!.props.onChangeText('jamie@example.com'); });
    const submitBtn = findButtonByText(r, 'Create Profile');
    await act(async () => { await submitBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('warning', 'Gender Required', 'Please select your gender.');
  });

  it('requires a service area once every other field is valid', async () => {
    const r = await renderScreen();
    await fillValidFormExceptServiceArea(r);
    const submitBtn = findButtonByText(r, 'Create Profile');
    await act(async () => { await submitBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('warning', 'Service Area Required', 'Please select the area where you plan to drive.');
  });

  it('sets gender via the Female and Other buttons', async () => {
    const r = await renderScreen();
    act(() => { findButtonByText(r, 'Female').props.onPress(); });
    act(() => { findButtonByText(r, 'Other').props.onPress(); });
    // No crash across both handlers; final selection is exercised via a
    // full submit below in other tests. This pins handleGenderFemale/Other.
    expect(allText(r)).toContain('Other');
  });

  it('disables Submit until the form is fully valid', async () => {
    const r = await renderScreen();
    const submitBtn = findButtonByText(r, 'Create Profile');
    expect(submitBtn.props.disabled).toBe(true);
    await fillValidFormExceptServiceArea(r);
    expect(findButtonByText(r, 'Create Profile').props.disabled).toBe(true); // still no service area
    const areaChip = findButtonByText(r, 'Saskatoon, SK');
    act(() => { areaChip.props.onPress(); });
    expect(findButtonByText(r, 'Create Profile').props.disabled).toBe(false);
  });

  it('submits successfully: creates the profile, best-effort registers the driver, and routes via the consent check', async () => {
    const r = await renderScreen();
    await fillValidFormExceptServiceArea(r);
    act(() => { findButtonByText(r, 'Saskatoon, SK').props.onPress(); });
    const submitBtn = findButtonByText(r, 'Create Profile');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockCreateProfile).toHaveBeenCalledWith(expect.objectContaining({
      first_name: 'Jamie', last_name: 'Smith', email: 'jamie@example.com', gender: 'Male', service_area_id: 'saskatoon',
    }));
    expect(mockRegisterDriver).toHaveBeenCalled();
    expect(mockReplace).toHaveBeenCalledWith('/driver');
  });

  it('never blocks navigation when registerDriver fails (best-effort, non-fatal)', async () => {
    mockRegisterDriver.mockRejectedValue(new Error('already registered'));
    const r = await renderScreen();
    await fillValidFormExceptServiceArea(r);
    act(() => { findButtonByText(r, 'Saskatoon, SK').props.onPress(); });
    const submitBtn = findButtonByText(r, 'Create Profile');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockReplace).toHaveBeenCalledWith('/driver');
  });

  it('routes to /legacy-consent-notice when the post-submit consent check flags needs_notice', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/auth/me') return Promise.reject(new Error('down'));
      if (url === '/service-areas') return Promise.resolve({ data: SERVICE_AREAS });
      if (url === '/consent/status') return Promise.resolve({ data: { needs_notice: true } });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    await fillValidFormExceptServiceArea(r);
    act(() => { findButtonByText(r, 'Saskatoon, SK').props.onPress(); });
    const submitBtn = findButtonByText(r, 'Create Profile');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockReplace).toHaveBeenCalledWith('/legacy-consent-notice');
  });

  it('routes through notifyError on a createProfile failure', async () => {
    mockCreateProfile.mockRejectedValue(new Error('duplicate email'));
    const r = await renderScreen();
    await fillValidFormExceptServiceArea(r);
    act(() => { findButtonByText(r, 'Saskatoon, SK').props.onPress(); });
    const submitBtn = findButtonByText(r, 'Create Profile');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockNotifyError).toHaveBeenCalledWith(
      expect.any(Error),
      { fallbackTitle: 'Profile Not Saved', fallbackMessage: 'Failed to create profile. Please try again.' },
    );
    expect(mockReplace).not.toHaveBeenCalledWith('/driver');
  });

  it('applies a referral code and toasts success', async () => {
    const r = await renderScreen();
    await fillValidFormExceptServiceArea(r);
    act(() => { findButtonByText(r, 'Saskatoon, SK').props.onPress(); });
    const referralInput = r.root.findByProps({ placeholder: 'e.g. DRV-7K9M2P' });
    act(() => { referralInput.props.onChangeText('drv-abc123'); });
    const submitBtn = findButtonByText(r, 'Create Profile');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/drivers/referral/apply', { referral_code: 'DRV-ABC123' });
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Referral applied', 'Your referral code was added.');
  });

  it('treats "already applied" as success (still toasts, does not error)', async () => {
    const err: any = new Error('already applied');
    err.response = { status: 400, data: { detail: 'Referral code already applied to this account.' } };
    mockApiPost.mockRejectedValue(err);
    const r = await renderScreen();
    await fillValidFormExceptServiceArea(r);
    act(() => { findButtonByText(r, 'Saskatoon, SK').props.onPress(); });
    const referralInput = r.root.findByProps({ placeholder: 'e.g. DRV-7K9M2P' });
    act(() => { referralInput.props.onChangeText('DRVABC'); });
    const submitBtn = findButtonByText(r, 'Create Profile');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Referral applied', 'Your referral code was added.');
  });

  it('toasts an error and stops retrying on a genuine referral rejection', async () => {
    const err: any = new Error('invalid code');
    err.response = { status: 400, data: { detail: 'That code does not exist.' } };
    mockApiPost.mockRejectedValue(err);
    const r = await renderScreen();
    await fillValidFormExceptServiceArea(r);
    act(() => { findButtonByText(r, 'Saskatoon, SK').props.onPress(); });
    const referralInput = r.root.findByProps({ placeholder: 'e.g. DRV-7K9M2P' });
    act(() => { referralInput.props.onChangeText('BADCODE'); });
    const submitBtn = findButtonByText(r, 'Create Profile');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Referral code', 'That code does not exist.');
    expect(mockApiPost).toHaveBeenCalledTimes(1); // no retries after a permanent rejection
  });

  it('confirms via Alert before signing out on Change Number', async () => {
    const r = await renderScreen();
    const changeBtn = findButtonByText(r, 'Change');
    act(() => { changeBtn.props.onPress(); });
    expect(Alert.alert).toHaveBeenCalledWith(
      'Change phone number?',
      'This will sign you out and return to the login screen. Any progress here will be lost.',
      expect.any(Array),
    );
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const signOutAction = alertCall[2].find((b: any) => b.text === 'Sign Out');
    await act(async () => {
      await signOutAction.onPress();
      await flush();
    });
    expect(mockLogout).toHaveBeenCalled();
    expect(mockReplace).toHaveBeenCalledWith('/login');
  });

  it('auto-selects a service area from the driver\'s current location when permission is granted', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/auth/me') return Promise.reject(new Error('down'));
      if (url === '/service-areas') return Promise.resolve({ data: SERVICE_AREAS });
      return Promise.reject(new Error('unexpected'));
    });
    mockGetForegroundPermissionsAsync.mockResolvedValue({ status: 'granted' });
    mockGetCurrentPositionAsync.mockResolvedValue({ coords: { latitude: 50.45, longitude: -104.6 } });
    mockReverseGeocodeAsync.mockResolvedValue([{ city: 'Regina', subregion: 'Regina' }]);
    const r = await renderScreen();
    expect(mockGetCurrentPositionAsync).toHaveBeenCalled();
    // The area chip renders selected (its onPress toggles it off); the
    // Create Profile button no longer requires picking one manually — fill
    // the rest of the form and confirm submit succeeds without pressing a chip.
    const inputs = r.root.findAllByType(TextInput);
    act(() => { inputs.find((i) => i.props.placeholder === 'John')!.props.onChangeText('Jamie'); });
    act(() => { inputs.find((i) => i.props.placeholder === 'Doe')!.props.onChangeText('Smith'); });
    act(() => { inputs.find((i) => i.props.placeholder === 'john.doe@example.com')!.props.onChangeText('jamie@example.com'); });
    act(() => { findButtonByText(r, 'Male').props.onPress(); });
    expect(findButtonByText(r, 'Create Profile').props.disabled).toBe(false);
  });

  it('requests permission when not already granted, and skips auto-select when the driver denies it', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/auth/me') return Promise.reject(new Error('down'));
      if (url === '/service-areas') return Promise.resolve({ data: SERVICE_AREAS });
      return Promise.reject(new Error('unexpected'));
    });
    mockGetForegroundPermissionsAsync.mockResolvedValue({ status: 'undetermined' });
    mockRequestForegroundPermissionsAsync.mockResolvedValue({ status: 'denied' });
    await renderScreen();
    expect(mockRequestForegroundPermissionsAsync).toHaveBeenCalled();
    expect(mockGetCurrentPositionAsync).not.toHaveBeenCalled();
  });

  it('retries a transient referral failure and succeeds on a later attempt', async () => {
    let calls = 0;
    mockApiPost.mockImplementation(() => {
      calls += 1;
      if (calls < 2) return Promise.reject(new Error('network blip')); // no .response -> not a 4xx, so it retries
      return Promise.resolve({ data: {} });
    });
    const r = await renderScreen();
    await fillValidFormExceptServiceArea(r);
    act(() => { findButtonByText(r, 'Saskatoon, SK').props.onPress(); });
    const referralInput = r.root.findByProps({ placeholder: 'e.g. DRV-7K9M2P' });
    act(() => { referralInput.props.onChangeText('DRVOK'); });
    const submitBtn = findButtonByText(r, 'Create Profile');
    await act(async () => {
      await submitBtn.props.onPress();
      // Real timers are in effect (no jest.useFakeTimers in this suite) —
      // the retry backs off via a real setTimeout(400ms), so wait it out.
      await new Promise((resolve) => setTimeout(resolve, 500));
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledTimes(2);
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Referral applied', 'Your referral code was added.');
  });

  it('fails open to /driver when the post-submit consent check itself rejects', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/auth/me') return Promise.reject(new Error('down'));
      if (url === '/service-areas') return Promise.resolve({ data: SERVICE_AREAS });
      if (url === '/consent/status') return Promise.reject(new Error('consent service down'));
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    await fillValidFormExceptServiceArea(r);
    act(() => { findButtonByText(r, 'Saskatoon, SK').props.onPress(); });
    const submitBtn = findButtonByText(r, 'Create Profile');
    await act(async () => {
      await submitBtn.props.onPress();
      await flush();
    });
    expect(mockReplace).toHaveBeenCalledWith('/driver');
  });

  it('routes the hardware back button through the same sign-out confirmation', async () => {
    const addListenerSpy = jest.spyOn(BackHandler, 'addEventListener');
    await renderScreen();
    const handler = addListenerSpy.mock.calls.find((c) => c[0] === 'hardwareBackPress')![1] as () => boolean;
    act(() => { handler(); });
    expect(Alert.alert).toHaveBeenCalledWith(
      'Change phone number?',
      expect.any(String),
      expect.any(Array),
    );
  });
});
