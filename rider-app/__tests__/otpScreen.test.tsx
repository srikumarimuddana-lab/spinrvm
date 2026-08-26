/**
 * app/otp.tsx — rider-app OTP verification (auth-critical). Pins:
 *  - no phoneNumber param -> immediate router.back()
 *  - Verify blocked (shake + toast) until exactly CODE_LENGTH digits
 *  - verify success: token path calls setTokens + Analytics.otpVerified;
 *    userData present sets the store directly + Analytics.login (skips
 *    initialize()); no userData falls back to initialize()
 *  - requires_reactivation routes to /reactivate-account with the token
 *    and deletion-date params, and never calls setTokens
 *  - a verify failure shakes, clears the code, and toasts
 *  - Resend: blocked while canResend is false; a 429 sets the countdown
 *    from Retry-After (or the "<n>s" detail message) and toasts a
 *    rate-limit warning; any other failure toasts a generic message;
 *    success re-arms the 30s countdown and toasts
 *  - the post-verify `user` effect: profile-incomplete -> /profile-setup;
 *    profile-complete routes via the (mocked) consent check --
 *    needs_notice -> /legacy-consent-notice, else -> /(tabs); a failed
 *    consent check fails open to /(tabs)
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, TextInput, ActivityIndicator } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const mockBack = jest.fn();
const mockReplace = jest.fn();
const mockRouter = { back: mockBack, replace: mockReplace };
let mockSearchParams: any;
jest.mock('expo-router', () => ({
  useRouter: () => mockRouter,
  useLocalSearchParams: () => mockSearchParams,
}));

const COLORS = {
  primary: '#EF4444', primaryDark: '#B91C1C', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a), post: (...a: any[]) => mockApiPost(...a) },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const mockOtpVerified = jest.fn();
const mockLogin = jest.fn();
jest.mock('@shared/analytics', () => ({
  Analytics: { otpVerified: (...a: any[]) => mockOtpVerified(...a), login: (...a: any[]) => mockLogin(...a) },
}));

const mockLogCompleteRegistration = jest.fn();
jest.mock('@shared/analytics/meta', () => ({
  logCompleteRegistration: (...a: any[]) => mockLogCompleteRegistration(...a),
}));

// Real zustand store, matching indexScreen.test.tsx's convention: a fresh
// literal per useAuthStore() call would destabilize this screen's [user,
// router] effect.
const mockInitialize = jest.fn();
const mockClearError = jest.fn();
const mockSetTokens = jest.fn();
jest.mock('@shared/store/authStore', () => {
  const { create: createStore } = require('zustand');
  const useAuthStore = createStore(() => ({
    user: null,
    initialize: mockInitialize,
    clearError: mockClearError,
    setTokens: mockSetTokens,
  }));
  return { useAuthStore };
});

import OtpScreen from '../app/otp';
import { useAuthStore } from '@shared/store/authStore';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<OtpScreen />);
    await flush();
  });
  return renderer!;
}

function findButtonByLabel(r: TestRenderer.ReactTestRenderer, label: string) {
  return r.root.findByProps({ accessibilityLabel: label });
}

async function enterCode(r: TestRenderer.ReactTestRenderer, code: string) {
  const input = r.root.findByType(TextInput);
  await act(async () => {
    input.props.onChangeText(code);
    await flush();
  });
}

// The 30s countdown re-arms its own setTimeout from inside a useEffect on
// every tick. React's passive-effect commit is a microtask, so a single
// `jest.advanceTimersByTime(30000)` only fires the FIRST scheduled timer --
// the next one hasn't been scheduled yet when the synchronous timer loop
// looks for more due callbacks. Stepping 1s at a time, flushing between
// each step, lets each tick's effect re-arm before the next advance.
async function exhaustCountdown() {
  for (let i = 0; i < 30; i++) {
    await act(async () => {
      jest.advanceTimersByTime(1000);
      await flush();
    });
  }
}

beforeEach(() => {
  jest.clearAllMocks();
  jest.useFakeTimers();
  mockSearchParams = { phoneNumber: '+15551234567', consentAccepted: 'true' };
  useAuthStore.setState({ user: null, initialize: mockInitialize, clearError: mockClearError, setTokens: mockSetTokens });
  mockApiPost.mockResolvedValue({ data: { token: 'access-tok', refresh_token: 'refresh-tok', expires_in: 900 } });
  mockApiGet.mockResolvedValue({ data: { needs_notice: false } });
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.useRealTimers();
});

describe('OtpScreen', () => {
  it('navigates back immediately when there is no phoneNumber param', async () => {
    mockSearchParams = {};
    await renderScreen();
    expect(mockBack).toHaveBeenCalled();
  });

  it('blocks Verify until exactly 4 digits are entered', async () => {
    const r = await renderScreen();
    await enterCode(r, '12');
    const verifyBtn = findButtonByLabel(r, 'Verify and continue');
    await act(async () => {
      await verifyBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'Invalid Code', 'Please enter the 4-digit code sent to your phone.', 'warning',
    );
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it('verifies successfully with a token: sets tokens and logs Analytics.otpVerified', async () => {
    const r = await renderScreen();
    await enterCode(r, '1234');
    const verifyBtn = findButtonByLabel(r, 'Verify and continue');
    await act(async () => {
      await verifyBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/auth/verify-otp', {
      phone: '+15551234567', code: '1234', client_app: 'rider', consent_accepted: true,
    });
    expect(mockSetTokens).toHaveBeenCalledWith('access-tok', 'refresh-tok', 900);
    expect(mockOtpVerified).toHaveBeenCalled();
  });

  it('sets the user directly and logs Analytics.login when userData is returned (skips initialize())', async () => {
    mockApiPost.mockResolvedValue({
      data: { token: 't', refresh_token: 'r', expires_in: 900, user: { id: 'u1', profile_complete: true } },
    });
    const r = await renderScreen();
    await enterCode(r, '1234');
    const verifyBtn = findButtonByLabel(r, 'Verify and continue');
    await act(async () => {
      await verifyBtn.props.onPress();
      await flush();
    });
    expect(mockLogin).toHaveBeenCalled();
    expect(mockInitialize).not.toHaveBeenCalled();
    expect(useAuthStore.getState().user).toEqual({ id: 'u1', profile_complete: true });
  });

  it('falls back to initialize() when no userData is returned', async () => {
    mockApiPost.mockResolvedValue({ data: { token: 't', refresh_token: 'r', expires_in: 900 } });
    const r = await renderScreen();
    await enterCode(r, '1234');
    const verifyBtn = findButtonByLabel(r, 'Verify and continue');
    await act(async () => {
      await verifyBtn.props.onPress();
      await flush();
    });
    expect(mockInitialize).toHaveBeenCalled();
  });

  it('logs CompleteRegistration only for a genuinely new account', async () => {
    mockApiPost.mockResolvedValue({
      data: { token: 't', refresh_token: 'r', expires_in: 900, is_new_user: true, meta_event_id: 'evt-1' },
    });
    const r = await renderScreen();
    await enterCode(r, '1234');
    const verifyBtn = findButtonByLabel(r, 'Verify and continue');
    await act(async () => {
      await verifyBtn.props.onPress();
      await flush();
    });
    expect(mockLogCompleteRegistration).toHaveBeenCalledWith({ eventId: 'evt-1', surface: 'rider' });
  });

  it('routes to /reactivate-account on requires_reactivation, without setting tokens', async () => {
    mockApiPost.mockResolvedValue({
      data: { requires_reactivation: true, reactivation_token: 'react-tok', deletion_scheduled_at: '2027-01-01' },
    });
    const r = await renderScreen();
    await enterCode(r, '1234');
    const verifyBtn = findButtonByLabel(r, 'Verify and continue');
    await act(async () => {
      await verifyBtn.props.onPress();
      await flush();
    });
    expect(mockReplace).toHaveBeenCalledWith({
      pathname: '/reactivate-account',
      params: { reactivationToken: 'react-tok', deletionScheduledAt: '2027-01-01' },
    });
    expect(mockSetTokens).not.toHaveBeenCalled();
  });

  it('shakes, clears the code, and toasts on a verify failure', async () => {
    mockApiPost.mockRejectedValue(new Error('invalid code'));
    const r = await renderScreen();
    await enterCode(r, '9999');
    const verifyBtn = findButtonByLabel(r, 'Verify and continue');
    await act(async () => {
      await verifyBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('Verification Failed', 'Invalid code. Please try again.', 'danger');
    expect(r.root.findByType(TextInput).props.value).toBe('');
  });

  it('does not show the Resend button while the countdown is active', async () => {
    const r = await renderScreen();
    expect(() => findButtonByLabel(r, 'Resend Code' as any)).toThrow();
  });

  it('resends the code successfully and re-arms the countdown', async () => {
    const r = await renderScreen();
    await exhaustCountdown();
    mockApiPost.mockResolvedValue({ data: {} });
    const resendBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Resend Code')))!;
    await act(async () => {
      await resendBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/auth/send-otp', { phone: '+15551234567' });
    expect(mockShowToast).toHaveBeenCalledWith(
      'Code Sent', 'A new verification code has been sent to your phone.', 'success',
    );
  });

  it('sets the countdown from Retry-After and warns on a 429', async () => {
    const r = await renderScreen();
    await exhaustCountdown();
    const err: any = new Error('rate limited');
    err.response = { status: 429, headers: { 'retry-after': '45' }, data: {} };
    mockApiPost.mockRejectedValue(err);
    const resendBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Resend Code')))!;
    await act(async () => {
      await resendBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'Too Many Attempts', 'Please wait 45 seconds before requesting another code.', 'warning',
    );
  });

  it('shows a generic failure toast on a non-429 resend error', async () => {
    const r = await renderScreen();
    await exhaustCountdown();
    mockApiPost.mockRejectedValue(new Error('server error'));
    const resendBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Resend Code')))!;
    await act(async () => {
      await resendBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('Failed', 'Could not resend code. Please try again.', 'danger');
  });

  it('routes to /profile-setup when the verified user has an incomplete profile', async () => {
    await renderScreen();
    await act(async () => {
      useAuthStore.setState({ user: { id: 'u1', profile_complete: false } as any });
      await flush();
    });
    expect(mockReplace).toHaveBeenCalledWith('/profile-setup');
  });

  it('routes to /(tabs) for a profile-complete user with no notice needed', async () => {
    mockApiGet.mockResolvedValue({ data: { needs_notice: false } });
    await renderScreen();
    await act(async () => {
      useAuthStore.setState({ user: { id: 'u1', profile_complete: true } as any });
      await flush();
    });
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('routes to /legacy-consent-notice when the consent check flags needs_notice', async () => {
    mockApiGet.mockResolvedValue({ data: { needs_notice: true } });
    await renderScreen();
    await act(async () => {
      useAuthStore.setState({ user: { id: 'u1', profile_complete: true } as any });
      await flush();
    });
    expect(mockReplace).toHaveBeenCalledWith('/legacy-consent-notice');
  });

  it('fails open to /(tabs) when the consent check itself fails', async () => {
    mockApiGet.mockRejectedValue(new Error('down'));
    await renderScreen();
    await act(async () => {
      useAuthStore.setState({ user: { id: 'u1', profile_complete: true } as any });
      await flush();
    });
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('treats a user with first/last name and email as profile-complete even without the flag set', async () => {
    await renderScreen();
    await act(async () => {
      useAuthStore.setState({ user: { id: 'u1', first_name: 'A', last_name: 'B', email: 'a@b.com' } as any });
      await flush();
    });
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('ignores digits typed past the code length (e.g. a paste)', async () => {
    const r = await renderScreen();
    await enterCode(r, '123456');
    expect(r.root.findByType(TextInput).props.value).toBe('');
  });

  it('toasts the generic failure message when verify resolves with no data at all', async () => {
    mockApiPost.mockResolvedValue({});
    const r = await renderScreen();
    await enterCode(r, '1234');
    const verifyBtn = findButtonByLabel(r, 'Verify and continue');
    await act(async () => {
      await verifyBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('Verification Failed', 'Invalid code. Please try again.', 'danger');
  });

  it('falls back to empty-string reactivation params when the backend omits them', async () => {
    mockApiPost.mockResolvedValue({ data: { requires_reactivation: true } });
    const r = await renderScreen();
    await enterCode(r, '1234');
    const verifyBtn = findButtonByLabel(r, 'Verify and continue');
    await act(async () => {
      await verifyBtn.props.onPress();
      await flush();
    });
    expect(mockReplace).toHaveBeenCalledWith({
      pathname: '/reactivate-account',
      params: { reactivationToken: '', deletionScheduledAt: '' },
    });
  });

  it('never calls setTokens when the response has no token, and still falls back to initialize()', async () => {
    mockApiPost.mockResolvedValue({ data: {} });
    const r = await renderScreen();
    await enterCode(r, '1234');
    const verifyBtn = findButtonByLabel(r, 'Verify and continue');
    await act(async () => {
      await verifyBtn.props.onPress();
      await flush();
    });
    expect(mockSetTokens).not.toHaveBeenCalled();
    expect(mockOtpVerified).toHaveBeenCalled();
    expect(mockInitialize).toHaveBeenCalled();
  });

  it('defaults refresh_token/expires_in when the response has a token but omits them', async () => {
    mockApiPost.mockResolvedValue({ data: { token: 'tok-only' } });
    const r = await renderScreen();
    await enterCode(r, '1234');
    const verifyBtn = findButtonByLabel(r, 'Verify and continue');
    await act(async () => {
      await verifyBtn.props.onPress();
      await flush();
    });
    expect(mockSetTokens).toHaveBeenCalledWith('tok-only', '', 900);
  });

  it('ignores a second Resend tap fired before the first one has updated canResend', async () => {
    const r = await renderScreen();
    await exhaustCountdown();
    mockApiPost.mockResolvedValue({ data: {} });
    const resendBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Resend Code')))!;
    await act(async () => {
      resendBtn.props.onPress(); // 1st, sets resendInFlight.current synchronously
      resendBtn.props.onPress(); // 2nd, guarded by resendInFlight.current
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledTimes(1);
  });

  it('defaults the resend countdown to 60s on a 429 with no Retry-After header and no parseable detail', async () => {
    const r = await renderScreen();
    await exhaustCountdown();
    const err: any = new Error('rate limited');
    err.response = { status: 429, headers: {}, data: {} };
    mockApiPost.mockRejectedValue(err);
    const resendBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Resend Code')))!;
    await act(async () => {
      await resendBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'Too Many Attempts', 'Please wait 60 seconds before requesting another code.', 'warning',
    );
  });

  it('parses the retry-seconds detail message when no Retry-After header is present', async () => {
    const r = await renderScreen();
    await exhaustCountdown();
    const err: any = new Error('rate limited');
    err.response = { status: 429, headers: {}, data: { detail: 'try again in 20s' } };
    mockApiPost.mockRejectedValue(err);
    const resendBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Resend Code')))!;
    await act(async () => {
      await resendBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'Too Many Attempts', 'Please wait 20 seconds before requesting another code.', 'warning',
    );
  });

  it('falls back the countdown state to 60s when the parsed detail seconds are zero (not positive), while the toast still shows the raw parsed value', async () => {
    const r = await renderScreen();
    await exhaustCountdown();
    const err: any = new Error('rate limited');
    err.response = { status: 429, headers: {}, data: { detail: 'wait 0s' } };
    mockApiPost.mockRejectedValue(err);
    const resendBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Resend Code')))!;
    await act(async () => {
      await resendBtn.props.onPress();
      await flush();
    });
    // The toast interpolates the raw parsed value (0) directly, but the
    // countdown *state* is guarded to never sit at a non-positive value
    // (`retrySeconds > 0 ? retrySeconds : 60`) — the two intentionally
    // diverge here.
    expect(mockShowToast).toHaveBeenCalledWith(
      'Too Many Attempts', 'Please wait 0 seconds before requesting another code.', 'warning',
    );
  });

  it('shows a spinner on Verify while the request is in flight', async () => {
    let resolvePost: (v: any) => void;
    mockApiPost.mockImplementation(() => new Promise((resolve) => { resolvePost = resolve; }));
    const r = await renderScreen();
    await enterCode(r, '1234');
    const verifyBtn = findButtonByLabel(r, 'Verify and continue');
    await act(async () => {
      verifyBtn.props.onPress(); // don't await — its fetch is deliberately left pending
      await flush();
    });
    expect(r.root.findAllByType(ActivityIndicator).length).toBeGreaterThan(0);
    await act(async () => { resolvePost!({ data: { token: 't', refresh_token: 'r', expires_in: 900 } }); await flush(); });
  });

  it('navigates back when the header back button is pressed', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => { backBtn.props.onPress(); });
    expect(mockBack).toHaveBeenCalled();
  });

  it('focuses the hidden input when the code boxes are tapped', async () => {
    const r = await renderScreen();
    const codeBoxes = r.root.findByProps({ activeOpacity: 1 });
    expect(() => { codeBoxes.props.onPress(); }).not.toThrow();
  });

  it('navigates back via "Change phone number"', async () => {
    const r = await renderScreen();
    const changeBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Change phone number')))!;
    act(() => { changeBtn.props.onPress(); });
    expect(mockBack).toHaveBeenCalled();
  });
});
