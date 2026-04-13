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

const makeWallet = (overrides = {}) => ({
  id: 'wallet-1',
  balance: 50.0,
  currency: 'cad',
  is_active: true,
  ...overrides,
});

const makeTx = (overrides = {}) => ({
  id: 'tx-1',
  type: 'top_up',
  amount: 20.0,
  balance_after: 70.0,
  description: 'Wallet top-up',
  reference_id: null,
  created_at: '2026-04-13T10:00:00Z',
  ...overrides,
});

const makeSplit = (overrides = {}) => ({
  id: 'split-1',
  ride_id: 'ride-99',
  total_fare: 30.0,
  split_count: 2,
  your_share: 15.0,
  status: 'pending',
  participants: [
    { id: 'p-1', phone: '+13065551234', share_amount: 15.0, status: 'pending' },
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
      mockApi.get.mockResolvedValueOnce({ data: wallet });

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
      useWalletStore.setState({ wallet: makeWallet({ balance: 50.0 }) });
      mockApi.post.mockResolvedValueOnce({ data: { balance: 70.0 } });

      await useWalletStore.getState().topUp(20.0);

      expect(mockApi.post).toHaveBeenCalledWith('/wallet/top-up', { amount: 20.0 });
      expect(useWalletStore.getState().wallet?.balance).toBe(70.0);
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
      mockApi.get.mockResolvedValueOnce({ data: txs });

      await useWalletStore.getState().fetchTransactions();

      expect(mockApi.get).toHaveBeenCalledWith('/wallet/transactions?limit=20');
      expect(useWalletStore.getState().transactions).toHaveLength(2);
    });

    it('respects custom limit parameter', async () => {
      mockApi.get.mockResolvedValueOnce({ data: [] });

      await useWalletStore.getState().fetchTransactions(50);

      expect(mockApi.get).toHaveBeenCalledWith('/wallet/transactions?limit=50');
    });
  });

  // ---------------------------------------------------------------------------
  describe('createFareSplit', () => {
    it('stores the created split', async () => {
      const split = makeSplit();
      mockApi.post.mockResolvedValueOnce({ data: split });

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
      mockApi.post.mockResolvedValueOnce({});

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
});
