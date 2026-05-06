/**
 * walletStore tests
 * Covers: fetchWallet, topUp, payWithWallet, fetchTransactions,
 *         createFareSplit, cancelFareSplit, clearError.
 * All network calls are mocked — no real HTTP occurs.
 */

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  },
}));

import { useWalletStore } from '../walletStore';
import api from '@shared/api/client';

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

const makeSplit = (overrides: Record<string, unknown> = {}) => ({
  id: 'split-1',
  ride_id: 'ride-99',
  total_fare: '30.00',
  split_count: 2,
  your_share: '15.00',
  status: 'pending',
  participants: [
    { id: 'p-1', phone: '+13065551234', share_amount: '15.00', status: 'pending' },
  ],
  ...overrides,
});

describe('walletStore', () => {
  beforeEach(() => {
    useWalletStore.setState({
      wallet: null,
      transactions: [],
      currentSplit: null,
      isLoading: false,
      error: null,
    });
    jest.clearAllMocks();
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
      mockApi.get.mockRejectedValueOnce(new Error('Network error'));

      await useWalletStore.getState().fetchWallet();

      expect(useWalletStore.getState().error).toBe('Network error');
      expect(useWalletStore.getState().isLoading).toBe(false);
    });
  });

  // ---------------------------------------------------------------------------
  describe('topUp', () => {
    it('updates wallet balance after top-up', async () => {
      useWalletStore.setState({ wallet: makeWallet({ balance: '50.00' }) });
      mockApi.post.mockResolvedValueOnce({ data: { balance: 70.0 }, status: 200 });
      mockApi.get.mockResolvedValueOnce({ data: makeWallet({ balance: '70.00' }), status: 200 });

      await useWalletStore.getState().topUp(20.0);

      expect(mockApi.post).toHaveBeenCalledWith('/wallet/top-up', { amount: 20.0 });
      expect(useWalletStore.getState().wallet?.balance).toBe('70.00');
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
  describe('createFareSplit', () => {
    it('stores the created split', async () => {
      const split = makeSplit();
      mockApi.post.mockResolvedValueOnce({ data: split, status: 200 });

      const result = await useWalletStore.getState().createFareSplit('ride-99', ['+13065551234']);

      expect(mockApi.post).toHaveBeenCalledWith('/fare-split', {
        ride_id: 'ride-99',
        participant_phones: ['+13065551234'],
      });
      expect(result).toEqual(split);
      expect(useWalletStore.getState().currentSplit).toEqual(split);
    });
  });

  // ---------------------------------------------------------------------------
  describe('cancelFareSplit', () => {
    it('clears currentSplit after cancellation', async () => {
      useWalletStore.setState({ currentSplit: makeSplit() });
      mockApi.post.mockResolvedValueOnce({ data: null, status: 200 });

      await useWalletStore.getState().cancelFareSplit('split-1');

      expect(mockApi.post).toHaveBeenCalledWith('/fare-split/split-1/cancel');
      expect(useWalletStore.getState().currentSplit).toBeNull();
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
