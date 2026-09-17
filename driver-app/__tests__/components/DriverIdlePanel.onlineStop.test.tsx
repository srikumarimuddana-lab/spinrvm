import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { DriverIdlePanel } from '../../components/dashboard/DriverIdlePanel';

jest.mock('@expo/vector-icons', () => ({ Ionicons: 'Ionicons' }));
jest.mock('expo-linear-gradient', () => ({ LinearGradient: 'View' }));
jest.mock('expo-constants', () => ({
  __esModule: true,
  default: { executionEnvironment: 'standalone' },
  ExecutionEnvironment: { StoreClient: 'storeClient', Standalone: 'standalone', Bare: 'bare' },
}));
jest.mock('expo-notifications', () => ({ scheduleNotificationAsync: jest.fn() }));
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(() => Promise.resolve('true')),
  setItem: jest.fn(() => Promise.resolve()),
}));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, left: 0, right: 0, bottom: 0 }),
}));
jest.mock('../../store/languageStore', () => ({
  useLanguageStore: () => ({ t: (key: string) => key }),
}));

const mockAuthState = {
  driver: {
    status: undefined as string | undefined,
    vehicle_color: 'White',
    vehicle_make: 'Toyota',
    vehicle_model: 'Grand Highlander',
    license_plate: 'KAIRAV',
  },
};
jest.mock('@shared/store/authStore', () => ({
  useAuthStore: (selector: (s: { driver: typeof mockAuthState.driver }) => unknown) =>
    selector(mockAuthState),
}));

it('keeps STOP enabled while online even if driver.status is missing', () => {
  const onToggleOnline = jest.fn();
  let tree!: TestRenderer.ReactTestRenderer;
  act(() => {
    tree = TestRenderer.create(
      <DriverIdlePanel
        isOnline
        onToggleOnline={onToggleOnline}
        pulseAnim={{ current: 1 }}
      />,
    );
  });
  const stop = tree.root.findAll((n) => n.props.accessibilityLabel === 'home.stop')[0];
  expect(stop.props.accessibilityState).toEqual(expect.objectContaining({ disabled: false }));
  act(() => { stop.props.onPress(); });
  expect(onToggleOnline).toHaveBeenCalled();
  act(() => { tree.unmount(); });
});
