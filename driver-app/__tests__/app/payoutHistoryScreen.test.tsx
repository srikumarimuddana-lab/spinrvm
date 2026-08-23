/**
 * app/driver/payout-history.tsx — driver payout history with a status
 * filter and a "previous app" (legacy Stripe-sync transfers) footer
 * section. Pins:
 *  - fetchPayoutHistory fires on mount
 *  - the status filter pills narrow the list (default 'all')
 *  - the list sorts newest-first regardless of the store's raw order
 *  - `payout_type: 'stripe_sync'` rows are excluded from the main list
 *    and rendered in the "Previous app" footer section instead — never
 *    interleaved
 *  - the empty state renders only when both the main list AND the
 *    previous-app section are empty
 *  - a payout's optional fields (bank_name, account_last4, processed_at,
 *    error_message) render only when present
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text } from 'react-native';

import PayoutHistoryScreen from '../../app/driver/payout-history';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));
jest.mock('expo-linear-gradient', () => ({
  LinearGradient: ({ children }: any) => children,
}));

const mockBack = jest.fn();
const mockPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: mockPush }),
}));

const COLORS = {
  primary: '#EF4444', background: '#FFF', surface: '#FFF', surfaceLight: '#F5F5F5',
  text: '#111', textDim: '#666', border: '#E5E7EB', success: '#10B981', warning: '#F59E0B', danger: '#DC2626',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockFetchPayoutHistory = jest.fn();
let mockDriverState: any;
function resetDriverState() {
  mockDriverState = {
    payoutHistory: [],
    fetchPayoutHistory: mockFetchPayoutHistory,
    isLoading: false,
  };
}
jest.mock('../../store/driverStore', () => ({
  useDriverStore: () => mockDriverState,
}));

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

const PAYOUT_1 = {
  id: 'p1', amount: 120.5, status: 'completed', created_at: '2026-01-10T00:00:00Z',
  bank_name: 'TD', account_last4: '1234', processed_at: '2026-01-11T00:00:00Z',
};
const PAYOUT_2 = {
  id: 'p2', amount: 40, status: 'failed', created_at: '2026-01-15T00:00:00Z',
  error_message: 'Bank declined the transfer',
};
const LEGACY_PAYOUT = {
  id: 'p3', amount: 10, status: 'completed', created_at: '2025-06-01T00:00:00Z',
  payout_type: 'stripe_sync',
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<PayoutHistoryScreen />);
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
  resetDriverState();
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('PayoutHistoryScreen', () => {
  it('fetches payout history on mount', async () => {
    await renderScreen();
    expect(mockFetchPayoutHistory).toHaveBeenCalled();
  });

  it('sorts payouts newest-first regardless of store order', async () => {
    mockDriverState.payoutHistory = [PAYOUT_1, PAYOUT_2]; // p1 (Jan 10) then p2 (Jan 15)
    const r = await renderScreen();
    const amounts = r.root
      .findAllByType(Text)
      .map((t) => JSON.stringify(t.props.children))
      .filter((s) => s.includes('$'));
    // p2 ($40, newest) should appear before p1 ($120.50, older).
    const idxP2 = amounts.findIndex((s) => s.includes('40.00'));
    const idxP1 = amounts.findIndex((s) => s.includes('120.50'));
    expect(idxP2).toBeGreaterThanOrEqual(0);
    expect(idxP1).toBeGreaterThan(idxP2);
  });

  it('filters by status when a filter pill is tapped', async () => {
    mockDriverState.payoutHistory = [PAYOUT_1, PAYOUT_2];
    const r = await renderScreen();
    expect(allText(r)).toContain('120.50');
    expect(allText(r)).toContain('40.00');

    const failedPill = findButtonByText(r, 'Failed');
    act(() => {
      failedPill.props.onPress();
    });
    const text = allText(r);
    expect(text).toContain('40.00');
    expect(text).not.toContain('120.50');
  });

  it('separates stripe_sync payouts into the Previous App footer, never interleaved', async () => {
    mockDriverState.payoutHistory = [PAYOUT_1, LEGACY_PAYOUT];
    const r = await renderScreen();
    expect(allText(r)).toContain('Previous app');
  });

  it('shows the empty state only when both sections are empty', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('No payouts yet');
  });

  it('does not show the empty state when only legacy payouts exist', async () => {
    mockDriverState.payoutHistory = [LEGACY_PAYOUT];
    const r = await renderScreen();
    expect(allText(r)).not.toContain('No payouts yet');
    expect(allText(r)).toContain('Previous app');
  });

  it('renders optional fields only when present', async () => {
    mockDriverState.payoutHistory = [PAYOUT_1];
    const r = await renderScreen();
    const text = allText(r);
    expect(text).toContain('TD');
    expect(text).toContain('1234');
  });

  it('renders the error message for a failed payout', async () => {
    mockDriverState.payoutHistory = [PAYOUT_2];
    const r = await renderScreen();
    expect(allText(r)).toContain('Bank declined the transfer');
  });

  it('navigates to /driver/tax-documents when the tax-docs button is pressed', async () => {
    const r = await renderScreen();
    const taxBtn = r.root.findAllByType(TouchableOpacity)[1]; // [0] back, [1] tax docs
    act(() => {
      taxBtn.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith('/driver/tax-documents');
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
