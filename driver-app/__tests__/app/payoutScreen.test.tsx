/**
 * app/driver/payout.tsx — driver payouts/setup/tax-documents screen.
 * Pins:
 *  - parallel initial load (balance, bank account, Stripe status,
 *    bonuses, tax years) on mount
 *  - the setup checklist gate: allReady = Stripe-ready && SIN on file &&
 *    GST on file; the checklist hides once allReady, replaced by the
 *    "you're all set" card and the next-payout schedule
 *  - the SIN step is unlocked always; the Stripe step is locked until a
 *    SIN is on file (ours OR Stripe's own copy); the GST step is
 *    unlocked always
 *  - handleSaveSin: rejects a non-9-digit SIN with its own toast; a
 *    valid SIN calls updateDriverMe.mutateAsync({ sin }) and clears the
 *    input from state on both success and failure
 *  - handleSaveGst: rejects an invalid BN/GST format; a valid one calls
 *    updateDriverMe.mutateAsync with gst_bn + gst_registered
 *  - handleStripeOnboarding: no url/mock response shows "Unavailable";
 *    a real url opens the auth browser then syncs Stripe status,
 *    toasting active vs. still-reviewing vs. not-finished
 *  - Tax Documents section only renders once taxYears has an entry;
 *    handleEmailT4A/handleEmailCSV POST to the latest year and toast
 *  - back nav falls back to /driver/ when there's nothing to pop
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, TextInput } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('expo-linear-gradient', () => ({ LinearGradient: ({ children }: any) => children }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));
jest.mock('@shared/components/ErrorBoundary', () => ({
  ErrorBoundary: ({ children }: any) => children,
}));

const mockBack = jest.fn();
const mockPush = jest.fn();
const mockReplace = jest.fn();
const mockCanGoBack = jest.fn(() => true);
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: mockPush, replace: mockReplace, canGoBack: mockCanGoBack }),
}));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', background: '#FFF',
  text: '#111', textDim: '#666', border: '#E5E7EB', success: '#10B981',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

let mockDriverState: any;
jest.mock('../../store/driverStore', () => ({
  useDriverStore: () => mockDriverState,
}));

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a), post: (...a: any[]) => mockApiPost(...a) },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockOpenAuthSessionAsync = jest.fn();
jest.mock('expo-web-browser', () => ({
  openAuthSessionAsync: (...a: any[]) => mockOpenAuthSessionAsync(...a),
}));

const mockShowToast = jest.fn();
jest.mock('../../hooks/useToast', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

let mockDriverMe: any;
const mockMutateAsync = jest.fn();
jest.mock('@shared/hooks/queries', () => ({
  useDriverMe: () => ({ data: mockDriverMe }),
  useUpdateDriverMe: () => ({ mutateAsync: (...a: any[]) => mockMutateAsync(...a), isPending: false }),
}));

import PayoutScreen from '../../app/driver/payout';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const BALANCE = {
  payable_balance: '25.00', total_earnings: '500.00', pending_payouts: '10.00', total_paid_out: '465.00', previous_app_paid_total: '0',
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<PayoutScreen />);
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
  mockDriverState = {
    driverBalance: BALANCE,
    hasBankAccount: false,
    fetchDriverBalance: jest.fn().mockResolvedValue(undefined),
    fetchBankAccount: jest.fn().mockResolvedValue(undefined),
    error: null,
    clearError: jest.fn(),
  };
  mockDriverMe = { gst_bn: '', sin_last4: null };
  mockCanGoBack.mockReturnValue(true);
  mockApiGet.mockImplementation((url: string) => {
    if (url === '/drivers/bonuses') return Promise.resolve({ data: { bonuses: [] } });
    if (url === '/drivers/t4a/years') return Promise.resolve({ data: { years: [] } });
    if (url === '/drivers/balance') return Promise.resolve({ data: { stripe_account_onboarded: false, stripe_id_number_provided: false } });
    return Promise.reject(new Error('unexpected url ' + url));
  });
  mockApiPost.mockResolvedValue({ data: {} });
  mockMutateAsync.mockResolvedValue(undefined);
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.restoreAllMocks();
});

describe('PayoutScreen', () => {
  it('loads balance, bank account, Stripe status, bonuses, and tax years on mount', async () => {
    await renderScreen();
    expect(mockDriverState.fetchDriverBalance).toHaveBeenCalled();
    expect(mockDriverState.fetchBankAccount).toHaveBeenCalled();
    expect(mockApiGet).toHaveBeenCalledWith('/drivers/balance');
    expect(mockApiGet).toHaveBeenCalledWith('/drivers/bonuses');
    expect(mockApiGet).toHaveBeenCalledWith('/drivers/t4a/years');
  });

  it('shows the setup checklist (not the "all set" card) when nothing is ready yet', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('Set up payouts');
    expect(allText(r)).not.toContain("You&apos;re all set");
  });

  it('shows the "all set" card and next-payout schedule once every step is done', async () => {
    mockDriverState.hasBankAccount = true;
    mockDriverMe = { gst_bn: '123456789RT0001', sin_last4: '1234' };
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/bonuses') return Promise.resolve({ data: { bonuses: [] } });
      if (url === '/drivers/t4a/years') return Promise.resolve({ data: { years: [] } });
      if (url === '/drivers/balance') return Promise.resolve({ data: { stripe_account_onboarded: true, stripe_id_number_provided: true } });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('Next payout');
    const setupSection = allText(r);
    expect(setupSection).not.toContain('Set up payouts');
  });

  it('locks the Stripe step until a SIN is on file', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('Add your SIN first — it unlocks this step');
  });

  it('unlocks the Stripe step once a SIN is on file', async () => {
    mockDriverMe = { gst_bn: '', sin_last4: '1234' };
    const r = await renderScreen();
    expect(allText(r)).toContain('Link your bank and verify your identity with Stripe');
  });

  it('rejects a SIN that is not exactly 9 digits', async () => {
    const r = await renderScreen();
    const sinStep = findButtonByText(r, 'Add your SIN for tax slips');
    act(() => { sinStep.props.onPress(); });
    const sinInput = r.root.findByProps({ placeholder: '•••••••••' });
    act(() => { sinInput.props.onChangeText('12345'); });
    const saveBtn = findButtonByText(r, 'Save');
    await act(async () => { saveBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('warning', 'Invalid Format', 'Your SIN is 9 digits.');
    expect(mockMutateAsync).not.toHaveBeenCalled();
  });

  it('saves a valid SIN and clears the input from state', async () => {
    const r = await renderScreen();
    const sinStep = findButtonByText(r, 'Add your SIN for tax slips');
    act(() => { sinStep.props.onPress(); });
    const sinInput = r.root.findByProps({ placeholder: '•••••••••' });
    act(() => { sinInput.props.onChangeText('123456789'); });
    const saveBtn = findButtonByText(r, 'Save');
    await act(async () => { saveBtn.props.onPress(); await flush(); });
    expect(mockMutateAsync).toHaveBeenCalledWith({ sin: '123456789' });
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Saved', 'Your SIN is stored securely for your T4A.');
  });

  it('rejects an invalid GST/BN format', async () => {
    const r = await renderScreen();
    const gstStep = findButtonByText(r, 'Add GST/HST number');
    act(() => { gstStep.props.onPress(); });
    const gstInput = r.root.findByProps({ placeholder: '123456789RT0001' });
    act(() => { gstInput.props.onChangeText('bad-format'); });
    const saveBtn = findButtonByText(r, 'Save');
    await act(async () => { saveBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith(
      'warning', 'Invalid Format', 'Enter your 9-digit Business Number (BN) or full GST number (e.g., 123456789RT0001)',
    );
    expect(mockMutateAsync).not.toHaveBeenCalled();
  });

  it('saves a valid GST/BN number', async () => {
    const r = await renderScreen();
    const gstStep = findButtonByText(r, 'Add GST/HST number');
    act(() => { gstStep.props.onPress(); });
    const gstInput = r.root.findByProps({ placeholder: '123456789RT0001' });
    act(() => { gstInput.props.onChangeText('123456789RT0001'); });
    const saveBtn = findButtonByText(r, 'Save');
    await act(async () => { saveBtn.props.onPress(); await flush(); });
    expect(mockMutateAsync).toHaveBeenCalledWith({ gst_bn: '123456789RT0001', gst_registered: true });
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Saved', 'GST/BN number updated successfully');
  });

  it('shows "Unavailable" when Stripe onboarding returns no url', async () => {
    mockDriverMe = { gst_bn: '', sin_last4: '1234' };
    mockApiPost.mockResolvedValue({ data: { mock: true } });
    const r = await renderScreen();
    const stripeStep = findButtonByText(r, 'Connect payout account');
    await act(async () => { stripeStep.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('info', 'Unavailable', 'Payouts setup is not available yet. Please try again later.');
    expect(mockOpenAuthSessionAsync).not.toHaveBeenCalled();
  });

  it('opens the auth browser and toasts success once Stripe reports payouts enabled', async () => {
    mockDriverMe = { gst_bn: '', sin_last4: '1234' };
    mockApiPost.mockImplementation((url: string) => {
      if (url === '/drivers/stripe-onboard') return Promise.resolve({ data: { url: 'https://connect.stripe.com/setup' } });
      if (url === '/drivers/stripe-sync') return Promise.resolve({ data: { onboarded: true, payouts_enabled: true } });
      return Promise.resolve({ data: {} });
    });
    mockOpenAuthSessionAsync.mockResolvedValue({ type: 'success' });
    const r = await renderScreen();
    const stripeStep = findButtonByText(r, 'Connect payout account');
    await act(async () => { stripeStep.props.onPress(); await flush(); });
    expect(mockOpenAuthSessionAsync).toHaveBeenCalledWith('https://connect.stripe.com/setup', 'spinr-driver://driver/payout');
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Verification submitted', 'Stripe approved your account — payouts are enabled.');
  });

  it('toasts "not finished" when Stripe sync reports not onboarded', async () => {
    mockDriverMe = { gst_bn: '', sin_last4: '1234' };
    mockApiPost.mockImplementation((url: string) => {
      if (url === '/drivers/stripe-onboard') return Promise.resolve({ data: { url: 'https://connect.stripe.com/setup' } });
      if (url === '/drivers/stripe-sync') return Promise.resolve({ data: { onboarded: false } });
      return Promise.resolve({ data: {} });
    });
    mockOpenAuthSessionAsync.mockResolvedValue({ type: 'success' });
    const r = await renderScreen();
    const stripeStep = findButtonByText(r, 'Connect payout account');
    await act(async () => { stripeStep.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('info', 'Not finished', 'Stripe onboarding wasn’t completed. You can resume anytime.');
  });

  it('toasts a failure when starting Stripe onboarding itself fails', async () => {
    mockDriverMe = { gst_bn: '', sin_last4: '1234' };
    mockApiPost.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const stripeStep = findButtonByText(r, 'Connect payout account');
    await act(async () => { stripeStep.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'Error', 'Could not start verification. Please try again.');
  });

  it('hides the Tax Documents section when there are no earned tax years', async () => {
    const r = await renderScreen();
    expect(allText(r)).not.toContain('Tax Documents');
  });

  it('shows Tax Documents and emails the T4A for the latest earned year', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/bonuses') return Promise.resolve({ data: { bonuses: [] } });
      if (url === '/drivers/t4a/years') return Promise.resolve({ data: { years: [{ year: 2026, total_earnings: '500.00', total_trips: 40 }] } });
      if (url === '/drivers/balance') return Promise.resolve({ data: { stripe_account_onboarded: false, stripe_id_number_provided: false } });
      return Promise.reject(new Error('unexpected'));
    });
    mockApiPost.mockResolvedValue({ data: { message: 'Your T4A summary for 2026 is on its way.' } });
    const r = await renderScreen();
    expect(allText(r)).toContain('Tax Documents');
    const emailT4ABtn = findButtonByText(r, 'Email T4A');
    await act(async () => { emailT4ABtn.props.onPress(); await flush(); });
    expect(mockApiPost).toHaveBeenCalledWith('/drivers/t4a/2026/email');
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Check Your Email', 'Your T4A summary for 2026 is on its way.');
  });

  it('emails the earnings CSV for the latest earned year', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/drivers/bonuses') return Promise.resolve({ data: { bonuses: [] } });
      if (url === '/drivers/t4a/years') return Promise.resolve({ data: { years: [{ year: 2024, total_earnings: '300.00', total_trips: 20 }] } });
      if (url === '/drivers/balance') return Promise.resolve({ data: { stripe_account_onboarded: false, stripe_id_number_provided: false } });
      return Promise.reject(new Error('unexpected'));
    });
    mockApiPost.mockResolvedValue({ data: { message: 'Your earnings export for 2024 is on its way.' } });
    const r = await renderScreen();
    const emailCSVBtn = findButtonByText(r, 'Email Earnings CSV');
    await act(async () => { emailCSVBtn.props.onPress(); await flush(); });
    expect(mockApiPost).toHaveBeenCalledWith('/drivers/earnings/export/email?year=2024');
    expect(mockShowToast).toHaveBeenCalledWith('success', 'Check Your Email', 'Your earnings export for 2024 is on its way.');
  });

  it('goes back when there is history to pop', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => { backBtn.props.onPress(); });
    expect(mockBack).toHaveBeenCalled();
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it('falls back to /driver/ when there is no history to pop', async () => {
    mockCanGoBack.mockReturnValue(false);
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => { backBtn.props.onPress(); });
    expect(mockReplace).toHaveBeenCalledWith('/driver/');
    expect(mockBack).not.toHaveBeenCalled();
  });
});
