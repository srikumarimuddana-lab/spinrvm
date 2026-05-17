import { create } from 'zustand';

export type ToastVariant = 'info' | 'success' | 'warning' | 'danger';

export interface ToastItem {
  id: string;
  title: string;
  message?: string;
  variant: ToastVariant;
  duration?: number;
}

interface ToastStore {
  current: ToastItem | null;
  show: (toast: Omit<ToastItem, 'id'>) => void;
  dismiss: () => void;
}

let _id = 0;

export const useToastStore = create<ToastStore>((set) => ({
  current: null,
  show: (toast) => set({ current: { ...toast, id: String(++_id) } }),
  dismiss: () => set({ current: null }),
}));

export function showToast(
  title: string,
  message?: string,
  variant: ToastVariant = 'info',
  duration?: number,
) {
  useToastStore.getState().show({ title, message, variant, duration });
}
