import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { DriverTopBar } from '../../components/dashboard/DriverTopBar';

jest.mock('@expo/vector-icons', () => ({ Ionicons: 'Ionicons' }));
jest.mock('../../store/languageStore', () => ({
  useLanguageStore: () => ({ t: (key: string) => key }),
}));
jest.mock('@shared/components/OfflineBanner', () => ({
  useNetworkStatus: () => ({ isOffline: false }),
}));
jest.mock('expo-router', () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, left: 0, right: 0, bottom: 0 }),
}));

function findByLabel(root: TestRenderer.ReactTestRenderer, label: string) {
  return root.root.findAll((n) => n.props.accessibilityLabel === label)[0];
}

function pressRetryChip(connectionState: 'disconnected' | 'reconnecting', label: string) {
  const onRetryConnection = jest.fn();
  let tree!: TestRenderer.ReactTestRenderer;
  act(() => {
    tree = TestRenderer.create(
      <DriverTopBar
        isOnline
        connectionState={connectionState}
        onRetryConnection={onRetryConnection}
      />,
    );
  });
  const chip = findByLabel(tree, label);
  expect(chip.props.accessibilityRole).toBe('button');
  act(() => { chip.props.onPress(); });
  expect(onRetryConnection).toHaveBeenCalledTimes(1);
  act(() => { tree.unmount(); });
}

it('lets the driver tap Connection lost to retry', () => {
  pressRetryChip('disconnected', 'dashboard.connectionLost. common.retry');
});

it('lets the driver tap Reconnecting to retry', () => {
  pressRetryChip('reconnecting', 'dashboard.reconnecting. common.retry');
});
