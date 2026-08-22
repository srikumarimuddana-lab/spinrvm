/**
 * app/notifications.tsx — rider notifications inbox. Pins:
 *  - GET /notifications?limit=50&offset=0 on mount
 *  - a load failure sets a distinct "couldn't load" empty state (not the
 *    cheerful "all caught up" copy — the file's own stated reason) with a
 *    working Retry
 *  - tapping an unread notification marks it read optimistically (PUT
 *    /notifications/:id/read) and rolls back on failure
 *  - "Mark all read" optimistically clears unread state and re-fetches on
 *    failure to restore accurate state
 *  - tap-through routing: lost_and_found (by case id, else the list),
 *    chat_message / ride_completed / driver_accepted / driver_arrived
 *    (all gated on having a ride/case id present)
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text } from 'react-native';

import NotificationsScreen from '../app/notifications';

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

const mockApiGet = jest.fn();
const mockApiPut = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a), put: (...a: any[]) => mockApiPut(...a) },
}));

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

const N1 = { id: 'n1', title: 'Ride update', body: 'Your driver is arriving', type: 'ride_update', is_read: false, created_at: new Date().toISOString() };
const N2 = { id: 'n2', title: 'Promo', body: '20% off', type: 'promotion', is_read: true, created_at: new Date().toISOString() };

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

beforeEach(() => {
  jest.clearAllMocks();
  mockApiGet.mockResolvedValue({ data: { notifications: [N1, N2], unread_count: 1 } });
  mockApiPut.mockResolvedValue({ data: {} });
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('NotificationsScreen', () => {
  it('loads notifications on mount', async () => {
    const r = await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/notifications?limit=50&offset=0');
    expect(allText(r)).toContain('Ride update');
  });

  it('shows the couldn\'t-load empty state (not the cheerful one) with a working retry', async () => {
    mockApiGet.mockRejectedValue(new Error('network down'));
    const r = await renderScreen();
    expect(allText(r)).toContain("Couldn't load notifications");
    expect(allText(r)).not.toContain('No notifications');

    mockApiGet.mockResolvedValue({ data: { notifications: [N1], unread_count: 1 } });
    const retryBtn = findCardByTitle(r, 'Retry');
    await act(async () => {
      await retryBtn.props.onPress();
      await flush();
    });
    expect(allText(r)).toContain('Ride update');
  });

  it('shows the cheerful empty state when there genuinely are no notifications', async () => {
    mockApiGet.mockResolvedValue({ data: { notifications: [], unread_count: 0 } });
    const r = await renderScreen();
    expect(allText(r)).toContain('No notifications');
  });

  it('marks an unread notification read optimistically on tap', async () => {
    const r = await renderScreen();
    const card = findCardByTitle(r, 'Ride update');
    await act(async () => {
      card.props.onPress();
      await flush();
    });
    expect(mockApiPut).toHaveBeenCalledWith('/notifications/n1/read');
  });

  it('rolls back the read state if the PUT fails', async () => {
    mockApiPut.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const card = findCardByTitle(r, 'Ride update');
    await act(async () => {
      card.props.onPress();
      await flush();
    });
    // Unread count should be restored to 1 after rollback -- surfaced via
    // "Mark all read" reappearing (it's hidden at unreadCount === 0).
    expect(allText(r)).toContain('Mark all read');
  });

  it('"Mark all read" clears unread state optimistically', async () => {
    const r = await renderScreen();
    const markAllBtn = findCardByTitle(r, 'Mark all read');
    await act(async () => {
      await markAllBtn.props.onPress();
      await flush();
    });
    expect(mockApiPut).toHaveBeenCalledWith('/notifications/read-all');
    expect(findCardByTitle(r, 'Mark all read')).toBeUndefined();
  });

  it('re-fetches to restore state when "Mark all read" fails', async () => {
    mockApiPut.mockImplementation((url: string) => {
      if (url === '/notifications/read-all') return Promise.reject(new Error('server error'));
      return Promise.resolve({ data: {} });
    });
    const r = await renderScreen();
    mockApiGet.mockClear();
    const markAllBtn = findCardByTitle(r, 'Mark all read');
    await act(async () => {
      await markAllBtn.props.onPress();
      await flush();
    });
    expect(mockApiGet).toHaveBeenCalledWith('/notifications?limit=50&offset=0');
  });

  it('routes to /lost-and-found-chat with the case id when present', async () => {
    mockApiGet.mockResolvedValue({
      data: {
        notifications: [{ ...N1, id: 'n3', title: 'Lost item found', type: 'lost_and_found', data: { case_id: 'case-1' } }],
        unread_count: 1,
      },
    });
    const r = await renderScreen();
    const card = findCardByTitle(r, 'Lost item found');
    await act(async () => {
      card.props.onPress();
      await flush();
    });
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/lost-and-found-chat', params: { caseId: 'case-1' } });
  });

  it('routes to the /lost-and-found list when no case id is present', async () => {
    mockApiGet.mockResolvedValue({
      data: { notifications: [{ ...N1, id: 'n4', title: 'Lost item update', type: 'lost_and_found_message' }], unread_count: 1 },
    });
    const r = await renderScreen();
    const card = findCardByTitle(r, 'Lost item update');
    await act(async () => {
      card.props.onPress();
      await flush();
    });
    expect(mockPush).toHaveBeenCalledWith('/lost-and-found');
  });

  it('does not navigate for a chat_message notification with no ride id', async () => {
    mockApiGet.mockResolvedValue({
      data: { notifications: [{ ...N1, id: 'n5', title: 'New message', type: 'chat_message' }], unread_count: 1 },
    });
    const r = await renderScreen();
    const card = findCardByTitle(r, 'New message');
    await act(async () => {
      card.props.onPress();
      await flush();
    });
    expect(mockPush).not.toHaveBeenCalled();
  });

  it('routes to /driver-arriving for a driver_accepted notification with a ride id', async () => {
    mockApiGet.mockResolvedValue({
      data: { notifications: [{ ...N1, id: 'n6', title: 'Driver on the way', type: 'driver_accepted', data: { ride_id: 'ride-1' } }], unread_count: 1 },
    });
    const r = await renderScreen();
    const card = findCardByTitle(r, 'Driver on the way');
    await act(async () => {
      card.props.onPress();
      await flush();
    });
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/driver-arriving', params: { rideId: 'ride-1' } });
  });

  it('navigates back when the back button is pressed', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => {
      backBtn.props.onPress();
    });
    expect(mockBack).toHaveBeenCalled();
  });
});
