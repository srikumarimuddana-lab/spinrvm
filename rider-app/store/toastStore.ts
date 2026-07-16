import { create } from 'zustand';
import { clampToastMessage, TOAST_TITLE_MAX } from '@shared/utils/toastMessage';

export type ToastVariant = 'info' | 'success' | 'warning' | 'danger';

export interface ToastItem {
  id: string;
  title: string;
  message?: string;
  variant: ToastVariant;
  duration?: number;
  // When this toast last became current. Bumped (without changing id) when an
  // identical toast is re-shown inside the dedupe window so the auto-dismiss
  // timer can be extended without restarting the enter animation.
  shownAt: number;
}

interface ToastStore {
  current: ToastItem | null;
  show: (toast: Omit<ToastItem, 'id' | 'shownAt'>) => void;
  dismiss: () => void;
}

// Identical toasts fired within this window collapse into one. Defense-in-depth
// against a single user action whose notice is raised by more than one code path
// (e.g. a cancel handled by both the local button and the server's WS echo),
// which otherwise minted a new id each time and restarted the banner's enter
// animation — a visible flicker. A genuine repeat OUTSIDE the window (the rider
// deliberately retries an action) still shows fresh.
const DEDUPE_WINDOW_MS = 1000;

let _id = 0;

export const useToastStore = create<ToastStore>((set, get) => ({
  current: null,
  show: (rawToast) => {
    // Hard length cap for every caller: the banner renders 1 title line and 2
    // message lines — clamp here (word-boundary + ellipsis) so no call site
    // can overflow it. Clamped BEFORE the dedupe compare so a repeat of the
    // same over-long toast still dedupes.
    const toast = {
      ...rawToast,
      title: clampToastMessage(rawToast.title, TOAST_TITLE_MAX),
      message: rawToast.message === undefined ? undefined : clampToastMessage(rawToast.message),
    };
    const now = Date.now();
    const cur = get().current;
    const sameContent =
      !!cur &&
      cur.title === toast.title &&
      cur.message === toast.message &&
      cur.variant === toast.variant;
    if (sameContent && now - cur!.shownAt < DEDUPE_WINDOW_MS) {
      // Keep the same id (no re-animate) and just refresh the timer + content.
      // Preserve a previously-set custom duration when the repeat omits one, so
      // a deduped repeat never shortens a deliberately-longer banner.
      set({ current: { ...cur!, ...toast, duration: toast.duration ?? cur!.duration, shownAt: now } });
      return;
    }
    set({ current: { ...toast, id: String(++_id), shownAt: now } });
  },
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
