/**
 * app/subscription/success.tsx — Stripe Checkout success landing screen
 * (a defense-in-depth deep-link target for
 * `spinr-driver://subscription/success?session_id=`). Pins:
 *  - no session_id -> skips verification, bounces back after the delay
 *  - a verified 'active' session shows the success toast
 *  - a non-active status shows the "processing" toast (payment still
 *    settling, not an error)
 *  - a verify-session failure never strands the driver -- same
 *    "processing" toast, still redirects (the webhook activates the pass
 *    server-side regardless)
 *  - always redirects to /driver/subscription via replace (not push), so
 *    back can't return to this transient screen
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';

import SubscriptionSuccessScreen from '../../app/subscription/success';

// A stable router object -- a fresh literal on every call would change the
// effect's [router] dependency identity on every re-render (e.g. the
// verifying->false state update), retriggering the effect's cleanup
// (clearing the just-scheduled redirect timer) and rescheduling it, which
// silently swallows the redirect under fake timers.
const mockReplace = jest.fn();
const mockRouter = { replace: mockReplace };
let mockParams: Record<string, string | undefined> = {};
jest.mock('expo-router', () => ({
  useRouter: () => mockRouter,
  useLocalSearchParams: () => mockParams,
}));

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));

const COLORS = { primary: '#EF4444', surface: '#FFF', text: '#111', textDim: '#666' };
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
}));

const mockShowToast = jest.fn();
jest.mock('../../hooks/useToast', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<SubscriptionSuccessScreen />);
    await flush();
  });
  return renderer!;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockParams = {};
  jest.useFakeTimers({ doNotFake: ['nextTick', 'queueMicrotask'] });
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.useRealTimers();
});

describe('SubscriptionSuccessScreen', () => {
  it('skips verification and bounces back when there is no session_id', async () => {
    await renderScreen();
    expect(mockApiGet).not.toHaveBeenCalled();
    await act(async () => {
      jest.runAllTimers();
      await flush();
    });
    expect(mockReplace).toHaveBeenCalledWith('/driver/subscription');
  });

  it('shows the success toast for a verified active session', async () => {
    mockParams = { session_id: 'cs_123' };
    mockApiGet.mockResolvedValue({ data: { status: 'active' } });
    await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/drivers/subscription/verify-session?session_id=cs_123');
    expect(mockShowToast).toHaveBeenCalledWith(
      'success',
      'Payment Successful!',
      'Your Spinr Pass is now active. Go online and start earning!',
    );
  });

  it('shows the processing toast for a non-active status', async () => {
    mockParams = { session_id: 'cs_123' };
    mockApiGet.mockResolvedValue({ data: { status: 'pending' } });
    await renderScreen();
    expect(mockShowToast).toHaveBeenCalledWith(
      'info',
      'Processing...',
      'Your payment is being confirmed. This may take a moment.',
    );
  });

  it('shows the processing toast (never strands the driver) when verify-session fails', async () => {
    mockParams = { session_id: 'cs_123' };
    mockApiGet.mockRejectedValue(new Error('network down'));
    await renderScreen();
    expect(mockShowToast).toHaveBeenCalledWith(
      'info',
      'Processing...',
      'Your payment is being confirmed. This may take a moment.',
    );
    await act(async () => {
      await flush();
      jest.advanceTimersByTime(1200);
      await flush();
    });
    expect(mockReplace).toHaveBeenCalledWith('/driver/subscription');
  });

  it('unwraps an array session_id param to its first value', async () => {
    mockParams = { session_id: ['cs_abc', 'cs_extra'] as any };
    mockApiGet.mockResolvedValue({ data: { status: 'active' } });
    await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/drivers/subscription/verify-session?session_id=cs_abc');
  });
});
