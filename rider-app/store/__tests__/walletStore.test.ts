/**
 * walletStore tests
 * Covers: fetchWallet, topUp, payWithWallet, fetchTransactions, clearError.
 * All network calls are mocked — no real HTTP occurs.
 */

import { useWalletStore } from '../walletStore';
import api from '@shared/api/client';

jest.mock('@shared/api/client', () => ({
  ...jest.requireActual('@shared/api/client'),
  __esModule: true,
  default: {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  },
}));

const mockApi = api as jest.Mocked<typeof api>;

const makeWallet = (overrides: Record<string, unknown> = {}) => ({
  id: 'wallet-1',
  balance: '50.00',
  currency: 'cad',
  is_active: true,
  ...overrides,
});

const makeTx = (overrides: Record<string, unknown> = {}) => ({
  id: 'tx-1',
  type: 'top_up',
  amount: '20.00',
  balance_after: '70.00',
  description: 'Wallet top-up',
  reference_id: null,
  created_at: '2026-04-13T10:00:00Z',
  ...overrides,
});

describe('walletStore', () => {
  beforeEach(() => {
    useWalletStore.setState({
      wallet: null,
      transactions: [],
      isLoading: false,
      error: null,
    });
    // resetAllMocks (not clearAllMocks) so unconsumed mockResolvedValueOnce
    // values can never leak between tests.
    jest.resetAllMocks();
  });

  // ---------------------------------------------------------------------------
  describe('fetchWallet', () => {
    it('stores wallet on success', async () => {
      const wallet = makeWallet();
      mockApi.get.mockResolvedValueOnce({ data: wallet, status: 200 });

      await useWalletStore.getState().fetchWallet();

      expect(mockApi.get).toHaveBeenCalledWith('/wallet');
      expect(useWalletStore.getState().wallet).toEqual(wallet);
      expect(useWalletStore.getState().isLoading).toBe(false);
    });

    it('sets error on failure', async () => {
      // getApiErrorMessage treats bare "Network Error" as Axios noise (not a
      // useful message for the user) and returns the caller's fallback —
      // see shared/api/client.ts.
      mockApi.get.mockRejectedValueOnce(new Error('Network error'));

      await useWalletStore.getState().fetchWallet();

      expect(useWalletStore.getState().error).toBe('Could not load your wallet. Please try again.');
      expect(useWalletStore.getState().isLoading).toBe(false);
    });
  });

  // ---------------------------------------------------------------------------
  describe('topUp', () => {
    it('returns PaymentSheet params and leaves the balance untouched', async () => {
      // topUp only creates the Stripe PaymentIntent; the balance updates
      // after the PaymentSheet flow completes and fetchWallet refetches.
      useWalletStore.setState({ wallet: makeWallet({ balance: '50.00' }) });
      const sheetParams = {
        paymentIntent: 'pi_secret',
        ephemeralKey: 'ek_test',
        customer: 'cus_1',
        publishableKey: 'pk_test',
      };
      mockApi.post.mockResolvedValueOnce({ data: sheetParams, status: 200 });

      const result = await useWalletStore.getState().topUp(20.0);

      expect(mockApi.post).toHaveBeenCalledWith('/wallet/top-up', { amount: 20.0 });
      expect(result).toEqual(sheetParams);
      expect(useWalletStore.getState().wallet?.balance).toBe('50.00');
      expect(useWalletStore.getState().isLoading).toBe(false);
    });

    it('throws and sets error when top-up fails', async () => {
      const err = new Error('Insufficient funds');
      (err as any).response = { data: { detail: 'Top-up failed' } };
      mockApi.post.mockRejectedValueOnce(err);

      await expect(useWalletStore.getState().topUp(5.0)).rejects.toThrow();
      expect(useWalletStore.getState().error).toBe('Top-up failed');
    });
  });

  // ---------------------------------------------------------------------------
  describe('fetchTransactions', () => {
    it('stores transactions list', async () => {
      const txs = [makeTx(), makeTx({ id: 'tx-2', type: 'ride_payment', amount: -9.5 })];
      mockApi.get.mockResolvedValueOnce({ data: { transactions: txs }, status: 200 });

      await useWalletStore.getState().fetchTransactions();

      expect(mockApi.get).toHaveBeenCalledWith('/wallet/transactions?limit=20');
      expect(useWalletStore.getState().transactions).toHaveLength(2);
    });

    it('respects custom limit parameter', async () => {
      mockApi.get.mockResolvedValueOnce({ data: [], status: 200 });

      await useWalletStore.getState().fetchTransactions(50);

      expect(mockApi.get).toHaveBeenCalledWith('/wallet/transactions?limit=50');
    });
  });

  // ---------------------------------------------------------------------------
  describe('clearError', () => {
    it('clears error state', () => {
      useWalletStore.setState({ error: 'Something went wrong' });
      useWalletStore.getState().clearError();
      expect(useWalletStore.getState().error).toBeNull();
    });
  });

  // ---------------------------------------------------------------------------
  // R-P3-7 — payWithWallet insufficient balance rejection
  // ---------------------------------------------------------------------------
  describe('payWithWallet', () => {
    it('deducts balance on success', async () => {
      useWalletStore.setState({ wallet: makeWallet({ balance: '50.00' }) });
      mockApi.post.mockResolvedValueOnce({ data: { balance: 40.5 }, status: 200 });
      mockApi.get.mockResolvedValueOnce({ data: makeWallet({ balance: '40.50' }), status: 200 });

      await useWalletStore.getState().payWithWallet('ride-99', 9.5);

      expect(mockApi.post).toHaveBeenCalledWith('/wallet/pay', { ride_id: 'ride-99', amount: 9.5 });
      expect(useWalletStore.getState().wallet?.balance).toBe('40.50');
      expect(useWalletStore.getState().isLoading).toBe(false);
    });

    it('sets error and rethrows when balance is insufficient', async () => {
      useWalletStore.setState({ wallet: makeWallet({ balance: '5.00' }) });

      const err: any = new Error('Request failed with status code 400');
      err.response = { data: { detail: 'Insufficient balance' } };
      mockApi.post.mockRejectedValueOnce(err);

      await expect(
        useWalletStore.getState().payWithWallet('ride-99', 20.0)
      ).rejects.toThrow();

      expect(useWalletStore.getState().error).toBe('Insufficient balance');
      expect(useWalletStore.getState().isLoading).toBe(false);
      // Balance should be unchanged — payment was rejected
      expect(useWalletStore.getState().wallet?.balance).toBe('5.00');
    });
  });

  // ---------------------------------------------------------------------------
  // R-P1-25: addTip idempotency
  // ---------------------------------------------------------------------------
  describe('addTip idempotency', () => {
    it('second tip call is rejected when a tip already exists', async () => {
      // Backend rejects duplicate tip with 400
      const err: any = new Error('Tip already added');
      err.response = { status: 400, data: { detail: 'A tip has already been added for this ride' } };
      mockApi.post.mockRejectedValueOnce(err);

      await expect(
        useWalletStore.getState().addTip?.('ride-99', 3.0)
      ).rejects.toThrow();
    });
  });
});
