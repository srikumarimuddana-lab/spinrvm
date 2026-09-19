/**
 * app/notifications.tsx — rider notifications inbox. Pins:
 *  - renders the list from useNotifications()
 *  - a load failure sets a distinct "couldn't load" empty state (not the
 *    cheerful "all caught up" copy) with a working Retry
 *  - tapping an unread notification calls useMarkNotificationRead
 *  - "Mark all read" calls useMarkAllNotificationsRead
 *  - per-item delete opens a confirm sheet and calls useDeleteNotification
 *  - "Clear all" opens a confirm sheet and calls useClearNotifications(false)
 *  - category tabs filter the visible list by the existing `type` taxonomy
 *  - tap-through routing: lost_and_found (by case id, else the list),
 *    chat_message / ride_completed / driver_accepted / driver_arrived
 *    (all gated on having a ride/case id present)
 *
 * The screen now sources data via the shared TanStack Query hooks
 * (@shared/hooks/queries) instead of ad-hoc useState + api.get/put, so this
 * file mocks that module directly — the same pattern settingsScreen.test.tsx
 * already uses for the same hooks module.
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, RefreshControl } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

const mockPush = jest.fn();
const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush, back: mockBack }),
}));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111',
  textDim: '#666', border: '#E5E7EB', orange: '#F97316', danger: '#DC2626',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

// ConfirmSheet is replaced with a lightweight double (matches
// savedPlacesScreen.test.tsx's convention) to bypass @gorhom/bottom-sheet.
jest.mock('../components/ConfirmSheet', () => (props: any) => {
  const { View, Text: RNText, TouchableOpacity: RNTouchableOpacity } = require('react-native');
  if (!props.visible) return null;
  return (
    <View>
      <RNText>{props.title}</RNText>
      <RNText>{props.message}</RNText>
      {(props.buttons || []).map((b: any, i: number) => (
        <RNTouchableOpacity key={i} onPress={b.onPress || props.onClose} accessibilityLabel={`confirm-${b.text}`}>
          <RNText>{b.text}</RNText>
        </RNTouchableOpacity>
      ))}
    </View>
  );
});

const N1 = { id: 'n1', title: 'Ride update', body: 'Your driver is arriving', type: 'ride_update', is_read: false, created_at: new Date().toISOString() };
const N2 = { id: 'n2', title: 'Promo', body: '20% off', type: 'promotion', is_read: true, created_at: new Date().toISOString() };

let mockNotificationsData: any = { notifications: [N1, N2], unread_count: 1 };
let mockIsLoading = false;
let mockIsError = false;
const mockRefetch = jest.fn();
const mockMarkRead = jest.fn();
const mockMarkAllRead = jest.fn();
const mockDeleteNotification = jest.fn();
const mockClearNotifications = jest.fn();

jest.mock('@shared/hooks/queries', () => ({
  useNotifications: () => ({
    data: mockNotificationsData,
    isLoading: mockIsLoading,
    isError: mockIsError,
    isFetching: false,
    refetch: mockRefetch,
  }),
  useMarkNotificationRead: () => ({ mutate: mockMarkRead }),
  useMarkAllNotificationsRead: () => ({ mutate: mockMarkAllRead }),
  useDeleteNotification: () => ({ mutate: mockDeleteNotification }),
  useClearNotifications: () => ({ mutate: mockClearNotifications }),
}));

import NotificationsScreen from '../app/notifications';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<NotificationsScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => JSON.stringify(t.props.children)).join(' | ');
}

function findCardByTitle(r: TestRenderer.ReactTestRenderer, title: string) {
  return r.root
    .findAllByType(TouchableOpacity)
    .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes(title)))!;
}

function findConfirmButton(r: TestRenderer.ReactTestRenderer, label: string) {
  return r.root.findByProps({ accessibilityLabel: `confirm-${label}` });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockNotificationsData = { notifications: [N1, N2], unread_count: 1 };
  mockIsLoading = false;
  mockIsError = false;
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('NotificationsScreen', () => {
  it('renders notifications from useNotifications()', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('Ride update');
  });

  it('shows the couldn\'t-load empty state (not the cheerful one) with a working retry', async () => {
    mockIsError = true;
    mockNotificationsData = undefined;
    const r = await renderScreen();
    expect(allText(r)).toContain("Couldn't load notifications");
    expect(allText(r)).not.toContain('No notifications');

    const retryBtn = findCardByTitle(r, 'Retry');
    act(() => { retryBtn.props.onPress(); });
    expect(mockRefetch).toHaveBeenCalled();
  });

  it('shows the cheerful empty state when there genuinely are no notifications', async () => {
    mockNotificationsData = { notifications: [], unread_count: 0 };
    const r = await renderScreen();
    expect(allText(r)).toContain('No notifications');
  });

  it('marks an unread notification read on tap', async () => {
    const r = await renderScreen();
    const card = findCardByTitle(r, 'Ride update');
    act(() => { card.props.onPress(); });
    expect(mockMarkRead).toHaveBeenCalledWith('n1');
  });

  it('"Mark all read" calls useMarkAllNotificationsRead', async () => {
    const r = await renderScreen();
    const markAllBtn = findCardByTitle(r, 'Mark all read');
    act(() => { markAllBtn.props.onPress(); });
    expect(mockMarkAllRead).toHaveBeenCalled();
  });

  it('hides "Mark all read" when unread_count is 0', async () => {
    mockNotificationsData = { notifications: [N2], unread_count: 0 };
    const r = await renderScreen();
    expect(() => findCardByTitle(r, 'Mark all read')).not.toThrow();
    expect(findCardByTitle(r, 'Mark all read')).toBeUndefined();
  });

  it('deleting a notification opens a confirm sheet and calls useDeleteNotification on confirm', async () => {
    const r = await renderScreen();
    const deleteBtn = r.root.findAllByProps({ accessibilityLabel: 'Delete notification' })[0];
    act(() => { deleteBtn.props.onPress(); });

    const confirmBtn = findConfirmButton(r, 'Delete');
    act(() => { confirmBtn.props.onPress(); });
    expect(mockDeleteNotification).toHaveBeenCalledWith('n1');
  });

  it('"Clear all" opens a confirm sheet and calls useClearNotifications(false) on confirm', async () => {
    const r = await renderScreen();
    const clearBtn = r.root.findAllByProps({ accessibilityLabel: 'Clear all notifications' })[0];
    act(() => { clearBtn.props.onPress(); });

    const confirmBtn = findConfirmButton(r, 'Clear All');
    act(() => { confirmBtn.props.onPress(); });
    expect(mockClearNotifications).toHaveBeenCalledWith(false);
  });

  it('does not show "Clear all" when the inbox is empty', async () => {
    mockNotificationsData = { notifications: [], unread_count: 0 };
    const r = await renderScreen();
    expect(r.root.findAllByProps({ accessibilityLabel: 'Clear all notifications' })).toHaveLength(0);
  });

  it('category tabs filter the visible list', async () => {
    mockNotificationsData = { notifications: [N1, N2], unread_count: 1 };
    const r = await renderScreen();
    expect(allText(r)).toContain('Ride update');
    expect(allText(r)).toContain('Promo');

    const promoTab = findCardByTitle(r, 'Promotions');
    act(() => { promoTab.props.onPress(); });

    expect(allText(r)).toContain('Promo');
    expect(allText(r)).not.toContain('Ride update');
  });

  it('routes to /lost-and-found-chat with the case id when present', async () => {
    mockNotificationsData = {
      notifications: [{ ...N1, id: 'n3', title: 'Lost item found', type: 'lost_and_found', data: { case_id: 'case-1' } }],
      unread_count: 1,
    };
    const r = await renderScreen();
    const card = findCardByTitle(r, 'Lost item found');
    act(() => { card.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/lost-and-found-chat', params: { caseId: 'case-1' } });
  });

  it('routes to the /lost-and-found list when no case id is present', async () => {
    mockNotificationsData = {
      notifications: [{ ...N1, id: 'n4', title: 'Lost item update', type: 'lost_and_found_message' }],
      unread_count: 1,
    };
    const r = await renderScreen();
    const card = findCardByTitle(r, 'Lost item update');
    act(() => { card.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/lost-and-found');
  });

  it('does not navigate for a chat_message notification with no ride id, and opens the detail modal instead', async () => {
    mockNotificationsData = {
      notifications: [{ ...N1, id: 'n5', title: 'New message', type: 'chat_message', body: 'Full message text' }],
      unread_count: 1,
    };
    const r = await renderScreen();
    const card = findCardByTitle(r, 'New message');
    act(() => { card.props.onPress(); });
    expect(mockPush).not.toHaveBeenCalled();
    const modal = r.root.findByProps({ testID: 'notification-detail-modal' });
    expect(modal.props.visible).toBe(true);
    const body = r.root.findByProps({ testID: 'notification-detail-body' });
    expect(body.props.children).toBe('Full message text');
  });

  it('routes to /driver-arriving for a driver_accepted notification with a ride id', async () => {
    mockNotificationsData = {
      notifications: [{ ...N1, id: 'n6', title: 'Driver on the way', type: 'driver_accepted', data: { ride_id: 'ride-1' } }],
      unread_count: 1,
    };
    const r = await renderScreen();
    const card = findCardByTitle(r, 'Driver on the way');
    act(() => { card.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/driver-arriving', params: { rideId: 'ride-1' } });
  });

  it('navigates back when the back button is pressed', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => { backBtn.props.onPress(); });
    expect(mockBack).toHaveBeenCalled();
  });

  it('routes to /ride-completed for a ride_completed notification with a ride id', async () => {
    mockNotificationsData = {
      notifications: [{ ...N1, id: 'n7', title: 'Trip finished', type: 'ride_completed', data: { ride_id: 'ride-2' } }],
      unread_count: 1,
    };
    const r = await renderScreen();
    const card = findCardByTitle(r, 'Trip finished');
    act(() => { card.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/ride-completed', params: { rideId: 'ride-2' } });
  });

  it('does not navigate for a ride_completed notification with no ride id', async () => {
    mockNotificationsData = {
      notifications: [{ ...N1, id: 'n8', title: 'Trip wrapped up', type: 'ride_completed' }],
      unread_count: 1,
    };
    const r = await renderScreen();
    const card = findCardByTitle(r, 'Trip wrapped up');
    act(() => { card.props.onPress(); });
    expect(mockPush).not.toHaveBeenCalled();
  });

  it('pull-to-refresh calls refetch', async () => {
    const r = await renderScreen();
    const refreshControl = r.root.findByType(RefreshControl);
    act(() => { refreshControl.props.onRefresh(); });
    expect(mockRefetch).toHaveBeenCalled();
  });

  it('renders each relative-time bucket: minutes, hours, and days ago', async () => {
    const now = Date.now();
    mockNotificationsData = {
      notifications: [
        { ...N1, id: 'n-min', title: 'Five min old', created_at: new Date(now - 5 * 60 * 1000).toISOString() },
        { ...N1, id: 'n-hr', title: 'Three hr old', created_at: new Date(now - 3 * 60 * 60 * 1000).toISOString() },
        { ...N1, id: 'n-day', title: 'Two day old', created_at: new Date(now - 2 * 24 * 60 * 60 * 1000).toISOString() },
      ],
      unread_count: 3,
    };
    const r = await renderScreen();
    expect(allText(r)).toContain('5m ago');
    expect(allText(r)).toContain('3h ago');
    expect(allText(r)).toContain('2d ago');
  });

  it('opens the detail modal for a fully unmapped type (e.g. ride_cancelled) and closing it hides it again', async () => {
    mockNotificationsData = {
      notifications: [{ ...N1, id: 'n9', title: 'Ride Cancelled', type: 'ride_cancelled', body: 'The rider cancelled this ride.' }],
      unread_count: 1,
    };
    const r = await renderScreen();
    const card = findCardByTitle(r, 'Ride Cancelled');
    act(() => { card.props.onPress(); });
    expect(mockPush).not.toHaveBeenCalled();
    let modal = r.root.findByProps({ testID: 'notification-detail-modal' });
    expect(modal.props.visible).toBe(true);

    const closeBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Close')),
    )!;
    act(() => { closeBtn.props.onPress(); });
    modal = r.root.findByProps({ testID: 'notification-detail-modal' });
    expect(modal.props.visible).toBe(false);
  });

  it('colors a safety notification icon with the danger color', async () => {
    mockNotificationsData = {
      notifications: [{ ...N1, id: 'n-safety', title: 'Safety alert', type: 'safety' }],
      unread_count: 1,
    };
    const r = await renderScreen();
    const icon = r.root.findAllByType(Ionicons).find((n) => n.props.name === 'shield-checkmark');
    expect(icon).toBeDefined();
    expect(icon!.props.color).toBe(COLORS.danger);
  });
});
