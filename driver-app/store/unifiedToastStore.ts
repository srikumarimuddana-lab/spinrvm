import { create } from 'zustand';

/**
 * State for the unified driver toast (components/UnifiedToast.tsx), used only
 * while settings.driver_unified_toast_enabled is on (migration 490). Modelled
 * on rider-app/store/toastStore.ts, with two deliberate differences:
 *
 * - No de-duplication. Every show() is a new toast (new id), so it re-animates,
 *   re-announces and restarts its timer, the same as react-native-toast-message
 *   replacing the banner on each Toast.show() today.
 * - No length clamping here. hooks/useToast.ts::showToast clamps before it
 *   routes, so both paths get identical text.
 *
 * Kept free of react-native imports so screens and hooks whose tests mock
 * react-native can still import it.
 */

export type UnifiedToastVariant = 'info' | 'success' | 'warning' | 'danger';

export interface UnifiedToastItem {
  id: number;
  title: string;
  message?: string;
  variant: UnifiedToastVariant;
  duration: number;
}

interface UnifiedToastStore {
  current: UnifiedToastItem | null;
  show: (toast: Omit<UnifiedToastItem, 'id'>) => void;
  /** Clears the toast; with an id, only if that toast is still the current one. */
  dismiss: (id?: number) => void;
}

let _id = 0;

export const useUnifiedToastStore = create<UnifiedToastStore>((set, get) => ({
  current: null,
  show: (toast) => set({ current: { ...toast, id: ++_id } }),
  dismiss: (id) => {
    // A newer toast can replace this one while its exit animation runs; only
    // clear the toast the finished animation belongs to.
    if (id !== undefined && get().current?.id !== id) return;
    set({ current: null });
  },
}));

// Migration 490, default off. Set from /drivers/config in
// useDriverDashboard; read by hooks/useToast.ts::showToast on every call.
// Lives here rather than in useToast.ts because many tests replace that module
// wholesale with a { showToast } mock.
let unifiedToastEnabled = false;

/** From /drivers/config: driver_unified_toast_enabled. */
export function setUnifiedToastEnabled(enabled: boolean): void {
  unifiedToastEnabled = enabled;
}

export function isUnifiedToastEnabled(): boolean {
  return unifiedToastEnabled;
}
