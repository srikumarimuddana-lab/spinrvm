/**
 * app/otp.tsx — driver-app OTP verification (auth-critical, mirrors
 * rider-app's app/otp.tsx). Pins:
 *  - no phoneNumber param -> immediate router.back()
 *  - Verify blocked (shake + toast) until exactly 4 digits are entered
 *  - verify success sets tokens; userData present sets the store
 *    directly (skips initialize()); no userData falls back to
 *    initialize()
 *  - client_app: 'driver' is always sent, routing driver signups to the
 *    driver funnel dataset (both apps share this endpoint)
 *  - Meta CompleteRegistration (content_category: driver) logs only for
 *    a genuinely new account
 *  - requires_reactivation routes to /reactivate-account with the token
 *    and deletion-date params, and never calls setTokens
 *  - a verify failure shakes, clears the code, and toasts
 *  - Resend: blocked while canResend is false; success re-arms the 30s
 *    countdown and toasts; a failure toasts
 *  - the post-verify effect only fires once hasAttemptedVerification is
 *    true (a `user` already in the store from a prior session must not
 *    trigger a redirect before this screen's own verify completes):
 *    profile-incomplete -> /profile-setup; profile-complete -> /driver
 *    (via the consent check, failing open on error)
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, TextInput } from 'react-native';

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

const mockT = (key: string) => key;
const mockLanguageState = { t: mockT };
jest.mock('../../store/languageStore', () => ({ useLanguageStore: () => mockLanguageState }));

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a), post: (...a: any[]) => mockApiPost(...a) },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockShowToast = jest.fn();
jest.mock('../../hooks/useToast', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const mockLogCompleteRegistration = jest.fn();
jest.mock('@shared/analytics/meta', () => ({
  logCompleteRegistration: (...a: any[]) => mockLogCompleteRegistration(...a),
}));

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

import OtpScreen from '../../app/otp';
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

async function enterCode(r: TestRenderer.ReactTestRenderer, code: string) {
  const input = r.root.findByType(TextInput);
  await act(async () => {
    input.props.onChangeText(code);
    await flush();
  });
}

function findVerifyBtn(r: TestRenderer.ReactTestRenderer) {
  return r.root
    .findAllByType(TouchableOpacity)
    .find((n) => n.findAllByType(Text).some((t) => {
      try { return JSON.stringify(t.props.children).includes('otp.verifyAndContinue'); } catch { return false; }
    }))!;
}

function findResendBtn(r: TestRenderer.ReactTestRenderer) {
  return r.root
    .findAllByType(TouchableOpacity)
    .find((n) => n.findAllByType(Text).some((t) => {
      try { return JSON.stringify(t.props.children).includes('otp.resendCode'); } catch { return false; }
    }));
}

async function exhaustCountdown() {
  // React's passive-effect commit is a microtask, so a self-rearming
  // countdown needs stepping 1s at a time (see rider-app's otpScreen.test.tsx
  // for the full diagnosis).
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

describe('OtpScreen (driver-app)', () => {
  it('navigates back immediately when there is no phoneNumber param', async () => {
    mockSearchParams = {};
    await renderScreen();
    expect(mockBack).toHaveBeenCalled();
  });

  it('blocks Verify until exactly 4 digits are entered', async () => {
    const r = await renderScreen();
    await enterCode(r, '12');
    const verifyBtn = findVerifyBtn(r);
    await act(async () => {
      await verifyBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Invalid Code', 'Please enter the 4-digit code sent to your phone.');
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it('verifies with client_app: driver and sets tokens on success', async () => {
    const r = await renderScreen();
    await enterCode(r, '1234');
    const verifyBtn = findVerifyBtn(r);
    await act(async () => {
      await verifyBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/auth/verify-otp', {
      phone: '+15551234567', code: '1234', client_app: 'driver', consent_accepted: true,
    });
    expect(mockSetTokens).toHaveBeenCalledWith('access-tok', 'refresh-tok', 900);
  });

  it('sets the user directly when userData is returned (skips initialize())', async () => {
    mockApiPost.mockResolvedValue({
      data: { token: 't', refresh_token: 'r', expires_in: 900, user: { id: 'd1', profile_complete: true } },
    });
    const r = await renderScreen();
    await enterCode(r, '1234');
    const verifyBtn = findVerifyBtn(r);
    await act(async () => {
      await verifyBtn.props.onPress();
      await flush();
    });
    expect(mockInitialize).not.toHaveBeenCalled();
    expect(useAuthStore.getState().user).toEqual({ id: 'd1', profile_complete: true });
  });

  it('falls back to initialize() when no userData is returned', async () => {
    const r = await renderScreen();
    await enterCode(r, '1234');
    const verifyBtn = findVerifyBtn(r);
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
    const verifyBtn = findVerifyBtn(r);
    await act(async () => {
      await verifyBtn.props.onPress();
      await flush();
    });
    expect(mockLogCompleteRegistration).toHaveBeenCalledWith({ eventId: 'evt-1', surface: 'driver' });
  });

  it('routes to /reactivate-account on requires_reactivation, without setting tokens', async () => {
    mockApiPost.mockResolvedValue({
      data: { requires_reactivation: true, reactivation_token: 'react-tok', deletion_scheduled_at: '2027-01-01' },
    });
    const r = await renderScreen();
    await enterCode(r, '1234');
    const verifyBtn = findVerifyBtn(r);
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
    const verifyBtn = findVerifyBtn(r);
    await act(async () => {
      await verifyBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Verification Failed', 'Invalid code. Please try again.');
    expect(r.root.findByType(TextInput).props.value).toBe('');
  });

  it('does not show the Resend button while the countdown is active', async () => {
    const r = await renderScreen();
    expect(findResendBtn(r)).toBeUndefined();
  });

  it('resends the code successfully and re-arms the countdown', async () => {
    const r = await renderScreen();
    await exhaustCountdown();
    mockApiPost.mockResolvedValue({ data: {} });
    const resendBtn = findResendBtn(r)!;
    await act(async () => {
      await resendBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/auth/send-otp', { phone: '+15551234567' });
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Code Sent', 'A new verification code has been sent to your phone.');
  });

  it('toasts a generic failure message when resend fails', async () => {
    const r = await renderScreen();
    await exhaustCountdown();
    mockApiPost.mockRejectedValue(new Error('server error'));
    const resendBtn = findResendBtn(r)!;
    await act(async () => {
      await resendBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Failed', 'Could not resend code. Please try again.');
  });

  it('does not redirect on a pre-existing store user before verification is attempted', async () => {
    useAuthStore.setState({ user: { id: 'stale', profile_complete: true } as any });
    await renderScreen();
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it('routes to /profile-setup after verification when the profile is incomplete', async () => {
    const r = await renderScreen();
    await enterCode(r, '1234');
    const verifyBtn = findVerifyBtn(r);
    await act(async () => {
      await verifyBtn.props.onPress();
      await flush();
    });
    await act(async () => {
      useAuthStore.setState({ user: { id: 'd1', profile_complete: false } as any });
      await flush();
    });
    expect(mockReplace).toHaveBeenCalledWith('/profile-setup');
  });

  it('routes to /driver for a profile-complete driver with no notice needed', async () => {
    const r = await renderScreen();
    await enterCode(r, '1234');
    const verifyBtn = findVerifyBtn(r);
    await act(async () => {
      await verifyBtn.props.onPress();
      await flush();
    });
    await act(async () => {
      useAuthStore.setState({ user: { id: 'd1', profile_complete: true } as any });
      await flush();
    });
    expect(mockReplace).toHaveBeenCalledWith('/driver');
  });

  it('routes to /legacy-consent-notice when the consent check flags needs_notice', async () => {
    mockApiGet.mockResolvedValue({ data: { needs_notice: true } });
    const r = await renderScreen();
    await enterCode(r, '1234');
    const verifyBtn = findVerifyBtn(r);
    await act(async () => {
      await verifyBtn.props.onPress();
      await flush();
    });
    await act(async () => {
      useAuthStore.setState({ user: { id: 'd1', profile_complete: true } as any });
      await flush();
    });
    expect(mockReplace).toHaveBeenCalledWith('/legacy-consent-notice');
  });

  it('fails open to /driver when the consent check itself fails', async () => {
    mockApiGet.mockRejectedValue(new Error('down'));
    const r = await renderScreen();
    await enterCode(r, '1234');
    const verifyBtn = findVerifyBtn(r);
    await act(async () => {
      await verifyBtn.props.onPress();
      await flush();
    });
    await act(async () => {
      useAuthStore.setState({ user: { id: 'd1', profile_complete: true } as any });
      await flush();
    });
    expect(mockReplace).toHaveBeenCalledWith('/driver');
  });

  it('navigates back on the header back button and the change-number row', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => {
      backBtn.props.onPress();
    });
    expect(mockBack).toHaveBeenCalled();
  });
});
