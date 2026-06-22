import { create } from 'zustand';
import api from '@shared/api/client';

function isAxiosError(e: unknown): e is { response?: { data?: { detail?: string } }; message?: string } {
  return typeof e === 'object' && e !== null && ('response' in e || 'message' in e);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null;
}

function arrayFromApi(payload: unknown, keys: string[]): unknown[] {
  if (Array.isArray(payload)) return payload.filter(Boolean);

  const record = asRecord(payload);
  if (!record) return [];

  for (const key of keys) {
    const candidate = record[key];
    if (Array.isArray(candidate)) return candidate.filter(Boolean);
  }

  return [];
}

function stringValue(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === 'string' && value.trim().length > 0) return value;
  }
  return '';
}

function numberValue(...values: unknown[]): number {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim().length > 0) {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return 0;
}

function normalizeQuest(raw: unknown): Quest | null {
  const record = asRecord(raw);
  if (!record) return null;

  const nested = asRecord(record.quest) ?? record;
  const id = stringValue(record.id, record.quest_id, nested.id, nested.quest_id);
  if (!id) return null;

  return {
    id,
    title: stringValue(record.title, nested.title, nested.name, 'Quest'),
    description: stringValue(record.description, nested.description, nested.content, nested.details),
    type: stringValue(record.type, nested.type, 'ride_count'),
    target_value: numberValue(record.target_value, record.targetValue, nested.target_value, nested.targetValue),
    reward_amount: numberValue(record.reward_amount, record.rewardAmount, nested.reward_amount, nested.rewardAmount),
    reward_type: stringValue(record.reward_type, record.rewardType, nested.reward_type, nested.rewardType, 'wallet_credit'),
    start_date: stringValue(record.start_date, record.startDate, nested.start_date, nested.startDate),
    end_date: stringValue(record.end_date, record.endDate, nested.end_date, nested.endDate),
    current_value: numberValue(record.current_value, record.currentValue),
    progress_pct: numberValue(record.progress_pct, record.progressPct, record.progress_percent, record.progressPercent),
    status: stringValue(record.status, 'available'),
    progress_id: stringValue(record.progress_id, record.progressId) || null,
  };
}

function normalizeMyQuestProgress(raw: unknown): MyQuestProgress | null {
  const record = asRecord(raw);
  if (!record) return null;

  const quest = normalizeQuest(record.quest ? record : { ...record, status: 'available' });
  const progressId = stringValue(record.progress_id, record.progressId, record.id);
  if (!quest || !progressId) return null;

  return {
    progress_id: progressId,
    quest: {
      id: quest.id,
      title: quest.title,
      description: quest.description,
      type: quest.type,
      target_value: quest.target_value,
      reward_amount: quest.reward_amount,
      reward_type: quest.reward_type,
      start_date: quest.start_date,
      end_date: quest.end_date,
    },
    current_value: numberValue(record.current_value, record.currentValue),
    progress_pct: numberValue(record.progress_pct, record.progressPct, record.progress_percent, record.progressPercent),
    status: stringValue(record.status, 'active'),
    started_at: stringValue(record.started_at, record.startedAt),
    completed_at: stringValue(record.completed_at, record.completedAt) || null,
    claimed_at: stringValue(record.claimed_at, record.claimedAt) || null,
  };
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
      const availableQuests = arrayFromApi(res.data, ['quests', 'available_quests', 'data'])
        .map(normalizeQuest)
        .filter((quest): quest is Quest => quest !== null);
      set({ availableQuests, isLoadingAvailable: false });
    } catch (error: unknown) {
      set({ error: isAxiosError(error) ? (error.message ?? 'Failed to fetch quests') : 'Failed to fetch quests', isLoadingAvailable: false });
    }
  },

  fetchMyQuests: async () => {
    try {
      set({ isLoadingMine: true });
      const res = await api.get<MyQuestProgress[] | { quests?: MyQuestProgress[]; my_quests?: MyQuestProgress[]; data?: MyQuestProgress[] }>('/quests/my-quests');
      const myQuests = arrayFromApi(res.data, ['quests', 'my_quests', 'data'])
        .map(normalizeMyQuestProgress)
        .filter((quest): quest is MyQuestProgress => quest !== null);
      set({ myQuests, isLoadingMine: false });
    } catch (error: unknown) {
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
      set({ error: isAxiosError(error) ? (error.response?.data?.detail || error.message || 'Failed to join quest') : 'Failed to join quest', isLoadingAvailable: false });
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
      set({ error: isAxiosError(error) ? (error.response?.data?.detail || error.message || 'Failed to claim reward') : 'Failed to claim reward', isLoadingMine: false });
      throw error;
    }
  },

  clearError: () => set({ error: null }),
}));
