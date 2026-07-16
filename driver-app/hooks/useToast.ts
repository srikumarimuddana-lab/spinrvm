import Toast from 'react-native-toast-message';
import { clampToastMessage, TOAST_TITLE_MAX } from '@shared/utils/toastMessage';

export type ToastType = 'success' | 'error' | 'warning' | 'info';

export function showToast(type: ToastType, title: string, message?: string) {
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
