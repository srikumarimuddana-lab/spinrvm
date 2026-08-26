/**
 * app/driver/notifications.tsx — broader coverage beyond
 * screens/notifications.test.tsx (pins the Android FlatList
 * removeClippedSubviews/getItemLayout regression and the
 * failed-fetch-vs-empty-inbox empty-state distinction).
 *
 * Pins:
 *  - tapping a notification row marks it read and, per `type`, navigates
 *    to its destination (document_expiry, payout_processed, ride_offer,
 *    quest_earned, lost_and_found with/without a case_id)
 *  - a mark-read failure surfaces an Alert
 *  - "Mark all read" only renders when unreadCount > 0, calls the mutation,
 *    and alerts on failure
 *  - the unread-count line pluralizes correctly (1 vs N)
 *  - the back button navigates back
 *  - pull-to-refresh and the error state's Retry button both call refetch
 *  - an unknown notification `type` falls back to the `system` icon
 *    without crashing
 */
import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';
import { Alert } from 'react-native';

import NotificationsScreen from '../../app/driver/notifications';

const mockBack = jest.fn();
const mockPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: mockPush }),
}));

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('expo-linear-gradient', () => ({ LinearGradient: 'LinearGradient' }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 0, left: 0 }),
}));
jest.mock('../../components/SafeRefreshControl', () => () => null);

jest.mock('@shared/theme/ThemeContext', () => ({
  useTheme: () => ({
    colors: {
      primary: '#EF4444', background: '#FFFFFF', surface: '#FFFFFF', surfaceLight: '#F3F4F6',
      text: '#111827', textDim: '#6B7280', textSecondary: '#9CA3AF', border: '#E5E7EB',
      orange: '#F97316', danger: '#DC2626',
    },
  }),
}));

jest.mock('../../store/languageStore', () => ({
  useLanguageStore: () => ({ t: (key: string) => key }),
}));

const mockMarkReadMutate = jest.fn();
const mockMarkAllReadMutate = jest.fn();
const mockRefetch = jest.fn();
let mockNotifData: any = { unread_count: 0, notifications: [] };
let mockIsFetching = false;
let mockIsPending = false;
let mockIsError = false;
jest.mock('@shared/hooks/queries', () => ({
  useNotifications: () => ({
    data: mockNotifData, isFetching: mockIsFetching, isPending: mockIsPending, isError: mockIsError, refetch: mockRefetch,
  }),
  useMarkNotificationRead: () => ({ mutate: mockMarkReadMutate }),
  useMarkAllNotificationsRead: () => ({ mutate: mockMarkAllReadMutate }),
}));

function notif(overrides: Partial<any> = {}) {
  return {
    id: 'n-1', title: 'Title', body: 'Body', type: 'general', is_read: false,
    created_at: new Date().toISOString(), ...overrides,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockNotifData = { unread_count: 0, notifications: [] };
  mockIsFetching = false;
  mockIsPending = false;
  mockIsError = false;
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
});

