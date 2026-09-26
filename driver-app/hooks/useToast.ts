import Toast from 'react-native-toast-message';
import { clampToastMessage, TOAST_TITLE_MAX } from '@shared/utils/toastMessage';
import {
  isUnifiedToastEnabled,
  useUnifiedToastStore,
  type UnifiedToastVariant,
} from '../store/unifiedToastStore';

export type ToastType = 'success' | 'error' | 'warning' | 'info';

// error renders with the danger styling in the unified toast (colors.danger,
// which mirrors colors.error in both themes).
const UNIFIED_VARIANT: Record<ToastType, UnifiedToastVariant> = {
  success: 'success',
  error: 'danger',
  warning: 'warning',
  info: 'info',
};

export function showToast(type: ToastType, title: string, message?: string) {
  // Migration 490, default off: render through the unified toast
  // (components/UnifiedToast.tsx) with the same clamps and 3.5 s duration.
  if (isUnifiedToastEnabled()) {
    useUnifiedToastStore.getState().show({
      title: clampToastMessage(title, TOAST_TITLE_MAX),
      message: message === undefined ? undefined : clampToastMessage(message),
      variant: UNIFIED_VARIANT[type] ?? 'info',
      duration: 3500,
    });
    return;
  }
  // Hard length cap for every caller: toastConfig renders 1 title line and 2
  // message lines — clamp here (word-boundary + ellipsis) so no call site can
  // overflow the banner.
  Toast.show({
    type,
    text1: clampToastMessage(title, TOAST_TITLE_MAX),
    text2: message === undefined ? undefined : clampToastMessage(message),
    visibilityTime: 3500,
    topOffset: 60,
  });
}
