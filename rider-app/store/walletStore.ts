import { create } from 'zustand';
import api, { getApiErrorMessage } from '@shared/api/client';
import { recordNonFatal } from '../utils/crashlytics';

export interface WalletInfo {
  id: string;
  balance: string; // MoneyString: always "0.00" format, never IEEE-754 float
  currency: string;
  is_active: boolean;
}

export interface FareBreakdownLine {
  label: string;
  amount: number | string | null;
  type: 'ride' | 'fee' | 'modifier' | 'tax' | 'discount' | 'tip';
}

export interface WalletTransactionMeta {
  ride_id?: string;
  ride_code?: string;
  fare_amount?: string;
  ride_fare?: string;
  tip_amount?: string;
  driver_id?: string;
  surge_multiplier?: string;
  pickup_address?: string;
  dropoff_address?: string;
  stripe_payment_intent_id?: string;
  discount_amount?: string;
  promo_code?: string;
  grand_total?: string;
  fare_breakdown?: FareBreakdownLine[];
}

export interface WalletTransaction {
  id: string;
  type: string;
  amount: string; // MoneyString: negative values arrive as "-12.50"
  balance_after: string; // MoneyString
  description: string | null;
  reference_id: string | null;
  metadata?: WalletTransactionMeta | null;
  created_at: string;
}

interface WalletState {
  wallet: WalletInfo | null;
  transactions: WalletTransaction[];
  isLoading: boolean;
  walletLoading: boolean;
  transactionsLoading: boolean;
  error: string | null;

  fetchWallet: () => Promise<void>;
  topUp: (amount: number) => Promise<{
    paymentIntent: string;
    ephemeralKey: string;
    customer: string;
    publishableKey: string;
  }>;
  payWithWallet: (rideId: string, amount: number) => Promise<void>;
  fetchTransactions: (limit?: number) => Promise<void>;
  addTip: (rideId: string, amount: number) => Promise<void>;

  clearError: () => void;
}

export const useWalletStore = create<WalletState>((set, get) => ({
  wallet: null,
  transactions: [],
  isLoading: false,
  walletLoading: false,
  transactionsLoading: false,
  error: null,

  fetchWallet: async () => {
    try {
      set({ walletLoading: true, error: null });
      const res = await api.get<WalletInfo>('/wallet');
      set({ wallet: res.data, walletLoading: false });
    } catch (error: unknown) {
      set({ error: getApiErrorMessage(error, 'Could not load your wallet. Please try again.'), walletLoading: false });
    }
  },

  topUp: async (amount: number) => {
    try {
      set({ isLoading: true, error: null });
      const res = await api.post<{
        paymentIntent: string;
        ephemeralKey: string;
        customer: string;
        publishableKey: string;
      }>('/wallet/top-up', { amount });
      set({ isLoading: false });
      return res.data;
    } catch (error: unknown) {
      set({ error: getApiErrorMessage(error, 'Top-up failed. Please try again.'), isLoading: false });
      throw error;
    }
  },

  payWithWallet: async (rideId: string, amount: number) => {
    try {
      set({ isLoading: true, error: null });
      await api.post('/wallet/pay', { ride_id: rideId, amount });
      await get().fetchWallet();
      set({ isLoading: false });
    } catch (error: unknown) {
      recordNonFatal(error, { store: 'walletStore', action: 'payWithWallet' });
      set({ error: getApiErrorMessage(error, 'Wallet payment failed. Please try again.'), isLoading: false });
      throw error;
    }
  },

  fetchTransactions: async (limit = 20) => {
    try {
      set({ transactionsLoading: true, error: null });
      const res = await api.get<{ transactions?: WalletTransaction[] }>(`/wallet/transactions?limit=${limit}`);
      set({ transactions: res.data.transactions || [], transactionsLoading: false });
    } catch (error: unknown) {
      set({ error: getApiErrorMessage(error, 'Could not load transactions. Please try again.'), transactionsLoading: false });
    }
  },

  addTip: async (rideId: string, amount: number) => {
    try {
      set({ isLoading: true, error: null });
      await api.post(`/rides/${rideId}/tip`, { amount });
      set({ isLoading: false });
    } catch (error: unknown) {
      set({ error: getApiErrorMessage(error, 'Could not add tip. Please try again.'), isLoading: false });
      throw error;
    }
  },

  clearError: () => set({ error: null }),
}));
