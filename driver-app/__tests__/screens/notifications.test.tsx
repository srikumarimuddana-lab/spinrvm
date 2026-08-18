import React from 'react';
import { FlatList } from 'react-native';
import { render } from '@testing-library/react-native';
import NotificationsScreen from '../../app/driver/notifications';

const mockUseNotifications = jest.fn();

jest.mock('@shared/hooks/queries', () => ({
  useNotifications: () => mockUseNotifications(),
  useMarkNotificationRead: () => ({ mutate: jest.fn() }),
  useMarkAllNotificationsRead: () => ({ mutate: jest.fn() }),
}));

jest.mock('@shared/theme/ThemeContext', () => ({
  useTheme: () => ({
    colors: {
      primary: '#EF4444',
      background: '#FFFFFF',
      surface: '#FFFFFF',
      surfaceLight: '#F3F4F6',
      text: '#111827',
      textDim: '#6B7280',
      textSecondary: '#9CA3AF',
      border: '#E5E7EB',
      orange: '#F97316',
      danger: '#DC2626',
    },
  }),
}));

jest.mock('../../store/languageStore', () => ({
  useLanguageStore: () => ({ t: (key: string) => key }),
}));

jest.mock('expo-router', () => ({
  useRouter: () => ({ back: jest.fn(), push: jest.fn() }),
}));

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('expo-linear-gradient', () => ({ LinearGradient: 'LinearGradient' }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 0, left: 0 }),
}));
jest.mock('../../components/SafeRefreshControl', () => () => null);

describe('Driver notifications inbox', () => {
  beforeEach(() => {
    mockUseNotifications.mockReturnValue({
      data: {
        unread_count: 41,
        notifications: [
          {
            id: 'notification-1',
            title: 'Document reminder',
            body: 'Your vehicle inspection expires soon. Upload the renewed document before going online.',
            type: 'document_expiry',
            is_read: false,
            created_at: new Date().toISOString(),
          },
        ],
      },
      isFetching: false,
      refetch: jest.fn(),
    });
  });

  it('keeps variable-height notification rows attached on Android', () => {
    const screen = render(<NotificationsScreen />);
    const list = screen.UNSAFE_getByType(FlatList);

    expect(screen.getByText('Document reminder')).toBeTruthy();
    expect(list.props.removeClippedSubviews).not.toBe(true);
    expect(list.props.getItemLayout).toBeUndefined();
  });

  // A failed fetch and an empty inbox both yield zero rows. Rendering the
  // same "all caught up" state for both is what let a 401'd inbox (App Check
  // token not yet minted, global retry:false) look empty while the dashboard
  // bell badge still read "N unread".
  it('shows a retryable error state — not "all caught up" — when the fetch fails', () => {
    mockUseNotifications.mockReturnValue({
      data: undefined,
      isFetching: false,
      isPending: false,
      isError: true,
      refetch: jest.fn(),
    });

    const screen = render(<NotificationsScreen />);

    expect(screen.getByText('notifications.loadFailed')).toBeTruthy();
    expect(screen.getByText('notifications.retry')).toBeTruthy();
    expect(screen.queryByText('notifications.allCaughtUp')).toBeNull();
  });

  it('shows the empty state when the inbox loaded successfully with no rows', () => {
    mockUseNotifications.mockReturnValue({
      data: { unread_count: 0, notifications: [] },
      isFetching: false,
      isPending: false,
      isError: false,
      refetch: jest.fn(),
    });

    const screen = render(<NotificationsScreen />);

    expect(screen.getByText('notifications.allCaughtUp')).toBeTruthy();
    expect(screen.queryByText('notifications.loadFailed')).toBeNull();
  });
});
