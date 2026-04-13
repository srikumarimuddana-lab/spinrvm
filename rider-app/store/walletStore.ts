import { create } from 'zustand';
import api from '@shared/api/client';

export interface WalletInfo {
  id: string;
  balance: number;
  currency: string;
  is_active: boolean;
}

export interface WalletTransaction {
  id: string;
  type: string;
  amount: number;
  balance_after: number;
  description: string | null;
  reference_id: string | null;
  created_at: string;
}

export interface FareSplit {
  id: string;
  ride_id: string;
  total_fare: number;
  split_count: number;
  your_share: number;
  status: string;
  participants: FareSplitParticipant[];
  created_at?: string;
}

export interface FareSplitParticipant {
  id: string;
  phone?: string;
  user_id?: string;
  share_amount: number;
  status: string;
  paid_at?: string;
}

interface WalletState {
  wallet: WalletInfo | null;
  transactions: WalletTransaction[];
  currentSplit: FareSplit | null;
  isLoading: boolean;
  error: string | null;

  fetchWallet: () => Promise<void>;
  topUp: (amount: number) => Promise<void>;
  payWithWallet: (rideId: string, amount: number) => Promise<void>;
  fetchTransactions: (limit?: number) => Promise<void>;
  transfer: (phone: string, amount: number) => Promise<void>;

  // Fare split actions
  createFareSplit: (rideId: string, phones: string[]) => Promise<FareSplit>;
  fetchFareSplitForRide: (rideId: string) => Promise<void>;
  respondToSplit: (participantId: string, action: 'accept' | 'decline') => Promise<void>;
  paySplitShare: (participantId: string, method: 'wallet' | 'card') => Promise<void>;
  cancelFareSplit: (splitId: string) => Promise<void>;

  clearError: () => void;
}

export const useWalletStore = create<WalletState>((set, get) => ({
  wallet: null,
  transactions: [],
  currentSplit: null,
  isLoading: false,
  error: null,

  fetchWallet: async () => {
    try {
      set({ isLoading: true, error: null });
      const res = await api.get('/wallet');
      set({ wallet: res.data, isLoading: false });
    } catch (error: any) {
      set({ error: error.message, isLoading: false });
    }
  },

  topUp: async (amount: number) => {
    try {
      set({ isLoading: true, error: null });
      const res = await api.post('/wallet/top-up', { amount });
      const wallet = get().wallet;
      if (wallet) {
        set({ wallet: { ...wallet, balance: res.data.balance }, isLoading: false });
      } else {
        set({ isLoading: false });
      }
    } catch (error: any) {
      set({ error: error.response?.data?.detail || error.message, isLoading: false });
      throw error;
    }
  },

  payWithWallet: async (rideId: string, amount: number) => {
    try {
      set({ isLoading: true, error: null });
      const res = await api.post('/wallet/pay', { ride_id: rideId, amount });
      const wallet = get().wallet;
      if (wallet) {
        set({ wallet: { ...wallet, balance: res.data.balance }, isLoading: false });
      }
    } catch (error: any) {
      set({ error: error.response?.data?.detail || error.message, isLoading: false });
      throw error;
    }
  },

  fetchTransactions: async (limit = 20) => {
    try {
      set({ isLoading: true, error: null });
      const res = await api.get(`/wallet/transactions?limit=${limit}`);
      set({ transactions: res.data.transactions || [], isLoading: false });
    } catch (error: any) {
      set({ error: error.message, isLoading: false });
    }
  },

  transfer: async (phone: string, amount: number) => {
    try {
      set({ isLoading: true, error: null });
      const res = await api.post('/wallet/transfer', { recipient_phone: phone, amount });
      const wallet = get().wallet;
      if (wallet) {
        set({ wallet: { ...wallet, balance: res.data.balance }, isLoading: false });
      }
    } catch (error: any) {
      set({ error: error.response?.data?.detail || error.message, isLoading: false });
      throw error;
    }
  },

  // Fare split
  createFareSplit: async (rideId: string, phones: string[]) => {
    try {
      set({ isLoading: true, error: null });
      const res = await api.post('/fare-split', { ride_id: rideId, participant_phones: phones });
      set({ currentSplit: res.data, isLoading: false });
      return res.data;
    } catch (error: any) {
      set({ error: error.response?.data?.detail || error.message, isLoading: false });
      throw error;
    }
  },

  fetchFareSplitForRide: async (rideId: string) => {
    try {
      const res = await api.get(`/fare-split/ride/${rideId}`);
      if (res.data.has_split) {
        set({ currentSplit: res.data.split });
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
    } catch (error: any) {
      set({ error: error.response?.data?.detail || error.message, isLoading: false });
      throw error;
    }
  },

  paySplitShare: async (participantId: string, method: 'wallet' | 'card') => {
    try {
      set({ isLoading: true, error: null });
      await api.post(`/fare-split/participant/${participantId}/pay`, { payment_method: method });
      set({ isLoading: false });
    } catch (error: any) {
      set({ error: error.response?.data?.detail || error.message, isLoading: false });
      throw error;
    }
  },

  cancelFareSplit: async (splitId: string) => {
    try {
      set({ isLoading: true, error: null });
      await api.post(`/fare-split/${splitId}/cancel`);
      set({ currentSplit: null, isLoading: false });
    } catch (error: any) {
      set({ error: error.response?.data?.detail || error.message, isLoading: false });
      throw error;
    }
  },

  clearError: () => set({ error: null }),
}));
