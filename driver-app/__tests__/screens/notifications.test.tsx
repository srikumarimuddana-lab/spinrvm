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
});
