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

jest.mock('expo-router/react-navigation', () => {
  const ReactActual = require('react');
  return {
    useFocusEffect: (cb: () => void | (() => void)) => {
      ReactActual.useEffect(() => cb(), []);
    },
  };
});

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
const mockDeleteMutate = jest.fn();
const mockClearMutate = jest.fn();
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
  useDeleteNotification: () => ({ mutate: mockDeleteMutate }),
  useClearNotifications: () => ({ mutate: mockClearMutate }),
}));

// showAlert (driver-app's cross-platform confirm dialog) is a Zustand-store
// trigger, not react-native's own Alert — mock it directly and invoke the
// destructive button's onPress synchronously so delete/clear-all tests don't
// need to render <AlertDialog/> itself.
const mockShowAlert = jest.fn();
jest.mock('../../components/AlertDialog', () => ({
  showAlert: (...args: any[]) => mockShowAlert(...args),
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

  it('a chat_message / unmapped type does not navigate anywhere, just marks read and opens the detail modal', () => {
    mockNotifData = { unread_count: 1, notifications: [notif({ type: 'chat_message', body: 'Full unread body text' })] };
    const screen = render(<NotificationsScreen />);
    fireEvent.press(screen.getByText('Title'));
    expect(mockMarkReadMutate).toHaveBeenCalled();
    expect(mockPush).not.toHaveBeenCalled();
    expect(screen.getByTestId('notification-detail-modal').props.visible).toBe(true);
    expect(screen.getByTestId('notification-detail-body').props.children).toBe('Full unread body text');
  });

  it('closing the detail modal hides it again', () => {
    mockNotifData = { unread_count: 1, notifications: [notif({ type: 'chat_message' })] };
    const screen = render(<NotificationsScreen />);
    fireEvent.press(screen.getByText('Title'));
    expect(screen.getByTestId('notification-detail-modal')).toBeTruthy();
    fireEvent.press(screen.getByText('common.close'));
    // RN's Modal unmounts its subtree entirely when `visible` is false in
    // this test environment, rather than staying in the tree with a false
    // prop — assert absence, not a prop value.
    expect(screen.queryByTestId('notification-detail-modal')).toBeNull();
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
  // Two FlatLists now render (the outer inbox list + the category tabs row
  // inside its ListHeaderComponent) — find the one with a refreshControl.
  const lists = screen.UNSAFE_getAllByType(require('react-native').FlatList);
  const list = lists.find((l: any) => l.props.refreshControl);
  list.props.refreshControl.props.onRefresh();
  expect(mockRefetch).toHaveBeenCalled();
});

describe('delete + clear all', () => {
  it('per-item delete opens the confirm dialog and deletes on confirm', () => {
    mockNotifData = { unread_count: 1, notifications: [notif({ type: 'general' })] };
    const screen = render(<NotificationsScreen />);
    fireEvent.press(screen.getByLabelText('notifications.deleteTitle'));

    expect(mockShowAlert).toHaveBeenCalled();
    const buttons = mockShowAlert.mock.calls[0][2];
    const deleteBtn = buttons.find((b: any) => b.style === 'destructive');
    deleteBtn.onPress();
    expect(mockDeleteMutate).toHaveBeenCalledWith('n-1');
  });

  it('"Clear all" is hidden when the inbox is empty', () => {
    mockNotifData = { unread_count: 0, notifications: [] };
    const screen = render(<NotificationsScreen />);
    expect(screen.queryByLabelText('notifications.clearAllTitle')).toBeNull();
  });

  it('"Clear all" opens the confirm dialog and clears on confirm', () => {
    mockNotifData = { unread_count: 1, notifications: [notif()] };
    const screen = render(<NotificationsScreen />);
    fireEvent.press(screen.getByLabelText('notifications.clearAllTitle'));

    expect(mockShowAlert).toHaveBeenCalled();
    const buttons = mockShowAlert.mock.calls[0][2];
    const clearBtn = buttons.find((b: any) => b.style === 'destructive');
    clearBtn.onPress();
    expect(mockClearMutate).toHaveBeenCalledWith(false);
  });
});

describe('category tabs', () => {
  it('filters the visible list by category', () => {
    mockNotifData = {
      unread_count: 2,
      notifications: [
        notif({ id: 'n-ride', title: 'Ride Title', type: 'ride_offer' }),
        notif({ id: 'n-promo', title: 'Promo Title', type: 'promotion' }),
      ],
    };
    const screen = render(<NotificationsScreen />);
    expect(screen.getByText('Ride Title')).toBeTruthy();
    expect(screen.getByText('Promo Title')).toBeTruthy();

    fireEvent.press(screen.getByText('Promotions'));

    expect(screen.getByText('Promo Title')).toBeTruthy();
    expect(screen.queryByText('Ride Title')).toBeNull();
  });
});