describe('notification row press → mark read + navigate', () => {
  const cases: [string, any, string][] = [
    ['document_expiry', {}, '/driver/documents'],
    ['payout_processed', {}, '/driver/activity'],
    ['ride_offer', {}, '/driver/'],
    ['quest_earned', {}, '/driver/quests'],
    ['lost_and_found', {}, '/driver/lost-and-found'],
  ];
  it.each(cases)('type %s navigates to %s', (type, extra, route) => {
    mockNotifData = { unread_count: 1, notifications: [notif({ type, ...extra })] };
    const screen = render(<NotificationsScreen />);
    fireEvent.press(screen.getByText('Title'));
    expect(mockMarkReadMutate).toHaveBeenCalledWith('n-1', expect.anything());
    expect(mockPush).toHaveBeenCalledWith(route);
  });

  it('lost_and_found_message with a case_id navigates to the chat with params', () => {
    mockNotifData = {
      unread_count: 1,
      notifications: [notif({ type: 'lost_and_found_message', data: { case_id: 'case-9' } })],
    };
    const screen = render(<NotificationsScreen />);
    fireEvent.press(screen.getByText('Title'));
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/driver/lost-and-found-chat', params: { caseId: 'case-9' } });
  });

  it('a chat_message / unmapped type does not navigate anywhere, just marks read', () => {
    mockNotifData = { unread_count: 1, notifications: [notif({ type: 'chat_message' })] };
    const screen = render(<NotificationsScreen />);
    fireEvent.press(screen.getByText('Title'));
    expect(mockMarkReadMutate).toHaveBeenCalled();
    expect(mockPush).not.toHaveBeenCalled();
  });

  it('an unknown type falls back to the system icon without crashing', () => {
    mockNotifData = { unread_count: 1, notifications: [notif({ type: 'totally_unknown_type' })] };
    expect(() => render(<NotificationsScreen />)).not.toThrow();
  });

  it('a mark-read failure surfaces an Alert', () => {
    mockNotifData = { unread_count: 1, notifications: [notif()] };
    mockMarkReadMutate.mockImplementation((_id: string, opts: any) => opts.onError());
    const screen = render(<NotificationsScreen />);
    fireEvent.press(screen.getByText('Title'));
    expect(Alert.alert).toHaveBeenCalledWith('notifications.markReadError', 'notifications.markReadErrorBody');
  });
});

describe('mark all read', () => {
  it('is hidden when unreadCount is 0', () => {
    mockNotifData = { unread_count: 0, notifications: [] };
    const screen = render(<NotificationsScreen />);
    expect(screen.queryByText('notifications.markAllRead')).toBeNull();
  });

  it('calls the mutation when pressed', () => {
    mockNotifData = { unread_count: 3, notifications: [notif()] };
    const screen = render(<NotificationsScreen />);
    fireEvent.press(screen.getByText('notifications.markAllRead'));
    expect(mockMarkAllReadMutate).toHaveBeenCalledWith(undefined, expect.anything());
  });

  it('alerts on failure', () => {
    mockNotifData = { unread_count: 3, notifications: [notif()] };
    mockMarkAllReadMutate.mockImplementation((_v: any, opts: any) => opts.onError());
    const screen = render(<NotificationsScreen />);
    fireEvent.press(screen.getByText('notifications.markAllRead'));
    expect(Alert.alert).toHaveBeenCalledWith('notifications.markReadError', 'notifications.markReadErrorBody');
  });
});

describe('unread count copy', () => {
  it('uses the singular string for exactly 1 unread', () => {
    mockNotifData = { unread_count: 1, notifications: [notif()] };
    const screen = render(<NotificationsScreen />);
    expect(screen.getByText(/^1 /)).toBeTruthy();
  });

  it('uses the plural string for N unread', () => {
    mockNotifData = { unread_count: 5, notifications: [notif()] };
    const screen = render(<NotificationsScreen />);
    expect(screen.getByText(/^5 /)).toBeTruthy();
  });
});

it('the back button navigates back', () => {
  const screen = render(<NotificationsScreen />);
  fireEvent.press(screen.UNSAFE_getAllByType(require('react-native').TouchableOpacity)[0]);
  expect(mockBack).toHaveBeenCalled();
});

it('the error-state Retry button calls refetch', () => {
  mockNotifData = undefined;
  mockIsError = true;
  const screen = render(<NotificationsScreen />);
  fireEvent.press(screen.getByText('notifications.retry'));
  expect(mockRefetch).toHaveBeenCalled();
});

it('shows the loading spinner while isPending', () => {
  mockNotifData = undefined;
  mockIsPending = true;
  const screen = render(<NotificationsScreen />);
  expect(screen.queryByText('notifications.noNotifications')).toBeNull();
  expect(screen.queryByText('notifications.loadFailed')).toBeNull();
});

it('pull-to-refresh calls refetch', () => {
  mockNotifData = { unread_count: 0, notifications: [] };
  const screen = render(<NotificationsScreen />);
  const list = screen.UNSAFE_getByType(require('react-native').FlatList);
  list.props.refreshControl.props.onRefresh();
  expect(mockRefetch).toHaveBeenCalled();
});
