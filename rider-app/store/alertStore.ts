import { create } from 'zustand';

interface AlertButton {
  text: string;
  style?: 'default' | 'cancel' | 'destructive';
  onPress?: () => void;
}

interface AlertState {
  visible: boolean;
  title: string;
  message: string;
  variant: 'info' | 'warning' | 'danger' | 'success';
  buttons?: AlertButton[];
}

interface AlertStore extends AlertState {
  showAlert: (opts: Omit<AlertState, 'visible'>) => void;
  hideAlert: () => void;
}

export const useAlertStore = create<AlertStore>((set) => ({
  visible: false,
  title: '',
  message: '',
  variant: 'info',
  buttons: undefined,
  showAlert: (opts) => set({ ...opts, visible: true }),
  hideAlert: () => set({ visible: false }),
}));

export function globalAlert(
  title: string,
  message: string,
  variant: AlertState['variant'] = 'info',
  buttons?: AlertButton[],
) {
  useAlertStore.getState().showAlert({ title, message, variant, buttons });
}
