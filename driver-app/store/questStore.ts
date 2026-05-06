import { create } from 'zustand';
import api from '@shared/api/client';

function isAxiosError(e: unknown): e is { response?: { data?: { detail?: string } }; message?: string } {
  return typeof e === 'object' && e !== null && ('response' in e || 'message' in e);
}

export interface Quest {
  id: string;
  title: string;
  description: string;
  type: string;
  target_value: number;
  reward_amount: number;
  reward_type: string;
  start_date: string;
  end_date: string;
  current_value: number;
  progress_pct: number;
  status: string;  // 'available' | 'active' | 'completed' | 'claimed' | 'expired'
  progress_id: string | null;
}

export interface MyQuestProgress {
  progress_id: string;
  quest: {
    id: string;
    title: string;
    description: string;
    type: string;
    target_value: number;
    reward_amount: number;
    reward_type: string;
    start_date: string;
    end_date: string;
  };
  current_value: number;
  progress_pct: number;
  status: string;
  started_at: string;
  completed_at: string | null;
  claimed_at: string | null;
}

interface QuestState {
  availableQuests: Quest[];
  myQuests: MyQuestProgress[];
  isLoading: boolean;
  error: string | null;

  fetchAvailableQuests: () => Promise<void>;
  fetchMyQuests: () => Promise<void>;
  joinQuest: (questId: string) => Promise<void>;
  claimReward: (progressId: string) => Promise<{ reward_amount: number }>;
  clearError: () => void;
}

export const useQuestStore = create<QuestState>((set, get) => ({
  availableQuests: [],
  myQuests: [],
  isLoading: false,
  error: null,

  fetchAvailableQuests: async () => {
    try {
      set({ isLoading: true, error: null });
      const res = await api.get<Quest[]>('/quests');
      set({ availableQuests: res.data || [], isLoading: false });
    } catch (error: unknown) {
      set({ error: isAxiosError(error) ? (error.message ?? 'Failed to fetch quests') : 'Failed to fetch quests', isLoading: false });
    }
  },

  fetchMyQuests: async () => {
    try {
      set({ isLoading: true, error: null });
      const res = await api.get<MyQuestProgress[]>('/quests/my-quests');
      set({ myQuests: res.data || [], isLoading: false });
    } catch (error: unknown) {
      set({ error: isAxiosError(error) ? (error.message ?? 'Failed to fetch quests') : 'Failed to fetch quests', isLoading: false });
    }
  },

  joinQuest: async (questId: string) => {
    try {
      set({ isLoading: true, error: null });
      await api.post(`/quests/${questId}/join`);
      // Refresh both lists
      await get().fetchAvailableQuests();
      await get().fetchMyQuests();
      set({ isLoading: false });
    } catch (error: unknown) {
      set({ error: isAxiosError(error) ? (error.response?.data?.detail || error.message || 'Failed to join quest') : 'Failed to join quest', isLoading: false });
      throw error;
    }
  },

  claimReward: async (progressId: string) => {
    try {
      set({ isLoading: true, error: null });
      const res = await api.post<{ reward_amount: number }>(`/quests/progress/${progressId}/claim`);
      await get().fetchMyQuests();
      set({ isLoading: false });
      return res.data;
    } catch (error: unknown) {
      set({ error: isAxiosError(error) ? (error.response?.data?.detail || error.message || 'Failed to claim reward') : 'Failed to claim reward', isLoading: false });
      throw error;
    }
  },

  clearError: () => set({ error: null }),
}));
