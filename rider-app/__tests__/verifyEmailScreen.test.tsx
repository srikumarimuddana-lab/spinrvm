/**
 * N14 (ACTION_ITEMS.md): rider-app UI for self-serve email verification.
 *
 * Pins app/verify-email.tsx (the sibling of app/otp.tsx for the phone flow):
 *  - fires POST /users/verify-email/request on mount when there's an
 *    unverified email on the account
 *  - the `already_verified` short-circuit skips the code-entry UI entirely
 *  - POST /users/verify-email/confirm fires with the entered code
 *  - each backend error case (PROFILE_EMAIL_MISSING, AUTH_OTP_INVALID,
 *    AUTH_OTP_EXPIRED, rate-limited, SYSTEM_SERVICE_UNAVAILABLE) surfaces a
 *    plain-English toast, not a raw backend sentinel like "ERR_OTP_INVALID"
 *  - a successful confirm merges `email_verified: true` into the auth store
 *    immediately (no reload, no re-fetch needed)
 *
 * Uses react-test-renderer directly, matching accountEmailVerification.test.tsx
 * and privacySettingsToggles.test.tsx conventions. `@shared/store/authStore`
 * is a real zustand store so `useAuthStore.setState(...)` behaves like
 * production.
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';

import VerifyEmailScreen from '../app/verify-email';
import { useAuthStore } from '@shared/store/authStore';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));

const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack }),
}));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const COLORS = {
  primary: '#EF4444', primaryDark: '#D32F2F', background: '#FFF', surface: '#FFF',
  surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

// tKey mirrors ../i18n's real lookup contract (i18n key → English copy, or
// the caller's fallback when absent) but reads straight from en.json instead
// of the real module — the real module pulls in AsyncStorage via its zustand
// persist wiring, which isn't available under Jest without a native-module
// mock. Reading the real en.json (a static JSON file, not the index.ts
// runtime) keeps these assertions honestly pinned to the actual copy every
// other screen shows for the same message_key (errors.auth.otp_invalid,
// errors.auth.otp_expired, errors.system.service_unavailable,
// errors.profile.email_missing), without needing to hand-duplicate it.
jest.mock('../i18n', () => {
  const en = require('../i18n/en.json');
  const getPath = (obj: any, path: string) =>
    path.split('.').reduce((o: any, k: string) => (o && typeof o === 'object' ? o[k] : undefined), obj);
  return {
    tKey: (key: string, fallback?: string) => {
      const value = getPath(en, key);
      return typeof value === 'string' ? value : (fallback ?? key);
    },
  };
});

const mockApiPost = jest.fn();
class MockRateLimitError extends Error {
  name = 'RateLimitError';
  retryAfterSeconds: number;
  constructor(message: string, retryAfterSeconds: number) {
    super(message);
    this.retryAfterSeconds = retryAfterSeconds;
  }
}
jest.mock('@shared/api/client', () => {
  const actual = jest.requireActual('@shared/api/client');
  return {
    __esModule: true,
    ...actual,
    default: { post: (...args: any[]) => mockApiPost(...args) },
    RateLimitError: MockRateLimitError,
  };
});

// `email_verified` isn't declared on the shared `User` type yet (see
// app/verify-email.tsx's `VerifiableUser` comment) — extend it locally for
// this test file's typing only.
type VerifiableUser = import('@shared/store/authStore').User & { email_verified?: boolean };

const mockDefaultUser: VerifiableUser = {
  id: 'rider-1',
  phone: '+15551234567',
  email: 'rider@example.com',
  role: 'rider',
  created_at: new Date().toISOString(),
  profile_complete: true,
};

jest.mock('@shared/store/authStore', () => {
  const { create: createStore } = require('zustand');
  const useAuthStore = createStore(() => ({ user: mockDefaultUser }));
  return { useAuthStore };
});

// SpinrApiError-shaped rejection, matching what @shared/api/client actually
// throws (message = backend's raw `error.message`, messageKey = the i18n key).
function apiError(opts: { message: string; messageKey?: string; code?: number }) {
  return { name: 'SpinrApiError', ...opts };
}

// CR-4138 root cause: a successful /users/verify-email/request response
// makes app/verify-email.tsx call setCountdown(30), which starts a real
// `setTimeout(..., 1000)` resend-countdown chain (verify-email.tsx's
// `[countdown]` effect). This file used to run under REAL timers, so that
// was a genuine OS-level timer handle — and `flush` below used to wait on
// one too (`new Promise((resolve) => setTimeout(resolve, 0))`) purely to
// let the mocked request's promise chain settle. Under full-suite Jest
// parallelism (many worker processes contending for a handful of CPUs —
// this repo's CI, and this box: `nproc` vs. a 61-file suite), a single
// test's wall-clock cost can stretch past that real timer's 1000ms mark
// before `afterEach`'s `unmount()` gets a chance to run its cleanup
// (`clearTimeout`) — a genuine race, not mere slowness. Losing that race
// let the countdown timer's callback fire on a component mid-unmount,
// `setState` outside `act()`, and reschedule *another* real 1s timer —
// reproduced locally: every `beforeEach`/`afterEach` in this file logged
// "not wrapped in act(...)" against `Root`/`VerifyEmailScreen` even in a
// single-file, non-parallel run. Those zombie real timers are exactly what
// full-suite runs report as "A worker process has failed to exit
// gracefully... tests leaking due to improper teardown" (also reproduced
// locally under `--maxWorkers` oversubscription) — and under enough CI
// contention, enough of them piled up mid-run to blow even the widened
// 20000ms per-test timeout the prior CR-4102/C31 follow-up commit applied
// here (see git history: that fix legitimately closed a *different* leak
// in privacySettingsToggles.test.tsx, but only papered over this file's
// own residual with more timeout headroom — which is exactly why CR-4138
// kept recurring afterward on unrelated PRs).
//
// The actual fix: `jest.useFakeTimers()` for this file (below, scoped to
// just setTimeout/clearTimeout — see that comment for why) makes
// `setTimeout` a purely in-memory, Jest-tracked construct — no real OS
// timer handle is ever created, so there is nothing left for the wall
// clock to race against, and nothing that can outlive a test's
// `unmount()` or the Jest environment itself. `flush()` no longer needs
// to *wait* on a real timer either — it only needs to let the mocked API
// call's promise chain (a real microtask, unaffected by fake timers)
// settle, so it now awaits a microtask tick directly.
const flush = async () => {
  await Promise.resolve();
  jest.advanceTimersByTime(0);
};

beforeEach(() => {
  // Scoped to setTimeout/clearTimeout only — that's the entire real-timer
  // surface the countdown effect uses. Leaving requestAnimationFrame real
  // matters: the shake-on-error tests below drive a real
  // `Animated.timing(..., { useNativeDriver: true }).start()`, whose
  // native-driver wiring (AnimatedProps.__makeNative -> RAF) breaks if RAF
  // is faked too — confirmed locally (faking every timer type turned 4
  // passing shake tests into "Unable to find node on an unmounted
  // component" failures).
  jest.useFakeTimers({
    doNotFake: [
      'nextTick', 'queueMicrotask',
      'requestAnimationFrame', 'cancelAnimationFrame',
      'requestIdleCallback', 'cancelIdleCallback',
      'setImmediate', 'clearImmediate',
      'setInterval', 'clearInterval',
      'Date', 'hrtime', 'performance',
    ],
  });
});

// Tracked so afterEach can unmount it: unmounting runs the countdown
// effect's cleanup (`clearTimeout`), which under fake timers just removes
// a bookkeeping entry from Jest's timer queue — never a real handle that
// could survive into a later test or keep the Jest environment alive past
// this file.
let mountedRenderer: TestRenderer.ReactTestRenderer | null = null;

async function renderScreen() {
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => {
    renderer = TestRenderer.create(<VerifyEmailScreen />);
    await flush();
    await flush();
  });
  mountedRenderer = renderer;
  return renderer;
}

function allText(renderer: TestRenderer.ReactTestRenderer): string {
  return JSON.stringify(renderer.toJSON());
}

function enterCode(renderer: TestRenderer.ReactTestRenderer, code: string) {
  const input = renderer.root.findByProps({ accessibilityLabel: 'Verification code' });
  act(() => {
    input.props.onChangeText(code);
  });
}

async function tapVerify(renderer: TestRenderer.ReactTestRenderer) {
  const btn = renderer.root.findByProps({ accessibilityLabel: 'Verify email' });
  await act(async () => {
    await btn.props.onPress();
    await flush();
  });
}

afterEach(() => {
  mountedRenderer?.unmount();
  mountedRenderer = null;
  // Restore real timers only after unmount's cleanup has run (still under
  // fake timers above), so `clearTimeout` in the countdown effect's
  // cleanup clears the actual fake-timer entry it scheduled, rather than
  // racing a timer-implementation swap mid-teardown.
  jest.useRealTimers();
});

beforeEach(() => {
  jest.clearAllMocks();
  useAuthStore.setState({ user: { ...mockDefaultUser } });
  mockApiPost.mockImplementation((url: string) => {
    if (url === '/users/verify-email/request') {
      return Promise.resolve({ data: { success: true, message: 'Verification code sent' } });
    }
    return Promise.resolve({ data: { success: true } });
  });
});

describe('VerifyEmailScreen — request on entry', () => {
  it('fires POST /users/verify-email/request on mount', async () => {
    await renderScreen();
    expect(mockApiPost).toHaveBeenCalledWith('/users/verify-email/request');
    // CR-4138: this test previously carried a widened 20000ms timeout as
    // a CR-4102/C31-follow-up band-aid for a flake under full-suite
    // parallelism (see the `flush`/fake-timers comment above for the real
    // root cause — a real 1s countdown timer racing `unmount()`). Fake
    // timers remove the wall-clock dependency entirely, so this reverts
    // to Jest's default per-test timeout; if this test is ever genuinely
    // slow again it should show up as an honest failure, not more slack.
  });

  it('short-circuits to the already-verified screen when the response says already_verified', async () => {
    mockApiPost.mockImplementation((url: string) => {
      if (url === '/users/verify-email/request') {
        return Promise.resolve({ data: { success: true, already_verified: true, message: 'Your email is already verified' } });
      }
      return Promise.resolve({ data: { success: true } });
    });
    const renderer = await renderScreen();
    expect(allText(renderer)).toContain('Email Verified');
    // Code-entry UI must not render once already verified.
    expect(() => renderer.root.findByProps({ accessibilityLabel: 'Verification code' })).toThrow();
    expect((useAuthStore.getState().user as VerifiableUser | null)?.email_verified).toBe(true);
  });

  it('surfaces PROFILE_EMAIL_MISSING with plain-English copy, not the raw backend message', async () => {
    mockApiPost.mockImplementation((url: string) => {
      if (url === '/users/verify-email/request') {
        return Promise.reject(
          apiError({
            message: 'Add an email to your profile before verifying it',
            messageKey: 'errors.profile.email_missing',
            code: 2001,
          }),
        );
      }
      return Promise.resolve({ data: {} });
    });
    await renderScreen();
    const [, message] = mockShowToast.mock.calls.find((c) => c[0] === 'Could Not Send Code') || [];
    expect(message).toBe('Add an email to your profile before verifying it.');
  });

  it('surfaces SYSTEM_SERVICE_UNAVAILABLE with the friendly i18n copy', async () => {
    mockApiPost.mockImplementation((url: string) => {
      if (url === '/users/verify-email/request') {
        return Promise.reject(
          apiError({
            message: 'Verification is temporarily unavailable, please try again later',
            messageKey: 'errors.system.service_unavailable',
            code: 9002,
          }),
        );
      }
      return Promise.resolve({ data: {} });
    });
    await renderScreen();
    const call = mockShowToast.mock.calls.find((c) => c[0] === 'Could Not Send Code');
    expect(call?.[1]).toBe('Spinr is temporarily unavailable. Please try again shortly.');
  });

  it('surfaces a rate-limited request as a "too many attempts" toast, not a generic failure', async () => {
    mockApiPost.mockImplementation((url: string) => {
      if (url === '/users/verify-email/request') {
        return Promise.reject(new MockRateLimitError('Too many verification requests — try again later', 3600));
      }
      return Promise.resolve({ data: {} });
    });
    const renderer = await renderScreen();
    const call = mockShowToast.mock.calls.find((c) => c[0] === 'Too Many Attempts');
    expect(call).toBeTruthy();
    expect(call?.[1]).toContain('Too many verification requests');
    // Resend must be disabled behind the countdown, not immediately retryable.
    expect(() => renderer.root.findByProps({ accessibilityLabel: 'Resend code' })).toThrow();
  });
});

describe('VerifyEmailScreen — confirm', () => {
  it('fires POST /users/verify-email/confirm with the entered code', async () => {
    const renderer = await renderScreen();
    enterCode(renderer, '1234');
    await tapVerify(renderer);
    expect(mockApiPost).toHaveBeenCalledWith('/users/verify-email/confirm', { code: '1234' });
  });

  it('on success, merges email_verified into the store and navigates back — no reload needed', async () => {
    mockApiPost.mockImplementation((url: string) => {
      if (url === '/users/verify-email/request') return Promise.resolve({ data: { success: true } });
      if (url === '/users/verify-email/confirm') return Promise.resolve({ data: { success: true, email_verified: true } });
      return Promise.resolve({ data: {} });
    });
    const renderer = await renderScreen();
    enterCode(renderer, '1234');
    await tapVerify(renderer);
    expect((useAuthStore.getState().user as VerifiableUser | null)?.email_verified).toBe(true);
    expect(mockBack).toHaveBeenCalled();
  });

  it('surfaces AUTH_OTP_INVALID as friendly copy, not the raw "ERR_OTP_INVALID" sentinel', async () => {
    mockApiPost.mockImplementation((url: string) => {
      if (url === '/users/verify-email/request') return Promise.resolve({ data: { success: true } });
      if (url === '/users/verify-email/confirm') {
        return Promise.reject(apiError({ message: 'ERR_OTP_INVALID', messageKey: 'errors.auth.otp_invalid', code: 1008 }));
      }
      return Promise.resolve({ data: {} });
    });
    const renderer = await renderScreen();
    enterCode(renderer, '9999');
    await tapVerify(renderer);
    const call = mockShowToast.mock.calls.find((c) => c[0] === 'Verification Failed');
    expect(call?.[1]).toBe("That code didn't match. Check the SMS and try again.");
    expect(call?.[1]).not.toContain('ERR_OTP_INVALID');
    expect((useAuthStore.getState().user as VerifiableUser | null)?.email_verified).toBeFalsy();
  });

  it('surfaces AUTH_OTP_EXPIRED as friendly copy, not the raw "ERR_OTP_EXPIRED" sentinel', async () => {
    mockApiPost.mockImplementation((url: string) => {
      if (url === '/users/verify-email/request') return Promise.resolve({ data: { success: true } });
      if (url === '/users/verify-email/confirm') {
        return Promise.reject(apiError({ message: 'ERR_OTP_EXPIRED', messageKey: 'errors.auth.otp_expired', code: 1007 }));
      }
      return Promise.resolve({ data: {} });
    });
    const renderer = await renderScreen();
    enterCode(renderer, '1234');
    await tapVerify(renderer);
    const call = mockShowToast.mock.calls.find((c) => c[0] === 'Verification Failed');
    expect(call?.[1]).toBe('That code has expired. Tap Resend to get a new one.');
    expect(call?.[1]).not.toContain('ERR_OTP_EXPIRED');
  });

  it('surfaces the OTP-lockout 429 as a "too many attempts" toast', async () => {
    mockApiPost.mockImplementation((url: string) => {
      if (url === '/users/verify-email/request') return Promise.resolve({ data: { success: true } });
      if (url === '/users/verify-email/confirm') {
        return Promise.reject(new MockRateLimitError('Too many failed attempts — try again later', 86400));
      }
      return Promise.resolve({ data: {} });
    });
    const renderer = await renderScreen();
    enterCode(renderer, '1234');
    await tapVerify(renderer);
    const call = mockShowToast.mock.calls.find((c) => c[0] === 'Too Many Attempts');
    expect(call?.[1]).toContain('Too many failed attempts');
    expect(mockBack).not.toHaveBeenCalled();
  });
});
