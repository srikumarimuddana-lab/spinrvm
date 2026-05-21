import Toast from 'react-native-toast-message';

export type ToastType = 'success' | 'error' | 'info';

export function showToast(type: ToastType, title: string, message?: string) {
  Toast.show({ type, text1: title, text2: message, visibilityTime: 3000, topOffset: 60 });
}
