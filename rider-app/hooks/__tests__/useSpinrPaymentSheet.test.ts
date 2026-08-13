/**
 * useSpinrPaymentSheet — unit tests
 *
 * Pins:
 *  - presentSheet returns ok:true when API + Stripe succeed
 *  - presentSheet returns ok:false with errorMessage when initPaymentSheet fails
 *  - presentSheet returns ok:false with errorMessage when presentPaymentSheet fails
 *  - presentSheet returns ok:false when API call throws
 *  - Canceled error (rider taps back) returns ok:false with "cancelled" message
 */

import { renderHook, act } from '@testing-library/react-native';
import { useStripe } from '@stripe/stripe-react-native';
import api from '@shared/api/client';
import { useSpinrPaymentSheet } from '../useSpinrPaymentSheet';

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { post: jest.fn() },
}));

jest.mock('@stripe/stripe-react-native', () => ({
  useStripe: jest.fn(),
}));

const mockApi = api as jest.Mocked<typeof api>;
const mockUseStripe = useStripe as jest.MockedFunction<typeof useStripe>;

const SHEET_SECRETS = {
  paymentIntent: 'pi_client_secret',
  ephemeralKey: 'ek_secret',
  customer: 'cus_test',
  publishableKey: 'pk_test',
};

function makeStripe(overrides: Partial<ReturnType<typeof useStripe>> = {}) {
  return {
    initPaymentSheet: jest.fn().mockResolvedValue({ error: undefined }),
    presentPaymentSheet: jest.fn().mockResolvedValue({ error: undefined }),
    ...overrides,
  } as unknown as ReturnType<typeof useStripe>;
}

beforeEach(() => {
  jest.clearAllMocks();
  (mockApi.post as jest.Mock).mockResolvedValue({ data: SHEET_SECRETS });
});

describe('useSpinrPaymentSheet', () => {
  it('returns ok:true when API + Stripe both succeed', async () => {
    mockUseStripe.mockReturnValue(makeStripe());
    const { result } = renderHook(() => useSpinrPaymentSheet());

    let outcome: Awaited<ReturnType<typeof result.current.presentSheet>>;
    await act(async () => {
      outcome = await result.current.presentSheet({ rideId: 'ride-1', amount: 15.5 });
    });
    expect(outcome!.ok).toBe(true);
    expect(mockApi.post).toHaveBeenCalledWith('/payments/payment-sheet', { amount: 15.5, ride_id: 'ride-1', tip_amount: 0 });
  });

  it('includes tipAmount in the total sent to the backend', async () => {
    mockUseStripe.mockReturnValue(makeStripe());
    const { result } = renderHook(() => useSpinrPaymentSheet());

    await act(async () => {
      await result.current.presentSheet({ rideId: 'ride-2', amount: 10, tipAmount: 2.5 });
    });
    expect((mockApi.post as jest.Mock).mock.calls[0][1].amount).toBe(12.5);
  });

  it('returns ok:false when initPaymentSheet returns an error', async () => {
    mockUseStripe.mockReturnValue(
      makeStripe({ initPaymentSheet: jest.fn().mockResolvedValue({ error: { message: 'Init failed' } }) }),
    );
    const { result } = renderHook(() => useSpinrPaymentSheet());

    let outcome: Awaited<ReturnType<typeof result.current.presentSheet>>;
    await act(async () => {
      outcome = await result.current.presentSheet({ rideId: 'ride-3', amount: 20 });
    });
    expect(outcome!.ok).toBe(false);
    expect(outcome!.errorMessage).toBe('Init failed');
  });

  it('returns ok:false when presentPaymentSheet returns an error', async () => {
    mockUseStripe.mockReturnValue(
      makeStripe({ presentPaymentSheet: jest.fn().mockResolvedValue({ error: { message: 'Declined', code: 'Failed' } }) }),
    );
    const { result } = renderHook(() => useSpinrPaymentSheet());

    let outcome: Awaited<ReturnType<typeof result.current.presentSheet>>;
    await act(async () => {
      outcome = await result.current.presentSheet({ rideId: 'ride-4', amount: 20 });
    });
    expect(outcome!.ok).toBe(false);
    expect(outcome!.errorMessage).toBe('Declined');
  });

  it('returns ok:false with cancel message when rider taps back (Canceled code)', async () => {
    mockUseStripe.mockReturnValue(
      makeStripe({ presentPaymentSheet: jest.fn().mockResolvedValue({ error: { message: 'Canceled', code: 'Canceled' } }) }),
    );
    const { result } = renderHook(() => useSpinrPaymentSheet());

    let outcome: Awaited<ReturnType<typeof result.current.presentSheet>>;
    await act(async () => {
      outcome = await result.current.presentSheet({ rideId: 'ride-5', amount: 20 });
    });
    expect(outcome!.ok).toBe(false);
    expect(outcome!.errorMessage).toBe('Payment cancelled.');
  });

  it('returns ok:false when API call throws', async () => {
    (mockApi.post as jest.Mock).mockRejectedValue(new Error('Network error'));
    mockUseStripe.mockReturnValue(makeStripe());
    const { result } = renderHook(() => useSpinrPaymentSheet());

    let outcome: Awaited<ReturnType<typeof result.current.presentSheet>>;
    await act(async () => {
      outcome = await result.current.presentSheet({ rideId: 'ride-6', amount: 20 });
    });
    expect(outcome!.ok).toBe(false);
    expect(outcome!.errorMessage).toBe('Network error');
  });
});
