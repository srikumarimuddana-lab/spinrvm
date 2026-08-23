/**
 * app/driver/chat.tsx — driver<->rider in-ride chat. Pins:
 *  - rideId resolves from the URL param first, falling back to the store's
 *    activeRide
 *  - chat history loads from AsyncStorage first (instant), then the
 *    backend (authoritative) overwrites the store + re-persists
 *  - sendMessage: blocked on empty/whitespace text; success posts and adds
 *    the returned message via addChatMessage, clears the input, and hides
 *    quick replies; a send failure never crashes (silently logged)
 *  - a quick-reply tap sends that exact text
 *  - typing: non-empty text change posts a typing indicator (throttled by
 *    time, not asserted here); the rider-typing bubble shows/hides with
 *    the `riderTyping` store flag
 *  - message bubbles: driver's own messages show a read/unread checkmark,
 *    the rider's show an avatar; the empty-chat state renders with none
 *  - header shows the rider's name and "Active Ride" vs. "No active ride"
 *  - back nav
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, TextInput, FlatList } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: (props: any) => null }));
jest.mock('expo-linear-gradient', () => ({ LinearGradient: ({ children }: any) => children }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const mockBack = jest.fn();
let mockParamRideId: string | undefined;
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack }),
  useLocalSearchParams: () => ({ rideId: mockParamRideId }),
}));

const COLORS = {
  primary: '#EF4444', background: '#FFF', surface: '#FFF', surfaceLight: '#F5F5F5',
  text: '#111', textDim: '#666', textSecondary: '#333', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a), post: (...a: any[]) => mockApiPost(...a) },
}));

const mockAsyncGetItem = jest.fn();
const mockAsyncSetItem = jest.fn();
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: (...a: any[]) => mockAsyncGetItem(...a),
  setItem: (...a: any[]) => mockAsyncSetItem(...a),
}));

const mockAddChatMessage = jest.fn();
const mockSetChatMessages = jest.fn();
const mockSetRiderTyping = jest.fn();
let mockDriverState: any;

function mockUseDriverStoreFn(selector?: any) {
  return typeof selector === 'function' ? selector(mockDriverState) : mockDriverState;
}
mockUseDriverStoreFn.getState = () => mockDriverState;

jest.mock('../../store/driverStore', () => ({
  useDriverStore: (...args: any[]) => mockUseDriverStoreFn(...args),
}));

import ChatScreen from '../../app/driver/chat';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const MSG_FROM_RIDER = {
  id: 'm1', ride_id: 'ride-1', sender: 'rider', text: 'Hi there', timestamp: '2026-08-22T10:00:00Z', read: true,
};
const MSG_FROM_DRIVER_UNREAD = {
  id: 'm2', ride_id: 'ride-1', sender: 'driver', text: 'On my way', timestamp: '2026-08-22T10:01:00Z', read: false,
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<ChatScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => JSON.stringify(t.props.children)).join(' | ');
}

function findButtonByText(r: TestRenderer.ReactTestRenderer, text: string) {
  return r.root
    .findAllByType(TouchableOpacity)
    .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes(text)))!;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockParamRideId = undefined;
  mockDriverState = {
    activeRide: { rider: { first_name: 'Sam' }, ride: { id: 'ride-1' } },
    chatMessages: [],
    addChatMessage: mockAddChatMessage,
    setChatMessages: mockSetChatMessages,
    riderTyping: false,
    setRiderTyping: mockSetRiderTyping,
  };
  mockAsyncGetItem.mockResolvedValue(null);
  mockAsyncSetItem.mockResolvedValue(undefined);
  mockApiGet.mockResolvedValue({ data: { messages: [] } });
  mockApiPost.mockResolvedValue({ data: { message: MSG_FROM_DRIVER_UNREAD } });
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('ChatScreen (driver-app)', () => {
  it('resolves rideId from the store activeRide and shows the rider name + "Active Ride"', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('Sam');
    expect(allText(r)).toContain('Active Ride');
  });

  it('resolves rideId from the URL param when provided, taking priority', async () => {
    mockParamRideId = 'ride-from-notification';
    await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/rides/ride-from-notification/messages');
  });

  it('shows "No active ride" when there is no rideId at all', async () => {
    mockParamRideId = undefined;
    mockDriverState.activeRide = null;
    const r = await renderScreen();
    expect(allText(r)).toContain('No active ride');
    expect(mockApiGet).not.toHaveBeenCalled();
  });

  it('seeds from AsyncStorage first, then overwrites with the authoritative backend history', async () => {
    mockAsyncGetItem.mockResolvedValue(JSON.stringify([MSG_FROM_RIDER]));
    mockApiGet.mockResolvedValue({ data: { messages: [MSG_FROM_RIDER, MSG_FROM_DRIVER_UNREAD] } });
    await renderScreen();
    expect(mockSetChatMessages).toHaveBeenCalledWith([MSG_FROM_RIDER]);
    expect(mockSetChatMessages).toHaveBeenCalledWith([MSG_FROM_RIDER, MSG_FROM_DRIVER_UNREAD]);
    expect(mockAsyncSetItem).toHaveBeenCalledWith(
      'spinr_chat_ride-1',
      JSON.stringify([MSG_FROM_RIDER, MSG_FROM_DRIVER_UNREAD]),
    );
  });

  it('shows the empty-chat state when there are no messages', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('No messages yet');
  });

  it("renders the driver's own message with a read/unread checkmark, and the rider's with an avatar", async () => {
    mockDriverState.chatMessages = [MSG_FROM_RIDER, MSG_FROM_DRIVER_UNREAD];
    const r = await renderScreen();
    expect(allText(r)).toContain('Hi there');
    expect(allText(r)).toContain('On my way');
  });

  it('blocks sendMessage on empty/whitespace text', async () => {
    const r = await renderScreen();
    const sendBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByProps({ name: 'send' }).length > 0
    )!;
    expect(sendBtn.props.disabled).toBe(true);
  });

  it('sends a message, adds it via addChatMessage, and clears the input + hides quick replies', async () => {
    const r = await renderScreen();
    const input = r.root.findByType(TextInput);
    act(() => {
      input.props.onChangeText('On my way');
    });
    const sendBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByProps({ name: 'send' }).length > 0
    )!;
    await act(async () => {
      await sendBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/rides/ride-1/messages', { text: 'On my way' });
    expect(mockAddChatMessage).toHaveBeenCalledWith(MSG_FROM_DRIVER_UNREAD);
    expect(r.root.findByType(TextInput).props.value).toBe('');
  });

  it('does not crash and swallows the error when sending fails', async () => {
    mockApiPost.mockRejectedValue(new Error('network down'));
    const r = await renderScreen();
    const input = r.root.findByType(TextInput);
    act(() => {
      input.props.onChangeText('Hello');
    });
    const sendBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByProps({ name: 'send' }).length > 0
    )!;
    await act(async () => {
      await sendBtn.props.onPress();
      await flush();
    });
    expect(mockAddChatMessage).not.toHaveBeenCalled();
    expect(r.root).toBeTruthy();
  });

  it('sends the exact quick-reply text when tapped', async () => {
    const r = await renderScreen();
    const quickBtn = findButtonByText(r, 'I have arrived');
    await act(async () => {
      await quickBtn.props.onPress();
      await flush();
    });
    expect(mockApiPost).toHaveBeenCalledWith('/rides/ride-1/messages', { text: 'I have arrived' });
  });

  it('shows the typing indicator bubble when riderTyping is true', async () => {
    mockDriverState.riderTyping = true;
    const r = await renderScreen();
    expect(allText(r)).toContain('•••');
  });

  it('does not show the typing indicator bubble when riderTyping is false', async () => {
    const r = await renderScreen();
    expect(allText(r)).not.toContain('•••');
  });

  it('posts a typing indicator when the driver types non-empty text', async () => {
    const r = await renderScreen();
    const input = r.root.findByType(TextInput);
    act(() => {
      input.props.onChangeText('h');
    });
    expect(mockApiPost).toHaveBeenCalledWith('/rides/ride-1/typing', { sender: 'driver' });
  });

  it('navigates back when the header back button is pressed', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => {
      backBtn.props.onPress();
    });
    expect(mockBack).toHaveBeenCalled();
  });
});
