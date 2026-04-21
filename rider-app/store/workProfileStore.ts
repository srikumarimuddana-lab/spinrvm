import { create } from 'zustand';
import AsyncStorage from '@react-native-async-storage/async-storage';
import api from '@shared/api/client';

const STORAGE_KEY = '@spinr:work_profile';

export interface WorkProfile {
  membership: {
    id: string;
    company_id: string;
    role: string;
    status: string;
    policy_override?: boolean;
  };
  company: {
    id: string | null;
    name: string | null;
  };
}

export interface AllowanceBalance {
  company_name: string | null;
  type: string | null;
  amount: number | null;
  used: number | null;
  remaining: number | null;
  period_start: string | null;
  period_end: string | null;
  status: string | null;
}

interface WorkProfileState {
  profiles: WorkProfile[];
  activeCompanyId: string | null;
  workModeEnabled: boolean;
  balance: AllowanceBalance | null;
  requests: any[];
  isLoading: boolean;
  balanceLoading: boolean;
  error: string | null;

  hydrate(): Promise<void>;
  fetchProfiles(): Promise<void>;
  setActiveCompany(id: string): Promise<void>;
  setWorkMode(enabled: boolean): Promise<void>;
  fetchBalance(): Promise<void>;
  fetchRequests(): Promise<void>;
  submitRequest(amount: number, reason: string): Promise<any>;
}

const _persist = async (activeCompanyId: string | null, workModeEnabled: boolean) => {
  try {
    await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify({ activeCompanyId, workModeEnabled }));
  } catch { /* best-effort */ }
};

export const useWorkProfileStore = create<WorkProfileState>((set, get) => ({
  profiles: [],
  activeCompanyId: null,
  workModeEnabled: false,
  balance: null,
  requests: [],
  isLoading: false,
  balanceLoading: false,
  error: null,

  hydrate: async () => {
    try {
      const raw = await AsyncStorage.getItem(STORAGE_KEY);
      if (raw) {
        const { activeCompanyId, workModeEnabled } = JSON.parse(raw);
        set({ activeCompanyId: activeCompanyId ?? null, workModeEnabled: !!workModeEnabled });
      }
    } catch { /* ignore */ }
  },

  fetchProfiles: async () => {
    set({ isLoading: true, error: null });
    try {
      const res = await api.get<WorkProfile[]>('/rider/work-profile');
      const profiles = res.data || [];
      const { activeCompanyId } = get();
      const validId = profiles.find(p => p.company.id === activeCompanyId)?.company.id ?? null;
      const firstId = profiles[0]?.company?.id ?? null;
      const nextId = validId ?? firstId;
      set({ profiles, isLoading: false, activeCompanyId: nextId });
    } catch (e: any) {
      set({ isLoading: false, error: e.message || 'Failed to load work profiles' });
    }
  },

  setActiveCompany: async (id) => {
    const { workModeEnabled } = get();
    set({ activeCompanyId: id, balance: null });
    await _persist(id, workModeEnabled);
  },

  setWorkMode: async (enabled) => {
    const { activeCompanyId } = get();
    set({ workModeEnabled: enabled });
    await _persist(activeCompanyId, enabled);
    if (enabled) get().fetchBalance();
  },

  fetchBalance: async () => {
    const { activeCompanyId } = get();
    if (!activeCompanyId) return;
    set({ balanceLoading: true });
    try {
      const res = await api.get<AllowanceBalance>(`/rider/work-profile/${activeCompanyId}/balance`);
      set({ balance: res.data, balanceLoading: false });
    } catch {
      set({ balanceLoading: false });
    }
  },

  fetchRequests: async () => {
    const { activeCompanyId } = get();
    if (!activeCompanyId) return;
    try {
      const res = await api.get<any[]>(`/rider/work-profile/${activeCompanyId}/allowance-requests`);
      set({ requests: res.data || [] });
    } catch {
      set({ requests: [] });
    }
  },

  submitRequest: async (amount, reason) => {
    const { activeCompanyId } = get();
    if (!activeCompanyId) throw new Error('No active work profile');
    const res = await api.post(
      `/rider/work-profile/${activeCompanyId}/allowance-requests`,
      { amount, reason },
    );
    await Promise.all([get().fetchRequests(), get().fetchBalance()]);
    return res.data;
  },
}));
