/**
 * app/driver/subscription.tsx — Spinr Pass subscription screen. Pins:
 *  - loads plans + current subscription + first payment page in
 *    parallel on mount; a free_mode response shows the free-mode
 *    celebration card instead of plan cards
 *  - handleSubscribe: an Alert confirm names Subscribe vs. Switch Plan
 *    depending on whether the driver already has a subscription, then
 *    doSubscribe() posts to /drivers/subscription/subscribe
 *  - doSubscribe: a checkout_url opens the in-app auth browser; a
 *    successful return with a session_id verifies it and toasts active
 *    vs. still-processing; no checkout_url (dev/test mode) activates
 *    immediately; any failure toasts
 *  - handleCancel: Alert confirm -> POST /cancel -> toast + reload
 *  - resendInvoice: POST /resend-invoice, success and failure toasts
 *  - loadMorePayments: fetches the next page and appends; a "Load more"
 *    link only appears while more payments remain
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, Alert } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

jest.mock('../../components/ScreenHeader', () => ({
  ScreenHeader: ({ children, rightAction }: any) => (
    <>
      {rightAction}
      {children}
    </>
  ),
}));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', textSecondary: '#333', border: '#E5E7EB',
  success: '#10B981', successBg: '#ECFDF5',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockCreateURL = jest.fn((..._a: unknown[]) => 'spinr-driver://subscription/success');
jest.mock('expo-linking', () => ({ createURL: (...a: any[]) => mockCreateURL(...a) }));

const mockOpenAuthSessionAsync = jest.fn();
jest.mock('expo-web-browser', () => ({
  openAuthSessionAsync: (...a: any[]) => mockOpenAuthSessionAsync(...a),
}));

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a), post: (...a: any[]) => mockApiPost(...a) },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockShowToast = jest.fn();
jest.mock('../../hooks/useToast', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

import SubscriptionScreen from '../../app/driver/subscription';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const PLAN_A = { id: 'plan-a', name: 'Daily Pass', price: 12, duration_days: 1, rides_per_day: 10, description: '', features: [] };
const PLAN_B = { id: 'plan-b', name: 'Weekly Pass', price: 40, duration_days: 7, rides_per_day: -1, description: '', features: ['Priority support'] };
const NO_SUB = { has_subscription: false };
const PAYMENT = {
  id: 'pay-1', plan_name: 'Daily Pass', amount: '12.00', subtotal: '10.71', gst_amount: '0.54', pst_amount: '0.75', hst_amount: '0.00',
  province: 'SK', currency: 'CAD', billing_reason: 'subscription_cycle', created_at: '2026-08-01T10:00:00Z',
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<SubscriptionScreen />);
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

beforeEach(() => {
  jest.clearAllMocks();
  mockApiGet.mockImplementation((url: string) => {
    if (url === '/drivers/subscription/plans') return Promise.resolve({ data: { plans: [PLAN_A, PLAN_B], free_mode: false, message: '' } });
    if (url === '/drivers/subscription/current') return Promise.resolve({ data: NO_SUB });
    if (url.startsWith('/drivers/subscription/payments')) return Promise.resolve({ data: { payments: [], total: 0 } });
    return Promise.reject(new Error('unexpected url ' + url));
  });
  mockApiPost.mockResolvedValue({ data: {} });
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.restoreAllMocks();
});

describe('SubscriptionScreen', () => {
  it('loads plans, current subscription, and payments in parallel on mount', async () => {
    const r = await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/drivers/subscription/plans');
    expect(mockApiGet).toHaveBeenCalledWith('/drivers/subscription/current');
    expect(mockApiGet).toHaveBeenCalledWith('/drivers/subscription/payments?limit=10&offset=0');
    expect(allText(r)).toContain('Daily Pass');
    expect(allText(r)).toContain('Weekly Pass');
  });

  it('shows the free-mode celebration card instead of plan cards when free_mode is true', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/subscription/plans') return Promise.resolve({ data: { plans: [], free_mode: true, message: 'Enjoy free rides this month!' } });
      if (url === '/drivers/subscription/current') return Promise.resolve({ data: NO_SUB });
      if (url.startsWith('/drivers/subscription/payments')) return Promise.resolve({ data: { payments: [], total: 0 } });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain("It's Free Right Now!");
    expect(allText(r)).toContain('Enjoy free rides this month!');
  });

  it('confirms via "Subscribe" (not "Switch Plan") when there is no current subscription', async () => {
    const r = await renderScreen();
    const subscribeBtn = findButtonByText(r, 'Subscribe');
    act(() => { subscribeBtn.props.onPress(); });
    expect(Alert.alert).toHaveBeenCalledWith(
      'Subscribe',
      expect.stringContaining('Subscribe to "Daily Pass" for $12.00/1 day?'),
      expect.any(Array),
    );
  });

  it('confirms via "Switch Plan?" when the driver already has a subscription', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/subscription/plans') return Promise.resolve({ data: { plans: [PLAN_A, PLAN_B], free_mode: false, message: '' } });
      if (url === '/drivers/subscription/current') {
        return Promise.resolve({ data: { has_subscription: true, subscription: { plan_name: 'Weekly Pass', plan_id: 'plan-b', price: 40, expires_at: '2026-09-01T00:00:00Z' }, rides_remaining: 'unlimited', today_rides: 3 } });
      }
      if (url.startsWith('/drivers/subscription/payments')) return Promise.resolve({ data: { payments: [], total: 0 } });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    const switchBtn = findButtonByText(r, 'Switch to this plan');
    act(() => { switchBtn.props.onPress(); });
    expect(Alert.alert).toHaveBeenCalledWith(
      'Switch Plan?',
      expect.stringContaining('You currently have "Weekly Pass"'),
      expect.any(Array),
    );
  });

  it('opens the checkout browser, verifies the session, and toasts active on success', async () => {
    mockApiPost.mockResolvedValue({ data: { checkout_url: 'https://checkout.stripe.com/session-1' } });
    mockOpenAuthSessionAsync.mockResolvedValue({ type: 'success', url: 'spinr-driver://subscription/success?session_id=sess_123' });
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/subscription/plans') return Promise.resolve({ data: { plans: [PLAN_A, PLAN_B], free_mode: false, message: '' } });
      if (url === '/drivers/subscription/current') return Promise.resolve({ data: NO_SUB });
      if (url.startsWith('/drivers/subscription/payments')) return Promise.resolve({ data: { payments: [], total: 0 } });
      if (url.startsWith('/drivers/subscription/verify-session')) return Promise.resolve({ data: { status: 'active' } });
      return Promise.reject(new Error('unexpected url ' + url));
    });
    const r = await renderScreen();
    const subscribeBtn = findButtonByText(r, 'Subscribe');
    act(() => { subscribeBtn.props.onPress(); });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const subscribeAction = alertCall[2].find((b: any) => b.text === 'Subscribe');
    await act(async () => { await subscribeAction.onPress(); await flush(); });
    expect(mockApiPost).toHaveBeenCalledWith('/drivers/subscription/subscribe', { plan_id: 'plan-a', success_url: 'spinr-driver://subscription/success' });
    expect(mockOpenAuthSessionAsync).toHaveBeenCalledWith('https://checkout.stripe.com/session-1', 'spinr-driver://subscription/success');
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Subscribed!', "You're now on the Daily Pass plan. Go online and start earning!");
  });

  it('toasts "Processing..." when the session does not verify as active', async () => {
    mockApiPost.mockResolvedValue({ data: { checkout_url: 'https://checkout.stripe.com/session-1' } });
    mockOpenAuthSessionAsync.mockResolvedValue({ type: 'success', url: 'spinr-driver://subscription/success?session_id=sess_123' });
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/subscription/plans') return Promise.resolve({ data: { plans: [PLAN_A, PLAN_B], free_mode: false, message: '' } });
      if (url === '/drivers/subscription/current') return Promise.resolve({ data: NO_SUB });
      if (url.startsWith('/drivers/subscription/payments')) return Promise.resolve({ data: { payments: [], total: 0 } });
      if (url.startsWith('/drivers/subscription/verify-session')) return Promise.resolve({ data: { status: 'pending' } });
      return Promise.reject(new Error('unexpected url ' + url));
    });
    const r = await renderScreen();
    const subscribeBtn = findButtonByText(r, 'Subscribe');
    act(() => { subscribeBtn.props.onPress(); });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const subscribeAction = alertCall[2].find((b: any) => b.text === 'Subscribe');
    await act(async () => { await subscribeAction.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('info', 'Processing...', 'Your payment is being confirmed. This may take a moment.');
  });

  it('activates immediately (dev/test mode) when no checkout_url is returned', async () => {
    mockApiPost.mockResolvedValue({ data: {} });
    const r = await renderScreen();
    const subscribeBtn = findButtonByText(r, 'Subscribe');
    act(() => { subscribeBtn.props.onPress(); });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const subscribeAction = alertCall[2].find((b: any) => b.text === 'Subscribe');
    await act(async () => { await subscribeAction.onPress(); await flush(); });
    expect(mockOpenAuthSessionAsync).not.toHaveBeenCalled();
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Subscribed!', "You're now on the Daily Pass plan. Go online and start earning!");
  });

  it('toasts a failure when the subscribe request itself fails', async () => {
    mockApiPost.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const subscribeBtn = findButtonByText(r, 'Subscribe');
    act(() => { subscribeBtn.props.onPress(); });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const subscribeAction = alertCall[2].find((b: any) => b.text === 'Subscribe');
    await act(async () => { await subscribeAction.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Error', 'Could not subscribe. Please try again.');
  });

  it('cancels the subscription via Alert confirm, then toasts and reloads', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/subscription/plans') return Promise.resolve({ data: { plans: [PLAN_A, PLAN_B], free_mode: false, message: '' } });
      if (url === '/drivers/subscription/current') {
        return Promise.resolve({ data: { has_subscription: true, subscription: { plan_name: 'Weekly Pass', plan_id: 'plan-b', price: 40, expires_at: '2026-09-01T00:00:00Z' }, rides_remaining: 'unlimited', today_rides: 3 } });
      }
      if (url.startsWith('/drivers/subscription/payments')) return Promise.resolve({ data: { payments: [], total: 0 } });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    const cancelLink = findButtonByText(r, 'Cancel subscription');
    act(() => { cancelLink.props.onPress(); });
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const cancelAction = alertCall[2].find((b: any) => b.text === 'Cancel Plan');
    await act(async () => { await cancelAction.onPress(); await flush(); });
    expect(mockApiPost).toHaveBeenCalledWith('/drivers/subscription/cancel');
    expect(mockShowToast).toHaveBeenCalledWith('info', 'Cancelled', 'Your subscription has been cancelled.');
  });

  it('resends an invoice and toasts success', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/subscription/plans') return Promise.resolve({ data: { plans: [], free_mode: false, message: '' } });
      if (url === '/drivers/subscription/current') return Promise.resolve({ data: NO_SUB });
      if (url.startsWith('/drivers/subscription/payments')) return Promise.resolve({ data: { payments: [PAYMENT], total: 1 } });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    const sendInvoiceBtn = findButtonByText(r, 'Send Invoice');
    await act(async () => { await sendInvoiceBtn.props.onPress(); await flush(); });
    expect(mockApiPost).toHaveBeenCalledWith('/drivers/subscription/payments/pay-1/resend-invoice');
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Invoice Sent', 'Invoice emailed to your address on file.');
  });

  it('toasts a failure when resending the invoice fails', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/subscription/plans') return Promise.resolve({ data: { plans: [], free_mode: false, message: '' } });
      if (url === '/drivers/subscription/current') return Promise.resolve({ data: NO_SUB });
      if (url.startsWith('/drivers/subscription/payments')) return Promise.resolve({ data: { payments: [PAYMENT], total: 1 } });
      return Promise.reject(new Error('unexpected'));
    });
    mockApiPost.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const sendInvoiceBtn = findButtonByText(r, 'Send Invoice');
    await act(async () => { await sendInvoiceBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Error', 'Could not send the invoice. Please try again.');
  });

  it('shows a "Load more" link only while more payments remain, and loads the next page', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/subscription/plans') return Promise.resolve({ data: { plans: [], free_mode: false, message: '' } });
      if (url === '/drivers/subscription/current') return Promise.resolve({ data: NO_SUB });
      if (url === '/drivers/subscription/payments?limit=10&offset=0') return Promise.resolve({ data: { payments: [PAYMENT], total: 2 } });
      if (url === '/drivers/subscription/payments?limit=10&offset=1') return Promise.resolve({ data: { payments: [{ ...PAYMENT, id: 'pay-2' }], total: 2 } });
      return Promise.reject(new Error('unexpected url ' + url));
    });
    const r = await renderScreen();
    const loadMoreBtn = findButtonByText(r, 'Load more');
    expect(loadMoreBtn).toBeTruthy();
    await act(async () => { await loadMoreBtn.props.onPress(); await flush(); });
    expect(mockApiGet).toHaveBeenCalledWith('/drivers/subscription/payments?limit=10&offset=1');
    expect(findButtonByText(r, 'Load more')).toBeUndefined();
  });
});
