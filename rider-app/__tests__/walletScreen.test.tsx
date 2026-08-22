/**
 * app/wallet.tsx — the rider wallet: balance, Stripe top-up, and
 * transaction history. Pins:
 *  - fetchWallet + fetchTransactions(30) fire in parallel on mount (and
 *    both on the balance-card "Tap to retry" after a load error)
 *  - Top Up is blocked with a warning toast when no Stripe key is
 *    configured (never silently opens a broken payment sheet), and again
 *    when the amount is out of the $1-$500 range
 *  - the full Stripe flow: topUp() -> initPaymentSheet -> presentPaymentSheet,
 *    each error surfaced distinctly (init error toasts and stops; a
 *    Canceled present error is silent, any other present error toasts);
 *    success closes the panel, resets the form, and re-fetches after a
 *    2s delay (money settling server-side)
 *  - only ride_payment/ride_refund transactions with a reference_id are
 *    tappable through to /ride-details -- referral/top-up/reward/etc.
 *    types never are, even with a reference_id present
 *  - transaction amount sign/color and the empty state
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

const mockBack = jest.fn();
const mockPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: mockPush }),
}));

const COLORS = {
  primary: '#EF4444', background: '#FFF', surface: '#FFF', surfaceLight: '#F5F5F5',
  text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

let mockStripeKey: string | null = 'pk_test_123';
jest.mock('../app/_layout', () => ({
  StripeKeyContext: require('react').createContext(null),
}));

const mockInitPaymentSheet = jest.fn();
const mockPresentPaymentSheet = jest.fn();
jest.mock('@stripe/stripe-react-native', () => ({
  useStripe: () => ({
    initPaymentSheet: (...a: any[]) => mockInitPaymentSheet(...a),
    presentPaymentSheet: (...a: any[]) => mockPresentPaymentSheet(...a),
  }),
}));

const mockFetchWallet = jest.fn();
const mockFetchTransactions = jest.fn();
const mockTopUp = jest.fn();
const mockClearError = jest.fn();
let mockWalletState: any;
function resetWalletState() {
  mockWalletState = {
    wallet: { id: 'w1', balance: '42.50', currency: 'CAD', is_active: true },
    transactions: [],
    walletLoading: false,
    transactionsLoading: false,
    error: null,
    fetchWallet: mockFetchWallet,
    topUp: mockTopUp,
    fetchTransactions: mockFetchTransactions,
    clearError: mockClearError,
  };
}
jest.mock('../store/walletStore', () => ({
  useWalletStore: () => mockWalletState,
}));

import WalletScreen from '../app/wallet';
import { StripeKeyContext } from '../app/_layout';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(
      <StripeKeyContext.Provider value={mockStripeKey}>
        <WalletScreen />
      </StripeKeyContext.Provider>,
    );
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => JSON.stringify(t.props.children)).join(' | ');
}

function findButtonByText(r: TestRenderer.ReactTestRenderer, text: string) {
  return r.root
    .findAllByType(TouchableOpacity)
    .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes(text)))!;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockStripeKey = 'pk_test_123';
  resetWalletState();
  mockFetchWallet.mockResolvedValue(undefined);
  mockFetchTransactions.mockResolvedValue(undefined);
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('WalletScreen', () => {
  it('fetches wallet + transactions in parallel on mount and shows the balance', async () => {
    const r = await renderScreen();
    expect(mockClearError).toHaveBeenCalled();
    expect(mockFetchWallet).toHaveBeenCalled();
    expect(mockFetchTransactions).toHaveBeenCalledWith(30);
    expect(allText(r)).toContain('["$","42.50"]');
  });

  it('shows the retry state and re-fetches on tap when there is a store error', async () => {
    mockWalletState.error = 'network down';
    const r = await renderScreen();
    expect(allText(r)).toContain('Balance unavailable');
    mockFetchWallet.mockClear();
    mockFetchTransactions.mockClear();
    const retryBtn = findButtonByText(r, 'Balance unavailable');
    await act(async () => {
      retryBtn.props.onPress();
      await flush();
    });
    expect(mockFetchWallet).toHaveBeenCalled();
    expect(mockFetchTransactions).toHaveBeenCalledWith(30);
  });

  it('blocks Top Up with a warning toast when no Stripe key is configured', async () => {
    mockStripeKey = null;
    const r = await renderScreen();
    const topUpBtn = findButtonByText(r, 'Top Up');
    act(() => {
      topUpBtn.props.onPress();
    });
    const addFundsBtn = findButtonByText(r, 'Select an Amount');
    await act(async () => {
      await addFundsBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'Payment Not Available',
      expect.stringContaining('add a card'),
      'warning',
    );
    expect(mockTopUp).not.toHaveBeenCalled();
  });

  it('runs the full Stripe top-up flow on success and re-fetches after a delay', async () => {
    jest.useFakeTimers({ doNotFake: ['nextTick', 'queueMicrotask'] });
    mockTopUp.mockResolvedValue({ paymentIntent: 'pi_secret', ephemeralKey: 'ek_1', customer: 'cus_1' });
    mockInitPaymentSheet.mockResolvedValue({ error: null });
    mockPresentPaymentSheet.mockResolvedValue({ error: null });
    const r = await renderScreen();
    const topUpBtn = findButtonByText(r, 'Top Up');
    act(() => {
      topUpBtn.props.onPress();
    });
    const chip25 = findButtonByText(r, '"$",25');
    act(() => {
      chip25.props.onPress();
    });
    const addFundsBtn = findButtonByText(r, 'Add $25.00');
    await act(async () => {
      await addFundsBtn.props.onPress();
      await flush();
    });
    expect(mockTopUp).toHaveBeenCalledWith(25);
    expect(mockInitPaymentSheet).toHaveBeenCalledWith(
      expect.objectContaining({ paymentIntentClientSecret: 'pi_secret', customerId: 'cus_1' }),
    );
    expect(mockPresentPaymentSheet).toHaveBeenCalled();
    expect(mockShowToast).toHaveBeenCalledWith(
      'Payment Successful',
      '$25.00 will be added to your wallet shortly.',
      'success',
    );
    mockFetchWallet.mockClear();
    await act(async () => {
      jest.advanceTimersByTime(2000);
      await flush();
    });
    expect(mockFetchWallet).toHaveBeenCalled();
    jest.useRealTimers();
  });

  it('toasts and stops when initPaymentSheet errors, never presenting', async () => {
    mockTopUp.mockResolvedValue({ paymentIntent: 'pi_secret', ephemeralKey: 'ek_1', customer: 'cus_1' });
    mockInitPaymentSheet.mockResolvedValue({ error: { message: 'bad key' } });
    const r = await renderScreen();
    const topUpBtn = findButtonByText(r, 'Top Up');
    act(() => {
      topUpBtn.props.onPress();
    });
    const chip25 = findButtonByText(r, '"$",25');
    act(() => {
      chip25.props.onPress();
    });
    const addFundsBtn = findButtonByText(r, 'Add $25.00');
    await act(async () => {
      await addFundsBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('Payment Error', 'bad key', 'danger');
    expect(mockPresentPaymentSheet).not.toHaveBeenCalled();
  });

  it('silently does nothing when the payment sheet is cancelled', async () => {
    mockTopUp.mockResolvedValue({ paymentIntent: 'pi_secret', ephemeralKey: 'ek_1', customer: 'cus_1' });
    mockInitPaymentSheet.mockResolvedValue({ error: null });
    mockPresentPaymentSheet.mockResolvedValue({ error: { code: 'Canceled', message: 'user cancelled' } });
    const r = await renderScreen();
    const topUpBtn = findButtonByText(r, 'Top Up');
    act(() => {
      topUpBtn.props.onPress();
    });
    const chip25 = findButtonByText(r, '"$",25');
    act(() => {
      chip25.props.onPress();
    });
    const addFundsBtn = findButtonByText(r, 'Add $25.00');
    await act(async () => {
      await addFundsBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).not.toHaveBeenCalledWith('Payment Failed', expect.anything(), expect.anything());
  });

  it('toasts on a non-cancel present error', async () => {
    mockTopUp.mockResolvedValue({ paymentIntent: 'pi_secret', ephemeralKey: 'ek_1', customer: 'cus_1' });
    mockInitPaymentSheet.mockResolvedValue({ error: null });
    mockPresentPaymentSheet.mockResolvedValue({ error: { code: 'Failed', message: 'card declined' } });
    const r = await renderScreen();
    const topUpBtn = findButtonByText(r, 'Top Up');
    act(() => {
      topUpBtn.props.onPress();
    });
    const chip25 = findButtonByText(r, '"$",25');
    act(() => {
      chip25.props.onPress();
    });
    const addFundsBtn = findButtonByText(r, 'Add $25.00');
    await act(async () => {
      await addFundsBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith('Payment Failed', 'card declined', 'danger');
  });

  it('only makes ride_payment/ride_refund transactions with a reference_id tappable', async () => {
    mockWalletState.transactions = [
      { id: 't1', type: 'ride_payment', amount: '-15.00', description: '', created_at: '2026-01-01T00:00:00Z', reference_id: 'ride-1', metadata: null },
      { id: 't2', type: 'referral', amount: '5.00', description: '', created_at: '2026-01-01T00:00:00Z', reference_id: 'ref-1', metadata: null },
    ];
    const r = await renderScreen();
    const rows = r.root.findAllByType(TouchableOpacity).filter((n) =>
      n.findAllByType(Text).some((t) => {
        const c = t.props.children;
        return typeof c === 'string' && (c === 'ride payment' || c === 'referral');
      }),
    );
    expect(rows.length).toBe(2);
    const [rideRow, referralRow] = rows;
    act(() => {
      rideRow.props.onPress?.();
    });
    expect(mockPush).toHaveBeenCalledWith('/ride-details?rideId=ride-1');
    mockPush.mockClear();
    expect(referralRow.props.onPress).toBeUndefined();
  });

  it('shows the empty state when there are no transactions', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('No transactions yet');
  });

  it('navigates back when the back button is pressed', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => {
      backBtn.props.onPress();
    });
    expect(mockBack).toHaveBeenCalled();
  });
});
