import { create } from 'zustand';
import api from '@shared/api/client';
import { recordNonFatal } from '../utils/crashlytics';

function isAxiosError(e: unknown): e is { response?: { data?: { detail?: string } }; message?: string } {
  return typeof e === 'object' && e !== null;
}

export interface WalletInfo {
  id: string;
  balance: string; // MoneyString: always "0.00" format, never IEEE-754 float
  currency: string;
  is_active: boolean;
}

export interface WalletTransaction {
  id: string;
  type: string;
  amount: string; // MoneyString: negative values arrive as "-12.50"
  balance_after: string; // MoneyString
  description: string | null;
  reference_id: string | null;
  created_at: string;
}

export interface FareSplit {
  id: string;
  ride_id: string;
  total_fare: string; // MoneyString
  split_count: number;
  your_share: string; // MoneyString
  status: string;
  participants: FareSplitParticipant[];
  created_at?: string;
}

export interface FareSplitParticipant {
  id: string;
  phone?: string;
  user_id?: string;
  share_amount: string; // MoneyString
  status: string;
  paid_at?: string;
}

interface WalletState {
  wallet: WalletInfo | null;
  transactions: WalletTransaction[];
  currentSplit: FareSplit | null;
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

  // Fare split actions
  createFareSplit: (rideId: string, phones: string[]) => Promise<FareSplit>;
  fetchFareSplitForRide: (rideId: string) => Promise<void>;
  respondToSplit: (participantId: string, action: 'accept' | 'decline') => Promise<void>;
  paySplitShare: (participantId: string, method: 'wallet' | 'card') => Promise<void>;
  cancelFareSplit: (splitId: string) => Promise<void>;
  addTip: (rideId: string, amount: number) => Promise<void>;

  clearError: () => void;
}

export const useWalletStore = create<WalletState>((set, get) => ({
  wallet: null,
  transactions: [],
  currentSplit: null,
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
      set({ error: isAxiosError(error) ? (error.message ?? null) : null, walletLoading: false });
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
      set({ error: isAxiosError(error) ? (error.response?.data?.detail || error.message || null) : null, isLoading: false });
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
      set({ error: isAxiosError(error) ? (error.response?.data?.detail || error.message || null) : null, isLoading: false });
      throw error;
    }
  },

  fetchTransactions: async (limit = 20) => {
    try {
      set({ transactionsLoading: true, error: null });
      const res = await api.get<{ transactions?: WalletTransaction[] }>(`/wallet/transactions?limit=${limit}`);
      set({ transactions: res.data.transactions || [], transactionsLoading: false });
    } catch (error: unknown) {
      set({ error: isAxiosError(error) ? (error.message ?? null) : null, transactionsLoading: false });
    }
  },

  // Fare split
  createFareSplit: async (rideId: string, phones: string[]) => {
    try {
      set({ isLoading: true, error: null });
      const res = await api.post<FareSplit>('/fare-split', { ride_id: rideId, participant_phones: phones });
      set({ currentSplit: res.data, isLoading: false });
      return res.data;
    } catch (error: unknown) {
      set({ error: isAxiosError(error) ? (error.response?.data?.detail || error.message || null) : null, isLoading: false });
      throw error;
    }
  },

  fetchFareSplitForRide: async (rideId: string) => {
    try {
      const res = await api.get<{ has_split?: boolean; split?: FareSplit }>(`/fare-split/ride/${rideId}`);
      if (res.data.has_split) {
        set({ currentSplit: res.data.split ?? null });
      } else {
        set({ currentSplit: null });
      }
    } catch {
      set({ currentSplit: null });
    }
  },

  respondToSplit: async (participantId: string, action: 'accept' | 'decline') => {
    try {
      set({ isLoading: true, error: null });
      await api.post(`/fare-split/participant/${participantId}/respond`, { action });
      set({ isLoading: false });
    } catch (error: unknown) {
      set({ error: isAxiosError(error) ? (error.response?.data?.detail || error.message || null) : null, isLoading: false });
      throw error;
    }
  },

  paySplitShare: async (participantId: string, method: 'wallet' | 'card') => {
    try {
      set({ isLoading: true, error: null });
      await api.post(`/fare-split/participant/${participantId}/pay`, { payment_method: method });
      set({ isLoading: false });
    } catch (error: unknown) {
      set({ error: isAxiosError(error) ? (error.response?.data?.detail || error.message || null) : null, isLoading: false });
      throw error;
    }
  },

  cancelFareSplit: async (splitId: string) => {
    try {
      set({ isLoading: true, error: null });
      await api.post(`/fare-split/${splitId}/cancel`);
      set({ currentSplit: null, isLoading: false });
    } catch (error: unknown) {
      set({ error: isAxiosError(error) ? (error.response?.data?.detail || error.message || null) : null, isLoading: false });
      throw error;
    }
  },

  addTip: async (rideId: string, amount: number) => {
    try {
      set({ isLoading: true, error: null });
      await api.post(`/rides/${rideId}/tip`, { amount });
      set({ isLoading: false });
    } catch (error: unknown) {
      set({ error: isAxiosError(error) ? (error.response?.data?.detail || error.message || null) : null, isLoading: false });
      throw error;
    }
  },

  clearError: () => set({ error: null }),
}));
