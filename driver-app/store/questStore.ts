import { create } from 'zustand';
import api, { getApiErrorMessage } from '@shared/api/client';

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null;
}

function arrayFromApi<T>(payload: unknown, keys: string[]): T[] {
  if (Array.isArray(payload)) return payload.filter(Boolean) as T[];

  const record = asRecord(payload);
  if (!record) return [];

  for (const key of keys) {
    const candidate = record[key];
    if (Array.isArray(candidate)) return candidate.filter(Boolean) as T[];
  }

  return [];
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
  isLoadingAvailable: boolean;
  isLoadingMine: boolean;
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
  isLoadingAvailable: false,
  isLoadingMine: false,
  error: null,

  fetchAvailableQuests: async () => {
    try {
      set({ isLoadingAvailable: true, error: null });
      const res = await api.get<Quest[] | { quests?: Quest[]; available_quests?: Quest[]; data?: Quest[] }>('/quests');
      const availableQuests = arrayFromApi<Quest>(res.data, ['quests', 'available_quests', 'data']);
      set({ availableQuests, isLoadingAvailable: false });
    } catch (error: unknown) {
      set({ error: getApiErrorMessage(error, 'Failed to fetch quests'), isLoadingAvailable: false });
    }
  },

  fetchMyQuests: async () => {
    try {
      set({ isLoadingMine: true });
      const res = await api.get<MyQuestProgress[] | { quests?: MyQuestProgress[]; my_quests?: MyQuestProgress[]; data?: MyQuestProgress[] }>('/quests/my-quests');
      const myQuests = arrayFromApi<MyQuestProgress>(res.data, ['quests', 'my_quests', 'data']);
      set({ myQuests, isLoadingMine: false });
    } catch {
      set({ isLoadingMine: false });
    }
  },

  joinQuest: async (questId: string) => {
    try {
      set({ isLoadingAvailable: true, error: null });
      await api.post(`/quests/${questId}/join`);
      await get().fetchAvailableQuests();
      await get().fetchMyQuests();
    } catch (error: unknown) {
      set({ error: getApiErrorMessage(error, 'Failed to join quest'), isLoadingAvailable: false });
      throw error;
    }
  },

  claimReward: async (progressId: string) => {
    try {
      set({ isLoadingMine: true, error: null });
      const res = await api.post<{ reward_amount: number }>(`/quests/progress/${progressId}/claim`);
      await get().fetchMyQuests();
      return res.data;
    } catch (error: unknown) {
      set({ error: getApiErrorMessage(error, 'Failed to claim reward'), isLoadingMine: false });
      throw error;
    }
  },

  clearError: () => set({ error: null }),
}));
