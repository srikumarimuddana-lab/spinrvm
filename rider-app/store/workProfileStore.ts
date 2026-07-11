import { create } from 'zustand';
import AsyncStorage from '@react-native-async-storage/async-storage';
import api from '@shared/api/client';

function isErrorLike(e: unknown): e is { message: string } {
  return typeof e === 'object' && e !== null && 'message' in e;
}

export interface AllowanceRequest {
  id: string;
  amount: string;
  reason: string;
  status: string;
  created_at: string;
}

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

export interface WorkTimeWindow {
  day: 'mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun';
  start: string;
  end: string;
}

export interface WorkPolicy {
  active?: boolean;
  max_fare_per_ride?: number | null;
  allowed_geofence?: Record<string, unknown> | null;
  allowed_time_windows?: WorkTimeWindow[] | null;
  allowed_payment_source?: 'allowance_only' | 'master_only' | 'both';
}

interface WorkProfileState {
  profiles: WorkProfile[];
  activeCompanyId: string | null;
  workModeEnabled: boolean;
  balance: AllowanceBalance | null;
  policy: WorkPolicy | null;
  requests: AllowanceRequest[];
  isLoading: boolean;
  balanceLoading: boolean;
  error: string | null;

  hydrate(): Promise<void>;
  fetchProfiles(): Promise<void>;
  setActiveCompany(id: string): Promise<void>;
  setWorkMode(enabled: boolean): Promise<void>;
  fetchBalance(): Promise<void>;
  fetchPolicy(): Promise<void>;
  fetchRequests(): Promise<void>;
  submitRequest(amount: number, reason: string): Promise<AllowanceRequest>;
  /** Accept a company invite token (app://join / spinr.app/join deep link).
   *  Returns the company name; refreshes profiles on success. */
  acceptInvite(token: string): Promise<string>;
  checkRide(fare: number, pickupAt?: Date): { ok: boolean; reasons: string[] };
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
  policy: null,
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

  acceptInvite: async (token: string) => {
    const res = await api.post<{ company?: { id?: string; name?: string } }>(
      '/rider/work-profile/accept-invite',
      { token },
    );
    await get().fetchProfiles();
    const company = res.data?.company;
    if (company?.id) {
      set({ activeCompanyId: company.id, workModeEnabled: true });
      _persist(company.id, true);
    }
    return company?.name || 'your company';
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
    } catch (e: unknown) {
      set({ isLoading: false, error: isErrorLike(e) ? (e.message || 'Failed to load work profiles') : 'Failed to load work profiles' });
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

  fetchPolicy: async () => {
    const { activeCompanyId } = get();
    if (!activeCompanyId) return;
    try {
      const res = await api.get<WorkPolicy>(`/company/${activeCompanyId}/policy`);
      set({ policy: res.data ?? null });
    } catch {
      set({ policy: null });
    }
  },

  checkRide: (fare, pickupAt) => {
    const { policy, workModeEnabled } = get();
    const reasons: string[] = [];
    if (!workModeEnabled || !policy || policy.active === false) {
      return { ok: true, reasons };
    }
    if (typeof policy.max_fare_per_ride === 'number' && fare > policy.max_fare_per_ride) {
      reasons.push(`Fare exceeds company cap of $${policy.max_fare_per_ride.toFixed(2)}.`);
    }
    const windows = policy.allowed_time_windows;
    if (windows && windows.length > 0) {
      const when = pickupAt ?? new Date();
      const dayKey = (['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'] as const)[when.getDay()];
      const hhmm = `${String(when.getHours()).padStart(2, '0')}:${String(when.getMinutes()).padStart(2, '0')}`;
      const inWindow = windows.some(w => w.day === dayKey && hhmm >= w.start && hhmm <= w.end);
      if (!inWindow) {
        reasons.push('This time is outside the allowed work hours.');
      }
    }
    return { ok: reasons.length === 0, reasons };
  },

  fetchRequests: async () => {
    const { activeCompanyId } = get();
    if (!activeCompanyId) return;
    try {
      const res = await api.get<AllowanceRequest[]>(`/rider/work-profile/${activeCompanyId}/allowance-requests`);
      set({ requests: res.data || [] });
    } catch {
      set({ requests: [] });
    }
  },

  submitRequest: async (amount, reason) => {
    const { activeCompanyId } = get();
    if (!activeCompanyId) throw new Error('No active work profile');
    const res = await api.post<AllowanceRequest>(
      `/rider/work-profile/${activeCompanyId}/allowance-requests`,
      { amount, reason },
    );
    await Promise.all([get().fetchRequests(), get().fetchBalance()]);
    return res.data;
  },
}));
